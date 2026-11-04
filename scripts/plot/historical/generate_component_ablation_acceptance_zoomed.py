from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from algorithm_names import ALGORITHM_NAMES, SHORT_NAMES
from record_compatibility import normalize_record, normalize_dataframe, resolve_input_path


import csv
import hashlib
import json
import math
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import matplotlib as mpl
import matplotlib.lines as mlines
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[3]

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

# Matplotlib fills multi-column legends column-major, so this renders as:
# row 1: Pure, Warm, HP; row 2: HFP, PRO-no-PFE, PRO.
LEGEND_ORDER = [
    "dwcg_pure",
    "dwcg_saginfe",
    "dwcg_warm",
    "dwcg_saginfe_pro_no_pfe",
    "dwcg_pure_hp",
    "dwcg_saginfe_pro",
]

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
OLD_BOX_WIDTH = 0.42
BOX_WIDTH = 0.54


@dataclass(frozen=True)
class FigureConfig:
    scale: str
    total_flows: int
    source: Path
    output_dir: Path
    stem: str
    footer: str
    y_limits: dict[str, tuple[float, float]]
    y_ticks: dict[str, list[float]]
    load_column: str
    total_column: str
    path_valid_column: str
    capacity_valid_column: str


S40 = FigureConfig(
    scale="S40",
    total_flows=160,
    source=ROOT / "results" / "frozen" / "component_ablation_s40_v3" / "component_ablation_seed_level.csv",
    output_dir=ROOT / "figures" / "component_ablation" / "alternatives" / "s40_zoomed_acceptance_review",
    stem="s40_component_ablation_acceptance_zoomed",
    footer="S40 (|T|=8, |F|=160) Component Ablation",
    y_limits={"Low": (92.0, 101.0), "Medium": (89.0, 97.5), "High": (72.0, 81.0)},
    y_ticks={"Low": [92, 94, 96, 98, 100], "Medium": [90, 92, 94, 96], "High": [72, 74, 76, 78, 80]},
    load_column="load",
    total_column="total_flow_count",
    path_valid_column="path_validity",
    capacity_valid_column="capacity_validity",
)

S50 = FigureConfig(
    scale="S50",
    total_flows=200,
    source=ROOT / "results" / "frozen" / "component_ablation_s50_v3_final" / "canonical_component_ablation_s50_180.csv",
    output_dir=ROOT / "figures" / "component_ablation" / "s50_review",
    stem="s50_component_ablation_acceptance_zoomed",
    footer="S50 (|T|=8, |F|=200) Component Ablation",
    y_limits={"Low": (93.0, 101.0), "Medium": (87.0, 97.0), "High": (72.0, 81.0)},
    y_ticks={"Low": [94, 96, 98, 100], "Medium": [88, 90, 92, 94, 96], "High": [72, 74, 76, 78, 80]},
    load_column="load_label",
    total_column="F",
    path_valid_column="path_valid",
    capacity_valid_column="capacity_valid",
)


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


def read_csv(path: Path) -> list[dict[str, str]]:
    with resolve_input_path(path).open("r", encoding="utf-8", newline="") as handle:
        return normalize_record(list(csv.DictReader(handle)))


def bool_value(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return value in {"True", "true", "1", "yes", "YES"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with resolve_input_path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def normalized_rows(config: FigureConfig) -> list[dict[str, str]]:
    rows = read_csv(config.source)
    expected = {(config.scale, load, seed, alg) for load in LOADS for seed in SEEDS for alg in ALGS}
    seen = Counter((r["scale"], r[config.load_column], int(r["seed"]), r["algorithm"]) for r in rows)
    if len(rows) != 180 or set(seen) != expected or any(count != 1 for count in seen.values()):
        raise SystemExit(f"{config.scale} acceptance source does not match expected 6x3x10 matrix")

    normalized: list[dict[str, str]] = []
    for row in rows:
        accepted = int(row["accepted_flow_count"])
        total = int(row[config.total_column])
        rate = float(row["acceptance_rate"])
        if row["scale"] != config.scale or total != config.total_flows:
            raise SystemExit(f"invalid identity row: {row}")
        if not (0 <= accepted <= total):
            raise SystemExit(f"invalid accepted-flow count: {row}")
        if not math.isclose(rate, accepted / total, rel_tol=0.0, abs_tol=1e-12):
            raise SystemExit(f"acceptance_rate mismatch: {row}")
        if not bool_value(row[config.path_valid_column]) or not bool_value(row[config.capacity_valid_column]):
            raise SystemExit(f"validity failure: {row}")
        normalized.append(
            {
                "load": row[config.load_column],
                "seed": row["seed"],
                "algorithm": row["algorithm"],
                "acceptance_percent": str(100.0 * rate),
            }
        )
    return normalized


def draw_boxpanel(ax, rows: list[dict[str, str]], load: str, config: FigureConfig, grayscale: bool, caption: str) -> None:
    positions = np.arange(len(ALGS), dtype=float)
    for idx, alg in enumerate(ALGS):
        values = [float(r["acceptance_percent"]) for r in rows if r["load"] == load and r["algorithm"] == alg]
        bp = ax.boxplot(
            [values],
            positions=[positions[idx]],
            widths=BOX_WIDTH,
            patch_artist=True,
            showfliers=False,
            showmeans=False,
            whis=1.5,
            zorder=2,
            boxprops={
                "facecolor": color_for(alg, grayscale),
                "edgecolor": color_for(alg, grayscale),
                "alpha": 0.22,
                "linewidth": 1.05,
            },
            medianprops={"color": "black", "linewidth": 1.9, "zorder": 9},
            whiskerprops={"color": color_for(alg, grayscale), "linewidth": 1.0},
            capprops={"color": color_for(alg, grayscale), "linewidth": 1.0},
        )
        for element in ["boxes", "whiskers", "caps", "medians"]:
            for artist in bp[element]:
                artist.set_zorder(7 if element == "medians" else 2)

        mean = statistics.mean(values)
        ax.plot(
            [positions[idx] - BOX_WIDTH / 2.05, positions[idx] + BOX_WIDTH / 2.05],
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
                float(row["acceptance_percent"]),
                s=22,
                marker=STYLE[alg]["marker"],
                facecolors=color_for(alg, grayscale),
                edgecolors="white" if not grayscale else "#F0F0F0",
                linewidths=0.45,
                alpha=0.82,
                zorder=5,
            )

    ax.set_ylim(*config.y_limits[load])
    ax.set_yticks(config.y_ticks[load])
    ax.set_xlim(-0.65, len(ALGS) - 0.35)
    ax.set_xticks(positions)
    ax.set_xticklabels([XTICK_LABEL[a] for a in ALGS], linespacing=1.05)
    ax.set_ylabel("Acceptance Rate (%)")
    ax.grid(axis="y", color="#D0D0D0", linestyle="-", alpha=0.55)
    ax.set_axisbelow(True)
    ax.text(0.5, -0.36, caption, transform=ax.transAxes, ha="center", va="top", fontsize=10.5)
    for spine in ax.spines.values():
        spine.set_linewidth(0.8)


def plot_acceptance(config: FigureConfig, rows: list[dict[str, str]], grayscale: bool) -> tuple[Path, Path]:
    setup_style()
    fig, axes = plt.subplots(3, 1, figsize=(8.9, 8.65), sharey=False)
    captions = {"Low": "(a) Low Load", "Medium": "(b) Medium Load", "High": "(c) High Load"}
    for ax, load in zip(axes, LOADS):
        draw_boxpanel(ax, rows, load, config, grayscale, captions[load])
    add_legend(fig, grayscale)
    fig.text(0.5, 0.018, config.footer, ha="center", va="center", fontsize=10)
    fig.subplots_adjust(top=0.885, bottom=0.115, left=0.095, right=0.985, hspace=0.66)

    stem = config.stem + ("_gray" if grayscale else "")
    png = config.output_dir / f"{stem}.png"
    pdf = config.output_dir / f"{stem}.pdf"
    config.output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    plt.close(fig)
    return png, pdf


def generate(config: FigureConfig) -> dict[str, object]:
    rows = normalized_rows(config)
    color_png, color_pdf = plot_acceptance(config, rows, grayscale=False)
    gray_png, gray_pdf = plot_acceptance(config, rows, grayscale=True)
    counts = Counter((r["load"], r["algorithm"]) for r in rows)
    audit = {
        "scale": config.scale,
        "canonical_source": str(config.source.relative_to(ROOT)),
        "canonical_source_sha256": sha256(config.source),
        "plotted_row_count": len(rows),
        "counts_per_load_algorithm": {f"{load}|{alg}": counts[(load, alg)] for load in LOADS for alg in ALGS},
        "algorithm_order": [FULL_LABEL[alg] for alg in ALGS],
        "legend_layout": {
            "columns": 3,
            "rows": 2,
            "contains_mean": False,
            "contains_median": False,
        },
        "box_width": BOX_WIDTH,
        "previous_box_width": OLD_BOX_WIDTH,
        "y_limits": {load: list(config.y_limits[load]) for load in LOADS},
        "y_ticks": {load: config.y_ticks[load] for load in LOADS},
        "footer": config.footer,
        "outputs": {
            "color": {"png": str(color_png.relative_to(ROOT)), "pdf": str(color_pdf.relative_to(ROOT))},
            "grayscale": {"png": str(gray_png.relative_to(ROOT)), "pdf": str(gray_pdf.relative_to(ROOT))},
        },
    }
    audit_path = config.output_dir / f"{config.stem}_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    audit["audit"] = str(audit_path.relative_to(ROOT))
    return audit


def main() -> int:
    result = {"S40": generate(S40), "S50": generate(S50)}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
