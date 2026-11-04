"""Analysis helpers for Phase 4A formal benchmark records."""

from record_compatibility import normalize_record
import json
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from calibration_runner import to_jsonable


ALGORITHM_DISPLAY_ORDER = {
    "saginfe": 0,
    "dwcg_pure": 1,
    "dwcg_saginfe": 2,
    "dwcg_saginfe_pro_no_pfe": 3,
    "dwcg_saginfe_pro": 4,
}


def analyze_formal_records(records: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    rows = [normalize_record(row) for row in records]
    grouped: Dict[Tuple[str, str, str, Optional[str]], List[Mapping[str, Any]]] = {}
    for row in rows:
        key = row["key"]
        grouped.setdefault((key["scale"], key["load_label"], key["algorithm"], key.get("variant_id")), []).append(row)

    summary_rows = []
    for (scale, load_label, algorithm, variant_id), group in sorted(
        grouped.items(),
        key=lambda item: (
            item[0][0],
            item[0][1],
            ALGORITHM_DISPLAY_ORDER.get(item[0][2], 99),
            item[0][2],
            item[0][3] or "",
        ),
    ):
        acceptances = [row.get("acceptance_rate") for row in group if row.get("acceptance_rate") is not None]
        accepted_counts = [row.get("accepted_flow_count") for row in group if row.get("accepted_flow_count") is not None]
        end_to_end_runtimes = [
            row.get("total_end_to_end_runtime_seconds")
            for row in group
            if row.get("total_end_to_end_runtime_seconds") is not None
        ]
        core_runtimes = [
            _algorithm_core_runtime(row)
            for row in group
            if _algorithm_core_runtime(row) is not None
        ]
        summary_rows.append({
            "scale": scale,
            "load_label": load_label,
            "algorithm": algorithm,
            "variant_id": variant_id,
            "variant": _dwcg_metadata_value(group, "variant"),
            "display_name": _dwcg_metadata_value(group, "display_name"),
            "pricing_strategy": _dwcg_metadata_value(group, "pricing_strategy"),
            "pricing_worker_count": _dwcg_metadata_value(group, "pricing_worker_count"),
            "parallel_pricing_enabled": _dwcg_metadata_value(group, "parallel_pricing_enabled"),
            "record_count": len(group),
            "median_accepted_flow_count": _median_or_none(accepted_counts),
            "median_acceptance_rate": _median_or_none(acceptances),
            "mean_acceptance_rate": mean(acceptances) if acceptances else None,
            "median_algorithm_core_runtime_seconds": _median_or_none(core_runtimes),
            "median_total_end_to_end_runtime_seconds": _median_or_none(end_to_end_runtimes),
            "median_runtime_seconds": _median_or_none(end_to_end_runtimes),
            "error_count": sum(1 for row in group if row.get("error_type")),
            "termination_statuses": sorted({str(row.get("termination_status")) for row in group}),
            "path_valid_count": sum(1 for row in group if row.get("path_validity") is True),
            "capacity_valid_count": sum(1 for row in group if row.get("capacity_validity") is True),
            "deterministic_work_budget_exhausted_count": sum(
                1 for row in group if (row.get("dwcg_metadata") or {}).get("deterministic_work_budget_exhausted")
            ),
            "hard_wall_clock_limit_count": sum(
                1 for row in group if (row.get("dwcg_metadata") or {}).get("hard_wall_clock_limit_reached")
            ),
            "internal_phase_limit_count": sum(
                1 for row in group if (row.get("dwcg_metadata") or {}).get("internal_phase_limit_reached")
            ),
            "max_cg_iterations_reached_count": sum(
                1 for row in group if (row.get("dwcg_metadata") or {}).get("max_cg_iterations_reached")
            ),
            "max_exact_pricing_calls_reached_count": sum(
                1 for row in group if (row.get("dwcg_metadata") or {}).get("max_exact_pricing_calls_reached")
            ),
            "runtime_accounting_valid_count": sum(
                1 for row in group
                if _runtime_metadata(row).get("runtime_accounting_valid") is True
            ),
            "median_completed_cg_iterations": _median_or_none([
                (row.get("dwcg_metadata") or {}).get("completed_cg_iteration_count")
                for row in group
                if (row.get("dwcg_metadata") or {}).get("completed_cg_iteration_count") is not None
            ]),
            "median_exact_pricing_calls": _median_or_none([
                (row.get("dwcg_metadata") or {}).get("exact_pricing_call_count")
                for row in group
                if (row.get("dwcg_metadata") or {}).get("exact_pricing_call_count") is not None
            ]),
            "median_strict_exact_pricing_calls": _median_or_none([
                (row.get("dwcg_metadata") or {}).get("strict_exact_pricing_call_count")
                for row in group
                if (row.get("dwcg_metadata") or {}).get("strict_exact_pricing_call_count") is not None
            ]),
            "median_early_stop_pricing_calls": _median_or_none([
                (row.get("dwcg_metadata") or {}).get("early_stop_pricing_call_count")
                for row in group
                if (row.get("dwcg_metadata") or {}).get("early_stop_pricing_call_count") is not None
            ]),
            "median_total_positive_column_count": _median_or_none([
                (row.get("dwcg_metadata") or {}).get("total_positive_column_count")
                for row in group
                if (row.get("dwcg_metadata") or {}).get("total_positive_column_count") is not None
            ]),
            "median_strict_exact_positive_column_count": _median_or_none([
                (row.get("dwcg_metadata") or {}).get("strict_exact_positive_column_count")
                for row in group
                if (row.get("dwcg_metadata") or {}).get("strict_exact_positive_column_count") is not None
            ]),
            "median_early_stop_positive_column_count": _median_or_none([
                (row.get("dwcg_metadata") or {}).get("early_stop_positive_column_count")
                for row in group
                if (row.get("dwcg_metadata") or {}).get("early_stop_positive_column_count") is not None
            ]),
            "median_pricing_runtime": _median_or_none([
                (row.get("dwcg_metadata") or {}).get("total_pricing_runtime")
                for row in group
                if (row.get("dwcg_metadata") or {}).get("total_pricing_runtime") is not None
            ]),
            "median_primary_pricing_runtime_seconds": _median_or_none([
                (row.get("dwcg_metadata") or {}).get("pure_primary_pricing_runtime_seconds")
                for row in group
                if (row.get("dwcg_metadata") or {}).get("pure_primary_pricing_runtime_seconds") is not None
            ]),
            "median_selective_pfe_runtime_seconds": _median_or_none([
                (row.get("dwcg_metadata") or {}).get("selective_pfe_runtime_seconds")
                for row in group
                if (row.get("dwcg_metadata") or {}).get("selective_pfe_runtime_seconds") is not None
            ]),
            "median_combined_pricing_runtime_seconds": _median_or_none([
                (row.get("dwcg_metadata") or {}).get("combined_pricing_runtime_seconds")
                for row in group
                if (row.get("dwcg_metadata") or {}).get("combined_pricing_runtime_seconds") is not None
            ]),
            "median_primary_pricing_unique_column_count": _median_or_none([
                (row.get("dwcg_metadata") or {}).get("primary_pricing_unique_column_count")
                for row in group
                if (row.get("dwcg_metadata") or {}).get("primary_pricing_unique_column_count") is not None
            ]),
            "median_selective_pfe_unique_candidate_count": _median_or_none([
                (row.get("dwcg_metadata") or {}).get("selective_pfe_unique_candidate_count")
                for row in group
                if (row.get("dwcg_metadata") or {}).get("selective_pfe_unique_candidate_count") is not None
            ]),
            "median_combined_pricing_unique_column_count": _median_or_none([
                (row.get("dwcg_metadata") or {}).get("combined_pricing_unique_column_count")
                for row in group
                if (row.get("dwcg_metadata") or {}).get("combined_pricing_unique_column_count") is not None
            ]),
            "median_normalized_physical_cost": _median_or_none([
                row.get("normalized_physical_cost")
                for row in group
                if row.get("normalized_physical_cost") is not None
            ]),
            "median_canonical_objective": _median_or_none([
                row.get("canonical_objective_value")
                for row in group
                if row.get("canonical_objective_value") is not None
            ]),
            "convergence_certification_count": sum(
                1 for row in group if (row.get("dwcg_metadata") or {}).get("convergence_certification")
            ),
            "final_restricted_rmp_optimal_count": sum(
                1 for row in group if (row.get("dwcg_metadata") or {}).get("restricted_pool_integer_optimal")
            ),
            "median_final_column_pool_size": _median_or_none([
                (row.get("dwcg_metadata") or {}).get("final_column_pool_size")
                for row in group
                if (row.get("dwcg_metadata") or {}).get("final_column_pool_size") is not None
            ]),
            "final_ordered_column_pool_fingerprint_count": len({
                (row.get("dwcg_metadata") or {}).get("final_ordered_column_pool_fingerprint")
                for row in group
                if (row.get("dwcg_metadata") or {}).get("final_ordered_column_pool_fingerprint")
            }),
            "error_messages": sorted({
                str(row.get("error_message"))
                for row in group
                if row.get("error_message")
            }),
        })

    return {
        "summary_rows": summary_rows,
        "paired_comparisons": paired_comparisons(rows),
        "parallel_pricing_analysis": parallel_pricing_analysis(rows),
        "pricing_strategy_analysis": pricing_strategy_analysis(rows),
        "comparison_semantics": {
            "primary": "accepted-flow count and acceptance rate",
            "secondary": "physical cost is directly compared only when accepted-flow counts are equal",
        },
    }


def compare_records(primary: Mapping[str, Any], secondary: Mapping[str, Any]) -> Dict[str, Any]:
    accepted_a = primary.get("accepted_flow_count")
    accepted_b = secondary.get("accepted_flow_count")
    objective_a = primary.get("canonical_objective_value")
    objective_b = secondary.get("canonical_objective_value")
    runtime_a = primary.get("total_end_to_end_runtime_seconds")
    runtime_b = secondary.get("total_end_to_end_runtime_seconds")
    acceptance_diff = None
    if primary.get("acceptance_rate") is not None and secondary.get("acceptance_rate") is not None:
        acceptance_diff = primary["acceptance_rate"] - secondary["acceptance_rate"]
    physical_comparison_allowed = accepted_a is not None and accepted_a == accepted_b
    physical_cost_diff = None
    if physical_comparison_allowed and primary.get("normalized_physical_cost") is not None and secondary.get("normalized_physical_cost") is not None:
        physical_cost_diff = primary["normalized_physical_cost"] - secondary["normalized_physical_cost"]
    return {
        "algorithm_a": primary["key"]["algorithm"],
        "algorithm_b": secondary["key"]["algorithm"],
        "variant_id_a": primary["key"].get("variant_id"),
        "variant_id_b": secondary["key"].get("variant_id"),
        "accepted_flow_difference": None if accepted_a is None or accepted_b is None else accepted_a - accepted_b,
        "acceptance_rate_difference": acceptance_diff,
        "objective_difference": None if objective_a is None or objective_b is None else objective_a - objective_b,
        "runtime_ratio_a_over_b": None if not runtime_a or not runtime_b else runtime_a / runtime_b,
        "physical_cost_difference": physical_cost_diff,
        "physical_cost_comparison_allowed": physical_comparison_allowed,
    }


def paired_comparisons(records: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    by_instance: Dict[Tuple[str, str], List[Mapping[str, Any]]] = {}
    for row in records:
        key = row["key"]
        by_instance.setdefault((key["scenario_id"], row.get("full_instance_fingerprint")), []).append(row)
    comparisons = []
    for group in by_instance.values():
        sorted_group = sorted(group, key=lambda row: (row["key"]["algorithm"], row["key"].get("variant_id") or ""))
        for i, left in enumerate(sorted_group):
            for right in sorted_group[i + 1:]:
                comparisons.append(compare_records(left, right))
    return comparisons


def parallel_pricing_analysis(records: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    by_pair: Dict[Tuple[str, str], Dict[Tuple[str, Optional[str]], Mapping[str, Any]]] = {}
    for row in records:
        key = row["key"]
        metadata = row.get("dwcg_metadata") or {}
        worker_count = metadata.get("pricing_worker_count")
        if not key.get("variant_id") or worker_count is None:
            continue
        by_pair.setdefault((key["scenario_id"], key["algorithm"]), {})[(key["algorithm"], key.get("variant_id"))] = row

    ratios: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    for (_, algorithm), group in by_pair.items():
        baseline = group.get((algorithm, "W1"))
        if baseline is None:
            continue
        baseline_meta = baseline.get("dwcg_metadata") or {}
        for (_, variant_id), row in group.items():
            if variant_id == "W1":
                continue
            metadata = row.get("dwcg_metadata") or {}
            worker_count = metadata.get("pricing_worker_count")
            if worker_count is None:
                continue
            pricing_base = baseline_meta.get("total_pricing_runtime")
            pricing_current = metadata.get("total_pricing_runtime")
            e2e_base = baseline.get("total_end_to_end_runtime_seconds")
            e2e_current = row.get("total_end_to_end_runtime_seconds")
            accepted_base = baseline.get("accepted_flow_count")
            accepted_current = row.get("accepted_flow_count")
            objective_base = baseline.get("canonical_objective_value")
            objective_current = row.get("canonical_objective_value")
            ratios.setdefault((algorithm, int(worker_count)), []).append({
                "pricing_speedup": None if not pricing_base or not pricing_current else pricing_base / pricing_current,
                "end_to_end_speedup": None if not e2e_base or not e2e_current else e2e_base / e2e_current,
                "accepted_flow_difference": None if accepted_base is None or accepted_current is None else accepted_current - accepted_base,
                "canonical_objective_difference": None if objective_base is None or objective_current is None else objective_current - objective_base,
                "fingerprint_agrees": (
                    metadata.get("final_ordered_column_pool_fingerprint")
                    == baseline_meta.get("final_ordered_column_pool_fingerprint")
                ),
                "termination_status_agrees": row.get("termination_status") == baseline.get("termination_status"),
                "equal_accepted_flow_count": accepted_base is not None and accepted_base == accepted_current,
            })

    summary = []
    for (algorithm, worker_count), values in sorted(ratios.items()):
        pricing_speedups = [item["pricing_speedup"] for item in values if item["pricing_speedup"] is not None]
        e2e_speedups = [item["end_to_end_speedup"] for item in values if item["end_to_end_speedup"] is not None]
        summary.append({
            "algorithm": algorithm,
            "baseline_variant_id": "W1",
            "variant_id": f"W{worker_count}",
            "pricing_worker_count": worker_count,
            "paired_run_count": len(values),
            "median_pricing_speedup": _median_or_none(pricing_speedups),
            "median_end_to_end_speedup": _median_or_none(e2e_speedups),
            "parallel_efficiency": None if not pricing_speedups else _median_or_none(pricing_speedups) / worker_count,
            "accepted_flow_differences": [item["accepted_flow_difference"] for item in values],
            "canonical_objective_differences": [item["canonical_objective_difference"] for item in values],
            "final_pool_fingerprint_agreement_count": sum(1 for item in values if item["fingerprint_agrees"]),
            "termination_status_agreement_count": sum(1 for item in values if item["termination_status_agrees"]),
            "equal_accepted_flow_count": sum(1 for item in values if item["equal_accepted_flow_count"]),
        })
    return summary


def pricing_strategy_analysis(records: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    target_pairs = (("ESV", "EX"), ("HYB_S5", "EX"), ("HYB_S5", "ESV"))
    by_pair: Dict[Tuple[str, str, str, int], Dict[str, Mapping[str, Any]]] = {}
    for row in records:
        key = row["key"]
        variant_id = key.get("variant_id")
        if key.get("algorithm") != "dwcg_pure" or variant_id not in {"EX", "ESV", "HYB_S5"}:
            continue
        by_pair.setdefault(
            (key["scale"], key["load_label"], key["scenario_id"], int(key["formal_seed"])),
            {},
        )[variant_id] = row

    grouped: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {}
    for (scale, load_label, _scenario_id, _seed), variants in by_pair.items():
        for left_variant, right_variant in target_pairs:
            if left_variant not in variants or right_variant not in variants:
                continue
            left = variants[left_variant]
            right = variants[right_variant]
            left_meta = left.get("dwcg_metadata") or {}
            right_meta = right.get("dwcg_metadata") or {}
            accepted_left = left.get("accepted_flow_count")
            accepted_right = right.get("accepted_flow_count")
            objective_left = left.get("canonical_objective_value")
            objective_right = right.get("canonical_objective_value")
            equal_acceptance = accepted_left is not None and accepted_left == accepted_right
            equivalent_work = (
                left_meta.get("completed_cg_iteration_count") == right_meta.get("completed_cg_iteration_count")
                and left_meta.get("exact_pricing_call_count") == right_meta.get("exact_pricing_call_count")
            )
            grouped.setdefault((scale, load_label, left_variant, right_variant), []).append({
                "accepted_flow_difference": None if accepted_left is None or accepted_right is None else accepted_left - accepted_right,
                "canonical_objective_difference": None if objective_left is None or objective_right is None else objective_left - objective_right,
                "runtime_ratio": _ratio(left.get("total_end_to_end_runtime_seconds"), right.get("total_end_to_end_runtime_seconds")),
                "pricing_runtime_ratio": _ratio(left_meta.get("total_pricing_runtime"), right_meta.get("total_pricing_runtime")),
                "equal_acceptance": equal_acceptance,
                "cost_difference": (
                    None if not equal_acceptance or left.get("normalized_physical_cost") is None or right.get("normalized_physical_cost") is None
                    else left["normalized_physical_cost"] - right["normalized_physical_cost"]
                ),
                "convergence_certification_difference": int(bool(left_meta.get("convergence_certification"))) - int(bool(right_meta.get("convergence_certification"))),
                "fingerprint_agrees_when_equivalent_work": (
                    None if not equivalent_work
                    else left_meta.get("final_ordered_column_pool_fingerprint") == right_meta.get("final_ordered_column_pool_fingerprint")
                ),
            })

    summary = []
    for (scale, load_label, left_variant, right_variant), values in sorted(grouped.items()):
        acceptance_diffs = [item["accepted_flow_difference"] for item in values if item["accepted_flow_difference"] is not None]
        objective_diffs = [item["canonical_objective_difference"] for item in values if item["canonical_objective_difference"] is not None]
        summary.append({
            "scale": scale,
            "load_label": load_label,
            "variant_a": left_variant,
            "variant_b": right_variant,
            "paired_run_count": len(values),
            "acceptance_win_tie_loss": _win_tie_loss_from_diffs(acceptance_diffs),
            "paired_accepted_flow_differences": acceptance_diffs,
            "canonical_objective_win_tie_loss": _win_tie_loss_from_diffs(objective_diffs),
            "runtime_ratios": [item["runtime_ratio"] for item in values if item["runtime_ratio"] is not None],
            "pricing_runtime_ratios": [item["pricing_runtime_ratio"] for item in values if item["pricing_runtime_ratio"] is not None],
            "equal_acceptance_pair_count": sum(1 for item in values if item["equal_acceptance"]),
            "cost_differences_equal_acceptance_only": [item["cost_difference"] for item in values if item["cost_difference"] is not None],
            "convergence_certification_differences": [item["convergence_certification_difference"] for item in values],
            "final_pool_fingerprint_agreement_when_equivalent_work_count": sum(
                1 for item in values if item["fingerprint_agrees_when_equivalent_work"] is True
            ),
        })
    return summary


def win_tie_loss(records: List[Mapping[str, Any]], algorithm_a: str, algorithm_b: str) -> Dict[str, int]:
    wins = ties = losses = 0
    by_instance: Dict[Tuple[str, str], Dict[str, Mapping[str, Any]]] = {}
    for row in records:
        key = row["key"]
        by_instance.setdefault((key["scenario_id"], row.get("full_instance_fingerprint")), {})[key["algorithm"]] = row
    for group in by_instance.values():
        if algorithm_a not in group or algorithm_b not in group:
            continue
        diff = compare_records(group[algorithm_a], group[algorithm_b])["accepted_flow_difference"]
        if diff is None:
            continue
        if diff > 0:
            wins += 1
        elif diff < 0:
            losses += 1
        else:
            ties += 1
    return {"wins": wins, "ties": ties, "losses": losses}


def _win_tie_loss_from_diffs(differences: List[float]) -> Dict[str, int]:
    return {
        "wins": sum(1 for value in differences if value > 0),
        "ties": sum(1 for value in differences if value == 0),
        "losses": sum(1 for value in differences if value < 0),
    }


def _ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def write_formal_reports(output_dir: Path, analysis: Mapping[str, Any]) -> None:
    (output_dir / "report.json").write_text(
        json.dumps(to_jsonable(analysis), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Formal Benchmark Report",
        "",
        "Acceptance is the primary objective. Physical cost is secondary and is compared directly only when accepted-flow counts are equal.",
        "",
        "## Summary",
    ]
    for row in analysis.get("summary_rows", []):
        label = row.get("display_name") or row["algorithm"]
        lines.append(
            f"- {row['scale']} {row['load_label']} {label}: "
            f"median acceptance {row.get('median_acceptance_rate')}, "
            f"median end-to-end runtime {row.get('median_total_end_to_end_runtime_seconds')}, "
            f"median total positive columns {row.get('median_total_positive_column_count')}"
        )
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _median_or_none(values: List[float]) -> Optional[float]:
    return median(values) if values else None


def _algorithm_core_runtime(record: Mapping[str, Any]) -> Optional[float]:
    for metadata_key in ("direct_ilp_metadata", "dwcg_metadata", "heuristic_metadata"):
        metadata = record.get(metadata_key) or {}
        value = metadata.get("algorithm_core_runtime_seconds")
        if value is not None:
            return value
    return record.get("total_end_to_end_runtime_seconds")


def _runtime_metadata(record: Mapping[str, Any]) -> Mapping[str, Any]:
    for metadata_key in ("direct_ilp_metadata", "dwcg_metadata", "heuristic_metadata"):
        metadata = record.get(metadata_key) or {}
        if metadata:
            return metadata
    return {}


def _dwcg_metadata_value(group: List[Mapping[str, Any]], key: str) -> Optional[Any]:
    values = {
        (row.get("dwcg_metadata") or {}).get(key)
        for row in group
        if (row.get("dwcg_metadata") or {}).get(key) is not None
    }
    if len(values) == 1:
        return next(iter(values))
    if not values:
        return None
    return sorted(values)
