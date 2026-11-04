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
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import matplotlib as mpl
import matplotlib.lines as mlines
import matplotlib.pyplot as plt
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = Path(__file__).resolve().parent
FROZEN_PACKAGE = ROOT / "results" / "frozen" / "parallel_pricing_v3"
FROZEN_MANIFEST = FROZEN_PACKAGE / "parallel_pricing_final_manifest.json"
SOURCE_CSV = ROOT / "outputs" / "parallel_pricing_v3_60_4796b5b" / "parallel_pricing_seed_level.csv"
SOURCE_JSONL = ROOT / "outputs" / "parallel_pricing_v3_60_4796b5b" / "parallel_pricing_runs.jsonl"
FROZEN_TAG = "parallel-pricing-v3-final"
FROZEN_COMMIT = "45847535a0d98c76b305d724156337ae1215aa14"

ALGORITHMS = ["dwcg_pure", "dwcg_saginfe_pro"]
DISPLAY = {
    "dwcg_pure": "DWCG-Pure",
    "dwcg_saginfe_pro": "DWCG-SAGINFE-PRO",
}
COLOR = {
    "dwcg_pure": "#ff7f0e",
    "dwcg_saginfe_pro": "#d627b9",
}
GRAY = {
    "dwcg_pure": "#666666",
    "dwcg_saginfe_pro": "#000000",
}
MARKER = {
    "dwcg_pure": "s",
    "dwcg_saginfe_pro": "X",
}
MARKER_SIZE = {
    "dwcg_pure": 18,
    "dwcg_saginfe_pro": 21,
}
WORKERS = [1, 2, 4]
WORKER_LABELS = {1: "1", 2: "2", 4: "4"}
X_AXIS_LABEL = "Number of Pricing Workers"
OFFSETS = [-0.12, 0.12]
SEED_JITTER = [-0.026, -0.020, -0.014, -0.008, -0.003, 0.003, 0.008, 0.014, 0.020, 0.026]
SEED_ALPHA = 0.42
TREND_ALPHA = 0.50
TREND_LINEWIDTH = 0.82
BOX_WIDTH = 0.18
PRICING_YLIM = (0.8, 4.2)
PRICING_TICKS = [1, 2, 3, 4]
E2E_YLIM = (0.95, 1.70)
E2E_TICKS = [1.0, 1.2, 1.4, 1.6]
PRICING_RUNTIME_YLIM = (80, 420)
PRICING_RUNTIME_TICKS = [100, 200, 300, 400]
TOTAL_RUNTIME_YLIM = (380, 780)
TOTAL_RUNTIME_TICKS = [400, 500, 600, 700]
EXPECTED_SPEEDUPS = {
    ("dwcg_pure", 2): (1.690, 1.679, 1.258, 1.256),
    ("dwcg_pure", 4): (2.681, 2.627, 1.458, 1.455),
    ("dwcg_saginfe_pro", 2): (1.795, 1.693, 1.277, 1.240),
    ("dwcg_saginfe_pro", 4): (2.832, 2.688, 1.465, 1.427),
}
EXPECTED_RUNTIMES = {
    ("dwcg_pure", 1): (316.476, 316.622, 629.123, 628.968),
    ("dwcg_pure", 2): (187.417, 189.646, 500.276, 502.810),
    ("dwcg_pure", 4): (118.562, 119.910, 431.760, 433.152),
    ("dwcg_saginfe_pro", 1): (331.766, 316.996, 678.101, 662.005),
    ("dwcg_saginfe_pro", 2): (185.355, 186.008, 531.246, 531.183),
    ("dwcg_saginfe_pro", 4): (117.741, 119.172, 463.118, 464.493),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with resolve_input_path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest() -> dict:
    with FROZEN_MANIFEST.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_and_validate() -> tuple[pd.DataFrame, dict]:
    df = read_dataframe(SOURCE_CSV)
    df["formal_seed"] = df["formal_seed"].astype(int)
    df["pricing_worker_count"] = df["pricing_worker_count"].astype(int)
    for col in [
        "accepted_flow_count",
        "acceptance_rate",
        "total_pricing_runtime",
        "total_end_to_end_runtime_seconds",
        "final_rmp_runtime_seconds",
    ]:
        df[col] = df[col].astype(float)

    expected = pd.MultiIndex.from_product(
        [ALGORITHMS, WORKERS, range(10)],
        names=["algorithm", "pricing_worker_count", "formal_seed"],
    )
    actual = pd.MultiIndex.from_frame(df[["algorithm", "pricing_worker_count", "formal_seed"]])
    duplicate = df[df.duplicated(["algorithm", "pricing_worker_count", "formal_seed"], keep=False)]
    missing = expected.difference(actual)
    extra = actual.difference(expected)

    if len(df) != 60:
        raise SystemExit(f"expected 60 rows, found {len(df)}")
    if not duplicate.empty:
        raise SystemExit(f"duplicate cells found: {duplicate[['algorithm', 'pricing_worker_count', 'formal_seed']].to_dict('records')}")
    if len(missing) or len(extra):
        raise SystemExit(f"missing cells={list(missing)} extra cells={list(extra)}")
    if set(df["scale"]) != {"S40"} or set(df["time_slots"].astype(int)) != {8} or set(df["load_label"]) != {"High"}:
        raise SystemExit("scenario fields are not fixed at S40/T=8/High")
    if not ((df["acceptance_rate"] - df["accepted_flow_count"] / 160.0).abs() < 1e-10).all():
        raise SystemExit("acceptance_rate does not match accepted_flow_count / 160")
    if not df[["total_pricing_runtime", "total_end_to_end_runtime_seconds"]].applymap(math.isfinite).all().all():
        raise SystemExit("non-finite runtime values found")
    if not (df[["total_pricing_runtime", "total_end_to_end_runtime_seconds"]] > 0).all().all():
        raise SystemExit("non-positive runtime values found")
    for col in ["path_validity", "capacity_validity", "runtime_accounting_valid"]:
        if not df[col].astype(bool).all():
            raise SystemExit(f"{col} is not true for all rows")
    if df["hard_wall_clock_limit_reached"].astype(bool).any():
        raise SystemExit("hard-wall hit rows found")

    acceptance_bad = []
    pairing_bad = []
    fp_cols = ["topology_fingerprint", "base_flow_fingerprint", "full_instance_fingerprint"]
    for alg in ALGORITHMS:
        for seed in range(10):
            cell = df[(df["algorithm"] == alg) & (df["formal_seed"] == seed)].sort_values("pricing_worker_count")
            if cell["accepted_flow_count"].nunique() != 1:
                acceptance_bad.append({"algorithm": alg, "seed": seed, "values": cell["accepted_flow_count"].tolist()})
            for col in fp_cols:
                if cell[col].nunique() != 1:
                    pairing_bad.append({"algorithm": alg, "seed": seed, "field": col})
    for seed in range(10):
        anchors = df[(df["pricing_worker_count"] == 1) & (df["formal_seed"] == seed)]
        for col in ["scale", "time_slots", "load_label", *fp_cols]:
            if anchors[col].nunique() != 1:
                pairing_bad.append({"algorithm": "cross_algorithm_W1", "seed": seed, "field": col})
    if acceptance_bad:
        raise SystemExit(f"acceptance is not preserved: {acceptance_bad}")
    if pairing_bad:
        raise SystemExit(f"instance pairing failed: {pairing_bad}")

    validation = {
        "row_count": int(len(df)),
        "matrix": "2 algorithms x 3 worker counts x 10 seeds",
        "algorithm_counts": dict(Counter(df["algorithm"])),
        "worker_counts": {str(k): int(v) for k, v in Counter(df["pricing_worker_count"]).items()},
        "duplicates": int(len(duplicate)),
        "missing_cells": int(len(missing)),
        "extra_cells": int(len(extra)),
        "acceptance_preserved_by_algorithm_seed": True,
        "pairing_certified_by_fingerprint": True,
        "nan_or_inf_plotting_values": 0,
    }
    return df, validation


def add_speedups(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for alg in ALGORITHMS:
        for seed in range(10):
            cell = df[(df["algorithm"] == alg) & (df["formal_seed"] == seed)].set_index("pricing_worker_count")
            base_pricing = float(cell.loc[1, "total_pricing_runtime"])
            base_e2e = float(cell.loc[1, "total_end_to_end_runtime_seconds"])
            base_key = f"{alg}|seed={seed}|W1"
            for worker in WORKERS:
                row = cell.loc[worker].to_dict()
                row["pricing_worker_count"] = worker
                row["formal_seed"] = seed
                row["algorithm"] = alg
                row["baseline_key"] = base_key
                row["baseline_pricing_runtime"] = base_pricing
                row["baseline_total_runtime"] = base_e2e
                row["pricing_speedup"] = base_pricing / float(row["total_pricing_runtime"])
                row["e2e_speedup"] = base_e2e / float(row["total_end_to_end_runtime_seconds"])
                rows.append(row)
    plotted = pd.DataFrame(rows)
    if not ((plotted[plotted["pricing_worker_count"] == 1]["pricing_speedup"] - 1.0).abs() < 1e-12).all():
        raise SystemExit("W1 pricing speedup is not exactly 1")
    if not ((plotted[plotted["pricing_worker_count"] == 1]["e2e_speedup"] - 1.0).abs() < 1e-12).all():
        raise SystemExit("W1 E2E speedup is not exactly 1")
    if not plotted[["pricing_speedup", "e2e_speedup"]].applymap(math.isfinite).all().all():
        raise SystemExit("non-finite speedup values found")
    return plotted


def summarize_speedups(plotted: pd.DataFrame) -> list[dict]:
    rows = []
    for alg in ALGORITHMS:
        for worker in WORKERS:
            cell = plotted[(plotted["algorithm"] == alg) & (plotted["pricing_worker_count"] == worker)].sort_values("formal_seed")
            rows.append({
                "algorithm": alg,
                "display": DISPLAY[alg],
                "worker": worker,
                "pricing_values": "|".join(f"{v:.6f}" for v in cell["pricing_speedup"]),
                "pricing_mean": float(cell["pricing_speedup"].mean()),
                "pricing_median": float(cell["pricing_speedup"].median()),
                "pricing_min": float(cell["pricing_speedup"].min()),
                "pricing_max": float(cell["pricing_speedup"].max()),
                "e2e_values": "|".join(f"{v:.6f}" for v in cell["e2e_speedup"]),
                "e2e_mean": float(cell["e2e_speedup"].mean()),
                "e2e_median": float(cell["e2e_speedup"].median()),
                "e2e_min": float(cell["e2e_speedup"].min()),
                "e2e_max": float(cell["e2e_speedup"].max()),
            })
    for row in rows:
        if row["worker"] == 1:
            continue
        expected = EXPECTED_SPEEDUPS[(row["algorithm"], row["worker"])]
        got = (
            round(row["pricing_mean"], 3),
            round(row["pricing_median"], 3),
            round(row["e2e_mean"], 3),
            round(row["e2e_median"], 3),
        )
        if got != expected:
            raise SystemExit(f"speedup summary mismatch for {(row['algorithm'], row['worker'])}: got {got}, expected {expected}")
    return rows


def summarize_runtimes(plotted: pd.DataFrame) -> list[dict]:
    rows = []
    for alg in ALGORITHMS:
        for worker in WORKERS:
            cell = plotted[(plotted["algorithm"] == alg) & (plotted["pricing_worker_count"] == worker)].sort_values("formal_seed")
            rows.append({
                "algorithm": alg,
                "display": DISPLAY[alg],
                "worker": worker,
                "pricing_runtime_values": "|".join(f"{v:.6f}" for v in cell["total_pricing_runtime"]),
                "pricing_runtime_mean": float(cell["total_pricing_runtime"].mean()),
                "pricing_runtime_median": float(cell["total_pricing_runtime"].median()),
                "pricing_runtime_min": float(cell["total_pricing_runtime"].min()),
                "pricing_runtime_max": float(cell["total_pricing_runtime"].max()),
                "total_runtime_values": "|".join(f"{v:.6f}" for v in cell["total_end_to_end_runtime_seconds"]),
                "total_runtime_mean": float(cell["total_end_to_end_runtime_seconds"].mean()),
                "total_runtime_median": float(cell["total_end_to_end_runtime_seconds"].median()),
                "total_runtime_min": float(cell["total_end_to_end_runtime_seconds"].min()),
                "total_runtime_max": float(cell["total_end_to_end_runtime_seconds"].max()),
            })
    for row in rows:
        expected = EXPECTED_RUNTIMES[(row["algorithm"], row["worker"])]
        got = (
            round(row["pricing_runtime_mean"], 3),
            round(row["pricing_runtime_median"], 3),
            round(row["total_runtime_mean"], 3),
            round(row["total_runtime_median"], 3),
        )
        if got != expected:
            raise SystemExit(f"runtime summary mismatch for {(row['algorithm'], row['worker'])}: got {got}, expected {expected}")
    return rows


def style(grayscale: bool) -> dict:
    colors = GRAY if grayscale else COLOR
    return {alg: {"color": colors[alg], "marker": MARKER[alg]} for alg in ALGORITHMS}


def draw_panel(ax, plotted: pd.DataFrame, metric: str, grayscale: bool, ideal: bool = False) -> None:
    styles = style(grayscale)
    if ideal:
        ax.plot(
            range(len(WORKERS)),
            WORKERS,
            color="#505050",
            linestyle=(0, (3.0, 2.2)),
            linewidth=0.75,
            alpha=0.72,
            zorder=1.8,
        )
    for worker_index, worker in enumerate(WORKERS):
        center = float(worker_index)
        for alg_index, alg in enumerate(ALGORITHMS):
            pos = center + OFFSETS[alg_index]
            cell = plotted[(plotted["pricing_worker_count"] == worker) & (plotted["algorithm"] == alg)].sort_values("formal_seed")
            values = cell[metric].to_numpy()
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
            x_values = [pos + SEED_JITTER[int(seed)] for seed in cell["formal_seed"]]
            ax.scatter(
                x_values,
                values,
                s=MARKER_SIZE[alg],
                marker=styles[alg]["marker"],
                facecolor=color,
                edgecolor="white" if not grayscale else "#F2F2F2",
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
        xs = [float(i) + OFFSETS[alg_index] for i in range(len(WORKERS))]
        ys = []
        for worker in WORKERS:
            cell = plotted[(plotted["pricing_worker_count"] == worker) & (plotted["algorithm"] == alg)]
            ys.append(float(cell[metric].mean()))
        ax.plot(xs, ys, color=styles[alg]["color"], linewidth=TREND_LINEWIDTH, alpha=TREND_ALPHA, zorder=2)
    ax.set_xticks(range(len(WORKERS)))
    ax.set_xticklabels([WORKER_LABELS[w] for w in WORKERS])
    ax.set_xlabel(X_AXIS_LABEL)
    ax.set_xlim(-0.48, len(WORKERS) - 1 + 0.48)
    ax.grid(axis="y", color="#D8D8D8", linewidth=0.5, alpha=0.78, zorder=1)
    for spine in ax.spines.values():
        spine.set_linewidth(0.8)
        spine.set_color("#303030")


def make_speedup_figure(plotted: pd.DataFrame, grayscale: bool) -> plt.Figure:
    mpl.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 8.8,
        "axes.labelsize": 9.2,
        "xtick.labelsize": 8.5,
        "ytick.labelsize": 8.5,
        "legend.fontsize": 8.6,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })
    fig, axes = plt.subplots(1, 2, figsize=(7.25, 3.55), dpi=300)
    fig.subplots_adjust(left=0.075, right=0.988, bottom=0.335, top=0.820, wspace=0.260)

    draw_panel(axes[0], plotted, "pricing_speedup", grayscale, ideal=True)
    axes[0].set_ylabel("Pricing Speedup")
    axes[0].set_ylim(*PRICING_YLIM)
    axes[0].set_yticks(PRICING_TICKS)

    draw_panel(axes[1], plotted, "e2e_speedup", grayscale, ideal=False)
    axes[1].set_ylabel("End-to-End Speedup")
    axes[1].set_ylim(*E2E_YLIM)
    axes[1].set_yticks(E2E_TICKS)

    styles = style(grayscale)
    handles = [
        mlines.Line2D([], [], color=styles[alg]["color"], marker=MARKER[alg], linestyle="-", linewidth=1.2, markersize=5.4, label=DISPLAY[alg])
        for alg in ALGORITHMS
    ]
    handles.append(
        mlines.Line2D([], [], color="#505050", linestyle=(0, (3.0, 2.2)), linewidth=0.75, label="Ideal linear speedup")
    )
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.992), ncol=3, frameon=False, columnspacing=1.7, handlelength=1.8)

    for ax, label in zip(axes, ["(a) Pricing Speedup", "(b) End-to-End Speedup"]):
        bbox = ax.get_position()
        fig.text((bbox.x0 + bbox.x1) / 2, bbox.y0 - 0.140, label, ha="center", va="top", fontsize=9.2)
    fig.text(0.5, 0.076, "Parallel Pricing with", ha="center", fontsize=8.8)
    fig.text(0.5, 0.038, "S40 (|T|=8, |F|=160, High Load)", ha="center", fontsize=8.8)
    return fig


def make_runtime_figure(plotted: pd.DataFrame, grayscale: bool) -> plt.Figure:
    mpl.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 8.8,
        "axes.labelsize": 9.2,
        "xtick.labelsize": 8.5,
        "ytick.labelsize": 8.5,
        "legend.fontsize": 8.6,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })
    fig, axes = plt.subplots(1, 2, figsize=(7.25, 3.55), dpi=300)
    fig.subplots_adjust(left=0.075, right=0.988, bottom=0.335, top=0.820, wspace=0.260)

    draw_panel(axes[0], plotted, "total_pricing_runtime", grayscale, ideal=False)
    axes[0].set_ylabel("Pricing Runtime (s)")
    axes[0].set_ylim(*PRICING_RUNTIME_YLIM)
    axes[0].set_yticks(PRICING_RUNTIME_TICKS)

    draw_panel(axes[1], plotted, "total_end_to_end_runtime_seconds", grayscale, ideal=False)
    axes[1].set_ylabel("Total Runtime (s)")
    axes[1].set_ylim(*TOTAL_RUNTIME_YLIM)
    axes[1].set_yticks(TOTAL_RUNTIME_TICKS)

    styles = style(grayscale)
    handles = [
        mlines.Line2D([], [], color=styles[alg]["color"], marker=MARKER[alg], linestyle="-", linewidth=1.2, markersize=5.4, label=DISPLAY[alg])
        for alg in ALGORITHMS
    ]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.992), ncol=2, frameon=False, columnspacing=1.7, handlelength=1.8)

    for ax, label in zip(axes, ["(a) Pricing Runtime", "(b) Total Runtime"]):
        bbox = ax.get_position()
        fig.text((bbox.x0 + bbox.x1) / 2, bbox.y0 - 0.140, label, ha="center", va="top", fontsize=9.2)
    fig.text(0.5, 0.076, "Parallel Pricing with", ha="center", fontsize=8.8)
    fig.text(0.5, 0.038, "S40 (|T|=8, |F|=160, High Load)", ha="center", fontsize=8.8)
    return fig


def write_plotted_rows(plotted: pd.DataFrame) -> None:
    order = {alg: i for i, alg in enumerate(ALGORITHMS)}
    out = plotted.copy()
    out["_algorithm_order"] = out["algorithm"].map(order)
    out = out.sort_values(["_algorithm_order", "formal_seed", "pricing_worker_count"])
    columns = [
        "algorithm",
        "formal_seed",
        "pricing_worker_count",
        "variant_id",
        "accepted_flow_count",
        "acceptance_rate",
        "total_pricing_runtime",
        "total_end_to_end_runtime_seconds",
        "pricing_speedup",
        "e2e_speedup",
        "baseline_key",
        "baseline_pricing_runtime",
        "baseline_total_runtime",
        "scale",
        "time_slots",
        "load_label",
        "pricing_strategy",
        "parallel_pricing_enabled",
        "topology_fingerprint",
        "base_flow_fingerprint",
        "full_instance_fingerprint",
    ]
    out[columns].to_csv(OUTPUT_DIR / "PLOTTED_ROWS.csv", index=False)


def write_source_hashes() -> dict:
    hashes = {
        "parallel_pricing_seed_level_csv": {
            "path": SOURCE_CSV.relative_to(ROOT).as_posix(),
            "sha256": sha256(SOURCE_CSV),
        },
        "parallel_pricing_runs_jsonl": {
            "path": SOURCE_JSONL.relative_to(ROOT).as_posix(),
            "sha256": sha256(SOURCE_JSONL),
        },
        "parallel_pricing_frozen_manifest": {
            "path": FROZEN_MANIFEST.relative_to(ROOT).as_posix(),
            "sha256": sha256(FROZEN_MANIFEST),
        },
    }
    lines = [f"{item['sha256']}  {item['path']}" for item in hashes.values()]
    (OUTPUT_DIR / "SOURCE_HASHES.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return hashes


def write_audit_and_manifest(
    source_df: pd.DataFrame,
    plotted: pd.DataFrame,
    validation: dict,
    summary: list[dict],
    outputs: list[str],
    source_hashes: dict,
) -> None:
    manifest = load_manifest()
    audit = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "review_only": True,
        "frozen_scientific_package": FROZEN_PACKAGE.relative_to(ROOT).as_posix(),
        "scientific_tag": FROZEN_TAG,
        "scientific_freeze_commit": FROZEN_COMMIT,
        "source_csv": SOURCE_CSV.relative_to(ROOT).as_posix(),
        "source_jsonl": SOURCE_JSONL.relative_to(ROOT).as_posix(),
        "source_hashes": source_hashes,
        "scenario": {
            "scale": "S40",
            "time_slots": 8,
            "total_flow_count": 160,
            "load": "High",
            "deadline_profile": "Balanced",
            "algorithms": ALGORITHMS,
            "workers": WORKERS,
            "seeds": list(range(10)),
        },
        "validation": validation,
        "plotted_row_count": int(len(plotted)),
        "acceptance_preservation": "accepted_flow_count identical across W1/W2/W4 for every algorithm/seed",
        "speedup_definition": {
            "pricing_speedup": "paired seed-level total_pricing_runtime(W1) / total_pricing_runtime(W)",
            "e2e_speedup": "paired seed-level total_end_to_end_runtime_seconds(W1) / total_end_to_end_runtime_seconds(W)",
            "ratio_of_means_used": False,
        },
        "axis_limits": {
            "pricing_speedup": list(PRICING_YLIM),
            "end_to_end_speedup": list(E2E_YLIM),
            "pricing_runtime_seconds": list(PRICING_RUNTIME_YLIM),
            "total_runtime_seconds": list(TOTAL_RUNTIME_YLIM),
        },
        "x_axis": {
            "tick_labels": [WORKER_LABELS[w] for w in WORKERS],
            "label": X_AXIS_LABEL,
            "worker_counts": WORKERS,
        },
        "ideal_reference": "thin neutral dashed line at speedup = worker count in pricing panel only",
        "style": {
            "layout": "1x2",
            "seed_alpha": SEED_ALPHA,
            "mean_zorder": 9,
            "median_zorder": 8,
            "seed_zorder": 5,
            "colors": COLOR,
            "grayscale_colors": GRAY,
            "markers": MARKER,
        },
        "summary": summary,
        "runtime_summary": summarize_runtimes(plotted),
        "runtime_decomposition_note": {
            "final_restricted_rmp_median_seconds": "approximately 302 s across worker counts",
            "non_pricing_fraction_pure_W1_W2_W4": [0.497, 0.626, 0.726],
            "non_pricing_fraction_pro_W1_W2_W4": [0.512, 0.651, 0.746],
            "note": "serial/non-pricing work limits end-to-end speedup; no formal Amdahl-law fit is claimed",
        },
        "frozen_manifest_reference": {
            "formal_source_commit": manifest.get("formal_source_commit"),
            "plan_fingerprint": manifest.get("plan_fingerprint"),
        },
        "outputs": outputs,
        "no_solver": True,
        "no_experiment_rerun": True,
        "frozen_package_modified": False,
    }
    (OUTPUT_DIR / "figure_manifest.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# Parallel Pricing V3 Speedup Figure Audit",
        "",
        f"Frozen scientific package: `{FROZEN_PACKAGE.relative_to(ROOT).as_posix()}`",
        f"Scientific tag: `{FROZEN_TAG}`",
        f"Scientific freeze commit: `{FROZEN_COMMIT}`",
        f"Primary plotted source: `{SOURCE_CSV.relative_to(ROOT).as_posix()}`",
        f"Matrix validation: PASS, {len(source_df)} source rows, {len(plotted)} plotted rows",
        "Expected matrix: 2 algorithms x 3 worker counts x 10 seeds = 60 rows",
        "Acceptance preservation: PASS, W1/W2/W4 accepted-flow counts match exactly for every algorithm/seed.",
        "Speedups: computed paired seed-by-seed from W1 baselines; no ratio of means used.",
        f"Pricing-panel y-limits: {PRICING_YLIM}",
        f"E2E-panel y-limits: {E2E_YLIM}",
        f"Pricing-runtime panel y-limits: {PRICING_RUNTIME_YLIM}",
        f"Total-runtime panel y-limits: {TOTAL_RUNTIME_YLIM}",
        f"X-axis tick labels: {', '.join(WORKER_LABELS[w] for w in WORKERS)}",
        f"X-axis label: {X_AXIS_LABEL}",
        "Ideal reference: thin neutral dashed ideal-linear-speedup line, pricing panel only.",
        "Runtime companion: actual measured pricing and end-to-end wall-clock seconds on linear y-axes.",
        "Seed visualization: deterministic jitter, light alpha points, all 10 paired seeds per cell.",
        "Mean/median visualization: red dotted mean line above black median line above seed points.",
        "Runtime decomposition note from prior audit: final restricted-RMP median runtime remains approximately 302 s across worker counts; non-pricing fraction increases Pure 0.497/0.626/0.726 and PRO 0.512/0.651/0.746.",
        "",
        "Generated files:",
        *[f"- `{name}`" for name in outputs],
        "",
        "Speedup summary:",
    ]
    for row in summary:
        lines.append(
            f"- {row['display']} {WORKER_LABELS[row['worker']]}: pricing mean/median "
            f"{row['pricing_mean']:.3f}/{row['pricing_median']:.3f}; E2E mean/median "
            f"{row['e2e_mean']:.3f}/{row['e2e_median']:.3f}"
        )
    lines.append("")
    lines.append("Runtime summary:")
    for row in summarize_runtimes(plotted):
        lines.append(
            f"- {row['display']} {WORKER_LABELS[row['worker']]}: pricing runtime mean/median "
            f"{row['pricing_runtime_mean']:.3f}/{row['pricing_runtime_median']:.3f} s; total runtime mean/median "
            f"{row['total_runtime_mean']:.3f}/{row['total_runtime_median']:.3f} s"
        )
    lines.extend([
        "",
        f"Plotting script used: `{(OUTPUT_DIR / 'generate_parallel_pricing_speedup_review.py').relative_to(ROOT).as_posix()}`",
        "No solver / no experiment rerun: confirmed",
        "Frozen scientific package modified: no",
    ])
    (OUTPUT_DIR / "FIGURE_AUDIT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_written(outputs: list[str]) -> None:
    for name in outputs:
        path = OUTPUT_DIR / name
        if not path.exists() or path.stat().st_size <= 0:
            raise SystemExit(f"missing or empty output: {name}")
    with (OUTPUT_DIR / "PLOTTED_ROWS.csv").open(encoding="utf-8", newline="") as handle:
        if sum(1 for _ in csv.DictReader(handle)) != 60:
            raise SystemExit("PLOTTED_ROWS.csv does not contain 60 rows")


def main() -> None:
    source_df, validation = load_and_validate()
    plotted = add_speedups(source_df)
    summary = summarize_speedups(plotted)
    runtime_summary = summarize_runtimes(plotted)
    write_plotted_rows(plotted)
    source_hashes = write_source_hashes()

    outputs = []
    for grayscale, stem in [(False, "parallel_pricing_speedup_1x2"), (True, "parallel_pricing_speedup_1x2_gray")]:
        fig = make_speedup_figure(plotted, grayscale)
        for suffix in ["png", "pdf"]:
            filename = f"{stem}.{suffix}"
            fig.savefig(OUTPUT_DIR / filename, dpi=600 if suffix == "png" else None, bbox_inches="tight", pad_inches=0.018)
            outputs.append(filename)
        plt.close(fig)
    for grayscale, stem in [(False, "parallel_pricing_runtime_1x2"), (True, "parallel_pricing_runtime_1x2_gray")]:
        fig = make_runtime_figure(plotted, grayscale)
        for suffix in ["png", "pdf"]:
            filename = f"{stem}.{suffix}"
            fig.savefig(OUTPUT_DIR / filename, dpi=600 if suffix == "png" else None, bbox_inches="tight", pad_inches=0.018)
            outputs.append(filename)
        plt.close(fig)
    support_files = ["PLOTTED_ROWS.csv", "FIGURE_AUDIT.md", "figure_manifest.json", "SOURCE_HASHES.sha256"]
    write_audit_and_manifest(source_df, plotted, validation, summary, outputs + support_files, source_hashes)
    validate_written(outputs + support_files)
    print(json.dumps({
        "status": "ok",
        "output_dir": OUTPUT_DIR.relative_to(ROOT).as_posix(),
        "outputs": outputs + support_files,
        "pricing_y_limits": PRICING_YLIM,
        "e2e_y_limits": E2E_YLIM,
        "pricing_runtime_y_limits": PRICING_RUNTIME_YLIM,
        "total_runtime_y_limits": TOTAL_RUNTIME_YLIM,
        "summary": summary,
        "runtime_summary": runtime_summary,
    }, indent=2))


if __name__ == "__main__":
    main()
