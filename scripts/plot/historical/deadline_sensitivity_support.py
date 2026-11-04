"""Generate review-v1 S40 deadline-sensitivity acceptance/runtime figures.

This script is plotting-only. It reads the frozen seed-level table and writes
review figures plus a lightweight plotted-data audit.
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
import hashlib
import json
from pathlib import Path
from statistics import mean, median

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, FixedFormatter
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = ROOT / "outputs" / "deadline_sensitivity_s40_v3_120_36b9632"
SOURCE_CSV = SOURCE_DIR / "deadline_sensitivity_seed_level.csv"
FROZEN_DIR = ROOT / "results" / "frozen" / "deadline_sensitivity_s40_v3"
FROZEN_MANIFEST = FROZEN_DIR / "deadline_sensitivity_manifest.json"
PROFILE_MANIFEST = FROZEN_DIR / "deadline_profile_manifest.json"
OUTPUT_DIR = Path(__file__).resolve().parent

ALGORITHMS = ["saginfe", "dwcg_pure", "dwcg_saginfe", "dwcg_saginfe_pro"]
DISPLAY = {
    "saginfe": "SAGINFE",
    "dwcg_pure": "DWCG-Pure",
    "dwcg_saginfe": "DWCG-SAGINFE",
    "dwcg_saginfe_pro": "DWCG-SAGINFE-PRO",
}
PROFILES = ["RealTime", "Balanced", "Flexible"]
PAPER_LABEL = {"RealTime": "Tight", "Balanced": "Balanced", "Flexible": "Loose"}

# Approved Main Scaling visual identities.
STYLE = {
    "saginfe": {"color": "#1f77b4", "marker": "o"},
    "dwcg_pure": {"color": "#ff7f0e", "marker": "s"},
    "dwcg_saginfe": {"color": "#2ca02c", "marker": "^"},
    "dwcg_saginfe_pro": {"color": "#d627b9", "marker": "X"},
}
GRAY_STYLE = {
    "saginfe": {"color": "#222222", "marker": "o"},
    "dwcg_pure": {"color": "#666666", "marker": "s"},
    "dwcg_saginfe": {"color": "#999999", "marker": "^"},
    "dwcg_saginfe_pro": {"color": "#000000", "marker": "X"},
}

PROFILE_X = {"RealTime": 0.0, "Balanced": 1.35, "Flexible": 2.70}
ALG_OFFSET = {
    "saginfe": -0.27,
    "dwcg_pure": -0.09,
    "dwcg_saginfe": 0.09,
    "dwcg_saginfe_pro": 0.27,
}
SEED_JITTER = [-0.036, -0.028, -0.020, -0.012, -0.004, 0.004, 0.012, 0.020, 0.028, 0.036]

ACCEPTANCE_FIELD = "acceptance_rate"
RUNTIME_FIELD = "total_end_to_end_runtime_seconds"
CONNECTING_STATISTIC = "mean"
ACCEPTANCE_YLIM = (68.0, 97.0)
ACCEPTANCE_TICKS = [70, 75, 80, 85, 90, 95]
RUNTIME_YLIM = (12.0, 1600.0)
RUNTIME_TICKS = [15, 20, 30, 60, 120, 300, 600, 1200]
RUNTIME_TICK_LABELS = ["15 s", "20 s", "30 s", "1 min", "2 min", "5 min", "10 min", "20 min"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with resolve_input_path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(resolve_input_path(path).read_text(encoding="utf-8"))


def validate(df: pd.DataFrame, source_sha256: str) -> dict:
    manifest = load_json(FROZEN_MANIFEST)
    profile_manifest = load_json(PROFILE_MANIFEST)
    expected_hash = manifest.get("hashes", {}).get("seed_level_csv")
    errors: list[str] = []

    if expected_hash and source_sha256 != expected_hash:
        errors.append(f"seed-level CSV hash mismatch: {source_sha256} != {expected_hash}")
    if int(manifest.get("record_count", -1)) != 120:
        errors.append("frozen manifest record_count is not 120")
    if manifest.get("pass") is not True:
        errors.append("frozen manifest pass flag is not true")

    if len(df) != 120:
        errors.append(f"expected 120 rows, found {len(df)}")
    if sorted(df["algorithm"].unique()) != sorted(ALGORITHMS):
        errors.append(f"unexpected algorithms: {sorted(df['algorithm'].unique())}")
    if sorted(df["deadline_profile"].unique()) != sorted(PROFILES):
        errors.append(f"unexpected profiles: {sorted(df['deadline_profile'].unique())}")
    if set(df["seed"].astype(int).unique()) != set(range(10)):
        errors.append(f"unexpected seeds: {sorted(df['seed'].astype(int).unique())}")

    duplicated = df.duplicated(["algorithm", "deadline_profile", "seed"])
    if duplicated.any():
        errors.append("duplicate algorithm/profile/seed cells present")

    expected_cells = {
        (algorithm, profile, seed)
        for algorithm in ALGORITHMS
        for profile in PROFILES
        for seed in range(10)
    }
    observed_cells = {
        (row.algorithm, row.deadline_profile, int(row.seed))
        for row in df.itertuples(index=False)
    }
    missing = sorted(expected_cells - observed_cells)
    extra = sorted(observed_cells - expected_cells)
    if missing:
        errors.append(f"missing cells: {missing}")
    if extra:
        errors.append(f"extra cells: {extra}")

    if (df["scale"] != "S40").any():
        errors.append("non-S40 rows present")
    if (df["load"] != "Medium").any():
        errors.append("non-Medium load rows present")
    if (df["total_flow_count"].astype(int) != 160).any():
        errors.append("non-160 flow rows present")
    if not all(profile_manifest.get("deadline_profiles", {}).get(p, {}).get("paper_label") == PAPER_LABEL[p] for p in PROFILES):
        errors.append("deadline profile paper-label mapping does not match profile manifest")

    for field in ["path_validity", "capacity_validity", "runtime_accounting_valid"]:
        if (df[field].astype(str).str.lower() != "true").any():
            errors.append(f"{field} contains invalid rows")
    if df["termination_status"].astype(str).str.upper().isin(["ERROR", "FAILED"]).any():
        errors.append("error/failed termination rows present")
    if ((df["accepted_flow_count"].astype(int) < 0) | (df["accepted_flow_count"].astype(int) > df["total_flow_count"].astype(int))).any():
        errors.append("accepted-flow count outside [0, total_flow_count]")

    for field in ["topology_fingerprint", "deadline_neutral_flow_fingerprint", "full_instance_fingerprint"]:
        if df[field].isna().any() or (df[field].astype(str).str.len() == 0).any():
            errors.append(f"{field} missing")

    fingerprint_issues = []
    for seed in range(10):
        seed_df = df[df["seed"].astype(int) == seed]
        if seed_df["topology_fingerprint"].nunique() != 1:
            fingerprint_issues.append({"seed": seed, "field": "topology_fingerprint"})
        if seed_df["deadline_neutral_flow_fingerprint"].nunique() != 1:
            fingerprint_issues.append({"seed": seed, "field": "deadline_neutral_flow_fingerprint"})
        for profile in PROFILES:
            cell = seed_df[seed_df["deadline_profile"] == profile]
            if cell["full_instance_fingerprint"].nunique() != 1:
                fingerprint_issues.append({"seed": seed, "profile": profile, "field": "full_instance_fingerprint"})
    if fingerprint_issues:
        errors.append(f"fingerprint pairing failed: {fingerprint_issues}")

    if errors:
        raise SystemExit("NO-GO for deadline-sensitivity figure:\n" + "\n".join(f"- {e}" for e in errors))

    return {
        "manifest": manifest,
        "profile_manifest": profile_manifest,
        "missing_cells": missing,
        "extra_cells": extra,
        "fingerprint_issues": fingerprint_issues,
    }


def summarize(df: pd.DataFrame) -> tuple[dict, dict]:
    summary = {}
    paired = {}
    for profile in PROFILES:
        for algorithm in ALGORITHMS:
            subset = df[(df["deadline_profile"] == profile) & (df["algorithm"] == algorithm)]
            summary[f"{PAPER_LABEL[profile]}:{DISPLAY[algorithm]}"] = {
                "mean_accepted_flow_count": round(float(subset["accepted_flow_count"].mean()), 3),
                "mean_acceptance_rate_percent": round(float(subset[ACCEPTANCE_FIELD].mean() * 100.0), 3),
                "median_total_runtime_seconds": round(float(subset[RUNTIME_FIELD].median()), 3),
            }

    for algorithm in ALGORITHMS:
        alg_rows = df[df["algorithm"] == algorithm]
        comparisons = {
            "Balanced - Tight": ("Balanced", "RealTime"),
            "Loose - Balanced": ("Flexible", "Balanced"),
            "Loose - Tight": ("Flexible", "RealTime"),
        }
        paired[DISPLAY[algorithm]] = {}
        for label, (hi, lo) in comparisons.items():
            deltas = []
            for seed in range(10):
                hi_value = int(alg_rows[(alg_rows["deadline_profile"] == hi) & (alg_rows["seed"].astype(int) == seed)]["accepted_flow_count"].iloc[0])
                lo_value = int(alg_rows[(alg_rows["deadline_profile"] == lo) & (alg_rows["seed"].astype(int) == seed)]["accepted_flow_count"].iloc[0])
                deltas.append(hi_value - lo_value)
            paired[DISPLAY[algorithm]][label] = {
                "delta_vector": deltas,
                "wins": sum(delta > 0 for delta in deltas),
                "ties": sum(delta == 0 for delta in deltas),
                "losses": sum(delta < 0 for delta in deltas),
                "mean_delta": round(mean(deltas), 3),
            }
    return summary, paired


def lighten(color: str, amount: float = 0.72) -> tuple[float, float, float, float]:
    import matplotlib.colors as mcolors

    r, g, b = mcolors.to_rgb(color)
    return (r + (1.0 - r) * amount, g + (1.0 - g) * amount, b + (1.0 - b) * amount, 0.55)


def grouped_position(profile: str, algorithm: str) -> float:
    return PROFILE_X[profile] + ALG_OFFSET[algorithm]


def draw_box_panel(ax, df: pd.DataFrame, value_field: str, y_factor: float, styles: dict, grayscale: bool) -> None:
    width = 0.135
    for profile in PROFILES:
        for algorithm in ALGORITHMS:
            subset = df[(df["deadline_profile"] == profile) & (df["algorithm"] == algorithm)].sort_values("seed")
            values = (subset[value_field].astype(float) * y_factor).tolist()
            pos = grouped_position(profile, algorithm)
            color = styles[algorithm]["color"]
            bp = ax.boxplot(
                [values],
                positions=[pos],
                widths=width,
                patch_artist=True,
                manage_ticks=False,
                showfliers=False,
                medianprops={"color": "#111111", "linewidth": 1.1},
                boxprops={"edgecolor": color, "linewidth": 1.0},
                whiskerprops={"color": color, "linewidth": 0.9},
                capprops={"color": color, "linewidth": 0.9},
            )
            bp["boxes"][0].set_facecolor(lighten(color, 0.70 if not grayscale else 0.84))
            x_values = [pos + SEED_JITTER[int(seed)] for seed in subset["seed"].astype(int)]
            ax.scatter(
                x_values,
                values,
                s=25 if algorithm != "dwcg_saginfe_pro" else 30,
                marker=styles[algorithm]["marker"],
                facecolor=color,
                edgecolor="white" if not grayscale else "#F2F2F2",
                linewidth=0.45,
                alpha=0.92,
                zorder=5,
            )

    for algorithm in ALGORITHMS:
        xs = [grouped_position(profile, algorithm) for profile in PROFILES]
        ys = [
            float(df[(df["deadline_profile"] == profile) & (df["algorithm"] == algorithm)][value_field].astype(float).mean() * y_factor)
            for profile in PROFILES
        ]
        ax.plot(xs, ys, color=styles[algorithm]["color"], linewidth=1.05, alpha=0.72, zorder=3)
        ax.scatter(xs, ys, marker=styles[algorithm]["marker"], s=18, color=styles[algorithm]["color"], alpha=0.95, zorder=6)


def style_axis(ax) -> None:
    ax.grid(axis="y", color="#D8D8D8", linewidth=0.5, alpha=0.8, zorder=1)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_linewidth(0.8)
        spine.set_color("#333333")
    ax.tick_params(axis="both", labelsize=9, colors="#222222", width=0.8)
    ax.set_xlim(-0.52, 3.22)
    ax.set_xticks([PROFILE_X[p] for p in PROFILES])
    ax.set_xticklabels([PAPER_LABEL[p] for p in PROFILES])


def make_figure(df: pd.DataFrame, grayscale: bool) -> tuple[plt.Figure, tuple[float, float], list[int], tuple[float, float], list[int]]:
    styles = GRAY_STYLE if grayscale else STYLE
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.labelsize": 9,
            "axes.titlesize": 10,
            "legend.fontsize": 8.7,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    fig, axes = plt.subplots(1, 2, figsize=(7.16, 3.15), constrained_layout=False)
    fig.subplots_adjust(left=0.075, right=0.985, bottom=0.225, top=0.775, wspace=0.26)

    draw_box_panel(axes[0], df, ACCEPTANCE_FIELD, 100.0, styles, grayscale)
    axes[0].set_title("(a) Acceptance Rate", loc="left", fontweight="normal", pad=5)
    axes[0].set_ylabel("Acceptance Rate (%)")
    axes[0].set_ylim(*ACCEPTANCE_YLIM)
    axes[0].set_yticks(ACCEPTANCE_TICKS)
    style_axis(axes[0])

    draw_box_panel(axes[1], df, RUNTIME_FIELD, 1.0, styles, grayscale)
    axes[1].set_title("(b) Total Runtime", loc="left", fontweight="normal", pad=5)
    axes[1].set_ylabel("Total Runtime")
    axes[1].set_yscale("log")
    axes[1].set_ylim(*RUNTIME_YLIM)
    axes[1].yaxis.set_major_locator(FixedLocator(RUNTIME_TICKS))
    axes[1].yaxis.set_major_formatter(FixedFormatter(RUNTIME_TICK_LABELS))
    axes[1].yaxis.set_minor_formatter(FixedFormatter([]))
    style_axis(axes[1])

    handles = [
        Line2D(
            [0],
            [0],
            color=styles[algorithm]["color"],
            marker=styles[algorithm]["marker"],
            linewidth=1.2,
            markersize=5.5,
            label=DISPLAY[algorithm],
        )
        for algorithm in ALGORITHMS
    ]
    fig.legend(
        handles=handles,
        labels=[DISPLAY[a] for a in ALGORITHMS],
        loc="upper center",
        bbox_to_anchor=(0.5, 0.975),
        ncol=4,
        frameon=False,
        handlelength=1.8,
        columnspacing=1.25,
        handletextpad=0.45,
    )
    fig.text(0.5, 0.058, "S40 (|T|=8, |F|=160, Medium Load) Deadline Sensitivity", ha="center", fontsize=9)
    return fig, ACCEPTANCE_YLIM, ACCEPTANCE_TICKS, RUNTIME_YLIM, RUNTIME_TICKS


def write_outputs(df: pd.DataFrame, summary: dict, paired: dict, validation: dict, source_sha256: str) -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = []
    for grayscale, suffix in [(False, ""), (True, "_gray")]:
        fig, acc_ylim, acc_ticks, rt_ylim, rt_ticks = make_figure(df, grayscale=grayscale)
        png = OUTPUT_DIR / f"deadline_sensitivity_s40_combined{suffix}.png"
        pdf = OUTPUT_DIR / f"deadline_sensitivity_s40_combined{suffix}.pdf"
        fig.savefig(png, dpi=600)
        fig.savefig(pdf)
        plt.close(fig)
        outputs.extend([png.name, pdf.name])

    audit = {
        "authoritative_source_path": str(SOURCE_CSV.relative_to(ROOT)),
        "authoritative_source_sha256": source_sha256,
        "frozen_package": str(FROZEN_DIR.relative_to(ROOT)),
        "frozen_tag_or_package_id": "deadline-sensitivity-s40-v3-final",
        "frozen_manifest_path": str(FROZEN_MANIFEST.relative_to(ROOT)),
        "profile_manifest_path": str(PROFILE_MANIFEST.relative_to(ROOT)),
        "experiment_source_commit": validation["manifest"].get("experiment_source_commit"),
        "formal_plan_fingerprint": validation["manifest"].get("formal_plan_fingerprint"),
        "source_hashes": validation["manifest"].get("hashes", {}),
        "plotted_row_count": int(len(df)),
        "algorithms": [{"internal": alg, "display": DISPLAY[alg]} for alg in ALGORITHMS],
        "deadline_profile_mapping": {profile: PAPER_LABEL[profile] for profile in PROFILES},
        "seeds": list(range(10)),
        "scale": "S40",
        "time_slots": 8,
        "flow_count": 160,
        "load": "Medium",
        "acceptance_field": ACCEPTANCE_FIELD,
        "runtime_field": RUNTIME_FIELD,
        "connecting_statistic_used": CONNECTING_STATISTIC,
        "acceptance_axis": {"limits_percent": list(ACCEPTANCE_YLIM), "ticks_percent": ACCEPTANCE_TICKS},
        "runtime_axis": {
            "scale": "log",
            "limits_seconds": list(RUNTIME_YLIM),
            "ticks_seconds": RUNTIME_TICKS,
            "tick_labels": RUNTIME_TICK_LABELS,
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
        "output_filenames": outputs + ["deadline_sensitivity_s40_figure_audit.json"],
    }
    audit_path = OUTPUT_DIR / "deadline_sensitivity_s40_figure_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    source_sha256 = sha256_file(SOURCE_CSV)
    df = read_dataframe(SOURCE_CSV)
    validation = validate(df, source_sha256)
    summary, paired = summarize(df)
    audit = write_outputs(df, summary, paired, validation, source_sha256)
    print(json.dumps({
        "status": "ok",
        "output_dir": str(OUTPUT_DIR.relative_to(ROOT)),
        "outputs": audit["output_filenames"],
        "summary": summary,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
