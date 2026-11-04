"""Generate PRO Pricing Schedule review-v1 figures.

Plotting-only review. Reads the frozen/canonical PRO pricing-schedule seed-level
CSV and writes review_v2 trade-off artifacts only. Does not run optimization or alter data.
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
SOURCE_CSV = ROOT / "outputs" / "pro_pricing_schedule_v3_100_bb65465" / "pro_pricing_schedule_seed_level.csv"
FROZEN_PACKAGE = ROOT / "results" / "frozen" / "pro_pricing_schedule_v3"
FINAL_MANIFEST = FROZEN_PACKAGE / "pro_pricing_schedule_final_manifest.json"
CONFIG = ROOT / "configs" / "formal" / "formal_pro_pricing_schedule_v3.json"
OUTPUT_DIR = Path(__file__).resolve().parent
AUDIT_DIR = OUTPUT_DIR / "audit"
DIST_DIR = OUTPUT_DIR / "distribution_1x2"
TRADE_DIR = OUTPUT_DIR / "tradeoff_1x2"

SCHEDULE_ORDER = ["EX", "HYB-S2", "HYB-S5", "HYB-S7", "ESV"]
LOAD_ORDER = ["Medium", "High"]
SEEDS = list(range(10))
TOTAL_FLOWS = 160
PHASES = 12
CALLS_PER_PHASE = TOTAL_FLOWS
SCHEDULE_PHASES = {"EX": (0, 12), "HYB-S2": (2, 10), "HYB-S5": (5, 7), "HYB-S7": (7, 5), "ESV": (12, 0)}
SCHEDULE_STRATEGY = {"EX": ("exact", None), "HYB-S2": ("hybrid", 2), "HYB-S5": ("hybrid", 5), "HYB-S7": ("hybrid", 7), "ESV": ("early_stop", None)}
LOAD_STYLE = {
    "Medium": {"color": "#4F90A6", "fill": "#CDE8EF", "marker": "s", "linestyle": "solid", "offset": -0.18},
    "High": {"color": "#7F6AA6", "fill": "#E1DAF0", "marker": "s", "linestyle": (0, (3.0, 2.0)), "offset": 0.18},
}
GRAY_LOAD_STYLE = {
    "Medium": {"color": "#4A4A4A", "fill": "#D9D9D9", "marker": "s", "linestyle": "solid", "offset": -0.18},
    "High": {"color": "#777777", "fill": "#EFEFEF", "marker": "s", "linestyle": (0, (3.0, 2.0)), "offset": 0.18},
}
SCHEDULE_STYLE = {
    "EX": {"marker": "p", "color": "#D18A96", "gray": "#1F1F1F", "name": "pentagon"},
    "HYB-S2": {"marker": "d", "color": "#68B7B0", "gray": "#4A4A4A", "name": "thin-diamond"},
    "HYB-S5": {"marker": "H", "color": "#BDB86D", "gray": "#6A6A6A", "name": "hexagon2"},
    "HYB-S7": {"marker": ">", "color": "#9B8BCC", "gray": "#8A8A8A", "name": "right-triangle"},
    "ESV": {"marker": "8", "color": "#E3A477", "gray": "#B0B0B0", "name": "octagon"},
}
MEAN_COLOR = "#8B0000"
MEDIAN_COLOR = "#111111"
MEAN_Z = 9
MEDIAN_Z = 8
SEED_Z = 5
BOX_Z = 3
TREND_Z = 2
SEED_JITTER = np.array([-0.052, -0.041, -0.030, -0.018, -0.006, 0.006, 0.018, 0.030, 0.041, 0.052])


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
    if sorted(df["algorithm"].unique()) != ["dwcg_saginfe_pro"]:
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
    manifest_sha = manifest.get("hashes", {}).get("seed_level_csv", {}).get("sha256")
    if manifest_sha != source_sha:
        errors.append("source CSV sha256 does not match final manifest")
    if df["source_kind"].value_counts().to_dict() != {"new": 80, "reused": 20}:
        errors.append("source kind counts mismatch")
    reused = df[df["source_kind"] == "reused"]
    if set(reused["pricing_schedule"]) != {"HYB-S5"} or len(reused) != 20:
        errors.append("reused rows are not exactly 20 HYB-S5 rows")
    phase_errors = []
    for sched, (early, strict) in SCHEDULE_PHASES.items():
        group = df[df["pricing_schedule"] == sched]
        strategy, switch = SCHEDULE_STRATEGY[sched]
        switches = set(None if pd.isna(v) or v == "" else int(float(v)) for v in group["hybrid_switch_iteration"])
        early_phase_counts = group["early_stop_phase_count"].dropna()
        strict_phase_counts = group["strict_exact_phase_count"].dropna()
        if len(early_phase_counts) and set(early_phase_counts.astype(int)) != {early}:
            phase_errors.append(sched + ":early_phase_count")
        if len(strict_phase_counts) and set(strict_phase_counts.astype(int)) != {strict}:
            phase_errors.append(sched + ":strict_phase_count")
        if set(group["early_stop_pricing_call_count"].astype(int)) != {early * CALLS_PER_PHASE}:
            phase_errors.append(sched + ":early_calls")
        if set(group["strict_exact_pricing_call_count"].astype(int)) != {strict * CALLS_PER_PHASE}:
            phase_errors.append(sched + ":strict_calls")
        if set(group["exact_pricing_call_count"].astype(int)) != {PHASES * CALLS_PER_PHASE}:
            phase_errors.append(sched + ":total_exact_budget")
        if set(group["pricing_strategy"].astype(str)) != {strategy} or switches != {switch}:
            phase_errors.append(sched + ":strategy")
    if phase_errors:
        errors.append({"schedule_semantics_errors": phase_errors})
    pairing_errors = []
    for (load, seed), group in df.groupby(["load_label", "formal_seed"]):
        for field in ["topology_fingerprint", "base_flow_fingerprint", "full_instance_fingerprint"]:
            if group[field].nunique() != 1:
                pairing_errors.append(f"{load}/{seed}: {field} mismatch")
    if pairing_errors:
        errors.append({"pairing_errors": pairing_errors})
    return {"pass": not errors, "errors": errors, "row_count": int(len(df)), "source_sha256": source_sha}


def compute_stats(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for load in LOAD_ORDER:
        for sched in SCHEDULE_ORDER:
            g = df[(df.load_label == load) & (df.pricing_schedule == sched)].copy()
            acc = g["accepted_flow_count"].astype(float).to_numpy()
            rate = g["acceptance_rate"].astype(float).to_numpy() * 100.0
            runtime = g["total_end_to_end_runtime_seconds"].astype(float).to_numpy()
            pricing = g["total_pricing_runtime"].astype(float).to_numpy()
            rows.append({"load_label": load, "pricing_schedule": sched, "early_stop_phases": SCHEDULE_PHASES[sched][0], "strict_phases": SCHEDULE_PHASES[sched][1], "n": len(g), "mean_accepted_flows": rf(acc.mean()), "median_accepted_flows": rf(np.median(acc)), "min_accepted_flows": rf(acc.min()), "max_accepted_flows": rf(acc.max()), "std_accepted_flows": rf(acc.std(ddof=1)), "mean_acceptance_rate_percent": rf(rate.mean()), "median_acceptance_rate_percent": rf(np.median(rate)), "acceptance_iqr_percent": rf(iqr(rate)), "mean_total_runtime_seconds": rf(runtime.mean()), "median_total_runtime_seconds": rf(np.median(runtime)), "runtime_iqr_seconds": rf(iqr(runtime)), "min_total_runtime_seconds": rf(runtime.min()), "max_total_runtime_seconds": rf(runtime.max()), "median_total_pricing_runtime_seconds": rf(np.median(pricing))})
    return pd.DataFrame(rows)


def paired_rows(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    pairs = []
    for load in LOAD_ORDER:
        for sched in SCHEDULE_ORDER[1:]:
            pairs.append((load, sched, "EX", f"{sched} - EX", "vs_EX"))
        for a, b in zip(SCHEDULE_ORDER[1:], SCHEDULE_ORDER[:-1]):
            pairs.append((load, a, b, f"{a} - {b}", "adjacent"))
    idx = df.set_index(["load_label", "formal_seed", "pricing_schedule"])
    for load, sched, base, label, kind in pairs:
        deltas = []
        ratios = []
        for seed in SEEDS:
            a = idx.loc[(load, seed, sched)]
            b = idx.loc[(load, seed, base)]
            deltas.append(float(a.accepted_flow_count) - float(b.accepted_flow_count))
            ratios.append(float(a.total_end_to_end_runtime_seconds) / float(b.total_end_to_end_runtime_seconds))
        rows.append({"load_label": load, "comparison_kind": kind, "schedule": sched, "baseline_schedule": base, "comparison": label, "n": len(deltas), "mean_accepted_flow_delta": rf(np.mean(deltas)), "median_accepted_flow_delta": rf(np.median(deltas)), "wins_schedule_gt_baseline": int(sum(d > 0 for d in deltas)), "ties": int(sum(d == 0 for d in deltas)), "losses_schedule_lt_baseline": int(sum(d < 0 for d in deltas)), "median_runtime_ratio_schedule_over_baseline": rf(np.median(ratios)), "mean_runtime_ratio_schedule_over_baseline": rf(np.mean(ratios))})
    return pd.DataFrame(rows)


def acceptance_ylim(values):
    vals = np.asarray(values, dtype=float)
    lo, hi = vals.min(), vals.max()
    pad = max(1.5, (hi - lo) * 0.10)
    return max(0.0, math.floor(lo - pad)), min(100.0, math.ceil(hi + pad))


def runtime_seconds_ticks(values):
    candidates = [(180, "3 min"), (300, "5 min"), (600, "10 min"), (900, "15 min")]
    lo, hi = min(values), max(values)
    return zip(*[(t, lab) for t, lab in candidates if lo * 0.80 <= t <= hi * 1.20])


def runtime_minutes_ticks(values_min):
    candidates = [(4, "4 min"), (6, "6 min"), (8, "8 min"), (10, "10 min"), (12, "12 min"), (14, "14 min")]
    lo, hi = min(values_min), max(values_min)
    return zip(*[(t, lab) for t, lab in candidates if lo - 0.5 <= t <= hi + 0.5])


def draw_distribution_panel(ax, df, metric, grayscale=False):
    styles = GRAY_LOAD_STYLE if grayscale else LOAD_STYLE
    is_accept = metric == "acceptance"
    positions = np.arange(len(SCHEDULE_ORDER), dtype=float)
    all_values = []
    for load in LOAD_ORDER:
        means = []
        xs = []
        style = styles[load]
        for x, sched in zip(positions, SCHEDULE_ORDER):
            g = df[(df.load_label == load) & (df.pricing_schedule == sched)].sort_values("formal_seed")
            vals = (g["acceptance_rate"].astype(float) * 100.0 if is_accept else g["total_end_to_end_runtime_seconds"].astype(float)).to_numpy()
            all_values.extend(vals.tolist())
            pos = x + style["offset"]
            xs.append(pos)
            means.append(float(vals.mean()))
            bp = ax.boxplot([vals], positions=[pos], widths=0.27, patch_artist=True, manage_ticks=False, showfliers=False, medianprops={"color": MEDIAN_COLOR, "linewidth": 1.2}, boxprops={"edgecolor": style["color"], "linewidth": 1.0}, whiskerprops={"color": style["color"], "linewidth": 0.85}, capprops={"color": style["color"], "linewidth": 0.85})
            bp["boxes"][0].set_facecolor(style["fill"])
            bp["boxes"][0].set_alpha(0.86)
            bp["boxes"][0].set_zorder(BOX_Z)
            for group in [bp["whiskers"], bp["caps"]]:
                for artist in group:
                    artist.set_zorder(BOX_Z + 1)
            for med in bp["medians"]:
                med.set(color=MEDIAN_COLOR, linewidth=1.25, zorder=MEDIAN_Z)
            face = style["color"] if load == "Medium" else ("none" if not grayscale else style["fill"])
            ax.scatter(pos + SEED_JITTER, vals, marker="s", s=15, facecolor=face, edgecolor=style["color"], linewidth=0.45, alpha=0.43, zorder=SEED_Z)
            ax.hlines(float(vals.mean()), pos - 0.105, pos + 0.105, colors=MEAN_COLOR if not grayscale else "#303030", linestyles=(0, (1.0, 1.2)), linewidth=1.55, alpha=0.96, zorder=MEAN_Z)
        ax.plot(xs, means, color=style["color"], linestyle=style["linestyle"], linewidth=0.85, alpha=0.50, zorder=TREND_Z)
        ax.scatter(xs, means, marker="s", s=13, color=style["color"], alpha=0.62, zorder=TREND_Z + 1)
    ax.set_xticks(positions)
    ax.set_xticklabels(SCHEDULE_ORDER, fontsize=8.2)
    ax.set_xlim(-0.65, len(SCHEDULE_ORDER) - 0.35)
    ax.grid(axis="y", color="#D8D8D8", linewidth=0.5, alpha=0.78, zorder=1)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color("#333333")
        spine.set_linewidth(0.8)
    ax.tick_params(axis="both", labelsize=8.0, width=0.8, colors="#222222")
    if is_accept:
        ax.set_ylim(*acceptance_ylim(all_values))
        ax.set_ylabel("Acceptance Rate (%)")
    else:
        ax.set_yscale("log")
        ax.set_ylim(min(all_values) * 0.82, max(all_values) * 1.18)
        ticks, labels = list(runtime_seconds_ticks(all_values))[0], list(runtime_seconds_ticks(all_values))[1]
        ax.yaxis.set_major_locator(FixedLocator(list(ticks)))
        ax.yaxis.set_major_formatter(FixedFormatter(list(labels)))
        ax.yaxis.set_minor_formatter(FixedFormatter([]))
        ax.set_ylabel("Total Runtime")
    return tuple(float(v) for v in ax.get_ylim())


def make_distribution(df, grayscale=False):
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8.5, "pdf.fonttype": 42, "ps.fonttype": 42})
    fig, axes = plt.subplots(1, 2, figsize=(7.25, 3.72), constrained_layout=False)
    fig.subplots_adjust(left=0.082, right=0.985, bottom=0.285, top=0.865, wspace=0.28)
    ylims = {"acceptance": draw_distribution_panel(axes[0], df, "acceptance", grayscale), "runtime": draw_distribution_panel(axes[1], df, "runtime", grayscale)}
    styles = GRAY_LOAD_STYLE if grayscale else LOAD_STYLE
    handles = [Line2D([0], [0], color=styles[l]["color"], linestyle=styles[l]["linestyle"], marker="s", markersize=5, linewidth=1.0, label=f"{l} Load", markerfacecolor=styles[l]["color"] if l == "Medium" else styles[l]["fill"]) for l in LOAD_ORDER]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.965), ncol=2, frameon=False, handlelength=1.7, columnspacing=1.8)
    for ax, label in zip(axes, ["(a) Acceptance Rate", "(b) Total Runtime"]):
        bbox = ax.get_position()
        fig.text((bbox.x0 + bbox.x1) / 2, bbox.y0 - 0.098, label, ha="center", va="top", fontsize=9.0)
    fig.text(0.5, 0.071, "DWCG-SAGINFE-PRO Pricing Schedule with", ha="center", fontsize=8.8)
    fig.text(0.5, 0.027, "S40 (|T|=8, |F|=160, Medium/High Load)", ha="center", fontsize=8.8)
    return fig, ylims


def schedule_color(sched: str, grayscale: bool) -> str:
    return SCHEDULE_STYLE[sched]["gray"] if grayscale else SCHEDULE_STYLE[sched]["color"]


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
        # Review v2 removes the redundant summary-point IQR error bars; seed points still show variability.
        ax.scatter([x], [y], marker=marker, s=78 if marker not in ("*", "8") else 100, facecolor=color, edgecolor="#222222" if not grayscale else "#111111", linewidth=0.65, alpha=0.96, zorder=5)
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
        handles.append(Line2D([0], [0], marker=marker, linestyle="none", markersize=6.2 if marker not in ("*", "8") else 7.4, markerfacecolor=color, markeredgecolor="#222222" if not grayscale else "#111111", markeredgewidth=0.55, label=sched))
    leg = fig.legend(handles=handles, title="Pricing Schedule", loc="upper center", bbox_to_anchor=(0.5, 0.965), ncol=5, frameon=False, handletextpad=0.45, columnspacing=1.15, borderaxespad=0.0)
    leg.get_title().set_fontsize(8.5)
    for text in leg.get_texts():
        text.set_fontsize(8.2)
    fig.text(0.5, 0.071, "DWCG-SAGINFE-PRO Pricing Schedule with", ha="center", fontsize=8.8)
    fig.text(0.5, 0.027, "S40 (|T|=8, |F|=160, Medium/High Load)", ha="center", fontsize=8.8)
    return fig


def monotonicity(df, stats):
    out = {"aggregate": {}, "seed_level": {}}
    for load in LOAD_ORDER:
        means = [float(stats[(stats.load_label == load) & (stats.pricing_schedule == s)].iloc[0].mean_accepted_flows) for s in SCHEDULE_ORDER]
        runtimes = [float(stats[(stats.load_label == load) & (stats.pricing_schedule == s)].iloc[0].median_total_runtime_seconds) for s in SCHEDULE_ORDER]
        violations = []
        for seed in SEEDS:
            vals = [float(df[(df.load_label == load) & (df.formal_seed == seed) & (df.pricing_schedule == s)].iloc[0].accepted_flow_count) for s in SCHEDULE_ORDER]
            for i in range(len(vals) - 1):
                if vals[i] < vals[i + 1]:
                    violations.append({"seed": seed, "left": SCHEDULE_ORDER[i], "right": SCHEDULE_ORDER[i + 1], "left_accepted": vals[i], "right_accepted": vals[i + 1]})
        out["aggregate"][load] = {"acceptance_nonincreasing_as_early_stop_increases": all(means[i] >= means[i+1] for i in range(len(means)-1)), "median_runtime_nonincreasing_as_early_stop_increases": all(runtimes[i] >= runtimes[i+1] for i in range(len(runtimes)-1)), "mean_accepted_flows_by_schedule": dict(zip(SCHEDULE_ORDER, means)), "median_runtime_seconds_by_schedule": dict(zip(SCHEDULE_ORDER, runtimes))}
        out["seed_level"][load] = {"acceptance_nonincreasing_for_every_seed": not violations, "violation_count": len(violations), "violations": violations}
    return out


def visual_checks(paths):
    checks = {}
    for key, path in paths.items():
        img = mpimg.imread(path)
        checks[key] = {"exists": path.exists(), "bytes": path.stat().st_size, "shape": list(img.shape), "nonwhite_pixel_count": int((img[:, :, :3] < 0.985).any(axis=2).sum())}
    checks["figure_b"] = {"panel_count": 2, "shared_legend_title": "Pricing Schedule", "runtime_axis": "linear", "runtime_units": "minutes", "scientific_notation_ticks": False, "direct_schedule_labels_in_axes": False, "seed_overlay": "included low alpha"}
    return checks


def main() -> int:
    manifest = json.loads(FINAL_MANIFEST.read_text(encoding="utf-8"))
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
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
    paired = paired_rows(df)
    stats.to_csv(AUDIT_DIR / "pro_pricing_schedule_seed_summary.csv", index=False)
    paired.to_csv(AUDIT_DIR / "pro_pricing_schedule_paired_comparisons.csv", index=False)

    fig = make_tradeoff(df, stats, False)
    fig.savefig(TRADE_DIR / "pro_pricing_schedule_tradeoff_1x2.png", dpi=600)
    fig.savefig(TRADE_DIR / "pro_pricing_schedule_tradeoff_1x2.pdf")
    plt.close(fig)
    fig = make_tradeoff(df, stats, True)
    fig.savefig(TRADE_DIR / "pro_pricing_schedule_tradeoff_1x2_gray.png", dpi=600)
    fig.savefig(TRADE_DIR / "pro_pricing_schedule_tradeoff_1x2_gray.pdf")
    plt.close(fig)

    paths = {
        "tradeoff_png": TRADE_DIR / "pro_pricing_schedule_tradeoff_1x2.png",
        "tradeoff_gray_png": TRADE_DIR / "pro_pricing_schedule_tradeoff_1x2_gray.png",
    }
    matrix_counts = df.groupby(["load_label", "pricing_schedule"]).size().unstack().to_dict()
    marker_mapping = {sched: {"marker": SCHEDULE_STYLE[sched]["name"], "matplotlib_marker": SCHEDULE_STYLE[sched]["marker"], "color": SCHEDULE_STYLE[sched]["color"], "grayscale_color": SCHEDULE_STYLE[sched]["gray"]} for sched in SCHEDULE_ORDER}
    audit = {
        "task_type": "pro_pricing_schedule_review_v2_tradeoff_no_errorbars",
        "authoritative_seed_level_source": str(SOURCE_CSV.relative_to(ROOT)),
        "source_sha256": source_sha,
        "frozen_package_path": str(FROZEN_PACKAGE.relative_to(ROOT)),
        "scientific_tag_or_plan": {"scientific_plan_tag": manifest.get("scientific_plan_tag"), "scientific_plan_commit": manifest.get("scientific_plan_commit"), "execution_commit_for_80_new_rows": manifest.get("execution_commit_for_80_new_rows")},
        "review_directory": str(OUTPUT_DIR.relative_to(ROOT)),
        "plotting_script": str(Path(__file__).resolve().relative_to(ROOT)),
        "experiment_scope": {"algorithm": "dwcg_saginfe_pro", "scale": "S40", "time_slots": 8, "flow_count": 160, "deadline_profile": "Balanced", "loads": LOAD_ORDER, "seeds": SEEDS, "schedules": SCHEDULE_ORDER, "matrix": "5 schedules x 2 loads x 10 seeds = 100 rows"},
        "reused_new_provenance": {"new_rows": 80, "reused_rows": 20, "reused_schedule": "HYB-S5", "reuse_source_tag": manifest.get("reused_source_tag"), "reuse_source_commit": manifest.get("reused_source_commit"), "reuse_source_path": manifest.get("input_dirs", {}).get("reused_source")},
        "matrix_completeness": {"complete": validation["pass"], "duplicates": int(df.duplicated(["load_label", "formal_seed", "pricing_schedule"]).sum()), "counts_by_load_schedule": matrix_counts},
        "metric_fields": {"accepted_flows": "accepted_flow_count", "acceptance_rate": "acceptance_rate", "total_runtime": "total_end_to_end_runtime_seconds", "pricing_runtime": "total_pricing_runtime", "pricing_related_fields": ["total_pricing_runtime", "exact_pricing_runtime", "final_rmp_runtime_seconds", "logical_pricing_task_count", "exact_pricing_budget_used", "generated_column_count", "inserted_column_count", "final_column_pool_size", "selective_pfe_call_count", "selective_pfe_runtime_seconds"]},
        "schedule_semantics": {s: {"early_stop_phases": e, "strict_exact_phases": st} for s, (e, st) in SCHEDULE_PHASES.items()},
        "figure_b_tradeoff": {"layout": "1x2 quality-runtime trade-off", "panels": ["(a) Medium Load", "(b) High Load"], "summary_coordinates": {"x": "median total_end_to_end_runtime_seconds / 60", "y": "mean acceptance_rate percent"}, "runtime_axis": "linear minutes", "shared_legend_title": "Pricing Schedule", "marker_color_mapping": marker_mapping, "summary_iqr_errorbars_removed": True, "footer": ["DWCG-SAGINFE-PRO Pricing Schedule with", "S40 (|T|=8, |F|=160, Medium/High Load)"]},
        "statistics": stats.to_dict(orient="records"),
        "paired_comparisons": paired.to_dict(orient="records"),
        "monotonicity": monotonicity(df, stats),
        "scientific_interpretation": "The frozen PRO study tests whether primary pricing schedule quality/runtime trade-offs remain visible inside the full DWCG-SAGINFE-PRO framework. ESV is fastest but lower quality; EX is slowest/highest quality; HYB-S2 and HYB-S5 are close-quality intermediate schedules, with HYB-S5 a compromise and HYB-S7 faster with lower admission.",
        "recommended_figure_set": "Review v2 revises only Figure B by removing redundant summary-point IQR error bars; Figure A remains review_v1.",
        "visual_validation": visual_checks(paths),
        "outputs": [str((TRADE_DIR / f).relative_to(ROOT)) for f in ["pro_pricing_schedule_tradeoff_1x2.png", "pro_pricing_schedule_tradeoff_1x2.pdf", "pro_pricing_schedule_tradeoff_1x2_gray.png", "pro_pricing_schedule_tradeoff_1x2_gray.pdf"]] + [str((AUDIT_DIR / f).relative_to(ROOT)) for f in ["pro_pricing_schedule_seed_summary.csv", "pro_pricing_schedule_paired_comparisons.csv", "pro_pricing_schedule_review_v2_audit.json"]] + [str(Path(__file__).resolve().relative_to(ROOT))],
        "safety": {"no_gurobi": True, "no_experiment_rerun": True, "no_scientific_data_modification": True, "no_frozen_package_modification": True, "no_commit_tag_push": True, "original_v3_root_only": True, "no_new_root_or_worktree": True},
    }
    (AUDIT_DIR / "pro_pricing_schedule_review_v2_audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "output_dir": str(OUTPUT_DIR.relative_to(ROOT)), "source_sha256": source_sha, "outputs": audit["outputs"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
