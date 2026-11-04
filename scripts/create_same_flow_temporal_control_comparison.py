"""Create solver-free derived outputs for V3 Same-flow Temporal Control."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calibration_runner import sha256_fingerprint, utc_now, write_json_atomic
from formal_runner import is_resume_skippable_record
from scripts.audit_formal_same_flow_temporal_control_v3 import (
    ALGORITHMS,
    TIME_SLOTS,
    audit,
    evidence_key,
    normalized_new_row,
    normalized_reuse_row,
    read_json,
    read_jsonl,
)


def mean(values: List[float]) -> float | None:
    return None if not values else float(statistics.mean(values))


def median(values: List[float]) -> float | None:
    return None if not values else float(statistics.median(values))


def ratio(numerator: Any, denominator: Any) -> float | None:
    if numerator in (None, 0) or denominator in (None, 0):
        return None
    return float(numerator) / float(denominator)


def meta_for_row(row: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = row.get("raw_record") or row
    if row.get("algorithm") == "saginfe":
        return raw.get("heuristic_metadata") or row.get("heuristic_metadata") or {}
    return raw.get("dwcg_metadata") or row.get("dwcg_metadata") or {}


def numeric(row: Mapping[str, Any], field: str) -> float | None:
    value = row.get(field)
    if value is None:
        value = meta_for_row(row).get(field)
    if value is None:
        return None
    return float(value)


def ensure_output_dir(path: Path, overwrite: bool) -> None:
    if path.exists() and any(path.iterdir()) and not overwrite:
        raise ValueError("Output directory is nonempty; pass --overwrite or choose a new directory.")
    path.mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, rows: List[Mapping[str, Any]], fieldnames: List[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: List[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def public_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    m = meta_for_row(row)
    return {
        "source_kind": row["source_kind"],
        "scale": row["scale"],
        "time_slots": row["time_slots"],
        "load_label": row["load_label"],
        "formal_seed": row["formal_seed"],
        "algorithm": row["algorithm"],
        "accepted_flow_count": row["accepted_flow_count"],
        "acceptance_rate": row["acceptance_rate"],
        "total_end_to_end_runtime_seconds": row["total_end_to_end_runtime_seconds"],
        "termination_status": row["termination_status"],
        "path_validity": row["path_validity"],
        "capacity_validity": row["capacity_validity"],
        "runtime_accounting_valid": row["runtime_accounting_valid"],
        "hard_wall_clock_limit_reached": row["hard_wall_clock_limit_reached"],
        "topology_fingerprint": row["topology_fingerprint"],
        "base_flow_fingerprint": row["base_flow_fingerprint"],
        "full_instance_fingerprint": row["full_instance_fingerprint"],
        "physical_base_fingerprint": row["physical_base_fingerprint"],
        "temporal_prefix_fingerprint": row["temporal_prefix_fingerprint"],
        "common_temporal_prefix_fingerprints": row["common_temporal_prefix_fingerprints"],
        "non_temporal_flow_fingerprint": row["non_temporal_flow_fingerprint"],
        "paired_timing_latent_fingerprint": row["paired_timing_latent_fingerprint"],
        "full_temporal_flow_fingerprint": row["full_temporal_flow_fingerprint"],
        "temporal_teg_node_count": row["temporal_teg_node_count"],
        "temporal_flow_anchor_time_slots": row.get("temporal_flow_anchor_time_slots"),
        "temporal_storage_edge_count": row["temporal_storage_edge_count"],
        "temporal_communication_edge_count": row["temporal_communication_edge_count"],
        "temporal_arrival_time_histogram": row["temporal_arrival_time_histogram"],
        "temporal_slack_histogram": row["temporal_slack_histogram"],
        "temporal_deadline_time_histogram": row["temporal_deadline_time_histogram"],
        "cg_iterations": m.get("completed_cg_iteration_count") or m.get("cg_iteration_count"),
        "logical_pricing_task_count": m.get("logical_pricing_task_count"),
        "exact_pricing_call_count": m.get("exact_pricing_call_count"),
        "exact_pricing_budget_used": m.get("exact_pricing_budget_used"),
        "pricing_strategy": m.get("pricing_strategy"),
        "hybrid_switch_iteration": m.get("hybrid_switch_iteration"),
        "total_pricing_runtime": m.get("total_pricing_runtime"),
        "exact_pricing_runtime": m.get("exact_pricing_runtime"),
        "rmp_lp_runtime_seconds": m.get("rmp_lp_runtime_seconds") or m.get("lp_rmp_runtime_seconds"),
        "final_rmp_runtime_seconds": m.get("final_rmp_runtime_seconds") or m.get("final_restricted_rmp_runtime_seconds"),
        "generated_column_count": m.get("generated_column_count"),
        "inserted_column_count": m.get("inserted_column_count"),
        "final_column_pool_size": m.get("final_column_pool_size"),
        "initial_saginfe_accepted_flow_count": m.get("initial_saginfe_accepted_flow_count") or m.get("initialization_accepted_flow_count"),
        "initialization_runtime_seconds": m.get("initialization_runtime_seconds") or m.get("initialization_generation_runtime_seconds"),
        "selective_pfe_call_count": m.get("selective_pfe_call_count"),
        "selective_pfe_round_count": m.get("selective_pfe_round_count"),
        "selective_pfe_candidate_count": m.get("selective_pfe_candidate_count"),
        "selective_pfe_positive_column_count": m.get("selective_pfe_positive_column_count"),
        "selective_pfe_runtime_seconds": m.get("selective_pfe_runtime_seconds"),
        "deterministic_work_budget_exhausted": m.get("deterministic_work_budget_exhausted"),
        "final_integer_rmp_status": m.get("final_integer_rmp_status"),
    }


def summarize(rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, int], List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["algorithm"], int(row["time_slots"]))].append(row)
    result = []
    for (algorithm, time_slots), group in sorted(groups.items()):
        result.append({
            "algorithm": algorithm,
            "time_slots": time_slots,
            "record_count": len(group),
            "mean_accepted_flow_count": mean([float(row["accepted_flow_count"]) for row in group if row["accepted_flow_count"] is not None]),
            "median_accepted_flow_count": median([float(row["accepted_flow_count"]) for row in group if row["accepted_flow_count"] is not None]),
            "mean_acceptance_rate": mean([float(row["acceptance_rate"]) for row in group if row["acceptance_rate"] is not None]),
            "median_acceptance_rate": median([float(row["acceptance_rate"]) for row in group if row["acceptance_rate"] is not None]),
            "mean_e2e_runtime_seconds": mean([float(row["total_end_to_end_runtime_seconds"]) for row in group if row["total_end_to_end_runtime_seconds"] is not None]),
            "median_e2e_runtime_seconds": median([float(row["total_end_to_end_runtime_seconds"]) for row in group if row["total_end_to_end_runtime_seconds"] is not None]),
            "median_temporal_teg_node_count": median([float(row["temporal_teg_node_count"]) for row in group if row["temporal_teg_node_count"] is not None]),
            "median_temporal_communication_edge_count": median([float(row["temporal_communication_edge_count"]) for row in group if row["temporal_communication_edge_count"] is not None]),
            "median_temporal_storage_edge_count": median([float(row["temporal_storage_edge_count"]) for row in group if row["temporal_storage_edge_count"] is not None]),
            "mean_total_pricing_runtime": mean([v for row in group if (v := numeric(row, "total_pricing_runtime")) is not None]),
            "median_total_pricing_runtime": median([v for row in group if (v := numeric(row, "total_pricing_runtime")) is not None]),
            "mean_final_rmp_runtime_seconds": mean([v for row in group if (v := numeric(row, "final_rmp_runtime_seconds")) is not None]),
            "median_final_rmp_runtime_seconds": median([v for row in group if (v := numeric(row, "final_rmp_runtime_seconds")) is not None]),
            "mean_generated_column_count": mean([v for row in group if (v := numeric(row, "generated_column_count")) is not None]),
            "mean_inserted_column_count": mean([v for row in group if (v := numeric(row, "inserted_column_count")) is not None]),
            "mean_final_column_pool_size": mean([v for row in group if (v := numeric(row, "final_column_pool_size")) is not None]),
            "mean_selective_pfe_positive_column_count": mean([v for row in group if (v := numeric(row, "selective_pfe_positive_column_count")) is not None]),
            "hard_wall_count": sum(row.get("hard_wall_clock_limit_reached") is True for row in group),
        })
    return result


def paired_by_horizon(rows: List[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    by_key = {(row["algorithm"], int(row["formal_seed"]), int(row["time_slots"])): row for row in rows}
    acceptance_rows = []
    runtime_rows = []
    for algorithm in ALGORITHMS:
        for baseline, time_slots in ((6, 8), (6, 10), (6, 12), (8, 10), (8, 12), (10, 12)):
            diffs = []
            runtime_ratios = []
            runtime_diffs = []
            wins = ties = losses = 0
            censored_pairs = 0
            for seed in range(10):
                base = by_key[(algorithm, seed, baseline)]
                row = by_key[(algorithm, seed, time_slots)]
                diff = float(row["accepted_flow_count"]) - float(base["accepted_flow_count"])
                diffs.append(diff)
                wins += diff > 0
                ties += diff == 0
                losses += diff < 0
                if row.get("hard_wall_clock_limit_reached") or base.get("hard_wall_clock_limit_reached"):
                    censored_pairs += 1
                    continue
                value = ratio(row["total_end_to_end_runtime_seconds"], base["total_end_to_end_runtime_seconds"])
                if value is not None:
                    runtime_ratios.append(value)
                    runtime_diffs.append(float(row["total_end_to_end_runtime_seconds"]) - float(base["total_end_to_end_runtime_seconds"]))
            acceptance_rows.append({
                "algorithm": algorithm,
                "baseline_time_slots": baseline,
                "time_slots": time_slots,
                "seed_count": 10,
                "wins": wins,
                "ties": ties,
                "losses": losses,
                "net_accepted_flow_difference": sum(diffs),
                "mean_accepted_flow_difference": mean(diffs),
                "median_accepted_flow_difference": median(diffs),
                "min_accepted_flow_difference": min(diffs),
                "max_accepted_flow_difference": max(diffs),
            })
            runtime_rows.append({
                "algorithm": algorithm,
                "baseline_time_slots": baseline,
                "time_slots": time_slots,
                "eligible_pair_count": len(runtime_ratios),
                "censored_pair_count": censored_pairs,
                "mean_e2e_runtime_difference_seconds": mean(runtime_diffs),
                "median_e2e_runtime_difference_seconds": median(runtime_diffs),
                "mean_e2e_runtime_ratio_t_over_baseline": mean(runtime_ratios),
                "median_e2e_runtime_ratio_t_over_baseline": median(runtime_ratios),
                "min_e2e_runtime_ratio_t_over_baseline": min(runtime_ratios) if runtime_ratios else None,
                "max_e2e_runtime_ratio_t_over_baseline": max(runtime_ratios) if runtime_ratios else None,
            })
    return acceptance_rows, runtime_rows


def complexity_summary(rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    by_t: Dict[int, List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_t[int(row["time_slots"])].append(row)
    result = []
    for time_slots, group in sorted(by_t.items()):
        result.append({
            "time_slots": time_slots,
            "record_count": len(group),
            "median_temporal_teg_node_count": median([float(row["temporal_teg_node_count"]) for row in group]),
            "median_temporal_communication_edge_count": median([float(row["temporal_communication_edge_count"]) for row in group]),
            "median_temporal_storage_edge_count": median([float(row["temporal_storage_edge_count"]) for row in group]),
            "mean_temporal_teg_node_count": mean([float(row["temporal_teg_node_count"]) for row in group]),
            "mean_temporal_communication_edge_count": mean([float(row["temporal_communication_edge_count"]) for row in group]),
            "mean_temporal_storage_edge_count": mean([float(row["temporal_storage_edge_count"]) for row in group]),
        })
    return result


def load_operational_rows(path: Path) -> List[Mapping[str, Any]]:
    rows = []
    for row in read_jsonl(path):
        key = row.get("key") or {}
        if key.get("algorithm") not in ALGORITHMS:
            continue
        rows.append({
            "algorithm": key.get("algorithm"),
            "time_slots": int(key.get("time_slots")),
            "formal_seed": int(key.get("formal_seed")),
            "accepted_flow_count": row.get("accepted_flow_count"),
            "total_end_to_end_runtime_seconds": row.get("total_end_to_end_runtime_seconds"),
        })
    return rows


def operational_vs_control(control_rows: List[Mapping[str, Any]], operational_rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    c = {(row["algorithm"], int(row["formal_seed"]), int(row["time_slots"])): row for row in control_rows}
    o = {(row["algorithm"], int(row["formal_seed"]), int(row["time_slots"])): row for row in operational_rows}
    result = []
    for algorithm in ALGORITHMS:
        for time_slots in TIME_SLOTS:
            diffs = []
            for seed in range(10):
                key = (algorithm, seed, time_slots)
                if key not in c or key not in o:
                    continue
                diffs.append(float(c[key]["accepted_flow_count"]) - float(o[key]["accepted_flow_count"]))
            result.append({
                "algorithm": algorithm,
                "time_slots": time_slots,
                "seed_count": len(diffs),
                "mean_control_minus_operational_accepted_flow_count": mean(diffs),
                "median_control_minus_operational_accepted_flow_count": median(diffs),
                "min_control_minus_operational_accepted_flow_count": min(diffs) if diffs else None,
                "max_control_minus_operational_accepted_flow_count": max(diffs) if diffs else None,
            })
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create V3 Same-flow Temporal Control comparison outputs.")
    parser.add_argument("--new-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--reuse-manifest-json", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-git-commit")
    parser.add_argument("--expected-plan-fingerprint")
    parser.add_argument("--operational-temporal-runs", type=Path, default=Path("outputs/temporal_scalability_v3_160_0caacc9/temporal_scalability_runs.jsonl"))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    audit_result = audit(args.new_dir, args.config, args.reuse_manifest_json, args.expected_git_commit, args.expected_plan_fingerprint)
    if not audit_result["pass"]:
        raise RuntimeError("Same-flow Temporal Control audit failed; refusing to create outputs.")
    ensure_output_dir(args.output_dir, args.overwrite)
    new_rows = [normalized_new_row(row) for row in read_jsonl(args.new_dir / "runs.jsonl") if is_resume_skippable_record(row)]
    reuse_rows = [normalized_reuse_row(row) for row in read_json(args.reuse_manifest_json).get("rows", [])]
    rows = sorted(new_rows + reuse_rows, key=evidence_key)
    public_rows = [public_row(row) for row in rows]
    summary_rows = summarize(rows)
    paired_acceptance_rows, paired_runtime_rows = paired_by_horizon(rows)
    complexity_rows = complexity_summary(rows)
    operational_rows = operational_vs_control(rows, load_operational_rows(args.operational_temporal_runs))

    write_jsonl(args.output_dir / "same_flow_temporal_control_runs.jsonl", public_rows)
    write_csv(args.output_dir / "same_flow_temporal_control_seed_level.csv", public_rows, list(public_rows[0].keys()))
    write_json_atomic(args.output_dir / "same_flow_temporal_control_seed_level.json", public_rows)
    write_csv(args.output_dir / "same_flow_temporal_control_summary.csv", summary_rows, list(summary_rows[0].keys()))
    write_json_atomic(args.output_dir / "same_flow_temporal_control_summary.json", summary_rows)
    t6_acceptance_rows = [row for row in paired_acceptance_rows if row["baseline_time_slots"] == 6]
    write_csv(args.output_dir / "same_flow_temporal_control_pairwise_vs_t6.csv", t6_acceptance_rows, list(t6_acceptance_rows[0].keys()))
    write_json_atomic(args.output_dir / "same_flow_temporal_control_pairwise_vs_t6.json", t6_acceptance_rows)
    write_csv(args.output_dir / "same_flow_temporal_control_pairwise_acceptance.csv", paired_acceptance_rows, list(paired_acceptance_rows[0].keys()))
    write_json_atomic(args.output_dir / "same_flow_temporal_control_pairwise_acceptance.json", paired_acceptance_rows)
    write_csv(args.output_dir / "same_flow_temporal_control_pairwise_runtime.csv", paired_runtime_rows, list(paired_runtime_rows[0].keys()))
    write_json_atomic(args.output_dir / "same_flow_temporal_control_pairwise_runtime.json", paired_runtime_rows)
    write_csv(args.output_dir / "same_flow_temporal_control_complexity_summary.csv", complexity_rows, list(complexity_rows[0].keys()))
    write_json_atomic(args.output_dir / "same_flow_temporal_control_complexity_summary.json", complexity_rows)
    write_csv(args.output_dir / "same_flow_vs_operational_temporal_summary.csv", operational_rows, list(operational_rows[0].keys()))
    write_json_atomic(args.output_dir / "same_flow_vs_operational_temporal_summary.json", operational_rows)
    write_json_atomic(args.output_dir / "same_flow_temporal_control_audit_summary.json", audit_result)
    manifest = {
        "manifest_type": "same_flow_temporal_control_v3_comparison",
        "created_at": utc_now(),
        "record_count": len(rows),
        "new_count": len(new_rows),
        "reused_count": len(reuse_rows),
        "time_slots": list(TIME_SLOTS),
        "algorithms": list(ALGORITHMS),
        "plan_fingerprint": audit_result["plan_fingerprint"],
        "same_flow_temporal_control_runs_sha256": sha256_fingerprint(public_rows),
        "audit_pass": audit_result["pass"],
    }
    write_json_atomic(args.output_dir / "same_flow_temporal_control_manifest.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
