from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from algorithm_names import ALGORITHM_NAMES, SHORT_NAMES
from record_compatibility import normalize_record, normalize_dataframe, resolve_input_path


import csv
import math
import statistics
import sys
from dataclasses import replace
from pathlib import Path

import matplotlib as mpl
import matplotlib.lines as mlines
import matplotlib.pyplot as plt
import numpy as np


ALT = Path(__file__).resolve().parent
sys.path.insert(0, str(ALT))

from generate_revised_grouped_candidates import (  # noqa: E402
    DIRECT_ORIGINAL,
    GROUPS,
    LOW_GROUPS,
    NON_ILP,
    ROOT,
    S20_MED_SUPP,
    S30_MED_SUPP,
    Row,
    deterministic_jitter,
    exact_direct_ok,
    format_seconds,
    read_jsonl,
    row_from_record,
    setup_style,
)


ACTIVE = Path(__file__).resolve().parent

ILP_FULL_S20_LOW_SEED0 = ROOT / "formal_outputs" / "direct_ilp_full_objective_cold_s20_low_seed0_v3_928be83" / "runs.jsonl"
ILP_FULL_S20_LOW_SEEDS1_9 = ROOT / "formal_outputs" / "direct_ilp_full_objective_cold_s20_low_seeds1_9_v3_a3c8256" / "runs.jsonl"
ILP_FULL_S30_LOW_SEED0 = ROOT / "formal_outputs" / "direct_ilp_full_objective_cold_s30_low_seed0_v3_15b4b8e" / "runs.jsonl"
ILP_FULL_S30_LOW_SEEDS1_9 = ROOT / "formal_outputs" / "direct_ilp_full_objective_cold_s30_low_seeds1_9_v3_06533bb" / "runs.jsonl"
ILP_FULL_MEDIUM_INTERRUPTED = ROOT / "formal_outputs" / "direct_ilp_full_objective_cold_s20_medium_seed0_v3_23d6bf0" / "runs.jsonl"

ALG_ADMISSION = "ilp_admission_only"
ALG_FULL = "ilp_full_objective"
NON_ILP_ALGS = ["saginfe", "dwcg_pure", "dwcg_saginfe", "dwcg_saginfe_pro"]
ACCEPTANCE_LOW_ALGS = NON_ILP_ALGS + [ALG_ADMISSION, ALG_FULL]
ACCEPTANCE_MEDIUM_ALGS = NON_ILP_ALGS + [ALG_ADMISSION]
RUNTIME_LOW_ALGS = NON_ILP_ALGS + [ALG_ADMISSION, ALG_FULL]
RUNTIME_MEDIUM_ALGS = NON_ILP_ALGS + [ALG_ADMISSION]
COST_ALL5_ALGS = NON_ILP_ALGS + [ALG_ADMISSION, ALG_FULL]
COST_NO_PURE_ALGS = ["saginfe", "dwcg_saginfe", "dwcg_saginfe_pro", ALG_ADMISSION, ALG_FULL]

LABEL = {
    "saginfe": "SAGINFE",
    "dwcg_pure": "DWCG-Pure",
    "dwcg_saginfe": "DWCG-SAGINFE",
    "dwcg_saginfe_pro": "DWCG-SAGINFE-PRO",
    ALG_ADMISSION: "ILP-AdmissionOnly",
    ALG_FULL: "ILP-FullObjective",
}

STYLE = {
    "saginfe": {"color": "#0072B2", "marker": "o"},
    "dwcg_pure": {"color": "#D55E00", "marker": "s"},
    "dwcg_saginfe": {"color": "#009E73", "marker": "^"},
    "dwcg_saginfe_pro": {"color": "#C51B7D", "marker": "X"},
    ALG_ADMISSION: {"color": "#5E2A8A", "marker": "D"},
    ALG_FULL: {"color": "#9C4D97", "marker": "P"},
}

GRAY = {
    "saginfe": "#222222",
    "dwcg_pure": "#555555",
    "dwcg_saginfe": "#777777",
    "dwcg_saginfe_pro": "#000000",
    ALG_ADMISSION: "#999999",
    ALG_FULL: "#C8C8C8",
}

MEAN_COLOR = "#8B1A1A"
MEAN_COLOR_GRAY = "#444444"
MEAN_LINESTYLE = ":"

COST_ELIGIBILITY_ALL5_SPLIT = {
    ("S20", "Low"): [0, 1, 2, 5, 7, 8, 9],
    ("S30", "Low"): [0, 2, 3, 4, 5, 6, 9],
}
COST_ELIGIBILITY_NO_PURE_SPLIT = {
    ("S20", "Low"): list(range(10)),
    ("S30", "Low"): list(range(10)),
}


def alg_color(alg: str, gray: bool) -> str:
    return GRAY[alg] if gray else STYLE[alg]["color"]


def relabel(row: Row, algorithm_id: str, source: str) -> Row:
    return replace(row, algorithm_id=algorithm_id, source=source)


def full_objective_ok(rec: dict) -> bool:
    meta = rec.get("direct_ilp_metadata") or {}
    return (
        rec.get("termination_status") == "OPTIMAL"
        and rec.get("objective_globally_optimal") is True
        and rec.get("path_validity") is True
        and rec.get("capacity_validity") is True
        and meta.get("objective_mode") == "scalarized"
        and meta.get("physical_cost_in_objective") is True
        and meta.get("global_full_objective_optimal") is True
        and meta.get("configured_gurobi_seed") == 0
        and float(meta.get("configured_mip_gap", 1.0)) == 0.0
        and float(meta.get("configured_mip_gap_abs", 1.0)) == 0.0
    )


def load_split_rows() -> list[Row]:
    rows: list[Row] = []

    for rec in read_jsonl(NON_ILP):
        k = rec["key"]
        if (k["scale"], k["load_label"]) in GROUPS and k["algorithm"] in NON_ILP_ALGS:
            if rec.get("path_validity") is True and rec.get("capacity_validity") is True:
                rows.append(row_from_record(rec, str(NON_ILP.relative_to(ROOT))))

    direct_original = read_jsonl(DIRECT_ORIGINAL)
    direct_s20_med = read_jsonl(S20_MED_SUPP)
    direct_s30_med = read_jsonl(S30_MED_SUPP / "pilot4" / "runs.jsonl") + read_jsonl(S30_MED_SUPP / "remaining6" / "runs.jsonl")
    direct_refs: dict[tuple[str, str, int], dict] = {}
    for rec in direct_original + direct_s20_med + direct_s30_med:
        k = rec["key"]
        if k["algorithm"] != "direct_ilp_acceptance_cold" or (k["scale"], k["load_label"]) not in GROUPS:
            continue
        if not exact_direct_ok(rec):
            continue
        key = (k["scale"], k["load_label"], int(k["formal_seed"]))
        if key not in direct_refs or k["load_label"] == "Medium":
            direct_refs[key] = rec
    for key in sorted(direct_refs):
        rows.append(relabel(row_from_record(direct_refs[key], "ILP-AdmissionOnly historical exact-admission source"), ALG_ADMISSION, "ILP-AdmissionOnly historical exact-admission source"))

    for path in [ILP_FULL_S20_LOW_SEED0, ILP_FULL_S20_LOW_SEEDS1_9, ILP_FULL_S30_LOW_SEED0, ILP_FULL_S30_LOW_SEEDS1_9]:
        for rec in read_jsonl(path):
            k = rec["key"]
            if (k["scale"], k["load_label"]) not in LOW_GROUPS:
                raise SystemExit(f"Full-objective ILP row outside Low groups: {path} {k}")
            if not full_objective_ok(rec):
                raise SystemExit(f"Full-objective ILP row is not exact/scalarized/valid: {path} {k}")
            rows.append(relabel(row_from_record(rec, str(path.relative_to(ROOT))), ALG_FULL, str(path.relative_to(ROOT))))

    validate_split_rows(rows)
    return rows


def by_key(rows: list[Row]) -> dict[tuple[str, str, int, str], Row]:
    return {(r.scale, r.load, r.seed, r.algorithm_id): r for r in rows}


def validate_split_rows(rows: list[Row]) -> None:
    by = by_key(rows)
    for group in GROUPS:
        for seed in range(10):
            ref = by.get((group[0], group[1], seed, ALG_ADMISSION))
            if ref is None:
                raise SystemExit(f"missing ILP-AdmissionOnly exact reference: {group} seed {seed}")
            for alg in NON_ILP_ALGS + [ALG_ADMISSION]:
                row = by.get((group[0], group[1], seed, alg))
                if row is None:
                    raise SystemExit(f"missing row: {group} seed {seed} {alg}")
                if (row.T, row.F, row.demand_multiplier) != (ref.T, ref.F, ref.demand_multiplier):
                    raise SystemExit(f"metadata mismatch: {group} seed {seed} {alg}")
                if (row.topology, row.base_flow, row.full_instance) != (ref.topology, ref.base_flow, ref.full_instance):
                    raise SystemExit(f"fingerprint mismatch: {group} seed {seed} {alg}")
                if not (row.path_valid and row.capacity_valid):
                    raise SystemExit(f"invalid path/capacity: {group} seed {seed} {alg}")
            full = by.get((group[0], group[1], seed, ALG_FULL))
            if group[1] == "Medium" and full is not None:
                raise SystemExit(f"ILP-FullObjective must not appear for Medium: {group} seed {seed}")
            if group in LOW_GROUPS:
                if full is None:
                    raise SystemExit(f"missing low-load ILP-FullObjective row: {group} seed {seed}")
                if (full.T, full.F, full.demand_multiplier) != (ref.T, ref.F, ref.demand_multiplier):
                    raise SystemExit(f"full-objective metadata mismatch: {group} seed {seed}")
                if (full.topology, full.base_flow, full.full_instance) != (ref.topology, ref.base_flow, ref.full_instance):
                    raise SystemExit(f"full-objective fingerprint mismatch: {group} seed {seed}")


def offsets(algs: list[str]) -> np.ndarray:
    width = 0.18 if len(algs) == 5 else 0.21
    return (np.arange(len(algs)) - (len(algs) - 1) / 2) * width


def box_width(algs: list[str]) -> float:
    return 0.082 if len(algs) == 5 else 0.105


def scale_label(scale: str, rows: list[Row]) -> str:
    sample = next(r for r in rows if r.scale == scale)
    return f"{scale}\n(|T|={sample.T}, |F|={sample.F})"


def scale_cost_label(group: tuple[str, str], rows: list[Row], n: int) -> str:
    sample = next(r for r in rows if (r.scale, r.load) == group)
    return f"{group[0]} {group[1]}\n(|T|={sample.T}, |F|={sample.F}; {n} Seeds)"


def add_shared_legend(fig, algs: list[str], gray: bool) -> None:
    def handles_for(legend_algs: list[str]) -> list[mlines.Line2D]:
        return [
            mlines.Line2D(
                [],
                [],
                color=alg_color(alg, gray),
                marker=STYLE[alg]["marker"],
                linestyle="None",
                markersize=5.3,
                label=LABEL[alg],
            )
            for alg in legend_algs
        ]

    if algs == ACCEPTANCE_LOW_ALGS or algs == COST_ALL5_ALGS or algs == RUNTIME_LOW_ALGS:
        row1 = ["saginfe", "dwcg_pure", "dwcg_saginfe", "dwcg_saginfe_pro"]
        row2 = [ALG_ADMISSION, ALG_FULL]
        leg1 = fig.legend(
            handles=handles_for(row1),
            loc="upper center",
            bbox_to_anchor=(0.5, 1.0),
            ncol=4,
            frameon=False,
            handletextpad=0.3,
            columnspacing=0.85,
        )
        fig.add_artist(leg1)
        fig.legend(
            handles=handles_for(row2),
            loc="upper center",
            bbox_to_anchor=(0.5, 0.935),
            ncol=2,
            frameon=False,
            handletextpad=0.3,
            columnspacing=0.85,
        )
        return

    handles = [
        mlines.Line2D(
            [],
            [],
            color=alg_color(alg, gray),
            marker=STYLE[alg]["marker"],
            linestyle="None",
            markersize=5.3,
            label=LABEL[alg],
        )
        for alg in algs
    ]
    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.0),
        ncol=len(algs),
        frameon=False,
        handletextpad=0.3,
        columnspacing=0.85,
    )


def human_time_tick(x, _pos=None) -> str:
    return {
        1: "1 s",
        10: "10 s",
        60: "1 min",
        600: "10 min",
        3600: "1 h",
        36000: "10 h",
        86400: "24 h",
    }.get(int(round(x)), "")


def draw_grouped_boxes(
    ax,
    rows: list[Row],
    groups: list[tuple[str, str]],
    algs: list[str],
    metric: str,
    gray: bool,
    percent: bool = False,
    eligible_seeds: dict[tuple[str, str], list[int]] | None = None,
) -> list[float]:
    centers = np.arange(len(groups), dtype=float) * 1.35
    offs = offsets(algs)
    data: list[list[float]] = []
    positions: list[float] = []
    box_algs: list[str] = []
    all_values: list[float] = []
    rows_by = by_key(rows)
    for gi, group in enumerate(groups):
        seeds = eligible_seeds[group] if eligible_seeds else list(range(10))
        for ai, alg in enumerate(algs):
            vals = []
            for seed in seeds:
                row = rows_by[(group[0], group[1], seed, alg)]
                val = getattr(row, metric)
                if percent:
                    val *= 100.0
                vals.append(val)
            data.append(vals)
            all_values.extend(vals)
            positions.append(centers[gi] + offs[ai])
            box_algs.append(alg)

    bp = ax.boxplot(data, positions=positions, widths=box_width(algs), patch_artist=True, showfliers=False)
    for patch, alg in zip(bp["boxes"], box_algs):
        patch.set_facecolor(alg_color(alg, gray))
        patch.set_edgecolor(alg_color(alg, gray))
        patch.set_alpha(0.22)
        patch.set_linewidth(0.95)
        patch.set_zorder(2)
    for part in ["whiskers", "caps"]:
        for item in bp[part]:
            item.set_color("#555555")
            item.set_linewidth(0.75)
            item.set_zorder(3)
    for item in bp["medians"]:
        item.set_color("#111111")
        item.set_linewidth(1.3)
        item.set_zorder(8)

    for gi, group in enumerate(groups):
        seeds = eligible_seeds[group] if eligible_seeds else list(range(10))
        for ai, alg in enumerate(algs):
            xs = []
            ys = []
            for seed in seeds:
                row = rows_by[(group[0], group[1], seed, alg)]
                val = getattr(row, metric)
                if percent:
                    val *= 100.0
                xs.append(centers[gi] + offs[ai] + deterministic_jitter(seed, ai, 0.022))
                ys.append(val)
            ax.scatter(
                xs,
                ys,
                s=23,
                marker=STYLE[alg]["marker"],
                color=alg_color(alg, gray),
                edgecolors="white",
                linewidths=0.35,
                zorder=4,
            )
            mean_x = centers[gi] + offs[ai]
            mean_y = statistics.fmean(ys)
            half_width = box_width(algs) * 0.42
            ax.plot(
                [mean_x - half_width, mean_x + half_width],
                [mean_y, mean_y],
                color=MEAN_COLOR_GRAY if gray else MEAN_COLOR,
                linewidth=1.35,
                linestyle=MEAN_LINESTYLE,
                solid_capstyle="round",
                zorder=9,
            )
    ax.set_xticks(centers)
    ax.set_xlim(centers[0] - 0.72, centers[-1] + 0.72)
    ax.grid(axis="y", color="#D8D8D8", linewidth=0.5, alpha=0.8)
    return all_values


def save(fig, stem: str, gray: bool) -> None:
    ACTIVE.mkdir(parents=True, exist_ok=True)
    name = stem + ("_grayscale" if gray else "")
    fig.savefig((ACTIVE / name).with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig((ACTIVE / name).with_suffix(".png"), dpi=300, bbox_inches="tight")
    plt.close(fig)


def acceptance(rows: list[Row], gray: bool) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.15, 3.58), sharey=True)
    all_values: list[float] = []
    all_values.extend(draw_grouped_boxes(axes[0], rows, [("S20", "Low"), ("S30", "Low")], ACCEPTANCE_LOW_ALGS, "acceptance_rate", gray=gray, percent=True))
    all_values.extend(draw_grouped_boxes(axes[1], rows, [("S20", "Medium"), ("S30", "Medium")], ACCEPTANCE_MEDIUM_ALGS, "acceptance_rate", gray=gray, percent=True))
    for ax, load, bottom in zip(axes, ["Low", "Medium"], ["(a) Low Load", "(b) Medium Load"]):
        ax.set_xticklabels([scale_label("S20", rows), scale_label("S30", rows)])
        ax.set_xlabel(bottom, labelpad=12)
        ax.set_ylabel("Acceptance Rate (%)")
        ax.tick_params(axis="y", which="both", labelleft=True)
    lower = max(0, math.floor(min(all_values) / 5) * 5 - 2)
    for ax in axes:
        ax.set_ylim(lower, 101.8)
    add_shared_legend(fig, ACCEPTANCE_LOW_ALGS, gray)
    fig.tight_layout(rect=(0, 0.02, 1, 0.88))
    save(fig, "acceptance_exact_quality", gray)


def runtime(rows: list[Row], gray: bool) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.15, 3.58), sharey=True)
    tick_positions = [10, 60, 600, 3600, 36000]
    for ax, load, bottom, algs in zip(
        axes,
        ["Low", "Medium"],
        ["(a) Low Load", "(b) Medium Load"],
        [RUNTIME_LOW_ALGS, RUNTIME_MEDIUM_ALGS],
    ):
        draw_grouped_boxes(ax, rows, [("S20", load), ("S30", load)], algs, "runtime", gray=gray)
        ax.set_xticklabels([scale_label("S20", rows), scale_label("S30", rows)])
        ax.set_xlabel(bottom, labelpad=12)
        ax.set_ylabel("Total Runtime")
        ax.set_yscale("log")
        ax.yaxis.set_major_locator(mpl.ticker.FixedLocator(tick_positions))
        ax.yaxis.set_major_formatter(mpl.ticker.FuncFormatter(human_time_tick))
        ax.yaxis.set_minor_formatter(mpl.ticker.NullFormatter())
        ax.tick_params(axis="y", which="both", labelleft=True)
    axes[1].text(
        0.985,
        0.035,
        "* ILP-FullObjective not continued:\nmedium runs were not pursued after one S20 seed exceeded 63 hours.",
        transform=axes[1].transAxes,
        ha="right",
        va="bottom",
        fontsize=4.5,
        color="#555555",
    )
    add_shared_legend(fig, RUNTIME_LOW_ALGS, gray)
    fig.tight_layout(rect=(0, 0.02, 1, 0.88))
    save(fig, "runtime_exact_quality", gray)


def cost(rows: list[Row], algs: list[str], eligible: dict[tuple[str, str], list[int]], stem: str, gray: bool) -> None:
    fig, ax = plt.subplots(figsize=(5.55, 3.35))
    draw_grouped_boxes(ax, rows, LOW_GROUPS, algs, "normalized_cost", gray=gray, eligible_seeds=eligible)
    ax.set_xticklabels([scale_cost_label(g, rows, len(eligible[g])) for g in LOW_GROUPS])
    ax.set_ylabel("Normalized Deployment Cost")
    add_shared_legend(fig, algs, gray)
    rect_top = 0.84 if len(algs) == 6 else 0.90
    fig.tight_layout(rect=(0, 0, 1, rect_top))
    if stem == "cost_exact_quality_all5":
        ax.text(
            0.985,
            0.035,
            "* 7 seeds shown:\nDWCG-Pure missed 100% acceptance in 3 seeds.",
            transform=ax.transAxes,
            ha="right",
            va="bottom",
            fontsize=5.9,
            color="#555555",
        )
    if stem == "cost_exact_quality_no_pure":
        ax.text(
            0.985,
            0.035,
            "* DWCG-Pure omitted:\nnot 100% acceptance in 3 seeds.",
            transform=ax.transAxes,
            ha="right",
            va="bottom",
            fontsize=5.9,
            color="#555555",
        )
    save(fig, stem, gray)


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with resolve_input_path(path).open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def audit(rows: list[Row]) -> None:
    audit_dir = ACTIVE / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    plot_rows = []
    for row in rows:
        plot_rows.append(
            {
                "algorithm_id": row.algorithm_id,
                "label": LABEL.get(row.algorithm_id, row.algorithm_id),
                "scale": row.scale,
                "load": row.load,
                "seed": row.seed,
                "accepted_flow_count": row.accepted,
                "acceptance_rate": row.acceptance_rate,
                "runtime": row.runtime,
                "normalized_cost": row.normalized_cost,
                "T": row.T,
                "F": row.F,
                "topology_fingerprint": row.topology,
                "base_flow_fingerprint": row.base_flow,
                "full_instance_fingerprint": row.full_instance,
                "source": row.source,
            }
        )
    write_csv(audit_dir / "split_ilp_plotted_rows.csv", plot_rows)

    cost_rows = []
    for name, algs, eligible in [
        ("all5_plus_full_objective", COST_ALL5_ALGS, COST_ELIGIBILITY_ALL5_SPLIT),
        ("no_pure_plus_full_objective", COST_NO_PURE_ALGS, COST_ELIGIBILITY_NO_PURE_SPLIT),
    ]:
        for group in LOW_GROUPS:
            for alg in algs:
                vals = [
                    r.normalized_cost
                    for r in rows
                    if (r.scale, r.load) == group and r.algorithm_id == alg and r.seed in eligible[group]
                ]
                if len(vals) != len(eligible[group]):
                    raise SystemExit(f"cost eligibility missing rows for {name} {group} {alg}")
                cost_rows.append(
                    {
                        "variant": name,
                        "group": f"{group[0]} {group[1]}",
                        "algorithm": LABEL[alg],
                        "n": len(vals),
                        "eligible_seeds": ",".join(map(str, eligible[group])),
                        "mean": statistics.fmean(vals),
                        "median": statistics.median(vals),
                        "min": min(vals),
                        "max": max(vals),
                    }
                )
                for seed in eligible[group]:
                    row = next(r for r in rows if (r.scale, r.load, r.seed, r.algorithm_id) == (group[0], group[1], seed, alg))
                    if row.accepted != row.F:
                        raise SystemExit(f"cost plotted row is not full-admission: {name} {group} seed {seed} {alg}")
    write_csv(audit_dir / "split_ilp_cost_summary.csv", cost_rows)

    medium_full = [r for r in rows if r.algorithm_id == ALG_FULL and r.load == "Medium"]
    if medium_full:
        raise SystemExit("ILP-FullObjective Medium rows found in plotted data")

    readme = f"""# Exact-Quality Final-Review Split-ILP Figures

This folder regenerates the approved exact-quality figure family while preserving the previously approved visual style as closely as possible.

## Semantic split

- `ILP-AdmissionOnly` is the old Direct ILP exact-anchor series relabeled. The underlying historical admission-only rows are unchanged.
- `ILP-FullObjective` is the new scalarized full-objective ILP series with objective `max 2*A - C`.

## Data sources

Non-ILP:

- `{NON_ILP.relative_to(ROOT)}`

ILP-AdmissionOnly:

- `{DIRECT_ORIGINAL.relative_to(ROOT)}`
- `{S20_MED_SUPP.relative_to(ROOT)}`
- `{(S30_MED_SUPP / 'pilot4' / 'runs.jsonl').relative_to(ROOT)}`
- `{(S30_MED_SUPP / 'remaining6' / 'runs.jsonl').relative_to(ROOT)}`

ILP-FullObjective:

- `{ILP_FULL_S20_LOW_SEED0.relative_to(ROOT)}`
- `{ILP_FULL_S20_LOW_SEEDS1_9.relative_to(ROOT)}`
- `{ILP_FULL_S30_LOW_SEED0.relative_to(ROOT)}`
- `{ILP_FULL_S30_LOW_SEEDS1_9.relative_to(ROOT)}`

Excluded diagnostic full-objective Medium output:

- `{ILP_FULL_MEDIUM_INTERRUPTED.relative_to(ROOT)}` (not plotted)

## Panel inclusion

- Acceptance Low Load: includes `ILP-AdmissionOnly` and `ILP-FullObjective`.
- Acceptance Medium Load: includes `ILP-AdmissionOnly`; does not include `ILP-FullObjective`.
- Runtime Low Load: includes `ILP-FullObjective` for S20 Low and S30 Low. Runtime Medium Load does not include `ILP-FullObjective`.
- Cost all5 variant: 7 seeds per low-load group, now with `ILP-FullObjective` added.
- Cost no-Pure variant: 10 seeds per low-load group, now with `ILP-FullObjective` added.

## Style preservation

The generator reuses the approved final-review layout: same subplot dimensions, zoomed acceptance y-axis, log-scale runtime axis, boxplot plus jittered scatter language, shared legend placement, and right-panel y-axis labels. Six-method legends are centered in two rows: the four non-ILP methods first, then the two ILP baselines. The only visual additions are the renamed ILP label and the new purple-family `ILP-FullObjective` marker where data exists.

## Final annotations

- Cost all5 7-seed figure, bottom-right inside the plotting box: `* 7 seeds shown: DWCG-Pure missed 100% acceptance in 3 seeds.`
- Cost no-Pure figure, bottom-right inside the plotting box: `* DWCG-Pure omitted: not 100% acceptance in 3 seeds.`
- Runtime figure, bottom-right inside the Medium Load subplot: `* ILP-FullObjective not continued: medium runs were not pursued after one S20 seed exceeded 63 hours.`

## Audit

- `audit/split_ilp_plotted_rows.csv`
- `audit/split_ilp_cost_summary.csv`

Audit assertions verify that `ILP-FullObjective` appears only for S20 Low and S30 Low, cost rows are full-admission on the selected seeds, and strict fingerprints match the `ILP-AdmissionOnly` exact-anchor instance where required.
"""
    (ACTIVE / "README.md").write_text(readme, encoding="utf-8")


def main() -> None:
    setup_style()
    rows = load_split_rows()
    audit(rows)
    for gray in [False, True]:
        acceptance(rows, gray)
        runtime(rows, gray)
        cost(rows, COST_ALL5_ALGS, COST_ELIGIBILITY_ALL5_SPLIT, "cost_exact_quality_all5", gray)
        cost(rows, COST_NO_PURE_ALGS, COST_ELIGIBILITY_NO_PURE_SPLIT, "cost_exact_quality_no_pure", gray)
    print("Created style-preserving split-ILP exact-quality figures in", ACTIVE.relative_to(ROOT))
    print("ILP-AdmissionOnly rows:", sum(1 for r in rows if r.algorithm_id == ALG_ADMISSION))
    print("ILP-FullObjective rows:", sum(1 for r in rows if r.algorithm_id == ALG_FULL))


if __name__ == "__main__":
    main()
