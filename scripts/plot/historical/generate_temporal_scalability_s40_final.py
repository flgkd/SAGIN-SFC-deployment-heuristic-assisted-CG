from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from algorithm_names import ALGORITHM_NAMES, SHORT_NAMES
from record_compatibility import normalize_record, normalize_dataframe, resolve_input_path


def read_dataframe(path, *args, **kwargs):
    return normalize_dataframe(pd.read_csv(resolve_input_path(path), *args, **kwargs))


import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import matplotlib as mpl
import matplotlib.lines as mlines
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "outputs" / "temporal_scalability_v3_160_0caacc9" / "temporal_scalability_seed_level.csv"
FROZEN_PACKAGE = ROOT / "results" / "frozen" / "temporal_scalability_v3"
FROZEN_MANIFEST = FROZEN_PACKAGE / "temporal_scalability_final_manifest.json"
FROZEN_AUDIT = FROZEN_PACKAGE / "temporal_scalability_final_audit.json"
OUTPUT_DIR = Path(__file__).resolve().parent

ALGORITHMS = ["saginfe", "dwcg_pure", "dwcg_saginfe", "dwcg_saginfe_pro"]
DISPLAY = {
    "saginfe": "SAGINFE",
    "dwcg_pure": "DWCG-Pure",
    "dwcg_saginfe": "DWCG-SAGINFE",
    "dwcg_saginfe_pro": "DWCG-SAGINFE-PRO",
}
COLOR = {
    "saginfe": "#1f77b4",
    "dwcg_pure": "#ff7f0e",
    "dwcg_saginfe": "#2ca02c",
    "dwcg_saginfe_pro": "#d627b9",
}
GRAY = {
    "saginfe": "#222222",
    "dwcg_pure": "#666666",
    "dwcg_saginfe": "#999999",
    "dwcg_saginfe_pro": "#000000",
}
MARKER = {
    "saginfe": "o",
    "dwcg_pure": "s",
    "dwcg_saginfe": "^",
    "dwcg_saginfe_pro": "X",
}
MARKER_SIZE = {
    "saginfe": 14,
    "dwcg_pure": 15,
    "dwcg_saginfe": 17,
    "dwcg_saginfe_pro": 18,
}
TIME_SLOTS = [6, 8, 10, 12]
TEG_NODES = {6: 240, 8: 320, 10: 400, 12: 480}
OFFSETS = [-0.27, -0.09, 0.09, 0.27]
SEED_JITTER = [-0.026, -0.020, -0.014, -0.008, -0.003, 0.003, 0.008, 0.014, 0.020, 0.026]
SEED_ALPHA = 0.40
TREND_ALPHA = 0.45
TREND_LINEWIDTH = 0.72
BOX_WIDTH = 0.16

CAPTION_NOTE = (
    "Operational Temporal Scalability varies |T| while preserving paired physical, deadline-neutral flow, "
    "and timing-latent fingerprints by seed; S40, |F|=160, Medium Load are fixed."
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with resolve_input_path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def human_time(seconds: float, _pos: int | None = None) -> str:
    if seconds < 60:
        return f"{int(seconds)} s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)} min" if abs(minutes - round(minutes)) < 1e-9 else f"{minutes:g} min"
    hours = minutes / 60
    return f"{hours:g} h"


def load_and_validate() -> tuple[pd.DataFrame, dict]:
    df = read_dataframe(SOURCE)
    df["time_slots"] = df["time_slots"].astype(int)
    df["seed"] = df["seed"].astype(int)
    expected = pd.MultiIndex.from_product([ALGORITHMS, TIME_SLOTS, range(10)], names=["algorithm", "time_slots", "seed"])
    actual = pd.MultiIndex.from_frame(df[["algorithm", "time_slots", "seed"]])
    duplicate = df[df.duplicated(["algorithm", "time_slots", "seed"], keep=False)]
    missing = expected.difference(actual)
    extra = actual.difference(expected)
    if len(df) != 160:
        raise SystemExit(f"expected 160 rows, found {len(df)}")
    if not duplicate.empty:
        raise SystemExit(f"duplicate algorithm/time/seed cells: {duplicate[[ 'algorithm', 'time_slots', 'seed' ]].to_dict('records')}")
    if len(missing):
        raise SystemExit(f"missing algorithm/time/seed cells: {list(missing)}")
    if len(extra):
        raise SystemExit(f"unexpected algorithm/time/seed cells: {list(extra)}")
    if set(df["scale"]) != {"S40"} or set(df["load"]) != {"Medium"} or set(df["total_flow_count"]) != {160}:
        raise SystemExit("non-fixed S40/Medium/F=160 fields found")
    if not ((df["acceptance_rate"] >= 0) & (df["acceptance_rate"] <= 1)).all():
        raise SystemExit("invalid acceptance_rate values")
    if not (df["total_end_to_end_runtime_seconds"] > 0).all():
        raise SystemExit("invalid total_end_to_end_runtime_seconds values")
    for col in ["path_validity", "capacity_validity", "runtime_accounting_valid"]:
        if not df[col].astype(bool).all():
            raise SystemExit(f"invalid {col} rows found")

    fp_cols = [
        "physical_base_fingerprint",
        "temporal_prefix_fingerprint",
        "non_temporal_flow_fingerprint",
        "paired_timing_latent_fingerprint",
        "full_temporal_flow_fingerprint",
        "full_instance_fingerprint",
    ]
    within = {}
    for col in fp_cols:
        bad = []
        for (t, seed), grp in df.groupby(["time_slots", "seed"]):
            if grp[col].nunique() != 1:
                bad.append({"time_slots": int(t), "seed": int(seed), "unique": int(grp[col].nunique())})
        within[col] = bad
        if bad:
            raise SystemExit(f"fingerprint {col} differs across algorithms within time/seed: {bad[:3]}")

    across = {}
    for col in fp_cols:
        across[col] = {str(seed): int(grp[col].nunique()) for seed, grp in df.groupby("seed")}

    validation = {
        "row_count": len(df),
        "matrix": "4 algorithms x 4 temporal settings x 10 seeds",
        "algorithms": ALGORITHMS,
        "time_slots": TIME_SLOTS,
        "seeds": list(range(10)),
        "scale": "S40",
        "load": "Medium",
        "total_flow_count": 160,
        "duplicate_cells": 0,
        "missing_cells": 0,
        "bad_runtime_rows": 0,
        "bad_acceptance_rows": 0,
        "bad_validity_rows": 0,
        "fingerprints_identical_across_algorithms_within_time_seed": True,
        "fingerprint_unique_counts_across_temporal_settings_by_seed": across,
    }
    return df, validation


def summarize(df: pd.DataFrame) -> dict:
    rows = []
    for t in TIME_SLOTS:
        for alg in ALGORITHMS:
            cell = df[(df["time_slots"] == t) & (df["algorithm"] == alg)]
            rows.append(
                {
                    "time_slots": t,
                    "algorithm": DISPLAY[alg],
                    "n": int(len(cell)),
                    "mean_acceptance_rate_percent": round(float(cell["acceptance_rate"].mean() * 100), 3),
                    "mean_accepted_flow_count": round(float(cell["accepted_flow_count"].mean()), 3),
                    "median_total_runtime_seconds": round(float(cell["total_end_to_end_runtime_seconds"].median()), 3),
                }
            )
    trends = {}
    for alg in ALGORITHMS:
        means = [float(df[(df["time_slots"] == t) & (df["algorithm"] == alg)]["accepted_flow_count"].mean()) for t in TIME_SLOTS]
        runtimes = [float(df[(df["time_slots"] == t) & (df["algorithm"] == alg)]["total_end_to_end_runtime_seconds"].median()) for t in TIME_SLOTS]
        trends[DISPLAY[alg]] = {
            "mean_accepted_flow_count_by_T": dict(zip([f"T{t}" for t in TIME_SLOTS], [round(x, 3) for x in means])),
            "median_runtime_seconds_by_T": dict(zip([f"T{t}" for t in TIME_SLOTS], [round(x, 3) for x in runtimes])),
            "acceptance_change_T12_minus_T6_flows": round(means[-1] - means[0], 3),
            "runtime_change_T12_over_T6_median_ratio": round(runtimes[-1] / runtimes[0], 3),
        }
    return {"cell_summary": rows, "trends": trends}


def style(color_mode_gray: bool) -> dict:
    return {alg: {"color": (GRAY if color_mode_gray else COLOR)[alg], "marker": MARKER[alg]} for alg in ALGORITHMS}


def draw_panel(ax, df: pd.DataFrame, metric: str, gray: bool) -> None:
    styles = style(gray)
    for t_index, t in enumerate(TIME_SLOTS):
        center = float(t_index)
        for alg_index, alg in enumerate(ALGORITHMS):
            pos = center + OFFSETS[alg_index]
            cell = df[(df["time_slots"] == t) & (df["algorithm"] == alg)].sort_values("seed")
            values = (cell[metric] * 100.0).to_numpy() if metric == "acceptance_rate" else cell[metric].to_numpy()
            color = styles[alg]["color"]
            bp = ax.boxplot(
                values,
                positions=[pos],
                widths=BOX_WIDTH,
                patch_artist=True,
                showfliers=False,
                whis=(0, 100),
                zorder=3,
            )
            for box in bp["boxes"]:
                box.set(facecolor=mpl.colors.to_rgba(color, 0.16), edgecolor=color, linewidth=0.95)
            for whisker in bp["whiskers"]:
                whisker.set(color=color, linewidth=0.8)
            for cap in bp["caps"]:
                cap.set(color=color, linewidth=0.8)
            for median in bp["medians"]:
                median.set(color="#202020", linewidth=1.35, zorder=8)
            x = [pos + SEED_JITTER[int(seed)] for seed in cell["seed"]]
            ax.scatter(
                x,
                values,
                s=MARKER_SIZE[alg],
                marker=styles[alg]["marker"],
                facecolor=color,
                edgecolor="white",
                linewidth=0.28,
                alpha=SEED_ALPHA,
                zorder=5,
            )
            mean_value = float(values.mean())
            ax.hlines(
                mean_value,
                pos - BOX_WIDTH * 0.42,
                pos + BOX_WIDTH * 0.42,
                colors="#8B0000",
                linestyles=(0, (1.0, 1.2)),
                linewidth=1.55,
                alpha=0.95,
                zorder=9,
            )
    for alg_index, alg in enumerate(ALGORITHMS):
        xs = [float(i) + OFFSETS[alg_index] for i in range(len(TIME_SLOTS))]
        ys = []
        for t in TIME_SLOTS:
            cell = df[(df["time_slots"] == t) & (df["algorithm"] == alg)]
            value = float(cell[metric].mean() * 100.0) if metric == "acceptance_rate" else float(cell[metric].mean())
            ys.append(value)
        ax.plot(xs, ys, color=styles[alg]["color"], linewidth=TREND_LINEWIDTH, alpha=TREND_ALPHA, zorder=2)
    ax.set_xticks(range(len(TIME_SLOTS)))
    ax.set_xticklabels([f"|T|={t}" for t in TIME_SLOTS])
    ax.set_xlim(-0.62, len(TIME_SLOTS) - 1 + 0.62)
    ax.grid(axis="y", color="#D8D8D8", linewidth=0.5, alpha=0.78, zorder=1)
    for spine in ax.spines.values():
        spine.set_linewidth(0.8)
        spine.set_color("#303030")


def create_figure(df: pd.DataFrame, gray: bool) -> plt.Figure:
    mpl.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8.8,
            "axes.labelsize": 9.2,
            "xtick.labelsize": 8.5,
            "ytick.labelsize": 8.5,
            "legend.fontsize": 8.6,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    fig, axes = plt.subplots(1, 2, figsize=(7.25, 3.55), dpi=300)
    fig.subplots_adjust(left=0.075, right=0.988, bottom=0.335, top=0.820, wspace=0.260)

    draw_panel(axes[0], df, "acceptance_rate", gray)
    axes[0].set_ylabel("Acceptance Rate (%)")
    axes[0].set_ylim(75, 101)
    axes[0].set_yticks([75, 80, 85, 90, 95, 100])

    draw_panel(axes[1], df, "total_end_to_end_runtime_seconds", gray)
    axes[1].set_ylabel("Total Runtime")
    axes[1].set_yscale("log")
    axes[1].set_ylim(15, 1800)
    runtime_ticks = [15, 20, 30, 60, 120, 300, 600, 1200, 1800]
    axes[1].yaxis.set_major_locator(mpl.ticker.FixedLocator(runtime_ticks))
    axes[1].yaxis.set_major_formatter(mpl.ticker.FuncFormatter(human_time))
    axes[1].yaxis.set_minor_formatter(mpl.ticker.NullFormatter())

    handles = []
    styles = style(gray)
    for alg in ALGORITHMS:
        handles.append(
            mlines.Line2D(
                [],
                [],
                color=styles[alg]["color"],
                marker=styles[alg]["marker"],
                linestyle="-",
                linewidth=1.2,
                markersize=5.4,
                label=DISPLAY[alg],
            )
        )
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.992), ncol=4, frameon=False, columnspacing=1.7, handlelength=1.6)

    for ax, label in zip(axes, ["(a) Acceptance Rate", "(b) Total Runtime"]):
        bbox = ax.get_position()
        fig.text((bbox.x0 + bbox.x1) / 2, bbox.y0 - 0.140, label, ha="center", va="top", fontsize=9.2)
    fig.text(0.5, 0.076, "Temporal Scalability with", ha="center", fontsize=8.8)
    fig.text(0.5, 0.038, "S40 (|F|=160, Medium Load)", ha="center", fontsize=8.8)
    return fig


def write_outputs(df: pd.DataFrame, validation: dict, source_hash: str) -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = []
    for gray in [False, True]:
        fig = create_figure(df, gray)
        suffix = "_gray" if gray else ""
        png = OUTPUT_DIR / f"temporal_scalability_combined{suffix}.png"
        pdf = OUTPUT_DIR / f"temporal_scalability_combined{suffix}.pdf"
        fig.savefig(png, dpi=600, bbox_inches="tight", pad_inches=0.018)
        fig.savefig(pdf, bbox_inches="tight", pad_inches=0.018)
        plt.close(fig)
        outputs.extend([png.name, pdf.name])
    with FROZEN_MANIFEST.open("r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    with FROZEN_AUDIT.open("r", encoding="utf-8") as fh:
        frozen_audit = json.load(fh)
    summary = summarize(df)
    audit = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "frozen_package": str(FROZEN_PACKAGE.relative_to(ROOT)),
        "frozen_tag_or_package_id": "temporal-scalability-v3-final",
        "experiment_source_commit": manifest.get("experiment_source_commit"),
        "plan_fingerprint": manifest.get("plan_fingerprint"),
        "authoritative_source_path": str(SOURCE.relative_to(ROOT)),
        "authoritative_source_sha256": source_hash,
        "plotted_row_count": int(len(df)),
        "validation": validation,
        "algorithms": [{"internal": alg, "display": DISPLAY[alg]} for alg in ALGORITHMS],
        "temporal_settings": [f"T{t}" for t in TIME_SLOTS],
        "x_axis_order": [f"T{t}" for t in TIME_SLOTS],
        "displayed_tick_labels": [f"|T|={t}" for t in TIME_SLOTS],
        "fixed_scenario_footer": "S40 (|F|=160, Medium Load)",
        "acceptance_field": "acceptance_rate",
        "runtime_field": "total_end_to_end_runtime_seconds",
        "connecting_statistic_used": "mean",
        "runtime_scale": "log",
        "acceptance_axis": {"limits_percent": [75, 101], "ticks_percent": [75, 80, 85, 90, 95, 100]},
        "runtime_axis": {"limits_seconds": [15, 1800], "ticks_seconds": [15, 20, 30, 60, 120, 300, 600, 1200, 1800], "tick_labels": [human_time(x) for x in [15, 20, 30, 60, 120, 300, 600, 1200, 1800]]},
        "style": {"seed_alpha": SEED_ALPHA, "deterministic_jitter": SEED_JITTER, "box_width": BOX_WIDTH, "trend_linewidth": TREND_LINEWIDTH, "trend_alpha": TREND_ALPHA},
        "caption_note": CAPTION_NOTE,
        "source_frozen_audit_pass": frozen_audit.get("pass"),
        "summary": summary,
        "output_filenames": outputs + ["temporal_scalability_figure_audit.json"],
        "no_scientific_data_modified": True,
    }
    audit_path = OUTPUT_DIR / "temporal_scalability_figure_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return audit


def main() -> None:
    df, validation = load_and_validate()
    source_hash = sha256(SOURCE)
    audit = write_outputs(df, validation, source_hash)
    print(json.dumps({"status": "ok", "output_dir": str(OUTPUT_DIR.relative_to(ROOT)), "outputs": audit["output_filenames"], "source_sha256": source_hash}, indent=2))


if __name__ == "__main__":
    main()
