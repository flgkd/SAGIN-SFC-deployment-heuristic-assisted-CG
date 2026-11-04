from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from algorithm_names import ALGORITHM_NAMES, SHORT_NAMES
from record_compatibility import normalize_record, normalize_dataframe, resolve_input_path


import csv
import json
import math
import statistics
from collections import Counter
from pathlib import Path

import matplotlib as mpl
import matplotlib.lines as mlines
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FuncFormatter, FixedLocator, NullFormatter

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "figures" / "component_ablation" / "final"
FROZEN = ROOT / "results" / "frozen" / "component_ablation_s40_v3"
SEED_LEVEL = FROZEN / "component_ablation_seed_level.csv"
MANIFEST = FROZEN / "component_ablation_manifest.json"

LOADS = ["Low", "Medium", "High"]
SEEDS = list(range(10))
ALGS = [
    "dwcg_pure",
    "dwcg_warm",
    "dwcg_pure_hp",
    "dwcg_saginfe",
    "dwcg_saginfe_pro_no_pfe",
    "dwcg_saginfe_pro",
]

FULL_LABEL = {algorithm: SHORT_NAMES[algorithm] for algorithm in ALGS}

XTICK_LABEL = {algorithm: SHORT_NAMES[algorithm] for algorithm in ALGS}

XTICK_WRAP_REASON = {
    "dwcg_pure": "one line; fits comfortably",
    "dwcg_warm": "one line; fits comfortably",
    "dwcg_pure_hp": "one line; fits comfortably",
    "dwcg_saginfe": "one line; fits comfortably",
    "dwcg_saginfe_pro_no_pfe": "wrapped once at the PRO modifier to avoid crowding the adjacent long PRO-family tick label",
    "dwcg_saginfe_pro": "one line; fits comfortably",
}

# Matplotlib fills multi-column legends column-major, so this order renders as:
# row 1: Pure, Warm, HP; row 2: HFP, PRO-no-PFE, PRO.
LEGEND_ORDER = [
    "dwcg_pure",
    "dwcg_saginfe",
    "dwcg_warm",
    "dwcg_saginfe_pro_no_pfe",
    "dwcg_pure_hp",
    "dwcg_saginfe_pro",
]

LEGEND_LAYOUT_BY_ROW = [["Pure", "Warm", "HP"], ["HFP", "PRO-no-PFE", "PRO"]]

STYLE = {
    "dwcg_pure": {"color": "#D55E00", "marker": "s"},
    "dwcg_warm": {"color": "#E69F00", "marker": "p"},
    "dwcg_pure_hp": {"color": "#CC79A7", "marker": "v"},
    "dwcg_saginfe": {"color": "#009E73", "marker": "^"},
    "dwcg_saginfe_pro_no_pfe": {"color": "#7B3294", "marker": "P"},
    "dwcg_saginfe_pro": {"color": "#C51B7D", "marker": "X"},
}

GRAY = {
    "dwcg_pure": "#555555",
    "dwcg_warm": "#333333",
    "dwcg_pure_hp": "#777777",
    "dwcg_saginfe": "#999999",
    "dwcg_saginfe_pro_no_pfe": "#BBBBBB",
    "dwcg_saginfe_pro": "#000000",
}

MEAN_COLOR = "#8B1A1A"
MEAN_GRAY = "#444444"
MEAN_LINESTYLE = ":"
FOOTER = "S40 (|T|=8, |F|=160) Component Ablation"


def setup_style() -> None:
    mpl.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.labelsize": 10,
            "axes.titlesize": 11,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "legend.fontsize": 9,
            "figure.dpi": 160,
            "savefig.dpi": 300,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.linewidth": 0.8,
            "grid.linewidth": 0.45,
        }
    )


def bool_value(value: str) -> bool:
    return value in {"True", "true", "1", "yes", "YES"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with resolve_input_path(path).open("r", encoding="utf-8", newline="") as handle:
        return normalize_record(list(csv.DictReader(handle)))


def load_rows() -> list[dict[str, str]]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    final_audit = manifest["final_audit"]
    if not final_audit.get("pass"):
        raise SystemExit("frozen component-ablation final audit is not PASS")
    if final_audit.get("record_count") != 180:
        raise SystemExit("frozen component-ablation record count is not 180")
    if final_audit.get("v2_experimental_data_used") != 0:
        raise SystemExit("frozen component-ablation manifest admits V2 records")

    rows = read_csv(SEED_LEVEL)
    expected = {("S40", load, seed, alg) for load in LOADS for seed in SEEDS for alg in ALGS}
    seen = Counter((r["scale"], r["load"], int(r["seed"]), r["algorithm"]) for r in rows)
    if len(rows) != 180 or set(seen) != expected or any(count != 1 for count in seen.values()):
        raise SystemExit("component-ablation seed-level table does not match the expected S40 6x3x10 matrix")

    for row in rows:
        accepted = int(row["accepted_flow_count"])
        total = int(row["total_flow_count"])
        rate = float(row["acceptance_rate"])
        runtime = float(row["total_end_to_end_runtime_seconds"])
        if total != 160 or not (0 <= accepted <= total):
            raise SystemExit(f"invalid acceptance row: {row}")
        if not math.isclose(rate, accepted / total, rel_tol=0.0, abs_tol=1e-12):
            raise SystemExit(f"acceptance_rate mismatch: {row}")
        if not bool_value(row["path_validity"]) or not bool_value(row["capacity_validity"]):
            raise SystemExit(f"validity failure: {row}")
        if not bool_value(row["runtime_accounting_valid"]):
            raise SystemExit(f"runtime accounting failure: {row}")
        if not math.isfinite(runtime) or runtime <= 0.0:
            raise SystemExit(f"runtime invalid for log plotting: {row}")
        if bool_value(row["hard_wall_clock_limit_reached"]):
            raise SystemExit(f"interrupted execution present: {row}")
    return rows


def deterministic_jitter(seed: int, scale: float = 0.052) -> float:
    pattern = [-4, -1, 3, -2, 4, 1, -3, 2, 0, 5]
    return pattern[seed % len(pattern)] * scale / 5.0


def color_for(alg: str, grayscale: bool) -> str:
    return GRAY[alg] if grayscale else STYLE[alg]["color"]


def add_legend(fig, grayscale: bool) -> None:
    handles = [
        mlines.Line2D(
            [],
            [],
            color=color_for(alg, grayscale),
            marker=STYLE[alg]["marker"],
            linestyle="None",
            markersize=5.6,
            label=FULL_LABEL[alg],
        )
        for alg in LEGEND_ORDER
    ]
    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.992),
        ncol=3,
        frameon=False,
        columnspacing=3.1,
        handletextpad=0.55,
        labelspacing=0.9,
        borderaxespad=0.0,
    )


def draw_boxpanel(
    ax,
    rows: list[dict[str, str]],
    load: str,
    values_for,
    ylabel: str,
    grayscale: bool,
    caption: str,
    *,
    y_limits: tuple[float, float] | None = None,
    log_scale: bool = False,
) -> None:
    positions = np.arange(len(ALGS), dtype=float)
    width = 0.42
    for idx, alg in enumerate(ALGS):
        values = [values_for(r) for r in rows if r["load"] == load and r["algorithm"] == alg]
        bp = ax.boxplot(
            [values],
            positions=[positions[idx]],
            widths=width,
            patch_artist=True,
            showfliers=False,
            showmeans=False,
            whis=1.5,
            zorder=2,
            boxprops={
                "facecolor": color_for(alg, grayscale),
                "edgecolor": color_for(alg, grayscale),
                "alpha": 0.22,
                "linewidth": 1.0,
            },
            medianprops={"color": "black", "linewidth": 1.7, "zorder": 9},
            whiskerprops={"color": color_for(alg, grayscale), "linewidth": 1.0},
            capprops={"color": color_for(alg, grayscale), "linewidth": 1.0},
        )
        for element in ["boxes", "whiskers", "caps", "medians"]:
            for artist in bp[element]:
                artist.set_zorder(7 if element == "medians" else 2)

        mean = statistics.mean(values)
        ax.plot(
            [positions[idx] - width / 2.05, positions[idx] + width / 2.05],
            [mean, mean],
            color=MEAN_GRAY if grayscale else MEAN_COLOR,
            linestyle=MEAN_LINESTYLE,
            linewidth=1.8,
            zorder=10,
            solid_capstyle="butt",
        )

        for row in rows:
            if row["load"] != load or row["algorithm"] != alg:
                continue
            seed = int(row["seed"])
            ax.scatter(
                positions[idx] + deterministic_jitter(seed),
                values_for(row),
                s=22,
                marker=STYLE[alg]["marker"],
                facecolors=color_for(alg, grayscale),
                edgecolors="white" if not grayscale else "#F0F0F0",
                linewidths=0.45,
                alpha=0.82,
                zorder=5,
            )

    if log_scale:
        ax.set_yscale("log")
        ax.yaxis.set_major_locator(FixedLocator([180, 300, 600, 900, 1200, 1800]))
        ax.yaxis.set_major_formatter(FuncFormatter(format_runtime_tick))
        ax.yaxis.set_minor_formatter(NullFormatter())
    if y_limits is not None:
        ax.set_ylim(*y_limits)
    ax.set_xlim(-0.65, len(ALGS) - 0.35)
    ax.set_xticks(positions)
    ax.set_xticklabels([XTICK_LABEL[a] for a in ALGS], linespacing=1.05)
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", color="#D0D0D0", linestyle="-", alpha=0.55)
    ax.set_axisbelow(True)
    ax.text(0.5, -0.36, caption, transform=ax.transAxes, ha="center", va="top", fontsize=10.5)
    for spine in ax.spines.values():
        spine.set_linewidth(0.8)


def format_runtime_tick(value: float, _pos: int | None = None) -> str:
    if value >= 60 and abs(value / 60 - round(value / 60)) < 1e-9:
        return f"{int(round(value / 60))} min"
    return f"{value:g} s"


def plot_figure(
    rows: list[dict[str, str]],
    *,
    metric: str,
    grayscale: bool,
) -> tuple[Path, Path]:
    setup_style()
    fig, axes = plt.subplots(3, 1, figsize=(8.9, 8.65), sharey=True)
    captions = {"Low": "(a) Low Load", "Medium": "(b) Medium Load", "High": "(c) High Load"}
    if metric == "acceptance":
        values_for = lambda r: 100.0 * float(r["acceptance_rate"])
        ylabel = "Acceptance Rate (%)"
        y_limits = (70.0, 101.5)
        log_scale = False
        stem = "component_ablation_acceptance_final"
    elif metric == "runtime":
        values_for = lambda r: float(r["total_end_to_end_runtime_seconds"])
        ylabel = "Total Runtime"
        y_limits = (180.0, 1800.0)
        log_scale = True
        stem = "component_ablation_runtime_final"
    else:
        raise ValueError(metric)

    for ax, load in zip(axes, LOADS):
        draw_boxpanel(
            ax,
            rows,
            load,
            values_for,
            ylabel,
            grayscale,
            captions[load],
            y_limits=y_limits,
            log_scale=log_scale,
        )
    add_legend(fig, grayscale)
    fig.text(0.5, 0.018, FOOTER, ha="center", va="center", fontsize=10)
    fig.subplots_adjust(top=0.885, bottom=0.115, left=0.095, right=0.985, hspace=0.66)

    if grayscale:
        stem += "_grayscale"
    png = OUT / f"{stem}.png"
    pdf = OUT / f"{stem}.pdf"
    fig.savefig(png, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    plt.close(fig)
    return png, pdf


def runtime_summary(rows: list[dict[str, str]]) -> dict[str, dict[str, dict[str, float]]]:
    summary: dict[str, dict[str, dict[str, float]]] = {}
    for load in LOADS:
        summary[load] = {}
        for alg in ALGS:
            values = [float(r["total_end_to_end_runtime_seconds"]) for r in rows if r["load"] == load and r["algorithm"] == alg]
            summary[load][alg] = {
                "min_seconds": min(values),
                "median_seconds": statistics.median(values),
                "mean_seconds": statistics.mean(values),
                "max_seconds": max(values),
            }
    return summary


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    outputs: dict[str, dict[str, dict[str, str]]] = {"acceptance": {}, "runtime": {}}
    for metric in ["acceptance", "runtime"]:
        for grayscale in [False, True]:
            png, pdf = plot_figure(rows, metric=metric, grayscale=grayscale)
            outputs[metric]["grayscale" if grayscale else "color"] = {"png": str(png), "pdf": str(pdf)}

    audit = {
        "experiment": "S40 component ablation",
        "row_count": len(rows),
        "source_data": str(SEED_LEVEL),
        "algorithm_order": [FULL_LABEL[alg] for alg in ALGS],
        "algorithm_label_mapping": FULL_LABEL,
        "style": STYLE,
        "grayscale": GRAY,
        "legend_layout": {
            "columns": 3,
            "rows": 2,
            "entry_order_by_row": LEGEND_LAYOUT_BY_ROW,
            "matplotlib_handle_order": [FULL_LABEL[alg] for alg in LEGEND_ORDER],
            "spacing": {
                "bbox_to_anchor": [0.5, 0.992],
                "mode": None,
                "columnspacing": 3.1,
                "labelspacing": 0.9,
                "handletextpad": 0.55,
            },
        },
        "x_tick_labels": XTICK_LABEL,
        "x_tick_one_line": [alg for alg, label in XTICK_LABEL.items() if "\n" not in label],
        "x_tick_wrapped": [alg for alg, label in XTICK_LABEL.items() if "\n" in label],
        "x_tick_wrap_reason": XTICK_WRAP_REASON,
        "footer": FOOTER,
        "acceptance": {
            "y_axis": [70.0, 101.5],
            "log_scale": False,
        },
        "runtime": {
            "metric": "total_end_to_end_runtime_seconds",
            "y_axis": [180.0, 1800.0],
            "log_scale": True,
            "tick_values_seconds": [180, 300, 600, 900, 1200, 1800],
            "tick_labels": [format_runtime_tick(v) for v in [180, 300, 600, 900, 1200, 1800]],
            "summary_seconds": runtime_summary(rows),
        },
        "outputs": outputs,
        "runtime_figure_generated": True,
        "cost_figure_generated": False,
        "frozen": False,
    }
    audit_path = OUT / "component_ablation_final_figures_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"audit": str(audit_path), "outputs": outputs}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
