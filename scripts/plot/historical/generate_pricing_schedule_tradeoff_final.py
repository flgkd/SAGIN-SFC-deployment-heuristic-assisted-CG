"""Generate review-v6-pastel Pricing Schedule trade-off figures.

Plotting-only review revision. Reads the already audited frozen derived
seed-level CSV and writes review_v6_pastel trade-off artifacts only.
"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from record_compatibility import normalize_record, normalize_dataframe, resolve_input_path


def read_dataframe(path, *args, **kwargs):
    return normalize_dataframe(pd.read_csv(resolve_input_path(path), *args, **kwargs))


import hashlib
import json
import math
from pathlib import Path

import matplotlib.image as mpimg
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedFormatter, FixedLocator
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SOURCE_CSV = ROOT / "outputs" / "pricing_schedule_v3_100_7cee2fe" / "pricing_schedule_seed_level.csv"
FROZEN_PACKAGE = ROOT / "results" / "frozen" / "pricing_schedule_v3"
FINAL_MANIFEST = FROZEN_PACKAGE / "pricing_schedule_final_manifest.json"
OUTPUT_DIR = Path(__file__).resolve().parent
AUDIT_DIR = OUTPUT_DIR / "audit"
TRADE_DIR = OUTPUT_DIR / "tradeoff_1x2"

SCHEDULE_ORDER = ["EX", "HYB-S2", "HYB-S5", "HYB-S7", "ESV"]
LOAD_ORDER = ["Medium", "High"]
SEEDS = list(range(10))
TOTAL_FLOWS = 160
PHASES = 12
CALLS_PER_PHASE = TOTAL_FLOWS
SCHEDULE_PHASES = {"EX": (0, 12), "HYB-S2": (2, 10), "HYB-S5": (5, 7), "HYB-S7": (7, 5), "ESV": (12, 0)}
SCHEDULE_STRATEGY = {"EX": ("exact", None), "HYB-S2": ("hybrid", 2), "HYB-S5": ("hybrid", 5), "HYB-S7": ("hybrid", 7), "ESV": ("early_stop", None)}
SCHEDULE_STYLE = {
    "EX": {"marker": "D", "color": "#E58CA0", "gray": "#1F1F1F", "name": "diamond"},
    "HYB-S2": {"marker": "P", "color": "#7FCFC6", "gray": "#4A4A4A", "name": "plus-filled"},
    "HYB-S5": {"marker": "h", "color": "#B8C96B", "gray": "#6A6A6A", "name": "hexagon"},
    "HYB-S7": {"marker": "<", "color": "#A89AD9", "gray": "#8A8A8A", "name": "left-triangle"},
    "ESV": {"marker": "*", "color": "#F2B37A", "gray": "#B0B0B0", "name": "star"},
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with resolve_input_path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def iqr(values) -> float:
    q75, q25 = np.percentile(np.asarray(values, dtype=float), [75, 25])
    return float(q75 - q25)


def rf(value, nd=6):
    return None if value is None or (isinstance(value, float) and math.isnan(value)) else round(float(value), nd)


def validate(df: pd.DataFrame, manifest: dict, source_sha: str) -> dict:
    errors = []
    expected_keys = {(load, seed, sched) for load in LOAD_ORDER for seed in SEEDS for sched in SCHEDULE_ORDER}
    actual_keys = set(zip(df["load_label"], df["formal_seed"].astype(int), df["pricing_schedule"]))
    if len(df) != 100:
        errors.append(f"expected 100 rows, found {len(df)}")
    if actual_keys != expected_keys:
        errors.append({"missing": sorted(expected_keys - actual_keys), "extra": sorted(actual_keys - expected_keys)})
    if df.duplicated(["load_label", "formal_seed", "pricing_schedule"]).any():
        errors.append("duplicate load/seed/schedule keys")
    if sorted(df["algorithm"].unique()) != ["dwcg_pure"]:
        errors.append(f"algorithm set mismatch: {sorted(df['algorithm'].unique())}")
    if sorted(df["load_label"].unique()) != sorted(LOAD_ORDER):
        errors.append(f"load set mismatch: {sorted(df['load_label'].unique())}")
    if sorted(df["pricing_schedule"].unique(), key=SCHEDULE_ORDER.index) != SCHEDULE_ORDER:
        errors.append(f"schedule set mismatch: {sorted(df['pricing_schedule'].unique())}")
    if sorted(df["formal_seed"].astype(int).unique()) != SEEDS:
        errors.append("seed set mismatch")
    if sorted(df["scale"].unique()) != ["S40"] or sorted(df["time_slots"].astype(int).unique()) != [8]:
        errors.append("scenario scale/time mismatch")
    if not ((df["acceptance_rate"].astype(float) * TOTAL_FLOWS).round(8) == df["accepted_flow_count"].astype(float)).all():
        errors.append("acceptance_rate is not accepted_flow_count / 160")
    for col in ["path_validity", "capacity_validity", "runtime_accounting_valid"]:
        if not df[col].astype(str).str.lower().eq("true").all():
            errors.append(f"{col} contains non-true values")
    manifest_sha = manifest.get("file_hashes", {}).get("outputs/pricing_schedule_v3_100_7cee2fe/pricing_schedule_seed_level.csv", {}).get("sha256")
    if manifest_sha != source_sha:
        errors.append("source CSV sha256 does not match final manifest")
    phase_errors = []
    for sched, (early, strict) in SCHEDULE_PHASES.items():
        group = df[df["pricing_schedule"] == sched]
        strategy, switch = SCHEDULE_STRATEGY[sched]
        switches = set(None if pd.isna(v) or v == "" else int(float(v)) for v in group["hybrid_switch_iteration"])
        if set(group["early_stop_pricing_call_count"].astype(int)) != {early * CALLS_PER_PHASE}:
            phase_errors.append(sched + ":early")
        if set(group["strict_exact_pricing_call_count"].astype(int)) != {strict * CALLS_PER_PHASE}:
            phase_errors.append(sched + ":strict")
        if set(group["exact_pricing_call_count"].astype(int)) != {PHASES * CALLS_PER_PHASE}:
            phase_errors.append(sched + ":total")
        if set(group["pricing_strategy"].astype(str)) != {strategy} or switches != {switch}:
            phase_errors.append(sched + ":strategy")
    if phase_errors:
        errors.append({"schedule_semantics_errors": phase_errors})
    return {"pass": not errors, "errors": errors, "row_count": int(len(df)), "source_sha256": source_sha}


def compute_stats(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for load in LOAD_ORDER:
        for sched in SCHEDULE_ORDER:
            g = df[(df.load_label == load) & (df.pricing_schedule == sched)].copy()
            rate = g["acceptance_rate"].astype(float).to_numpy() * 100.0
            runtime = g["total_end_to_end_runtime_seconds"].astype(float).to_numpy()
            rows.append({
                "load_label": load,
                "pricing_schedule": sched,
                "n": len(g),
                "mean_acceptance_rate_percent": rf(rate.mean()),
                "median_total_runtime_seconds": rf(np.median(runtime)),
                "acceptance_iqr_percent": rf(iqr(rate)),
                "runtime_iqr_seconds": rf(iqr(runtime)),
                "min_total_runtime_seconds": rf(runtime.min()),
                "max_total_runtime_seconds": rf(runtime.max()),
            })
    return pd.DataFrame(rows)


def acceptance_ylim(values):
    vals = np.asarray(values, dtype=float)
    lo, hi = vals.min(), vals.max()
    pad = max(2.0, (hi - lo) * 0.10)
    return max(0.0, math.floor(lo - pad)), min(100.0, math.ceil(hi + pad))


def runtime_minutes_ticks(values_min):
    candidates = [(4, "4 min"), (6, "6 min"), (8, "8 min"), (10, "10 min"), (12, "12 min")]
    lo, hi = min(values_min), max(values_min)
    return zip(*[(t, lab) for t, lab in candidates if lo - 0.5 <= t <= hi + 0.5])


def schedule_color(sched: str, grayscale: bool) -> str:
    style = SCHEDULE_STYLE[sched]
    return style["gray"] if grayscale else style["color"]


def draw_tradeoff_panel(ax, df, stats, load, grayscale=False):
    x_vals = []
    y_vals = []
    for sched in SCHEDULE_ORDER:
        color = schedule_color(sched, grayscale)
        marker = SCHEDULE_STYLE[sched]["marker"]
        g = df[(df.load_label == load) & (df.pricing_schedule == sched)]
        sx = g["total_end_to_end_runtime_seconds"].astype(float).to_numpy() / 60.0
        sy = g["acceptance_rate"].astype(float).to_numpy() * 100.0
        ax.scatter(sx, sy, marker=marker, s=16, facecolor=color, edgecolor=color, alpha=0.18, linewidth=0.35, zorder=2)
        row = stats[(stats.load_label == load) & (stats.pricing_schedule == sched)].iloc[0]
        x = float(row.median_total_runtime_seconds) / 60.0
        y = float(row.mean_acceptance_rate_percent)
        x_vals.append(x)
        y_vals.append(y)
        ax.errorbar(x, y, xerr=float(row.runtime_iqr_seconds) / 120.0, yerr=float(row.acceptance_iqr_percent) / 2.0, fmt="none", ecolor=color, elinewidth=0.8, alpha=0.34, zorder=3)
        ax.scatter([x], [y], marker=marker, s=78 if marker != "*" else 112, facecolor=color, edgecolor="#222222" if not grayscale else "#111111", linewidth=0.65, alpha=0.96, zorder=5)
    ax.plot(x_vals, y_vals, color="#6F6F6F", linestyle=(0, (2.0, 2.2)), linewidth=0.85, alpha=0.44, zorder=1)
    all_runtime = df[df.load_label == load]["total_end_to_end_runtime_seconds"].astype(float).to_numpy() / 60.0
    all_accept = df[df.load_label == load]["acceptance_rate"].astype(float).to_numpy() * 100.0
    ax.set_xlim(max(0, all_runtime.min() - 0.75), all_runtime.max() + 0.95)
    ax.set_ylim(*acceptance_ylim(all_accept))
    ticks, labels = list(runtime_minutes_ticks(all_runtime))[0], list(runtime_minutes_ticks(all_runtime))[1]
    ax.xaxis.set_major_locator(FixedLocator(list(ticks)))
    ax.xaxis.set_major_formatter(FixedFormatter(list(labels)))
    ax.set_xlabel("Median Total Runtime")
    ax.set_ylabel("Mean Acceptance Rate (%)")
    ax.grid(True, color="#D8D8D8", linewidth=0.5, alpha=0.75)
    for spine in ax.spines.values():
        spine.set_color("#333333")
        spine.set_linewidth(0.8)
    ax.tick_params(axis="both", labelsize=8.0, width=0.8, colors="#222222")


def make_tradeoff(df, stats, grayscale=False):
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8.5, "pdf.fonttype": 42, "ps.fonttype": 42})
    fig, axes = plt.subplots(1, 2, figsize=(7.25, 3.72), constrained_layout=False)
    fig.subplots_adjust(left=0.082, right=0.982, bottom=0.305, top=0.805, wspace=0.30)
    for ax, load, label in zip(axes, LOAD_ORDER, ["(a) Medium Load", "(b) High Load"]):
        draw_tradeoff_panel(ax, df, stats, load, grayscale)
        bbox = ax.get_position()
        fig.text((bbox.x0 + bbox.x1) / 2, bbox.y0 - 0.145, label, ha="center", va="top", fontsize=9.0)
    handles = []
    for sched in SCHEDULE_ORDER:
        color = schedule_color(sched, grayscale)
        marker = SCHEDULE_STYLE[sched]["marker"]
        handles.append(Line2D([0], [0], marker=marker, linestyle="none", markersize=6.2 if marker != "*" else 8.0, markerfacecolor=color, markeredgecolor="#222222" if not grayscale else "#111111", markeredgewidth=0.55, label=sched))
    leg = fig.legend(handles=handles, title="Pricing Schedule", loc="upper center", bbox_to_anchor=(0.5, 0.965), ncol=5, frameon=False, handletextpad=0.45, columnspacing=1.15, borderaxespad=0.0)
    leg.get_title().set_fontsize(8.5)
    for text in leg.get_texts():
        text.set_fontsize(8.2)
    fig.text(0.5, 0.071, "DWCG-Pure Pricing Schedule with", ha="center", fontsize=8.8)
    fig.text(0.5, 0.027, "S40 (|T|=8, |F|=160, Medium/High Load)", ha="center", fontsize=8.8)
    return fig


def visual_checks(paths):
    checks = {}
    for key, path in paths.items():
        img = mpimg.imread(path)
        checks[key] = {"exists": path.exists(), "bytes": path.stat().st_size, "shape": list(img.shape), "nonwhite_pixel_count": int((img[:, :, :3] < 0.985).any(axis=2).sum())}
    checks["tradeoff"] = {
        "panel_count": 2,
        "direct_schedule_text_labels_removed": True,
        "shared_legend_title": "Pricing Schedule",
        "summary_coordinates": {"x": "median total_end_to_end_runtime_seconds / 60", "y": "mean acceptance_rate percent"},
        "runtime_axis": "linear",
        "runtime_units": "minutes",
        "scientific_notation_ticks": False,
        "seed_overlay": "all seeds included at low alpha",
        "neutral_schedule_order_connector": "thin gray dashed line",
    }
    return checks


def main() -> int:
    manifest = json.loads(FINAL_MANIFEST.read_text(encoding="utf-8"))
    source_sha = sha256_file(SOURCE_CSV)
    df = read_dataframe(SOURCE_CSV)
    df["formal_seed"] = df["formal_seed"].astype(int)
    validation = validate(df, manifest, source_sha)
    if not validation["pass"]:
        print(json.dumps(validation, indent=2, sort_keys=True))
        return 2
    for d in (AUDIT_DIR, TRADE_DIR):
        d.mkdir(parents=True, exist_ok=True)
    stats = compute_stats(df)
    stats.to_csv(AUDIT_DIR / "pricing_schedule_tradeoff_summary.csv", index=False)

    fig = make_tradeoff(df, stats, False)
    fig.savefig(TRADE_DIR / "pricing_schedule_tradeoff_1x2.png", dpi=600)
    fig.savefig(TRADE_DIR / "pricing_schedule_tradeoff_1x2.pdf")
    plt.close(fig)
    fig = make_tradeoff(df, stats, True)
    fig.savefig(TRADE_DIR / "pricing_schedule_tradeoff_1x2_gray.png", dpi=600)
    fig.savefig(TRADE_DIR / "pricing_schedule_tradeoff_1x2_gray.pdf")
    plt.close(fig)

    paths = {
        "tradeoff_png": TRADE_DIR / "pricing_schedule_tradeoff_1x2.png",
        "tradeoff_gray_png": TRADE_DIR / "pricing_schedule_tradeoff_1x2_gray.png",
    }
    marker_mapping = {sched: {"marker": SCHEDULE_STYLE[sched]["name"], "matplotlib_marker": SCHEDULE_STYLE[sched]["marker"], "color": SCHEDULE_STYLE[sched]["color"], "grayscale_color": SCHEDULE_STYLE[sched]["gray"]} for sched in SCHEDULE_ORDER}
    audit = {
        "task_type": "review_v6_pastel_tradeoff_plotting_only_revision",
        "authoritative_seed_level_source": str(SOURCE_CSV.relative_to(ROOT)),
        "source_sha256": source_sha,
        "frozen_package_path": str(FROZEN_PACKAGE.relative_to(ROOT)),
        "scientific_tag": "pricing-schedule-v3-final",
        "validation": validation,
        "review_directory": str(OUTPUT_DIR.relative_to(ROOT)),
        "plotting_script": str(Path(__file__).resolve().relative_to(ROOT)),
        "schedule_order": SCHEDULE_ORDER,
        "schedule_semantics": {s: {"early_stop_phases": e, "strict_exact_phases": st} for s, (e, st) in SCHEDULE_PHASES.items()},
        "schedule_marker_color_mapping": marker_mapping,
        "tradeoff_1x2": {
            "panel_count": 2,
            "panels": ["(a) Medium Load", "(b) High Load"],
            "direct_in_plot_schedule_labels_removed": True,
            "shared_legend_title": "Pricing Schedule",
            "summary_coordinates": {"x": "median total_end_to_end_runtime_seconds / 60", "y": "mean acceptance_rate percent"},
            "runtime_axis": "linear",
            "runtime_display_units": "minutes",
            "runtime_ticks": ["4 min", "6 min", "8 min", "10 min", "12 min"],
            "connector": "thin light neutral dashed line connecting EX -> HYB-S2 -> HYB-S5 -> HYB-S7 -> ESV",
            "seed_overlay": "all seed-level points retained, small and low alpha",
            "footer_spacing": {"first_line_y": 0.071, "second_line_y": 0.027, "line_gap_figure_fraction": 0.044},
            "xlabel_to_panel_caption_spacing": {"panel_caption_offset_below_axes": 0.145, "bottom_margin": 0.305},
            "top_title_removed": True,
        },
        "outputs": [str((TRADE_DIR / f).relative_to(ROOT)) for f in ["pricing_schedule_tradeoff_1x2.png", "pricing_schedule_tradeoff_1x2.pdf", "pricing_schedule_tradeoff_1x2_gray.png", "pricing_schedule_tradeoff_1x2_gray.pdf"]] + [str((AUDIT_DIR / f).relative_to(ROOT)) for f in ["pricing_schedule_tradeoff_summary.csv", "pricing_schedule_tradeoff_review_v6_pastel_audit.json"]] + [str((OUTPUT_DIR / "README.md").relative_to(ROOT)), str(Path(__file__).resolve().relative_to(ROOT))],
        "visual_validation": visual_checks(paths),
        "safety": {"no_gurobi": True, "no_experiment_rerun": True, "no_scientific_data_modification": True, "no_frozen_package_modification": True, "no_commit_tag_push": True, "original_v3_root_only": True, "no_new_root_or_worktree": True},
    }
    readme = """# Pricing Schedule Trade-off Review V6 Pastel

Plotting-only revision for the DWCG-Pure Pricing Schedule trade-off figure.

- Authoritative seed-level source: `outputs/pricing_schedule_v3_100_7cee2fe/pricing_schedule_seed_level.csv`
- Source SHA256: `{source_sha}`
- Output directory: `figures/pricing_schedule/review_v6_pastel`
- Plotting script: `figures/pricing_schedule/review_v6_pastel/generate_pricing_schedule_tradeoff_review_v6_pastel.py`

The five pricing schedules are encoded by a shared legend titled `Pricing Schedule`. Direct in-plot schedule text labels remain removed from the data region. Review V6 Pastel keeps the Review V4/V5 marker shapes and replaces only the color palette with the requested lighter pastel-style palette.

Marker/color mapping:

- EX: diamond (`D`), `{ex_color}`
- HYB-S2: plus-filled (`P`), `{s2_color}`
- HYB-S5: hexagon (`h`), `{s5_color}`
- HYB-S7: left-triangle (`<`), `{s7_color}`
- ESV: star (`*`), `{esv_color}`

The grayscale version keeps the same marker shapes with distinct gray levels. Seed points are retained with low alpha, summary markers are larger, and the schedule-order connector is a thin neutral dashed line.

No scientific data were modified. No experiment was rerun. Gurobi was not invoked. No commit, tag, or push was performed.
""".format(source_sha=source_sha, ex_color=SCHEDULE_STYLE["EX"]["color"], s2_color=SCHEDULE_STYLE["HYB-S2"]["color"], s5_color=SCHEDULE_STYLE["HYB-S5"]["color"], s7_color=SCHEDULE_STYLE["HYB-S7"]["color"], esv_color=SCHEDULE_STYLE["ESV"]["color"])
    (OUTPUT_DIR / "README.md").write_text(readme, encoding="utf-8")
    (AUDIT_DIR / "pricing_schedule_tradeoff_review_v6_pastel_audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "output_dir": str(OUTPUT_DIR.relative_to(ROOT)), "source_sha256": source_sha, "outputs": audit["outputs"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
