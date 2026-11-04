"""Create solver-free derived outputs for the V3 parallel-pricing study."""

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
from scripts.audit_formal_parallel_pricing_v3 import ALGORITHMS, WORKERS, audit, evidence_key, normalized_row, read_jsonl, seed_key


def ensure_output_dir(path: Path, overwrite: bool) -> None:
    if path.exists() and any(path.iterdir()) and not overwrite:
        raise ValueError("Output directory is nonempty; choose a new directory or pass --overwrite.")
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


def median(values: List[float]) -> float | None:
    return None if not values else float(statistics.median(values))


def mean(values: List[float]) -> float | None:
    return None if not values else float(statistics.mean(values))


def ratio(numerator: Any, denominator: Any) -> float | None:
    if numerator in (None, 0) or denominator in (None, 0):
        return None
    return float(numerator) / float(denominator)


def censored(row: Mapping[str, Any]) -> bool:
    return bool(row.get("hard_wall_clock_limit_reached"))


def speedup_row(baseline: Mapping[str, Any], current: Mapping[str, Any], metric: str, worker_count: int) -> Dict[str, Any]:
    baseline_censored = censored(baseline)
    current_censored = censored(current)
    eligible = not baseline_censored and not current_censored
    value = ratio(baseline.get(metric), current.get(metric)) if eligible else None
    return {
        "scale": baseline["scale"],
        "time_slots": baseline["time_slots"],
        "load_label": baseline["load_label"],
        "formal_seed": baseline["formal_seed"],
        "baseline_variant_id": "W1",
        "algorithm": baseline["algorithm"],
        "variant_id": current["variant_id"],
        "pricing_worker_count": worker_count,
        "baseline_runtime": baseline.get(metric),
        "variant_runtime": current.get(metric),
        "baseline_censored": baseline_censored,
        "variant_censored": current_censored,
        "ordinary_speedup_eligible": eligible,
        "speedup": value,
        "parallel_efficiency": None if value is None else value / worker_count,
    }


def public_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "scale": row["scale"],
        "time_slots": row["time_slots"],
        "load_label": row["load_label"],
        "formal_seed": row["formal_seed"],
        "algorithm": row["algorithm"],
        "variant_id": row["variant_id"],
        "pricing_worker_count": row["pricing_worker_count"],
        "parallel_pricing_enabled": row["parallel_pricing_enabled"],
        "pricing_strategy": row["pricing_strategy"],
        "hybrid_switch_iteration": row["hybrid_switch_iteration"],
        "topology_fingerprint": row["topology_fingerprint"],
        "base_flow_fingerprint": row["base_flow_fingerprint"],
        "full_instance_fingerprint": row["full_instance_fingerprint"],
        "accepted_flow_count": row["accepted_flow_count"],
        "acceptance_rate": row["acceptance_rate"],
        "total_end_to_end_runtime_seconds": row["total_end_to_end_runtime_seconds"],
        "total_pricing_runtime": row["total_pricing_runtime"],
        "exact_pricing_runtime": row["exact_pricing_runtime"],
        "final_rmp_runtime_seconds": row["final_rmp_runtime_seconds"],
        "cg_iteration_count": row["cg_iteration_count"],
        "logical_pricing_task_count": row["logical_pricing_task_count"],
        "exact_pricing_budget_used": row["exact_pricing_budget_used"],
        "exact_pricing_call_count": row["exact_pricing_call_count"],
        "generated_column_count": row["generated_column_count"],
        "inserted_column_count": row["inserted_column_count"],
        "final_column_pool_size": row["final_column_pool_size"],
        "termination_status": row["termination_status"],
        "final_integer_rmp_status": row["final_integer_rmp_status"],
        "hard_wall_clock_limit_reached": row["hard_wall_clock_limit_reached"],
        "path_validity": row["path_validity"],
        "capacity_validity": row["capacity_validity"],
        "runtime_accounting_valid": row["runtime_accounting_valid"],
    }


def summary(rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["algorithm"]), str(row["variant_id"]))].append(row)
    result = []
    for (algorithm, variant), group in sorted(groups.items(), key=lambda item: (ALGORITHMS.index(item[0][0]), WORKERS[item[0][1]])):
        def values(name: str) -> List[float]:
            return [float(row[name]) for row in group if row.get(name) is not None]
        result.append({
            "variant_id": variant,
            "algorithm": algorithm,
            "pricing_worker_count": WORKERS[variant],
            "record_count": len(group),
            "hard_wall_count": sum(1 for row in group if row.get("hard_wall_clock_limit_reached") is True),
            "median_e2e_runtime_seconds": median(values("total_end_to_end_runtime_seconds")),
            "mean_e2e_runtime_seconds": mean(values("total_end_to_end_runtime_seconds")),
            "median_pricing_runtime_seconds": median(values("total_pricing_runtime")),
            "mean_pricing_runtime_seconds": mean(values("total_pricing_runtime")),
            "median_exact_pricing_runtime_seconds": median(values("exact_pricing_runtime")),
            "median_final_rmp_runtime_seconds": median(values("final_rmp_runtime_seconds")),
            "median_accepted_flow_count": median(values("accepted_flow_count")),
            "median_logical_pricing_task_count": median(values("logical_pricing_task_count")),
            "median_exact_pricing_budget_used": median(values("exact_pricing_budget_used")),
            "median_final_column_pool_size": median(values("final_column_pool_size")),
        })
    return result


def pairwise(rows: List[Mapping[str, Any]], metric: str, algorithm: str) -> List[Dict[str, Any]]:
    by_scenario: Dict[Tuple[str, int, str, int], Dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        if row["algorithm"] == algorithm:
            by_scenario[seed_key(row)][row["variant_id"]] = row
    result = []
    for scenario, group in sorted(by_scenario.items()):
        if "W1" not in group:
            continue
        for variant in ("W2", "W4"):
            if variant in group:
                result.append(speedup_row(group["W1"], group[variant], metric, WORKERS[variant]))
    return result


def equivalence_summary(rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    by_scenario: Dict[Tuple[str, int, str, int, str], Dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        scenario = (*seed_key(row), str(row["algorithm"]))
        by_scenario[scenario][row["variant_id"]] = row
    exact_fields = (
        "topology_fingerprint",
        "base_flow_fingerprint",
        "full_instance_fingerprint",
        "pricing_strategy",
        "hybrid_switch_iteration",
        "logical_pricing_task_count",
        "exact_pricing_budget_used",
        "exact_pricing_call_count",
        "deterministic_work_budget_exhausted",
    )
    result = []
    for scenario, group in sorted(by_scenario.items()):
        result.append({
            "scale": scenario[0],
            "time_slots": scenario[1],
            "load_label": scenario[2],
            "formal_seed": scenario[3],
            "algorithm": scenario[4],
            "worker_variants_present": ",".join(sorted(group)),
            "required_exact_equivalence": all(len({group[v].get(field) for v in group}) == 1 for field in exact_fields),
            "accepted_flow_values": ",".join(str(group[v].get("accepted_flow_count")) for v in sorted(group)),
            "final_column_pool_sizes": ",".join(str(group[v].get("final_column_pool_size")) for v in sorted(group)),
            "termination_statuses": ",".join(str(group[v].get("termination_status")) for v in sorted(group)),
            "hard_wall_count": sum(1 for row in group.values() if row.get("hard_wall_clock_limit_reached") is True),
        })
    return result


def speedup_summary(pair_rows: List[Mapping[str, Any]], algorithm: str) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in pair_rows:
        groups[str(row["variant_id"])].append(row)
    result = []
    for variant, group in sorted(groups.items(), key=lambda item: WORKERS[item[0]]):
        eligible = [float(row["speedup"]) for row in group if row.get("ordinary_speedup_eligible") and row.get("speedup") is not None]
        result.append({
            "variant_id": variant,
            "algorithm": algorithm,
            "pricing_worker_count": WORKERS[variant],
            "paired_run_count": len(group),
            "ordinary_speedup_eligible_count": len(eligible),
            "censored_pair_count": len(group) - len(eligible),
            "median_speedup": median(eligible),
            "mean_speedup": mean(eligible),
            "min_speedup": min(eligible) if eligible else None,
            "max_speedup": max(eligible) if eligible else None,
            "median_parallel_efficiency": None if not eligible else median(eligible) / WORKERS[variant],
        })
    return result


def cross_algorithm_summary(rows: List[Mapping[str, Any]], pure_e2e, pro_e2e, pure_pricing, pro_pricing) -> List[Dict[str, Any]]:
    summaries = []
    for metric_name, pure_rows, pro_rows in (
        ("e2e_speedup", pure_e2e, pro_e2e),
        ("pricing_speedup", pure_pricing, pro_pricing),
    ):
        for variant in ("W2", "W4"):
            pure_values = [float(row["speedup"]) for row in pure_rows if row["variant_id"] == variant and row.get("ordinary_speedup_eligible") and row.get("speedup") is not None]
            pro_values = [float(row["speedup"]) for row in pro_rows if row["variant_id"] == variant and row.get("ordinary_speedup_eligible") and row.get("speedup") is not None]
            summaries.append({
                "metric": metric_name,
                "variant_id": variant,
                "pricing_worker_count": WORKERS[variant],
                "pure_eligible_count": len(pure_values),
                "pro_eligible_count": len(pro_values),
                "pure_median": median(pure_values),
                "pro_median": median(pro_values),
                "pro_minus_pure_median": None if not pure_values or not pro_values else median(pro_values) - median(pure_values),
            })
    return summaries


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create V3 parallel-pricing comparison outputs.")
    parser.add_argument("--new-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-git-commit")
    parser.add_argument("--expected-plan-fingerprint")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    audit_result = audit(args.new_dir, args.config, args.expected_git_commit, args.expected_plan_fingerprint)
    if not audit_result["pass"]:
        raise RuntimeError("Parallel-pricing audit failed; refusing to create comparison outputs.")
    ensure_output_dir(args.output_dir, args.overwrite)
    rows = sorted([normalized_row(row) for row in read_jsonl(args.new_dir / "runs.jsonl")], key=evidence_key)
    public_rows = [public_row(row) for row in rows]
    summary_rows = summary(rows)
    pure_e2e_rows = pairwise(rows, "total_end_to_end_runtime_seconds", "dwcg_pure")
    pure_pricing_rows = pairwise(rows, "total_pricing_runtime", "dwcg_pure")
    pro_e2e_rows = pairwise(rows, "total_end_to_end_runtime_seconds", "dwcg_saginfe_pro")
    pro_pricing_rows = pairwise(rows, "total_pricing_runtime", "dwcg_saginfe_pro")
    equivalence_rows = equivalence_summary(rows)
    cross_rows = cross_algorithm_summary(rows, pure_e2e_rows, pro_e2e_rows, pure_pricing_rows, pro_pricing_rows)
    pure_e2e_summary = speedup_summary(pure_e2e_rows, "dwcg_pure")
    pure_pricing_summary = speedup_summary(pure_pricing_rows, "dwcg_pure")
    pro_e2e_summary = speedup_summary(pro_e2e_rows, "dwcg_saginfe_pro")
    pro_pricing_summary = speedup_summary(pro_pricing_rows, "dwcg_saginfe_pro")

    write_jsonl(args.output_dir / "parallel_pricing_runs.jsonl", public_rows)
    seed_fields = list(public_rows[0]) if public_rows else []
    write_csv(args.output_dir / "parallel_pricing_seed_level.csv", public_rows, seed_fields)
    write_json_atomic(args.output_dir / "parallel_pricing_seed_level.json", public_rows)
    summary_fields = list(summary_rows[0]) if summary_rows else []
    write_csv(args.output_dir / "parallel_pricing_summary.csv", summary_rows, summary_fields)
    write_json_atomic(args.output_dir / "parallel_pricing_summary.json", summary_rows)
    pair_fields = list(pure_e2e_rows[0]) if pure_e2e_rows else []
    write_csv(args.output_dir / "pure_pairwise_e2e_speedup.csv", pure_e2e_rows, pair_fields)
    write_json_atomic(args.output_dir / "pure_pairwise_e2e_speedup.json", pure_e2e_rows)
    write_csv(args.output_dir / "pure_pairwise_pricing_speedup.csv", pure_pricing_rows, pair_fields)
    write_json_atomic(args.output_dir / "pure_pairwise_pricing_speedup.json", pure_pricing_rows)
    write_csv(args.output_dir / "pro_pairwise_e2e_speedup.csv", pro_e2e_rows, pair_fields)
    write_json_atomic(args.output_dir / "pro_pairwise_e2e_speedup.json", pro_e2e_rows)
    write_csv(args.output_dir / "pro_pairwise_pricing_speedup.csv", pro_pricing_rows, pair_fields)
    write_json_atomic(args.output_dir / "pro_pairwise_pricing_speedup.json", pro_pricing_rows)
    eq_fields = list(equivalence_rows[0]) if equivalence_rows else []
    write_csv(args.output_dir / "parallel_pricing_equivalence_summary.csv", equivalence_rows, eq_fields)
    write_json_atomic(args.output_dir / "parallel_pricing_equivalence_summary.json", equivalence_rows)
    cross_fields = list(cross_rows[0]) if cross_rows else []
    write_csv(args.output_dir / "parallel_pricing_cross_algorithm_summary.csv", cross_rows, cross_fields)
    write_json_atomic(args.output_dir / "parallel_pricing_cross_algorithm_summary.json", cross_rows)
    write_json_atomic(args.output_dir / "parallel_pricing_speedup_summary.json", {
        "pure_e2e": pure_e2e_summary,
        "pure_pricing": pure_pricing_summary,
        "pro_e2e": pro_e2e_summary,
        "pro_pricing": pro_pricing_summary,
    })
    write_json_atomic(args.output_dir / "parallel_pricing_audit_summary.json", audit_result)
    manifest = {
        "manifest_type": "parallel_pricing_v3_comparison",
        "created_at": utc_now(),
        "new_source_dir": str(args.new_dir),
        "record_count": len(rows),
        "new_count": len(rows),
        "reused_count": 0,
        "scenario_count": len({(*seed_key(row), row["algorithm"]) for row in rows}),
        "seed_scenario_count": len({seed_key(row) for row in rows}),
        "algorithms": list(ALGORITHMS),
        "worker_variants": list(WORKERS),
        "plan_fingerprint": audit_result["plan_fingerprint"],
        "parallel_pricing_runs_sha256": sha256_fingerprint(public_rows),
        "audit_pass": audit_result["pass"],
    }
    write_json_atomic(args.output_dir / "parallel_pricing_manifest.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
