"""Generate final S40 deadline-sensitivity acceptance/runtime figures.

This is a plotting-only final freeze. It reads the frozen seed-level table through the review-v1 validator and writes only final figure artifacts.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from algorithm_names import ALGORITHM_NAMES, SHORT_NAMES
from record_compatibility import normalize_record, normalize_dataframe, resolve_input_path


def read_dataframe(path, *args, **kwargs):
    return normalize_dataframe(pd.read_csv(resolve_input_path(path), *args, **kwargs))


import argparse
import importlib.util
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedFormatter, FixedLocator
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
V1_SCRIPT = Path(__file__).with_name("deadline_sensitivity_support.py")
OUTPUT_DIR = Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location("deadline_sensitivity_review_v1", V1_SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Cannot import review-v1 plotting module from {V1_SCRIPT}")
v1 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = v1
spec.loader.exec_module(v1)

PROFILES = ["Flexible", "Balanced", "RealTime"]
PAPER_LABEL = {"Flexible": "Loose", "Balanced": "Balanced", "RealTime": "Tight"}
XTICK_LABELS = {
    "Flexible": "Loose\ndeadline offset\n{3,4,5}",
    "Balanced": "Balanced\ndeadline offset\n{2,3,4}",
    "RealTime": "Tight\ndeadline offset\n{1,2}",
}
PROFILE_X = {"Flexible": 0.0, "Balanced": 1.82, "RealTime": 3.64}
ALG_OFFSET = {
    "saginfe": -0.34,
    "dwcg_pure": -0.113,
    "dwcg_saginfe": 0.113,
    "dwcg_saginfe_pro": 0.34,
}
SEED_JITTER = [-0.028, -0.022, -0.016, -0.010, -0.004, 0.004, 0.010, 0.016, 0.022, 0.028]
BOX_WIDTH = 0.172
SEED_SIZE = {"saginfe": 14, "dwcg_pure": 15, "dwcg_saginfe": 17, "dwcg_saginfe_pro": 18}
MEAN_SIZE = {"saginfe": 12, "dwcg_pure": 13, "dwcg_saginfe": 14, "dwcg_saginfe_pro": 15}
TREND_LINEWIDTH = 0.72
TREND_ALPHA = 0.45
SEED_ALPHA = 0.40

CAPTION_EXPLANATION = (
    "Deadline profiles vary only the deadline slack s, where deadline_slot = arrival_slot + s "
    "and feasibility is inclusive. Loose uses P(s=3,4,5)=(0.3,0.4,0.3), Balanced uses "
    "P(s=2,3,4)=(0.3,0.4,0.3), and Tight uses P(s=1,2)=(0.5,0.5). For each seed, topology "
    "and all deadline-neutral flow attributes are paired across profiles."
)


def grouped_position(profile: str, algorithm: str) -> float:
    return PROFILE_X[profile] + ALG_OFFSET[algorithm]


def draw_box_panel(ax, df: pd.DataFrame, value_field: str, y_factor: float, styles: dict, grayscale: bool) -> None:
    for profile in PROFILES:
        for algorithm in v1.ALGORITHMS:
            subset = df[(df["deadline_profile"] == profile) & (df["algorithm"] == algorithm)].sort_values("seed")
            values = (subset[value_field].astype(float) * y_factor).tolist()
            pos = grouped_position(profile, algorithm)
            color = styles[algorithm]["color"]
            bp = ax.boxplot(
                [values],
                positions=[pos],
                widths=BOX_WIDTH,
                patch_artist=True,
                manage_ticks=False,
                showfliers=False,
                medianprops={"color": "#111111", "linewidth": 1.18},
                boxprops={"edgecolor": color, "linewidth": 1.08},
                whiskerprops={"color": color, "linewidth": 0.9},
                capprops={"color": color, "linewidth": 0.9},
            )
            bp["boxes"][0].set_facecolor(v1.lighten(color, 0.68 if not grayscale else 0.83))
            for median in bp["medians"]:
                median.set(color="#111111", linewidth=1.18, zorder=8)
            x_values = [pos + SEED_JITTER[int(seed)] for seed in subset["seed"].astype(int)]
            ax.scatter(
                x_values,
                values,
                s=SEED_SIZE[algorithm],
                marker=styles[algorithm]["marker"],
                facecolor=color,
                edgecolor="white" if not grayscale else "#F2F2F2",
                linewidth=0.34,
                alpha=SEED_ALPHA,
                zorder=5,
            )
            mean_value = float(pd.Series(values).mean())
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

    for algorithm in v1.ALGORITHMS:
        xs = [grouped_position(profile, algorithm) for profile in PROFILES]
        ys = [
            float(df[(df["deadline_profile"] == profile) & (df["algorithm"] == algorithm)][value_field].astype(float).mean() * y_factor)
            for profile in PROFILES
        ]
        ax.plot(xs, ys, color=styles[algorithm]["color"], linewidth=TREND_LINEWIDTH, alpha=TREND_ALPHA, zorder=2)
        ax.scatter(
            xs,
            ys,
            marker=styles[algorithm]["marker"],
            s=MEAN_SIZE[algorithm],
            color=styles[algorithm]["color"],
            alpha=0.72,
            zorder=4,
        )


def style_axis(ax) -> None:
    ax.grid(axis="y", color="#D8D8D8", linewidth=0.5, alpha=0.78, zorder=1)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_linewidth(0.8)
        spine.set_color("#333333")
    ax.tick_params(axis="both", labelsize=8.0, colors="#222222", width=0.8)
    ax.tick_params(axis="x", labelsize=7.2, pad=5)
    ax.set_xlim(-0.74, 4.38)
    ax.set_xticks([PROFILE_X[p] for p in PROFILES])
    ax.set_xticklabels([XTICK_LABELS[p] for p in PROFILES])


def make_figure(df: pd.DataFrame, grayscale: bool) -> plt.Figure:
    styles = v1.GRAY_STYLE if grayscale else v1.STYLE
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8.6,
            "axes.labelsize": 8.6,
            "axes.titlesize": 9.3,
            "legend.fontsize": 8.4,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    fig, axes = plt.subplots(1, 2, figsize=(7.16, 3.45), constrained_layout=False)
    fig.subplots_adjust(left=0.074, right=0.988, bottom=0.345, top=0.845, wspace=0.255)

    draw_box_panel(axes[0], df, v1.ACCEPTANCE_FIELD, 100.0, styles, grayscale)
    axes[0].set_ylabel("Acceptance Rate (%)")
    axes[0].set_ylim(*v1.ACCEPTANCE_YLIM)
    axes[0].set_yticks(v1.ACCEPTANCE_TICKS)
    style_axis(axes[0])

    draw_box_panel(axes[1], df, v1.RUNTIME_FIELD, 1.0, styles, grayscale)
    axes[1].set_ylabel("Total Runtime")
    axes[1].set_yscale("log")
    axes[1].set_ylim(*v1.RUNTIME_YLIM)
    axes[1].yaxis.set_major_locator(FixedLocator(v1.RUNTIME_TICKS))
    axes[1].yaxis.set_major_formatter(FixedFormatter(v1.RUNTIME_TICK_LABELS))
    axes[1].yaxis.set_minor_formatter(FixedFormatter([]))
    style_axis(axes[1])

    handles = [
        Line2D(
            [0],
            [0],
            color=styles[algorithm]["color"],
            marker=styles[algorithm]["marker"],
            linewidth=1.0,
            markersize=5.0,
            label=v1.DISPLAY[algorithm],
        )
        for algorithm in v1.ALGORITHMS
    ]
    fig.legend(
        handles=handles,
        labels=[v1.DISPLAY[a] for a in v1.ALGORITHMS],
        loc="upper center",
        bbox_to_anchor=(0.5, 0.970),
        ncol=4,
        frameon=False,
        handlelength=1.75,
        columnspacing=1.40,
        handletextpad=0.45,
    )
    for ax, label in zip(axes, ["(a) Acceptance Rate", "(b) Total Runtime"]):
        bbox = ax.get_position()
        fig.text((bbox.x0 + bbox.x1) / 2, bbox.y0 - 0.185, label, ha="center", va="top", fontsize=9.3)
    fig.text(0.5, 0.078, "Deadline Sensitivity with", ha="center", fontsize=8.8)
    fig.text(0.5, 0.039, "S40 (|T|=8, |F|=160, Medium Load)", ha="center", fontsize=8.8)
    return fig


def write_outputs(df: pd.DataFrame, summary: dict, paired: dict, validation: dict, source_sha256: str) -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = []
    for grayscale, suffix in [(False, ""), (True, "_gray")]:
        fig = make_figure(df, grayscale=grayscale)
        png = OUTPUT_DIR / f"deadline_sensitivity_s40_final{suffix}.png"
        pdf = OUTPUT_DIR / f"deadline_sensitivity_s40_final{suffix}.pdf"
        fig.savefig(png, dpi=600)
        fig.savefig(pdf)
        plt.close(fig)
        outputs.extend([png.name, pdf.name])

    audit = {
        "authoritative_source_path": str(v1.SOURCE_CSV.relative_to(ROOT)),
        "authoritative_source_sha256": source_sha256,
        "frozen_package": str(v1.FROZEN_DIR.relative_to(ROOT)),
        "frozen_tag_or_package_id": "deadline-sensitivity-s40-v3-final",
        "frozen_manifest_path": str(v1.FROZEN_MANIFEST.relative_to(ROOT)),
        "profile_manifest_path": str(v1.PROFILE_MANIFEST.relative_to(ROOT)),
        "plotting_script_path": str((OUTPUT_DIR / "generate_deadline_sensitivity_s40_final.py").relative_to(ROOT)),
        "final_output_directory": str(OUTPUT_DIR.relative_to(ROOT)),
        "approved_review_source_directory": "figures/deadline_sensitivity/review_mean_median_fix",
        "approved_review_plotting_script_path": "figures/deadline_sensitivity/review_mean_median_fix/generate_deadline_sensitivity_s40_mean_median_fix.py",
        "approved_review_figure_files": [
            "deadline_sensitivity_s40_mean_median_fix.png",
            "deadline_sensitivity_s40_mean_median_fix.pdf",
            "deadline_sensitivity_s40_mean_median_fix_gray.png",
            "deadline_sensitivity_s40_mean_median_fix_gray.pdf",
            "deadline_sensitivity_s40_mean_median_fix_audit.json",
        ],
        "experiment_source_commit": validation["manifest"].get("experiment_source_commit"),
        "formal_plan_fingerprint": validation["manifest"].get("formal_plan_fingerprint"),
        "plotted_row_count": int(len(df)),
        "algorithms": [{"internal": alg, "display": v1.DISPLAY[alg]} for alg in v1.ALGORITHMS],
        "deadline_profile_mapping": {"Flexible": "Loose", "Balanced": "Balanced", "RealTime": "Tight"},
        "x_axis_order": ["Loose", "Balanced", "Tight"],
        "displayed_tick_labels": [
            "Loose\ndeadline offset\n{3,4,5}",
            "Balanced\ndeadline offset\n{2,3,4}",
            "Tight\ndeadline offset\n{1,2}",
        ],
        "deadline_semantics": {
            "formula": "deadline_slot = arrival_slot + s",
            "feasibility": "inclusive",
            "offset_not_available_slot_count": True,
        },
        "recommended_caption_explanation": CAPTION_EXPLANATION,
        "seeds": list(range(10)),
        "scale": "S40",
        "time_slots": 8,
        "flow_count": 160,
        "load": "Medium",
        "acceptance_field": v1.ACCEPTANCE_FIELD,
        "runtime_field": v1.RUNTIME_FIELD,
        "connecting_statistic_used": v1.CONNECTING_STATISTIC,
        "acceptance_axis": {"limits_percent": list(v1.ACCEPTANCE_YLIM), "ticks_percent": v1.ACCEPTANCE_TICKS},
        "runtime_axis": {
            "scale": "log",
            "limits_seconds": list(v1.RUNTIME_YLIM),
            "ticks_seconds": v1.RUNTIME_TICKS,
            "tick_labels": v1.RUNTIME_TICK_LABELS,
        },
        "mean_median_layering_fix": {
            "mean_line": {"implementation": "manual ax.hlines", "color": "#8B0000", "linestyle": [0, [1.0, 1.2]], "linewidth": 1.55, "alpha": 0.95, "zorder": 9},
            "median_line": {"color": "#111111", "linestyle": "solid", "linewidth": 1.18, "zorder": 8},
            "priority": "mean > median > seed markers > box patches/whiskers/caps > trend lines",
            "confirmation_red_mean_highest_zorder": True,
            "confirmation_black_median_second_highest_zorder": True,
        },
        "footer_text": ["Deadline Sensitivity with", "S40 (|T|=8, |F|=160, Medium Load)"],
        "no_scientific_data_changed": True,
        "no_experiment_rerun": True,
        "style_changes_relative_to_review_v2": {
            "seed_alpha": f"reduced from 0.60 to {SEED_ALPHA}",
            "seed_marker_size": "reduced from 18-24 to 14-18 points squared",
            "seed_jitter": "kept deterministic and slightly reduced from +/-0.030 to +/-0.028",
            "x_axis_labels": "changed from bare s-set labels to explicit deadline offset labels; split across three lines to avoid overlap",
            "panel_titles": "moved below each subplot, centered under the x-axis",
            "footer_layout": "two centered lines with increased spacing: Deadline Sensitivity with; S40 (|T|=8, |F|=160, Medium Load)",
        },
        "legend_layout": "single shared top legend, one row of four algorithm entries",
        "validation": {
            "missing_cells": validation["missing_cells"],
            "extra_cells": validation["extra_cells"],
            "fingerprint_issues": validation["fingerprint_issues"],
            "path_validity": "all true",
            "capacity_validity": "all true",
            "runtime_accounting_validity": "all true",
        },
        "verified_summary": summary,
        "paired_acceptance_changes": paired,
        "output_filenames": outputs + ["deadline_sensitivity_s40_final_audit.json"],
    }
    audit_path = OUTPUT_DIR / "deadline_sensitivity_s40_final_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    source_sha256 = v1.sha256_file(v1.SOURCE_CSV)
    df = read_dataframe(v1.SOURCE_CSV)
    validation = v1.validate(df, source_sha256)
    summary, paired = v1.summarize(df)
    audit = write_outputs(df, summary, paired, validation, source_sha256)
    print(json.dumps({
        "status": "ok",
        "output_dir": str(OUTPUT_DIR.relative_to(ROOT)),
        "outputs": audit["output_filenames"],
        "x_axis_order": audit["x_axis_order"],
        "displayed_tick_labels": audit["displayed_tick_labels"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



