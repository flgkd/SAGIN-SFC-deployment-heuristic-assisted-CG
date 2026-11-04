from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from record_compatibility import normalize_record, normalize_dataframe, resolve_input_path


import csv
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import matplotlib as mpl
import matplotlib.lines as mlines
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "figures" / "exact_quality" / "alternatives"
REV = OUT / "revised_grouped"
AUDIT = REV / "audit"

DIRECT_ORIGINAL = ROOT / "formal_outputs" / "direct_ilp_acceptance_cold_s20_s30_v3_d047966" / "runs.jsonl"
NON_ILP = ROOT / "outputs" / "merged_formal_v3_480_dwcg_saginfe_pro_970415f" / "merged_runs.jsonl"
S20_MED_SUPP = ROOT / "results" / "frozen" / "direct_ilp_s20_medium_unlimited_certification_v3_9cb37f8" / "runs.jsonl"
S30_MED_SUPP = ROOT / "results" / "frozen" / "direct_ilp_s30_medium_unlimited_certification_v3_0aefb5f"

GROUPS = [("S20", "Low"), ("S20", "Medium"), ("S30", "Low"), ("S30", "Medium")]
LOW_GROUPS = [("S20", "Low"), ("S30", "Low")]
ALG_ORDER = ["saginfe", "dwcg_pure", "dwcg_saginfe", "dwcg_saginfe_pro", "direct_ilp_acceptance_cold"]
ALG_NO_PURE = ["saginfe", "dwcg_saginfe", "dwcg_saginfe_pro", "direct_ilp_acceptance_cold"]
LABEL = {
    "saginfe": "SAGINFE",
    "dwcg_pure": "DWCG-Pure",
    "dwcg_saginfe": "DWCG-SAGINFE",
    "dwcg_saginfe_pro": "PRO",
    "direct_ilp_acceptance_cold": "ILP-AdmissionOnly",
}
STYLE = {
    "saginfe": {"color": "#0072B2", "marker": "o", "linestyle": "-"},
    "dwcg_pure": {"color": "#D55E00", "marker": "s", "linestyle": "--"},
    "dwcg_saginfe": {"color": "#009E73", "marker": "^", "linestyle": "-."},
    "dwcg_saginfe_pro": {"color": "#C51B7D", "marker": "X", "linestyle": ":"},
    "direct_ilp_acceptance_cold": {"color": "#6A3D9A", "marker": "D", "linestyle": "-"},
}
GRAY = {
    "saginfe": "#222222",
    "dwcg_pure": "#555555",
    "dwcg_saginfe": "#777777",
    "dwcg_saginfe_pro": "#000000",
    "direct_ilp_acceptance_cold": "#AAAAAA",
}


@dataclass(frozen=True)
class Row:
    scale: str
    load: str
    seed: int
    algorithm_id: str
    accepted: int
    acceptance_rate: float
    runtime: float
    normalized_cost: float | None
    T: int
    F: int
    demand_multiplier: float
    topology: str
    base_flow: str
    full_instance: str
    path_valid: bool
    capacity_valid: bool
    runtime_accounting_valid: bool | None
    source: str


def read_jsonl(path: Path) -> list[dict]:
    with resolve_input_path(path).open(encoding="utf-8") as f:
        return [normalize_record(json.loads(line)) for line in f if line.strip()]


def rec_runtime(rec: dict) -> tuple[float, bool | None]:
    if rec.get("direct_ilp_metadata"):
        meta = rec["direct_ilp_metadata"]
    elif rec.get("dwcg_metadata"):
        meta = rec["dwcg_metadata"]
    else:
        meta = rec.get("heuristic_metadata") or {}
    runtime = meta.get("total_end_to_end_runtime_seconds", rec.get("total_end_to_end_runtime_seconds"))
    return float(runtime), meta.get("runtime_accounting_valid")


def exact_direct_ok(rec: dict) -> bool:
    meta = rec.get("direct_ilp_metadata") or {}
    return (
        meta.get("solver_status_name") == "OPTIMAL"
        and meta.get("termination_status") == "OPTIMAL"
        and meta.get("safe_integer_acceptance_lower_bound") == meta.get("safe_integer_acceptance_upper_bound")
        and rec.get("accepted_flow_count") == meta.get("safe_integer_acceptance_lower_bound")
        and float(meta.get("mip_gap", rec.get("mip_gap", 1.0))) == 0.0
        and rec.get("objective_globally_optimal") is True
        and rec.get("acceptance_count_certified") is True
        and rec.get("path_validity") is True
        and rec.get("capacity_validity") is True
    )


def row_from_record(rec: dict, source: str) -> Row:
    k = rec["key"]
    runtime, runtime_valid = rec_runtime(rec)
    return Row(
        scale=k["scale"],
        load=k["load_label"],
        seed=int(k["formal_seed"]),
        algorithm_id=k["algorithm"],
        accepted=int(rec["accepted_flow_count"]),
        acceptance_rate=float(rec["acceptance_rate"]),
        runtime=runtime,
        normalized_cost=None if rec.get("normalized_physical_cost") is None else float(rec["normalized_physical_cost"]),
        T=int(k["time_slots"]),
        F=int(rec["total_flow_count"]),
        demand_multiplier=float(k["demand_multiplier"]),
        topology=rec["topology_fingerprint"],
        base_flow=rec["base_flow_fingerprint"],
        full_instance=rec["full_instance_fingerprint"],
        path_valid=rec.get("path_validity") is True,
        capacity_valid=rec.get("capacity_validity") is True,
        runtime_accounting_valid=runtime_valid,
        source=source,
    )


def load_rows() -> list[Row]:
    rows: list[Row] = []
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
    for rec in read_jsonl(NON_ILP):
        k = rec["key"]
        if (k["scale"], k["load_label"]) in GROUPS and k["algorithm"] in ALG_ORDER:
            if rec.get("path_validity") is True and rec.get("capacity_validity") is True:
                rows.append(row_from_record(rec, "merged_formal_v3_480"))
    for key in sorted(direct_refs):
        rows.append(row_from_record(direct_refs[key], "direct_ilp_exact_reference"))
    validate_rows(rows)
    return rows


def validate_rows(rows: list[Row]) -> None:
    by = {(r.scale, r.load, r.seed, r.algorithm_id): r for r in rows}
    for group in GROUPS:
        refs = [by.get((group[0], group[1], seed, "direct_ilp_acceptance_cold")) for seed in range(10)]
        if any(r is None for r in refs):
            raise SystemExit(f"missing Direct ILP exact reference for {group}")
        for seed in range(10):
            ref = by[(group[0], group[1], seed, "direct_ilp_acceptance_cold")]
            for alg in ALG_ORDER:
                row = by.get((group[0], group[1], seed, alg))
                if row is None:
                    raise SystemExit(f"missing row: {group} seed {seed} {alg}")
                if (row.T, row.F, row.demand_multiplier) != (ref.T, ref.F, ref.demand_multiplier):
                    raise SystemExit(f"metadata mismatch: {group} seed {seed} {alg}")
                if (row.topology, row.base_flow, row.full_instance) != (ref.topology, ref.base_flow, ref.full_instance):
                    raise SystemExit(f"fingerprint mismatch: {group} seed {seed} {alg}")
                if not (row.path_valid and row.capacity_valid):
                    raise SystemExit(f"invalid path/capacity: {group} seed {seed} {alg}")


def setup_style() -> None:
    mpl.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8,
            "axes.labelsize": 9,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 8,
            "axes.linewidth": 0.7,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def color(alg: str, gray: bool = False) -> str:
    return GRAY[alg] if gray else STYLE[alg]["color"]


def scenario_label(group: tuple[str, str], rows: list[Row], n: int | None = None) -> str:
    sample = next(r for r in rows if (r.scale, r.load) == group)
    suffix = f"; n={n}" if n is not None else ""
    return f"{group[0]} {group[1]}\n(|T|={sample.T}, |F|={sample.F}{suffix})"


def offsets(algs: list[str]) -> np.ndarray:
    width = 0.13 if len(algs) == 5 else 0.16
    return (np.arange(len(algs)) - (len(algs) - 1) / 2) * width


def deterministic_jitter(seed: int, idx: int, width: float = 0.022) -> float:
    return ((seed * 17 + idx * 7) % 7 - 3) * width / 3


def grouped_box_points(
    rows: list[Row],
    groups: list[tuple[str, str]],
    algs: list[str],
    metric: str,
    ylabel: str,
    out_stem: str,
    gray: bool = False,
    log_y: bool = False,
    percent: bool = False,
    zoom_acceptance: bool = False,
    eligible_seeds: dict[tuple[str, str], list[int]] | None = None,
) -> None:
    fig, ax = plt.subplots(figsize=(6.85, 3.45))
    centers = np.arange(len(groups), dtype=float)
    offs = offsets(algs)
    all_values = []
    positions = []
    data = []
    box_algs = []
    for gi, group in enumerate(groups):
        seed_filter = eligible_seeds[group] if eligible_seeds else list(range(10))
        for ai, alg in enumerate(algs):
            vals = []
            for seed in seed_filter:
                r = next(r for r in rows if (r.scale, r.load, r.seed, r.algorithm_id) == (group[0], group[1], seed, alg))
                v = getattr(r, metric)
                if percent:
                    v *= 100.0
                vals.append(v)
            data.append(vals)
            all_values.extend(vals)
            positions.append(centers[gi] + offs[ai])
            box_algs.append(alg)
    bp = ax.boxplot(data, positions=positions, widths=0.1 if len(algs) == 5 else 0.13, patch_artist=True, showfliers=False)
    for patch, alg in zip(bp["boxes"], box_algs):
        patch.set_facecolor(color(alg, gray))
        patch.set_alpha(0.22)
        patch.set_edgecolor(color(alg, gray))
        patch.set_linewidth(1.0)
    for part in ["whiskers", "caps"]:
        for item in bp[part]:
            item.set_color("#555555")
            item.set_linewidth(0.8)
    for item in bp["medians"]:
        item.set_color("#111111")
        item.set_linewidth(1.15)

    for gi, group in enumerate(groups):
        seed_filter = eligible_seeds[group] if eligible_seeds else list(range(10))
        for ai, alg in enumerate(algs):
            xs, ys = [], []
            for seed in seed_filter:
                r = next(r for r in rows if (r.scale, r.load, r.seed, r.algorithm_id) == (group[0], group[1], seed, alg))
                val = getattr(r, metric) * (100.0 if percent else 1.0)
                xs.append(centers[gi] + offs[ai] + deterministic_jitter(seed, ai))
                ys.append(val)
            ax.scatter(
                xs,
                ys,
                s=24,
                marker=STYLE[alg]["marker"],
                color=color(alg, gray),
                edgecolors="white",
                linewidths=0.35,
                zorder=4,
            )
            ax.scatter([centers[gi] + offs[ai]], [statistics.fmean(ys)], marker="_", s=55, color="#111111", linewidths=1.4, zorder=5)

    for gi in range(len(groups) - 1):
        ax.axvline((centers[gi] + centers[gi + 1]) / 2, color="#D0D0D0", linewidth=0.45, zorder=0)
    ax.set_xticks(centers)
    ax.set_xticklabels([scenario_label(g, rows, None if eligible_seeds is None else len(eligible_seeds[g])) for g in groups])
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", color="#D8D8D8", linewidth=0.5, alpha=0.8)
    if log_y:
        ax.set_yscale("log")
        ax.yaxis.set_major_locator(mpl.ticker.FixedLocator([1, 10, 60, 600, 3600, 36000, 86400]))
        ax.yaxis.set_major_formatter(mpl.ticker.FuncFormatter(format_seconds))
    if zoom_acceptance:
        lower = max(0, math.floor(min(all_values) / 5) * 5 - 2)
        ax.set_ylim(lower, 101.8)
    handles = [
        mlines.Line2D([], [], color=color(alg, gray), marker=STYLE[alg]["marker"], linestyle="None", markersize=5.5, label=LABEL[alg])
        for alg in algs
    ]
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=len(algs), frameon=False, handletextpad=0.4, columnspacing=1.0)
    fig.tight_layout()
    stem = REV / out_stem
    if gray:
        stem = stem.with_name(stem.name + "_grayscale")
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(stem.with_suffix(".png"), dpi=300, bbox_inches="tight")
    plt.close(fig)


def format_seconds(x, _pos=None) -> str:
    if x >= 3600:
        return f"{x/3600:g} h"
    if x >= 60:
        return f"{x/60:g} min"
    return f"{x:g} s"


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with resolve_input_path(path).open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)


def cost_eligibility(rows: list[Row], algs: list[str]) -> list[dict]:
    out = []
    for group in LOW_GROUPS:
        eligible = []
        excluded = []
        for seed in range(10):
            seed_rows = [r for r in rows if (r.scale, r.load, r.seed) == (group[0], group[1], seed) and r.algorithm_id in algs]
            by_alg = {r.algorithm_id: r for r in seed_rows}
            reasons = []
            if set(by_alg) != set(algs):
                reasons.append("missing_algorithm")
            if any(not (by_alg[a].path_valid and by_alg[a].capacity_valid) for a in by_alg):
                reasons.append("invalid_path_or_capacity")
            if any(by_alg[a].accepted != by_alg[a].F for a in by_alg):
                low = [LABEL[a] for a in by_alg if by_alg[a].accepted != by_alg[a].F]
                reasons.append("not_full_admission:" + ",".join(low))
            if any(by_alg[a].normalized_cost is None or not math.isfinite(by_alg[a].normalized_cost) for a in by_alg):
                reasons.append("missing_cost")
            if not reasons and len(by_alg) == len(algs):
                eligible.append(seed)
            else:
                excluded.append(f"{seed} ({'; '.join(reasons)})")
        out.append(
            {
                "group": f"{group[0]} {group[1]}",
                "algorithm_set": ",".join(LABEL[a] for a in algs),
                "eligible_n": len(eligible),
                "eligible_seeds": ",".join(map(str, eligible)),
                "excluded": "; ".join(excluded),
            }
        )
    return out


def audit_tables(rows: list[Row]) -> tuple[dict[tuple[str, str], list[int]], dict[tuple[str, str], list[int]]]:
    metadata = []
    coverage = []
    for group in GROUPS:
        sample = next(r for r in rows if (r.scale, r.load) == group)
        metadata.append(
            {
                "group": f"{group[0]} {group[1]}",
                "T": sample.T,
                "F": sample.F,
                "seeds": "0,1,2,3,4,5,6,7,8,9",
                "algorithm_count": len(ALG_ORDER),
            }
        )
        for alg in ALG_ORDER:
            rs = [r for r in rows if (r.scale, r.load) == group and r.algorithm_id == alg]
            coverage.append(
                {
                    "group": f"{group[0]} {group[1]}",
                    "algorithm": LABEL[alg],
                    "records": len(rs),
                    "strict_fingerprint_matches": len(rs),
                    "path_valid": sum(r.path_valid for r in rs),
                    "capacity_valid": sum(r.capacity_valid for r in rs),
                }
            )
    write_csv(AUDIT / "scenario_metadata.csv", metadata)
    write_csv(AUDIT / "coverage_and_validity.csv", coverage)
    all5 = cost_eligibility(rows, ALG_ORDER)
    no_pure = cost_eligibility(rows, ALG_NO_PURE)
    write_csv(AUDIT / "cost_eligibility_all5.csv", all5)
    write_csv(AUDIT / "cost_eligibility_no_pure.csv", no_pure)
    for name, elig, algs in [("cost_values_all5.csv", all5, ALG_ORDER), ("cost_values_no_pure.csv", no_pure, ALG_NO_PURE)]:
        value_rows = []
        for item in elig:
            group = tuple(item["group"].split(" ", 1))
            seeds = [] if not item["eligible_seeds"] else [int(x) for x in item["eligible_seeds"].split(",")]
            for alg in algs:
                vals = [
                    r.normalized_cost
                    for r in rows
                    if (r.scale, r.load) == group and r.algorithm_id == alg and r.seed in seeds
                ]
                value_rows.append(
                    {
                        "group": item["group"],
                        "algorithm": LABEL[alg],
                        "n": len(vals),
                        "mean": statistics.fmean(vals) if vals else "",
                        "median": statistics.median(vals) if vals else "",
                        "q1": float(np.quantile(vals, 0.25)) if vals else "",
                        "q3": float(np.quantile(vals, 0.75)) if vals else "",
                        "min": min(vals) if vals else "",
                        "max": max(vals) if vals else "",
                    }
                )
        write_csv(AUDIT / name, value_rows)
    return (
        {(e["group"].split(" ", 1)[0], e["group"].split(" ", 1)[1]): [int(x) for x in e["eligible_seeds"].split(",") if x] for e in all5},
        {(e["group"].split(" ", 1)[0], e["group"].split(" ", 1)[1]): [int(x) for x in e["eligible_seeds"].split(",") if x] for e in no_pure},
    )


def main() -> None:
    setup_style()
    REV.mkdir(parents=True, exist_ok=True)
    AUDIT.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    all5_eligible, no_pure_eligible = audit_tables(rows)
    for group in LOW_GROUPS:
        if len(all5_eligible[group]) != 7:
            raise SystemExit(f"all-five cost eligibility is not n=7 for {group}: {all5_eligible[group]}")
        if len(no_pure_eligible[group]) != 10:
            raise SystemExit(f"no-pure cost eligibility is not n=10 for {group}: {no_pure_eligible[group]}")

    for gray in [False, True]:
        grouped_box_points(
            rows,
            GROUPS,
            ALG_ORDER,
            "acceptance_rate",
            "Acceptance rate (%)",
            "acceptance_grouped_scenario_box_points_zoom",
            gray=gray,
            percent=True,
            zoom_acceptance=True,
        )
        grouped_box_points(
            rows,
            GROUPS,
            ALG_ORDER,
            "runtime",
            "Total end-to-end runtime",
            "runtime_grouped_scenario_box_points_log",
            gray=gray,
            log_y=True,
        )
        grouped_box_points(
            rows,
            LOW_GROUPS,
            ALG_ORDER,
            "normalized_cost",
            "Normalized deployment cost",
            "cost_grouped_scenario_box_points_all5_n7",
            gray=gray,
            eligible_seeds=all5_eligible,
        )
        grouped_box_points(
            rows,
            LOW_GROUPS,
            ALG_NO_PURE,
            "normalized_cost",
            "Normalized deployment cost",
            "cost_grouped_scenario_box_points_no_pure_n10",
            gray=gray,
            eligible_seeds=no_pure_eligible,
        )

    readme = [
        "# Revised Grouped Exact-Quality Candidates",
        "",
        "These are targeted revisions of the selected boxplot designs. The x-axis is scenario group; algorithms are encoded only in the shared legend.",
        "",
        "Files:",
        "- `acceptance_grouped_scenario_box_points_zoom.{png,pdf}`",
        "- `runtime_grouped_scenario_box_points_log.{png,pdf}`",
        "- `cost_grouped_scenario_box_points_all5_n7.{png,pdf}`",
        "- `cost_grouped_scenario_box_points_no_pure_n10.{png,pdf}`",
        "",
        "Grayscale previews use `_grayscale` suffix.",
        "",
        "Cost semantics: `normalized_physical_cost` is used as realized normalized deployment cost. Direct ILP is admission-only (`physical_cost_in_objective=false`), so its cost is not a cost optimum.",
        "",
        "Cost eligibility masks are stored in `audit/cost_eligibility_all5.csv` and `audit/cost_eligibility_no_pure.csv`.",
    ]
    (REV / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")

    print("SCENARIO METADATA")
    print((AUDIT / "scenario_metadata.csv").read_text(encoding="utf-8"))
    print("COST FIGURE A ELIGIBILITY")
    print((AUDIT / "cost_eligibility_all5.csv").read_text(encoding="utf-8"))
    print("COST FIGURE B ELIGIBILITY")
    print((AUDIT / "cost_eligibility_no_pure.csv").read_text(encoding="utf-8"))
    print("COST VALUES A")
    print((AUDIT / "cost_values_all5.csv").read_text(encoding="utf-8"))
    print("COST VALUES B")
    print((AUDIT / "cost_values_no_pure.csv").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
