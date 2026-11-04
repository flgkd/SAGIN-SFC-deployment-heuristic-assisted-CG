"""Create the future 240-record Direct ILP optimality comparison dataset.

This is read-only with respect to source records. It must be run only after
the formal Direct ILP S20/S30 outputs exist and pass audit.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Mapping, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from audit_direct_ilp_acceptance_cold_common import (
    BASELINE_DIR,
    DIRECT_ALGORITHM,
    FINGERPRINT_FIELDS,
    RUNTIME_ACCOUNTING_CLOCK_TOLERANCE_SECONDS,
    direct_meta,
    read_json,
    read_jsonl,
    run_key,
    runtime_accounting_diagnostics,
    scenario_key,
)


COMPARISON_ALGORITHMS = ("direct_ilp_acceptance_cold", "saginfe", "dwcg_pure", "dwcg_saginfe_pro")
HEURISTIC_ALGORITHMS = ("saginfe", "dwcg_pure", "dwcg_saginfe_pro")
EXPECTED_SCENARIOS = {
    (scale, load, seed)
    for scale in ("S20", "S30")
    for load in ("Low", "Medium", "High")
    for seed in range(10)
}


def require(condition: bool, message: str, errors: List[str]) -> None:
    if not condition:
        errors.append(message)


def group_by_scenario(rows: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str, int], List[Dict[str, Any]]]:
    grouped: Dict[Tuple[str, str, int], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[scenario_key(row)].append(row)
    return grouped


def runtime(row: Mapping[str, Any]) -> float | None:
    return row.get("total_end_to_end_runtime_seconds")


def mean(values: List[float]) -> float | None:
    return sum(values) / len(values) if values else None


def median_or_none(values: List[float]) -> float | None:
    return median(values) if values else None


def write_csv(path: Path, rows: List[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def runtime_exclusion_reason(runtime_diagnostics: Mapping[str, Any]) -> str | None:
    errors = runtime_diagnostics.get("runtime_accounting_errors") or []
    if not errors:
        return None
    return "; ".join(str(error) for error in errors)


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def row_runtime_for_aggregates(row: Mapping[str, Any]) -> float | None:
    if row["key"]["algorithm"] == DIRECT_ALGORITHM:
        return row.get("direct_ilp_runtime_seconds_for_aggregates")
    return runtime(row)


def termination_timeout(row: Mapping[str, Any]) -> bool:
    termination = row.get("termination_status")
    meta = direct_meta(row)
    return termination in {
        "TIME_LIMIT_WITH_INCUMBENT",
        "TIME_LIMIT_NO_INCUMBENT",
        "E2E_HARD_WALL_WITH_INCUMBENT",
        "E2E_HARD_WALL_NO_INCUMBENT",
    } or meta.get("solver_time_limit_reached") is True


def certification_flag(row: Mapping[str, Any]) -> bool:
    if row["key"]["algorithm"] == DIRECT_ALGORITHM:
        meta = direct_meta(row)
        return bool(meta.get("acceptance_optimality_certified") or row.get("acceptance_count_certified"))
    return False


def summarize_per_scale_load_algorithm(combined: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in combined:
        key = row["key"]
        grouped[(key["scale"], key["load_label"], key["algorithm"])].append(row)
    summary_rows: List[Dict[str, Any]] = []
    for (scale, load, algorithm), rows in sorted(grouped.items()):
        accepted_values = [float(row["accepted_flow_count"]) for row in rows if row.get("accepted_flow_count") is not None]
        acceptance_rates = [float(row["acceptance_rate"]) for row in rows if row.get("acceptance_rate") is not None]
        runtime_values = [row_runtime_for_aggregates(row) for row in rows]
        runtime_values = [float(value) for value in runtime_values if value is not None]
        summary_rows.append({
            "scale": scale,
            "load_label": load,
            "algorithm": algorithm,
            "record_count": len(rows),
            "mean_accepted_flow_count": mean(accepted_values),
            "median_accepted_flow_count": median_or_none(accepted_values),
            "mean_acceptance_rate": mean(acceptance_rates),
            "median_acceptance_rate": median_or_none(acceptance_rates),
            "runtime_eligible_count": len(runtime_values),
            "median_runtime_seconds": median_or_none(runtime_values),
            "timeout_rate": sum(1 for row in rows if termination_timeout(row)) / len(rows),
            "optimality_certification_count": sum(1 for row in rows if certification_flag(row)),
        })
    return summary_rows


def summarize_certified_scenarios(scenario_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    certified = [row for row in scenario_rows if row["direct_ilp_certified"]]
    algorithm_rows: List[Dict[str, Any]] = []
    for algorithm in HEURISTIC_ALGORITHMS:
        gaps: List[float] = []
        hits = 0
        wins = ties = losses = 0
        for row in certified:
            direct_value = row["direct_ilp_acceptance_cold_accepted_flow_count"]
            value = row[f"{algorithm}_accepted_flow_count"]
            if value is None or direct_value is None:
                continue
            gap = float(direct_value - value)
            gaps.append(gap)
            if value == direct_value:
                hits += 1
                ties += 1
            elif value > direct_value:
                wins += 1
            else:
                losses += 1
        algorithm_rows.append({
            "algorithm": algorithm,
            "certified_scenario_count": len(certified),
            "mean_gap_to_certified_optimum": mean(gaps),
            "median_gap_to_certified_optimum": median_or_none(gaps),
            "max_gap_to_certified_optimum": max(gaps) if gaps else None,
            "optimal_hit_count": hits,
            "optimal_hit_rate": hits / len(certified) if certified else None,
            "paired_wins_vs_direct_ilp": wins,
            "paired_ties_vs_direct_ilp": ties,
            "paired_losses_vs_direct_ilp": losses,
        })
    return {
        "scenario_count": len(certified),
        "algorithm_summaries": algorithm_rows,
    }


def uncertified_scenario_rows(scenario_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in scenario_rows:
        if row["direct_ilp_certified"]:
            continue
        direct_incumbent = row["direct_ilp_incumbent"]
        out = {
            "scale": row["scale"],
            "load_label": row["load_label"],
            "formal_seed": row["formal_seed"],
            "direct_ilp_incumbent": direct_incumbent,
            "direct_ilp_integer_lower_bound": row["direct_ilp_integer_lower_bound"],
            "direct_ilp_integer_upper_bound": row["direct_ilp_integer_upper_bound"],
        }
        for algorithm in COMPARISON_ALGORITHMS:
            accepted = row[f"{algorithm}_accepted_flow_count"]
            out[f"{algorithm}_accepted_flow_count"] = accepted
            if algorithm != DIRECT_ALGORITHM:
                out[f"{algorithm}_exceeds_cold_direct_ilp_incumbent"] = (
                    accepted is not None and direct_incumbent is not None and accepted > direct_incumbent
                )
        rows.append(out)
    return rows


def equal_budget_summary_rows(scenario_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    runtime_exclusions = [row for row in scenario_rows if row["direct_ilp_runtime_excluded_from_aggregates"]]
    direct_runtime_values = [
        row["direct_ilp_runtime_seconds_for_aggregates"]
        for row in scenario_rows
        if row["direct_ilp_runtime_seconds_for_aggregates"] is not None
    ]
    for algorithm in HEURISTIC_ALGORITHMS:
        wins = ties = losses = 0
        runtime_values = [row[f"{algorithm}_runtime_seconds"] for row in scenario_rows if row[f"{algorithm}_runtime_seconds"] is not None]
        for row in scenario_rows:
            direct_value = row["direct_ilp_acceptance_cold_accepted_flow_count"]
            value = row[f"{algorithm}_accepted_flow_count"]
            if value is None or direct_value is None:
                continue
            if value > direct_value:
                wins += 1
            elif value == direct_value:
                ties += 1
            else:
                losses += 1
        rows.append({
            "comparison": f"{DIRECT_ALGORITHM}_vs_{algorithm}",
            "compared_algorithm": algorithm,
            "scenario_count": len(scenario_rows),
            "compared_algorithm_wins": wins,
            "ties": ties,
            "compared_algorithm_losses": losses,
            "direct_ilp_runtime_eligible_count": len(direct_runtime_values),
            "direct_ilp_median_runtime_seconds": median_or_none([float(value) for value in direct_runtime_values]),
            "compared_algorithm_runtime_eligible_count": len(runtime_values),
            "compared_algorithm_median_runtime_seconds": median_or_none([float(value) for value in runtime_values]),
            "direct_ilp_runtime_exclusion_count": len(runtime_exclusions),
            "direct_ilp_runtime_exclusion_keys": [
                f"{row['scale']}|{row['load_label']}|{row['formal_seed']}" for row in runtime_exclusions
            ],
        })
    return rows


def audit_comparison(combined: List[Dict[str, Any]], scenario_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    errors: List[str] = []
    grouped: Dict[Tuple[str, str, int], List[Dict[str, Any]]] = defaultdict(list)
    for row in combined:
        grouped[scenario_key(row)].append(row)
    require(len(combined) == 240, f"record count {len(combined)} != 240", errors)
    require(len(grouped) == 60, f"scenario count {len(grouped)} != 60", errors)
    for scenario, rows in grouped.items():
        algorithms = {row["key"]["algorithm"] for row in rows}
        require(algorithms == set(COMPARISON_ALGORITHMS), f"{scenario} algorithm set mismatch {sorted(algorithms)}", errors)
        require(len(rows) == 4, f"{scenario} paired record count {len(rows)} != 4", errors)
        direct = next((row for row in rows if row["key"]["algorithm"] == DIRECT_ALGORITHM), None)
        if direct is None:
            continue
        for row in rows:
            for field in FINGERPRINT_FIELDS:
                require(row.get(field) == direct.get(field), f"{scenario} {field} mismatch for {row['key']['algorithm']}", errors)
    duplicate_keys = [key for key, count in Counter(run_key(row) for row in combined).items() if count != 1]
    require(not duplicate_keys, f"duplicate run keys {duplicate_keys}", errors)
    runtime_exclusions = [row for row in scenario_rows if row["direct_ilp_runtime_excluded_from_aggregates"]]
    require(len(runtime_exclusions) == 1, f"runtime exclusion count {len(runtime_exclusions)} != 1", errors)
    if runtime_exclusions:
        excluded = runtime_exclusions[0]
        require(
            (excluded["scale"], excluded["load_label"], int(excluded["formal_seed"])) == ("S30", "High", 7),
            f"runtime exclusion key mismatch {excluded}",
            errors,
        )
    for row in combined:
        if row["key"]["algorithm"] != DIRECT_ALGORITHM:
            continue
        require("runtime_accounting_valid_raw" in row, f"{run_key(row)} missing raw runtime flag", errors)
        require("runtime_accounting_valid_recomputed" in row, f"{run_key(row)} missing recomputed runtime flag", errors)
        if row.get("termination_status") == "TIME_LIMIT_WITH_INCUMBENT":
            require(row.get("objective_globally_optimal") is not True, f"{run_key(row)} TIME_LIMIT mislabeled optimal", errors)
    return {
        "pass": not errors,
        "errors": errors,
        "record_count": len(combined),
        "scenario_count": len(grouped),
        "runtime_exclusion_count": len(runtime_exclusions),
        "runtime_exclusions": runtime_exclusions,
    }


def build_comparison(direct_dir: Path, baseline_dir: Path, output_dir: Path) -> Dict[str, Any]:
    errors: List[str] = []
    direct_rows = read_jsonl(direct_dir / "runs.jsonl")
    baseline_rows = read_jsonl(baseline_dir / "merged_runs.jsonl")
    baseline_manifest = read_json(baseline_dir / "merge_manifest.json")
    require(baseline_manifest.get("pass") is True, "baseline manifest pass mismatch", errors)

    direct_by_scenario = group_by_scenario(direct_rows)
    baseline_by_scenario = group_by_scenario(
        row for row in baseline_rows if row["key"]["algorithm"] in HEURISTIC_ALGORITHMS
    )
    require(set(direct_by_scenario) == EXPECTED_SCENARIOS, "Direct ILP scenario set mismatch", errors)
    require(set(baseline_by_scenario) >= EXPECTED_SCENARIOS, "baseline scenario set incomplete", errors)

    combined: List[Dict[str, Any]] = []
    scenario_rows: List[Dict[str, Any]] = []
    for scenario in sorted(EXPECTED_SCENARIOS):
        direct_matches = direct_by_scenario.get(scenario, [])
        require(len(direct_matches) == 1, f"{scenario} direct record count mismatch", errors)
        if len(direct_matches) != 1:
            continue
        direct = direct_matches[0]
        heuristics = [row for row in baseline_by_scenario.get(scenario, []) if row["key"]["algorithm"] in HEURISTIC_ALGORITHMS]
        require(len(heuristics) == 3, f"{scenario} heuristic baseline count mismatch", errors)
        for row in heuristics:
            for field in FINGERPRINT_FIELDS:
                require(row.get(field) == direct.get(field), f"{scenario} {field} mismatch for {row['key']['algorithm']}", errors)
        meta = direct_meta(direct)
        certified = bool(meta.get("acceptance_optimality_certified") or direct.get("acceptance_count_certified"))
        direct_incumbent = direct.get("accepted_flow_count")
        lower = meta.get("safe_integer_acceptance_lower_bound")
        upper = meta.get("safe_integer_acceptance_upper_bound")
        optimum = direct_incumbent if certified else None
        runtime_diagnostics = runtime_accounting_diagnostics(direct, direct_meta(direct).get("configured_end_to_end_limit_seconds"))
        runtime_valid = runtime_diagnostics["runtime_accounting_valid_recomputed"] is True
        runtime_exclusion = runtime_exclusion_reason(runtime_diagnostics)
        aggregate_runtime = runtime(direct) if runtime_valid else None
        for row in [direct] + heuristics:
            out = dict(row)
            out["comparison_dataset"] = "direct_ilp_optimality_s20_s30_240"
            out["comparison_algorithm_set"] = COMPARISON_ALGORITHMS
            out["direct_ilp_certified_optimum"] = optimum
            out["direct_ilp_integer_acceptance_lower_bound"] = lower
            out["direct_ilp_integer_acceptance_upper_bound"] = upper
            if row["key"]["algorithm"] == DIRECT_ALGORITHM:
                out.update({
                    "runtime_accounting_valid_raw": runtime_diagnostics["runtime_accounting_valid_raw"],
                    "runtime_accounting_valid_recomputed": runtime_diagnostics["runtime_accounting_valid_recomputed"],
                    "runtime_accounting_residual_seconds": runtime_diagnostics["runtime_accounting_residual_seconds"],
                    "runtime_accounting_tolerance_seconds": runtime_diagnostics["runtime_accounting_tolerance_seconds"],
                    "runtime_accounting_recomputed_residual_seconds": runtime_diagnostics["runtime_accounting_recomputed_residual_seconds"],
                    "runtime_accounting_errors": runtime_diagnostics["runtime_accounting_errors"],
                    "direct_ilp_runtime_seconds_for_aggregates": aggregate_runtime,
                    "direct_ilp_runtime_excluded_from_aggregates": not runtime_valid,
                    "direct_ilp_runtime_exclusion_reason": runtime_exclusion,
                })
            if row["key"]["algorithm"] in HEURISTIC_ALGORITHMS:
                accepted = row.get("accepted_flow_count")
                out["gap_to_certified_optimum"] = (optimum - accepted) if optimum is not None and accepted is not None else None
                out["hits_certified_optimum"] = (accepted == optimum) if optimum is not None and accepted is not None else None
                out["exceeds_cold_direct_ilp_incumbent"] = accepted is not None and direct_incumbent is not None and accepted > direct_incumbent
            combined.append(out)
        accepted_by_alg = {row["key"]["algorithm"]: row.get("accepted_flow_count") for row in [direct] + heuristics}
        runtime_by_alg = {
            row["key"]["algorithm"]: (
                aggregate_runtime if row["key"]["algorithm"] == DIRECT_ALGORITHM else row.get("total_end_to_end_runtime_seconds")
            )
            for row in [direct] + heuristics
        }
        scenario_rows.append({
            "scale": scenario[0],
            "load_label": scenario[1],
            "formal_seed": scenario[2],
            "direct_ilp_status": direct.get("termination_status"),
            "direct_ilp_certified": certified,
            "direct_ilp_incumbent": direct_incumbent,
            "direct_ilp_integer_lower_bound": lower,
            "direct_ilp_integer_upper_bound": upper,
            "direct_ilp_solver_time_limit": meta.get("solver_time_limit_reached") is True,
            "direct_ilp_e2e_hard_wall": meta.get("hard_wall_clock_limit_reached") is True,
            "runtime_accounting_valid_raw": runtime_diagnostics["runtime_accounting_valid_raw"],
            "runtime_accounting_valid_recomputed": runtime_diagnostics["runtime_accounting_valid_recomputed"],
            "runtime_accounting_residual_seconds": runtime_diagnostics["runtime_accounting_residual_seconds"],
            "runtime_accounting_tolerance_seconds": runtime_diagnostics["runtime_accounting_tolerance_seconds"],
            "runtime_accounting_recomputed_residual_seconds": runtime_diagnostics["runtime_accounting_recomputed_residual_seconds"],
            "direct_ilp_runtime_seconds_for_aggregates": aggregate_runtime,
            "direct_ilp_runtime_excluded_from_aggregates": not runtime_valid,
            "direct_ilp_runtime_exclusion_reason": runtime_exclusion,
            **{f"{algorithm}_accepted_flow_count": accepted_by_alg.get(algorithm) for algorithm in COMPARISON_ALGORITHMS},
            **{f"{algorithm}_runtime_seconds": runtime_by_alg.get(algorithm) for algorithm in COMPARISON_ALGORITHMS},
        })

    keys = [run_key(row) for row in combined]
    require(len(combined) == 240, f"combined size {len(combined)} != 240", errors)
    require(not [key for key, count in Counter(keys).items() if count != 1], "duplicate comparison run keys", errors)
    if errors:
        return {"pass": False, "errors": errors, "record_count": len(combined)}

    certified_rows = [row for row in scenario_rows if row["direct_ilp_certified"]]
    uncertified_rows = [row for row in scenario_rows if not row["direct_ilp_certified"]]
    runtime_values = [
        row["direct_ilp_runtime_seconds_for_aggregates"]
        for row in scenario_rows
        if row["direct_ilp_runtime_seconds_for_aggregates"] is not None
    ]
    runtime_excluded_rows = [row for row in scenario_rows if row["direct_ilp_runtime_excluded_from_aggregates"]]
    per_scale_load_algorithm_summary = summarize_per_scale_load_algorithm(combined)
    certified_summary = summarize_certified_scenarios(scenario_rows)
    uncertified_rows_output = uncertified_scenario_rows(scenario_rows)
    equal_budget_rows = equal_budget_summary_rows(scenario_rows)
    comparison_audit = audit_comparison(combined, scenario_rows)
    require(comparison_audit["pass"] is True, f"comparison audit failed: {comparison_audit['errors']}", errors)
    if errors:
        return {"pass": False, "errors": errors, "record_count": len(combined), "scenario_count": len(scenario_rows)}
    summary = {
        "pass": True,
        "errors": [],
        "direct_dir": str(direct_dir),
        "baseline_dir": str(baseline_dir),
        "output_dir": str(output_dir),
        "record_count": len(combined),
        "scenario_count": len(scenario_rows),
        "algorithm_set": COMPARISON_ALGORITHMS,
        "direct_ilp_certified_scenario_count": len(certified_rows),
        "direct_ilp_certification_rate": len(certified_rows) / len(scenario_rows),
        "direct_ilp_uncertified_scenario_count": len(uncertified_rows),
        "direct_ilp_solver_time_limit_rate": sum(1 for row in scenario_rows if row["direct_ilp_solver_time_limit"]) / len(scenario_rows),
        "direct_ilp_e2e_hard_wall_rate": sum(1 for row in scenario_rows if row["direct_ilp_e2e_hard_wall"]) / len(scenario_rows),
        "direct_ilp_runtime_accounting_recomputed_valid_count": sum(1 for row in scenario_rows if row["runtime_accounting_valid_recomputed"]),
        "direct_ilp_runtime_accounting_raw_valid_count": sum(1 for row in scenario_rows if row["runtime_accounting_valid_raw"] is True),
        "direct_ilp_runtime_accounting_tolerance_seconds": RUNTIME_ACCOUNTING_CLOCK_TOLERANCE_SECONDS,
        "direct_ilp_runtime_aggregate_count": len(runtime_values),
        "direct_ilp_runtime_excluded_scenario_count": len(runtime_excluded_rows),
        "direct_ilp_runtime_excluded_scenarios": [
            {
                "scale": row["scale"],
                "load_label": row["load_label"],
                "formal_seed": row["formal_seed"],
                "reason": row["direct_ilp_runtime_exclusion_reason"],
            }
            for row in runtime_excluded_rows
        ],
        "direct_ilp_median_runtime_seconds": median(runtime_values) if runtime_values else None,
        "comparison_audit_pass": comparison_audit["pass"],
        "comparison_audit_errors": comparison_audit["errors"],
        "notes": [
            "Physical cost is intentionally excluded.",
            "Certified scenarios compare heuristic accepted-flow counts against the Direct ILP certified optimum.",
            "Uncertified scenarios preserve the Direct ILP integer acceptance interval and do not label the incumbent as optimal.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    write_jsonl(output_dir / "comparison_runs.jsonl", combined)
    write_csv(output_dir / "scenario_summary.csv", scenario_rows)
    write_csv(output_dir / "per_scale_load_algorithm_summary.csv", per_scale_load_algorithm_summary)
    write_json(output_dir / "per_scale_load_algorithm_summary.json", {"rows": per_scale_load_algorithm_summary})
    write_json(output_dir / "certified_direct_ilp_summary.json", certified_summary)
    write_csv(output_dir / "certified_direct_ilp_algorithm_summary.csv", certified_summary["algorithm_summaries"])
    write_csv(output_dir / "uncertified_direct_ilp_scenarios.csv", uncertified_rows_output)
    write_json(output_dir / "uncertified_direct_ilp_scenarios.json", {"rows": uncertified_rows_output})
    write_csv(output_dir / "equal_budget_comparison_summary.csv", equal_budget_rows)
    write_json(output_dir / "equal_budget_comparison_summary.json", {"rows": equal_budget_rows})
    write_json(output_dir / "comparison_audit.json", comparison_audit)
    write_json(output_dir / "comparison_manifest.json", summary)
    return summary


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the future 240-record Direct ILP optimality comparison.")
    parser.add_argument("--direct-dir", required=True, type=Path)
    parser.add_argument("--baseline-dir", type=Path, default=BASELINE_DIR)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    summary = build_comparison(args.direct_dir, args.baseline_dir, args.output_dir)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
