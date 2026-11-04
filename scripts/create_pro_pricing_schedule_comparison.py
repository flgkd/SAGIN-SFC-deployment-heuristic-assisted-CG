"""Create solver-free derived outputs for the matched V3 PRO pricing-schedule study."""

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
from scripts.audit_formal_pro_pricing_schedule_v3 import (
    LOADS,
    SCHEDULES,
    audit,
    evidence_key,
    normalized_new_row,
    normalized_reuse_row,
    read_json,
    read_jsonl,
    scenario_key,
)
from formal_runner import is_resume_skippable_record


STRATEGY_PAIRS = (("EX", "ESV"), ("EX", "HYB-S5"), ("ESV", "HYB-S5"))
SWITCH_PAIRS = (("HYB-S2", "HYB-S5"), ("HYB-S2", "HYB-S7"), ("HYB-S5", "HYB-S7"))


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


def public_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "scale": row["scale"],
        "time_slots": row["time_slots"],
        "load_label": row["load_label"],
        "formal_seed": row["formal_seed"],
        "algorithm": row["algorithm"],
        "pricing_schedule": row["pricing_schedule"],
        "source_kind": row["source_kind"],
        "reuse_classification": row["reuse_classification"],
        "source_commit": row.get("source_commit") or row.get("git_commit"),
        "source_tag": row.get("source_tag"),
        "topology_fingerprint": row["topology_fingerprint"],
        "base_flow_fingerprint": row["base_flow_fingerprint"],
        "full_instance_fingerprint": row["full_instance_fingerprint"],
        "pricing_strategy": row["pricing_strategy"],
        "hybrid_switch_iteration": row["hybrid_switch_iteration"],
        "accepted_flow_count": row["accepted_flow_count"],
        "acceptance_rate": row["acceptance_rate"],
        "total_end_to_end_runtime_seconds": row["total_end_to_end_runtime_seconds"],
        "total_pricing_runtime": row["total_pricing_runtime"],
        "exact_pricing_runtime": row.get("exact_pricing_runtime"),
        "final_rmp_runtime_seconds": row.get("final_rmp_runtime_seconds"),
        "cg_iteration_count": row.get("cg_iteration_count"),
        "logical_pricing_task_count": row.get("logical_pricing_task_count"),
        "exact_pricing_budget_used": row.get("exact_pricing_budget_used"),
        "generated_column_count": row.get("generated_column_count"),
        "inserted_column_count": row.get("inserted_column_count"),
        "final_column_pool_size": row.get("final_column_pool_size"),
        "final_ordered_column_pool_fingerprint": row.get("final_ordered_column_pool_fingerprint"),
        "selective_pfe_enabled": row.get("selective_pfe_enabled"),
        "selective_pfe_call_count": row.get("selective_pfe_call_count"),
        "selective_pfe_candidate_count": row.get("selective_pfe_candidate_count"),
        "selective_pfe_round_count": row.get("selective_pfe_round_count"),
        "selective_pfe_positive_column_count": row.get("selective_pfe_positive_column_count"),
        "selective_pfe_runtime_seconds": row.get("selective_pfe_runtime_seconds"),
        "early_stop_phase_count": row.get("early_stop_phase_count"),
        "strict_exact_phase_count": row.get("strict_exact_phase_count"),
        "initial_saginfe_accepted_flow_count": row.get("initial_saginfe_accepted_flow_count"),
        "final_acceptance_gain_over_saginfe": row.get("final_acceptance_gain_over_saginfe"),
        "initialization_runtime_seconds": row.get("initialization_runtime_seconds"),
        "initialization_generation_runtime_seconds": row.get("initialization_generation_runtime_seconds"),
        "final_integer_rmp_status": row.get("final_integer_rmp_status"),
        "exact_pricing_call_count": row["exact_pricing_call_count"],
        "early_stop_pricing_call_count": row["early_stop_pricing_call_count"],
        "strict_exact_pricing_call_count": row["strict_exact_pricing_call_count"],
        "termination_status": row["termination_status"],
        "path_validity": row["path_validity"],
        "capacity_validity": row["capacity_validity"],
        "runtime_accounting_valid": row["runtime_accounting_valid"],
    }


def summarize(rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["load_label"], row["pricing_schedule"])].append(row)
    result = []
    for (load, schedule), group in sorted(groups.items()):
        accepted = [float(row["accepted_flow_count"]) for row in group if row["accepted_flow_count"] is not None]
        acceptance_rate = [float(row["acceptance_rate"]) for row in group if row["acceptance_rate"] is not None]
        e2e = [float(row["total_end_to_end_runtime_seconds"]) for row in group if row["total_end_to_end_runtime_seconds"] is not None]
        pricing = [float(row["total_pricing_runtime"]) for row in group if row["total_pricing_runtime"] is not None]
        result.append({
            "load_label": load,
            "pricing_schedule": schedule,
            "record_count": len(group),
            "mean_accepted_flow_count": mean(accepted),
            "median_accepted_flow_count": median(accepted),
            "mean_acceptance_rate": mean(acceptance_rate),
            "median_acceptance_rate": median(acceptance_rate),
            "median_e2e_runtime_seconds": median(e2e),
            "mean_e2e_runtime_seconds": mean(e2e),
            "median_pricing_runtime_seconds": median(pricing),
            "mean_pricing_runtime_seconds": mean(pricing),
            "median_exact_pricing_runtime_seconds": median([float(row["exact_pricing_runtime"]) for row in group if row.get("exact_pricing_runtime") is not None]),
            "median_cg_iteration_count": median([float(row["cg_iteration_count"]) for row in group if row.get("cg_iteration_count") is not None]),
            "median_generated_column_count": median([float(row["generated_column_count"]) for row in group if row.get("generated_column_count") is not None]),
            "median_inserted_column_count": median([float(row["inserted_column_count"]) for row in group if row.get("inserted_column_count") is not None]),
            "median_final_column_pool_size": median([float(row["final_column_pool_size"]) for row in group if row.get("final_column_pool_size") is not None]),
            "median_selective_pfe_call_count": median([float(row["selective_pfe_call_count"]) for row in group if row.get("selective_pfe_call_count") is not None]),
            "median_selective_pfe_candidate_count": median([float(row["selective_pfe_candidate_count"]) for row in group if row.get("selective_pfe_candidate_count") is not None]),
            "median_selective_pfe_round_count": median([float(row["selective_pfe_round_count"]) for row in group if row.get("selective_pfe_round_count") is not None]),
            "median_selective_pfe_positive_column_count": median([float(row["selective_pfe_positive_column_count"]) for row in group if row.get("selective_pfe_positive_column_count") is not None]),
            "median_selective_pfe_runtime_seconds": median([float(row["selective_pfe_runtime_seconds"]) for row in group if row.get("selective_pfe_runtime_seconds") is not None]),
            "deterministic_work_budget_exhausted_count": sum(1 for row in group if row["termination_status"] == "DETERMINISTIC_WORK_BUDGET_EXHAUSTED"),
            "hard_wall_count": sum(1 for row in group if row["hard_wall_clock_limit_reached"] is True),
        })
    return result


def paired(rows: List[Mapping[str, Any]], pairs: Tuple[Tuple[str, str], ...]) -> List[Dict[str, Any]]:
    by_scenario: Dict[Tuple[str, int, str, int], Dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        by_scenario[scenario_key(row)][row["pricing_schedule"]] = row
    result = []
    for load in LOADS:
        for left, right in pairs:
            diffs: List[float] = []
            runtime_ratios: List[float] = []
            pricing_ratios: List[float] = []
            seeds = []
            for scenario, group in sorted(by_scenario.items()):
                if scenario[2] != load or left not in group or right not in group:
                    continue
                left_row, right_row = group[left], group[right]
                diff = float(left_row["accepted_flow_count"]) - float(right_row["accepted_flow_count"])
                diffs.append(diff)
                seeds.append(scenario[3])
                runtime_value = ratio(left_row["total_end_to_end_runtime_seconds"], right_row["total_end_to_end_runtime_seconds"])
                pricing_value = ratio(left_row["total_pricing_runtime"], right_row["total_pricing_runtime"])
                if runtime_value is not None:
                    runtime_ratios.append(runtime_value)
                if pricing_value is not None:
                    pricing_ratios.append(pricing_value)
            result.append({
                "load_label": load,
                "schedule_a": left,
                "schedule_b": right,
                "seed_count": len(seeds),
                "wins": sum(1 for value in diffs if value > 0),
                "ties": sum(1 for value in diffs if value == 0),
                "losses": sum(1 for value in diffs if value < 0),
                "net_accepted_flow_difference": sum(diffs),
                "mean_paired_accepted_flow_difference": mean(diffs),
                "median_paired_accepted_flow_difference": median(diffs),
                "median_e2e_runtime_ratio_a_over_b": median(runtime_ratios),
                "median_pricing_runtime_ratio_a_over_b": median(pricing_ratios),
                "seeds": ",".join(str(seed) for seed in seeds),
            })
    return result


def complexity(rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["load_label"], row["pricing_schedule"])].append(row)
    result = []
    for (load, schedule), group in sorted(groups.items()):
        def values(name: str) -> List[float]:
            return [float(row[name]) for row in group if row.get(name) is not None]
        result.append({
            "load_label": load,
            "pricing_schedule": schedule,
            "record_count": len(group),
            "median_exact_pricing_call_count": median(values("exact_pricing_call_count")),
            "median_early_stop_pricing_call_count": median(values("early_stop_pricing_call_count")),
            "median_strict_exact_pricing_call_count": median(values("strict_exact_pricing_call_count")),
            "median_total_pricing_runtime": median(values("total_pricing_runtime")),
            "median_total_end_to_end_runtime_seconds": median(values("total_end_to_end_runtime_seconds")),
        })
    return result


def load_pure_rows(path: Path) -> List[Dict[str, Any]]:
    rows = []
    for row in read_jsonl(path):
        if row.get("algorithm") != "dwcg_pure":
            continue
        rows.append({
            "scale": row.get("scale"),
            "time_slots": int(row.get("time_slots", 0)),
            "load_label": row.get("load_label"),
            "formal_seed": int(row.get("formal_seed", -1)),
            "algorithm": row.get("algorithm"),
            "pricing_schedule": row.get("pricing_schedule"),
            "accepted_flow_count": row.get("accepted_flow_count"),
            "acceptance_rate": row.get("acceptance_rate"),
            "total_end_to_end_runtime_seconds": row.get("total_end_to_end_runtime_seconds"),
            "total_pricing_runtime": row.get("total_pricing_runtime"),
            "exact_pricing_call_count": row.get("exact_pricing_call_count"),
            "early_stop_pricing_call_count": row.get("early_stop_pricing_call_count"),
            "strict_exact_pricing_call_count": row.get("strict_exact_pricing_call_count"),
            "topology_fingerprint": row.get("topology_fingerprint"),
            "base_flow_fingerprint": row.get("base_flow_fingerprint"),
            "full_instance_fingerprint": row.get("full_instance_fingerprint"),
        })
    return rows


def pure_pro_interaction(rows: List[Mapping[str, Any]], pure_rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    pro_by_key = {
        (row["load_label"], int(row["formal_seed"]), row["pricing_schedule"]): row
        for row in rows
    }
    pure_by_key = {
        (row["load_label"], int(row["formal_seed"]), row["pricing_schedule"]): row
        for row in pure_rows
    }
    paired_rows = []
    for key in sorted(set(pro_by_key) & set(pure_by_key)):
        pro = pro_by_key[key]
        pure = pure_by_key[key]
        paired_rows.append({
            "load_label": key[0],
            "formal_seed": key[1],
            "pricing_schedule": key[2],
            "instance_fingerprint_match": (
                pro.get("topology_fingerprint") == pure.get("topology_fingerprint")
                and pro.get("base_flow_fingerprint") == pure.get("base_flow_fingerprint")
                and pro.get("full_instance_fingerprint") == pure.get("full_instance_fingerprint")
            ),
            "pure_accepted_flow_count": pure.get("accepted_flow_count"),
            "pro_accepted_flow_count": pro.get("accepted_flow_count"),
            "pro_minus_pure_accepted_flow_count": (
                None
                if pure.get("accepted_flow_count") is None or pro.get("accepted_flow_count") is None
                else float(pro["accepted_flow_count"]) - float(pure["accepted_flow_count"])
            ),
            "pure_e2e_runtime_seconds": pure.get("total_end_to_end_runtime_seconds"),
            "pro_e2e_runtime_seconds": pro.get("total_end_to_end_runtime_seconds"),
            "pro_over_pure_e2e_runtime_ratio": ratio(pro.get("total_end_to_end_runtime_seconds"), pure.get("total_end_to_end_runtime_seconds")),
            "pure_pricing_runtime_seconds": pure.get("total_pricing_runtime"),
            "pro_pricing_runtime_seconds": pro.get("total_pricing_runtime"),
            "pro_over_pure_pricing_runtime_ratio": ratio(pro.get("total_pricing_runtime"), pure.get("total_pricing_runtime")),
        })
    groups: Dict[Tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for row in paired_rows:
        groups[(row["load_label"], row["pricing_schedule"])].append(row)
    result = []
    for (load, schedule), group in sorted(groups.items()):
        diffs = [float(row["pro_minus_pure_accepted_flow_count"]) for row in group if row["pro_minus_pure_accepted_flow_count"] is not None]
        e2e_ratios = [float(row["pro_over_pure_e2e_runtime_ratio"]) for row in group if row["pro_over_pure_e2e_runtime_ratio"] is not None]
        pricing_ratios = [float(row["pro_over_pure_pricing_runtime_ratio"]) for row in group if row["pro_over_pure_pricing_runtime_ratio"] is not None]
        result.append({
            "load_label": load,
            "pricing_schedule": schedule,
            "seed_count": len(group),
            "instance_fingerprint_match_count": sum(1 for row in group if row["instance_fingerprint_match"]),
            "mean_pro_minus_pure_accepted_flow_count": mean(diffs),
            "median_pro_minus_pure_accepted_flow_count": median(diffs),
            "min_pro_minus_pure_accepted_flow_count": min(diffs) if diffs else None,
            "max_pro_minus_pure_accepted_flow_count": max(diffs) if diffs else None,
            "median_pro_over_pure_e2e_runtime_ratio": median(e2e_ratios),
            "median_pro_over_pure_pricing_runtime_ratio": median(pricing_ratios),
        })
    return result


def esv_behavior(rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    result = []
    for row in sorted((row for row in rows if row["pricing_schedule"] == "ESV"), key=evidence_key):
        initial = row.get("initial_saginfe_accepted_flow_count")
        final = row.get("accepted_flow_count")
        result.append({
            "load_label": row["load_label"],
            "formal_seed": row["formal_seed"],
            "initial_saginfe_accepted_flow_count": initial,
            "final_esv_accepted_flow_count": final,
            "final_minus_initial_acceptance_gain": None if initial is None or final is None else int(final) - int(initial),
            "selective_pfe_positive_column_count": row.get("selective_pfe_positive_column_count"),
            "early_stop_pricing_call_count": row.get("early_stop_pricing_call_count"),
            "strict_exact_pricing_call_count": row.get("strict_exact_pricing_call_count"),
            "strict_fallback_triggered": bool(row.get("strict_exact_pricing_call_count") or 0),
            "final_integer_rmp_status": row.get("final_integer_rmp_status"),
            "final_rmp_runtime_seconds": row.get("final_rmp_runtime_seconds"),
        })
    return result


def runtime_decomposition(rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["load_label"], row["pricing_schedule"])].append(row)
    result = []
    for (load, schedule), group in sorted(groups.items()):
        def values(name: str) -> List[float]:
            return [float(row[name]) for row in group if row.get(name) is not None]

        residuals = []
        for row in group:
            e2e = row.get("total_end_to_end_runtime_seconds")
            pricing = row.get("total_pricing_runtime")
            pfe = row.get("selective_pfe_runtime_seconds") or 0.0
            warm = row.get("initialization_runtime_seconds")
            final_rmp = row.get("final_rmp_runtime_seconds") or 0.0
            if e2e is not None and pricing is not None and warm is not None:
                residuals.append(float(e2e) - float(pricing) - float(pfe) - float(warm) - float(final_rmp))
        result.append({
            "load_label": load,
            "pricing_schedule": schedule,
            "record_count": len(group),
            "mean_e2e_runtime_seconds": mean(values("total_end_to_end_runtime_seconds")),
            "median_e2e_runtime_seconds": median(values("total_end_to_end_runtime_seconds")),
            "mean_primary_pricing_runtime_seconds": mean(values("total_pricing_runtime")),
            "median_primary_pricing_runtime_seconds": median(values("total_pricing_runtime")),
            "mean_selective_pfe_runtime_seconds": mean(values("selective_pfe_runtime_seconds")),
            "median_selective_pfe_runtime_seconds": median(values("selective_pfe_runtime_seconds")),
            "mean_warm_initialization_runtime_seconds": mean(values("initialization_runtime_seconds")),
            "median_warm_initialization_runtime_seconds": median(values("initialization_runtime_seconds")),
            "mean_final_rmp_runtime_seconds": mean(values("final_rmp_runtime_seconds")),
            "median_final_rmp_runtime_seconds": median(values("final_rmp_runtime_seconds")),
            "mean_recorded_residual_serial_runtime_seconds": mean(residuals),
            "median_recorded_residual_serial_runtime_seconds": median(residuals),
        })
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create V3 PRO pricing-schedule comparison outputs.")
    parser.add_argument("--new-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--reuse-manifest-json", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-git-commit")
    parser.add_argument("--expected-plan-fingerprint")
    parser.add_argument("--pure-reference-runs", type=Path, default=Path("outputs/pricing_schedule_v3_100_7cee2fe/pricing_schedule_runs.jsonl"))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    audit_result = audit(args.new_dir, args.config, args.reuse_manifest_json, args.expected_git_commit, args.expected_plan_fingerprint, args.pure_reference_runs)
    if not audit_result["pass"]:
        raise RuntimeError("Pricing-schedule audit failed; refusing to create comparison outputs.")
    ensure_output_dir(args.output_dir, args.overwrite)
    new_rows = [normalized_new_row(row) for row in read_jsonl(args.new_dir / "runs.jsonl") if is_resume_skippable_record(row)]
    reuse_rows = [normalized_reuse_row(row) for row in read_json(args.reuse_manifest_json).get("rows", [])]
    rows = sorted(new_rows + reuse_rows, key=evidence_key)
    public_rows = [public_row(row) for row in rows]
    summary_rows = summarize(rows)
    strategy_rows = paired(rows, STRATEGY_PAIRS)
    switch_rows = paired(rows, SWITCH_PAIRS)
    complexity_rows = complexity(rows)
    pure_rows = load_pure_rows(args.pure_reference_runs)
    interaction_rows = pure_pro_interaction(rows, pure_rows)
    esv_rows = esv_behavior(rows)
    runtime_rows = runtime_decomposition(rows)
    write_jsonl(args.output_dir / "pro_pricing_schedule_runs.jsonl", public_rows)
    seed_fields = list(public_rows[0].keys()) if public_rows else []
    write_csv(args.output_dir / "pro_pricing_schedule_seed_level.csv", public_rows, seed_fields)
    write_json_atomic(args.output_dir / "pro_pricing_schedule_seed_level.json", public_rows)
    summary_fields = list(summary_rows[0].keys()) if summary_rows else []
    write_csv(args.output_dir / "pro_pricing_schedule_summary.csv", summary_rows, summary_fields)
    write_json_atomic(args.output_dir / "pro_pricing_schedule_summary.json", summary_rows)
    pair_fields = list(strategy_rows[0].keys()) if strategy_rows else []
    write_csv(args.output_dir / "pro_pricing_strategy_pairwise_acceptance.csv", strategy_rows, pair_fields)
    write_json_atomic(args.output_dir / "pro_pricing_strategy_pairwise_acceptance.json", strategy_rows)
    write_csv(args.output_dir / "pro_pricing_strategy_pairwise_runtime.csv", strategy_rows, pair_fields)
    write_json_atomic(args.output_dir / "pro_pricing_strategy_pairwise_runtime.json", strategy_rows)
    switch_fields = list(switch_rows[0].keys()) if switch_rows else []
    write_csv(args.output_dir / "pro_hybrid_switch_pairwise_acceptance.csv", switch_rows, switch_fields)
    write_json_atomic(args.output_dir / "pro_hybrid_switch_pairwise_acceptance.json", switch_rows)
    write_csv(args.output_dir / "pro_hybrid_switch_pairwise_runtime.csv", switch_rows, switch_fields)
    write_json_atomic(args.output_dir / "pro_hybrid_switch_pairwise_runtime.json", switch_rows)
    complexity_fields = list(complexity_rows[0].keys()) if complexity_rows else []
    write_csv(args.output_dir / "pro_pricing_schedule_complexity_summary.csv", complexity_rows, complexity_fields)
    write_json_atomic(args.output_dir / "pro_pricing_schedule_complexity_summary.json", complexity_rows)
    interaction_fields = list(interaction_rows[0].keys()) if interaction_rows else []
    write_csv(args.output_dir / "pro_pricing_schedule_pure_pro_interaction_summary.csv", interaction_rows, interaction_fields)
    write_json_atomic(args.output_dir / "pro_pricing_schedule_pure_pro_interaction_summary.json", interaction_rows)
    esv_fields = list(esv_rows[0].keys()) if esv_rows else []
    write_csv(args.output_dir / "pro_pricing_schedule_esv_behavior_summary.csv", esv_rows, esv_fields)
    write_json_atomic(args.output_dir / "pro_pricing_schedule_esv_behavior_summary.json", esv_rows)
    runtime_fields = list(runtime_rows[0].keys()) if runtime_rows else []
    write_csv(args.output_dir / "pro_pricing_schedule_runtime_decomposition.csv", runtime_rows, runtime_fields)
    write_json_atomic(args.output_dir / "pro_pricing_schedule_runtime_decomposition.json", runtime_rows)
    write_json_atomic(args.output_dir / "pro_pricing_schedule_audit_summary.json", audit_result)
    manifest = {
        "manifest_type": "pro_pricing_schedule_v3_comparison",
        "created_at": utc_now(),
        "new_source_dir": str(args.new_dir),
        "reuse_manifest_json": str(args.reuse_manifest_json),
        "record_count": len(rows),
        "new_count": len(new_rows),
        "reused_count": len(reuse_rows),
        "scenario_count": len({scenario_key(row) for row in rows}),
        "loads": list(LOADS),
        "pricing_schedules": list(SCHEDULES),
        "plan_fingerprint": audit_result["plan_fingerprint"],
        "pro_pricing_schedule_runs_sha256": sha256_fingerprint(public_rows),
        "audit_pass": audit_result["pass"],
    }
    write_json_atomic(args.output_dir / "pro_pricing_schedule_manifest.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
