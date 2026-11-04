"""Read-only audit for the V3 DWCG-Pure parallel-pricing study."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from record_compatibility import normalize_record, resolve_input_path

from scripts.experiment_record_validation import recorded_runtime_limit_errors
from calibration_runner import sha256_fingerprint, write_json_atomic


LOAD = "High"
ALGORITHMS = ("dwcg_pure", "dwcg_saginfe_pro")
WORKERS = {"W1": 1, "W2": 2, "W4": 4}
MISSING = object()


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    path = resolve_input_path(path)
    if not path.exists():
        return []
    return [normalize_record(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_optional(path: Path | None, payload: Mapping[str, Any]) -> None:
    if path is not None:
        write_json_atomic(path, payload)


def meta(row: Mapping[str, Any]) -> Mapping[str, Any]:
    return row.get("dwcg_metadata") or {}


def variant(row: Mapping[str, Any]) -> str:
    return str((row.get("key") or {}).get("variant_id"))


def normalized_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    key = row.get("key") or {}
    row_meta = meta(row)
    return {
        "scale": key.get("scale"),
        "time_slots": int(key.get("time_slots", 0)),
        "load_label": key.get("load_label"),
        "formal_seed": int(key.get("formal_seed", -1)),
        "algorithm": key.get("algorithm"),
        "variant_id": key.get("variant_id"),
        "pricing_worker_count": row_meta.get("pricing_worker_count"),
        "parallel_pricing_enabled": row_meta.get("parallel_pricing_enabled"),
        "pricing_strategy": row_meta.get("pricing_strategy"),
        "hybrid_switch_iteration": row_meta.get("hybrid_switch_iteration"),
        "topology_fingerprint": row.get("topology_fingerprint"),
        "base_flow_fingerprint": row.get("base_flow_fingerprint"),
        "full_instance_fingerprint": row.get("full_instance_fingerprint"),
        "path_validity": row.get("path_validity"),
        "capacity_validity": row.get("capacity_validity"),
        "runtime_accounting_valid": row_meta.get("runtime_accounting_valid", row.get("runtime_accounting_valid")),
        "integration_only": row.get("integration_only"),
        "run_category": row.get("run_category"),
        "git_commit": row.get("git_commit"),
        "configuration_snapshot": row.get("configuration_snapshot") or {},
        "configured_max_cg_iterations": row_meta.get("configured_max_cg_iterations"),
        "configured_max_exact_pricing_calls": row_meta.get("configured_max_exact_pricing_calls"),
        "configured_hard_time_limit_seconds": row_meta.get("configured_hard_time_limit_seconds"),
        "configured_final_rmp_reserve_seconds": row_meta.get("configured_final_rmp_reserve_seconds"),
        "configured_final_rmp_time_cap_seconds": row_meta.get("configured_final_rmp_time_cap_seconds"),
        "solver_threads_per_worker": row_meta.get("solver_threads_per_worker"),
        "pricing_worker_threads": row_meta.get("pricing_worker_threads"),
        "deterministic_mode": row_meta.get("deterministic_mode"),
        "pricing_strategy_invariant_valid": row_meta.get("pricing_strategy_invariant_valid"),
        "initialization_source": row_meta.get("initialization_source"),
        "base_algorithm": row_meta.get("base_algorithm"),
        "pro_family_algorithm": row_meta.get("pro_family_algorithm"),
        "pro_candidate_role": row_meta.get("pro_candidate_role"),
        "saginfe_initialization_used": row_meta.get("saginfe_initialization_used"),
        "selective_pfe_enabled": row_meta.get("selective_pfe_enabled"),
        "selective_pfe_may_run": row_meta.get("selective_pfe_may_run"),
        "selective_pfe_call_count": row_meta.get("selective_pfe_call_count"),
        "selective_pfe_candidate_count": row_meta.get("selective_pfe_candidate_count"),
        "selective_pfe_round_count": row_meta.get("selective_pfe_round_count"),
        "selective_pfe_positive_column_count": row_meta.get("selective_pfe_positive_column_count"),
        "selective_pfe_runtime_seconds": row_meta.get("selective_pfe_runtime_seconds"),
        "initialization_generation_runtime_seconds": row_meta.get("initialization_generation_runtime_seconds"),
        "final_rmp_mip_start_used": row_meta.get("final_rmp_mip_start_used"),
        "incumbent_safeguard_used": row_meta.get("incumbent_safeguard_used"),
        "logical_pricing_task_count": row_meta.get("logical_pricing_task_count"),
        "exact_pricing_budget_used": row_meta.get("exact_pricing_budget_used"),
        "exact_pricing_budget_policy": row_meta.get("exact_pricing_budget_policy"),
        "exact_pricing_call_count": row_meta.get("exact_pricing_call_count"),
        "deterministic_work_budget_exhausted": row_meta.get("deterministic_work_budget_exhausted"),
        "cg_iteration_count": row_meta.get("cg_iteration_count"),
        "completed_cg_iteration_count": row_meta.get("completed_cg_iteration_count"),
        "generated_column_count": row_meta.get("generated_column_count"),
        "inserted_column_count": row_meta.get("inserted_column_count"),
        "final_column_pool_size": row_meta.get("final_column_pool_size"),
        "accepted_flow_count": row.get("accepted_flow_count"),
        "acceptance_rate": row.get("acceptance_rate"),
        "total_end_to_end_runtime_seconds": row.get("total_end_to_end_runtime_seconds"),
        "total_pricing_runtime": row_meta.get("total_pricing_runtime"),
        "exact_pricing_runtime": row_meta.get("exact_pricing_runtime"),
        "final_rmp_runtime_seconds": row_meta.get("final_rmp_runtime_seconds"),
        "final_integer_rmp_status": row_meta.get("final_integer_rmp_status"),
        "hard_wall_clock_limit_reached": row_meta.get("hard_wall_clock_limit_reached", row.get("hard_wall_clock_limit_reached")),
        "termination_status": row.get("termination_status"),
        "raw_record": row,
    }


def evidence_key(row: Mapping[str, Any]) -> Tuple[str, int, str, int, str, str]:
    return str(row["scale"]), int(row["time_slots"]), str(row["load_label"]), int(row["formal_seed"]), str(row["algorithm"]), str(row["variant_id"])


def seed_key(row: Mapping[str, Any]) -> Tuple[str, int, str, int]:
    return str(row["scale"]), int(row["time_slots"]), str(row["load_label"]), int(row["formal_seed"])


def algorithm_seed_key(row: Mapping[str, Any]) -> Tuple[str, int, str, int, str]:
    base = seed_key(row)
    return base[0], base[1], base[2], base[3], str(row["algorithm"])


def require(condition: bool, message: str, errors: List[str]) -> None:
    if not condition:
        errors.append(message)


def absent_null_or_false(value: Any) -> bool:
    return value in (None, False)


def absent_null_or_zero(value: Any) -> bool:
    return value in (None, 0, 0.0)


def validate_config(config: Mapping[str, Any], expected_fingerprint: str, errors: List[str]) -> None:
    scenario = config.get("scenario") or {}
    output = scenario.get("output_settings") or {}
    require(config.get("version") == "v3", "config version mismatch", errors)
    require(config.get("plan_kind") == "formal_parallel_pricing", "config plan_kind mismatch", errors)
    require(config.get("integration_only") is False, "config integration_only mismatch", errors)
    require(config.get("smoke") is False, "config smoke mismatch", errors)
    require(scenario.get("scales") == ["S40"], "config scale mismatch", errors)
    require(scenario.get("time_slots") == 8, "config T mismatch", errors)
    require(scenario.get("loads") == [{"label": "High", "demand_multiplier": 0.8}], "config load mismatch", errors)
    require(scenario.get("deadline_profile") == "Balanced", "config deadline mismatch", errors)
    require(scenario.get("scale_flow_counts") == {"S40": 160}, "config F mismatch", errors)
    require(output.get("expected_total_evidence_rows") == 60, "config total evidence count mismatch", errors)
    require(output.get("expected_new_formal_rows") == 60, "config new count mismatch", errors)
    require(output.get("expected_reused_rows") == 0, "config reuse count mismatch", errors)
    require(output.get("algorithm_ids") == list(ALGORITHMS), "config algorithms mismatch", errors)
    require(output.get("worker_variant_ids") == ["W1", "W2", "W4"], "config worker variants mismatch", errors)
    require(sha256_fingerprint(config) == expected_fingerprint, "computed plan fingerprint mismatch", errors)


def validate_common_row(row: Mapping[str, Any], errors: List[str]) -> None:
    key = evidence_key(row)
    expected_worker = WORKERS.get(str(row["variant_id"]))
    require(row["scale"] == "S40", f"{key} scale mismatch", errors)
    require(row["time_slots"] == 8, f"{key} T mismatch", errors)
    require(row["load_label"] == LOAD, f"{key} load mismatch", errors)
    require(row["algorithm"] in ALGORITHMS, f"{key} algorithm mismatch", errors)
    require(row["variant_id"] in WORKERS, f"{key} unexpected worker variant", errors)
    require(row["pricing_worker_count"] == expected_worker, f"{key} worker count mismatch", errors)
    require(row["parallel_pricing_enabled"] is (expected_worker > 1), f"{key} parallel flag mismatch", errors)
    require(row["pricing_strategy"] == "hybrid", f"{key} pricing strategy mismatch", errors)
    require(row["hybrid_switch_iteration"] == 5, f"{key} hybrid switch mismatch", errors)
    if row["algorithm"] == "dwcg_pure":
        require(row["initialization_source"] == "pure", f"{key} Pure initialization source mismatch", errors)
        require(row["saginfe_initialization_used"] is False, f"{key} Pure SAGINFE initialization mismatch", errors)
        require(row["base_algorithm"] in (None, "dwcg_pure"), f"{key} Pure base algorithm mismatch", errors)
        require(absent_null_or_false(row["pro_family_algorithm"]), f"{key} Pure PRO-family flag mismatch", errors)
        require(row["pro_candidate_role"] in (None, False), f"{key} Pure PRO candidate role mismatch", errors)
        require(absent_null_or_false(row["selective_pfe_enabled"]), f"{key} Pure selective PFE enabled mismatch", errors)
        require(absent_null_or_false(row["selective_pfe_may_run"]), f"{key} Pure PFE flag mismatch", errors)
        for pfe_field in (
            "selective_pfe_call_count",
            "selective_pfe_candidate_count",
            "selective_pfe_round_count",
            "selective_pfe_positive_column_count",
            "selective_pfe_runtime_seconds",
        ):
            require(absent_null_or_zero(row[pfe_field]), f"{key} Pure {pfe_field} shows PFE activity", errors)
    if row["algorithm"] == "dwcg_saginfe_pro":
        require(row["initialization_source"] == "saginfe", f"{key} PRO initialization source mismatch", errors)
        require(row["base_algorithm"] in (None, "dwcg_saginfe_pro"), f"{key} PRO base algorithm mismatch", errors)
        require(row["pro_family_algorithm"] is True, f"{key} PRO family flag mismatch", errors)
        require(row["pro_candidate_role"] == "full_selective_pfe", f"{key} PRO candidate role mismatch", errors)
        require(row["saginfe_initialization_used"] is True, f"{key} PRO SAGINFE initialization mismatch", errors)
        require(row["selective_pfe_enabled"] is True, f"{key} PRO selective PFE enabled mismatch", errors)
        require(row["selective_pfe_may_run"] is True, f"{key} PRO selective PFE may-run mismatch", errors)
        require(row["selective_pfe_call_count"] is not None, f"{key} PRO selective PFE call count missing", errors)
        require(row["selective_pfe_candidate_count"] is not None, f"{key} PRO selective PFE candidate count missing", errors)
        require(row["selective_pfe_round_count"] is not None, f"{key} PRO selective PFE round count missing", errors)
        require(row["selective_pfe_positive_column_count"] is not None, f"{key} PRO selective PFE positive count missing", errors)
        require(row["selective_pfe_runtime_seconds"] is not None, f"{key} PRO selective PFE runtime missing", errors)
        require(row["incumbent_safeguard_used"] is True, f"{key} PRO incumbent safeguard mismatch", errors)
    require(row["path_validity"] is True, f"{key} path invalid", errors)
    require(row["capacity_validity"] is True, f"{key} capacity invalid", errors)
    require(row["runtime_accounting_valid"] is True, f"{key} runtime accounting invalid", errors)
    require(row["integration_only"] is False, f"{key} pilot row admitted", errors)
    require(row["run_category"] == "formal_paper_or_diagnostic", f"{key} run category mismatch", errors)
    require(row["configured_max_cg_iterations"] == 12, f"{key} max CG mismatch", errors)
    require(row["configured_max_exact_pricing_calls"] == 2080, f"{key} exact pricing cap mismatch", errors)
    errors.extend(f"{key} {message}" for message in recorded_runtime_limit_errors(row))
    require(row["configured_final_rmp_reserve_seconds"] == 300.0, f"{key} final RMP reserve mismatch", errors)
    require(row["configured_final_rmp_time_cap_seconds"] == 300.0, f"{key} final RMP cap mismatch", errors)
    require(row["solver_threads_per_worker"] == 1, f"{key} solver thread mismatch", errors)
    require(row["pricing_worker_threads"] == 1, f"{key} pricing worker thread mismatch", errors)
    require(row["deterministic_mode"] is True, f"{key} deterministic mode mismatch", errors)
    require(row["pricing_strategy_invariant_valid"] is True, f"{key} pricing invariant invalid", errors)
    require(row["exact_pricing_budget_policy"] == "pre_submit_logical_task_budget", f"{key} budget policy mismatch", errors)
    require(row["logical_pricing_task_count"] is not None, f"{key} logical task count missing", errors)
    require(row["exact_pricing_budget_used"] is not None, f"{key} exact budget used missing", errors)
    require(row["exact_pricing_call_count"] is not None, f"{key} exact pricing call count missing", errors)
    require(row["hard_wall_clock_limit_reached"] in (True, False), f"{key} hard-wall flag missing", errors)


def audit(output_dir: Path, config_path: Path, expected_commit: str | None, expected_fingerprint: str | None) -> Dict[str, Any]:
    errors: List[str] = []
    config = read_json(config_path)
    plan_fp = expected_fingerprint or sha256_fingerprint(config)
    validate_config(config, plan_fp, errors)
    manifest = read_json(output_dir / "manifest.json") if (output_dir / "manifest.json").exists() else {}
    rows = [normalized_row(row) for row in read_jsonl(output_dir / "runs.jsonl")]
    keys = [evidence_key(row) for row in rows]
    expected_keys = {("S40", 8, LOAD, seed, algorithm, worker) for seed in range(10) for algorithm in ALGORITHMS for worker in WORKERS}
    actual_keys = set(keys)
    duplicates = [key for key, count in Counter(keys).items() if count != 1]

    require(manifest.get("plan_fingerprint") == plan_fp, "manifest plan fingerprint mismatch", errors)
    require(manifest.get("run_category") == "formal_paper_or_diagnostic", "manifest is not formal", errors)
    require(manifest.get("integration_only") is False, "manifest integration_only mismatch", errors)
    require(len(rows) == 60, f"expected 60 rows, found {len(rows)}", errors)
    require(not duplicates, f"duplicate evidence keys: {duplicates[:10]}", errors)
    require(not sorted(expected_keys - actual_keys), f"missing evidence keys: {sorted(expected_keys - actual_keys)[:10]}", errors)
    require(not sorted(actual_keys - expected_keys), f"extra evidence keys: {sorted(actual_keys - expected_keys)[:10]}", errors)
    require(Counter(row["algorithm"] for row in rows) == Counter({"dwcg_pure": 30, "dwcg_saginfe_pro": 30}), "algorithm counts mismatch", errors)
    require(Counter(row["variant_id"] for row in rows) == Counter({"W1": 20, "W2": 20, "W4": 20}), "worker counts mismatch", errors)
    require(Counter((row["algorithm"], row["variant_id"]) for row in rows) == Counter({(algorithm, worker): 10 for algorithm in ALGORITHMS for worker in WORKERS}), "algorithm-worker counts mismatch", errors)
    require(Counter(row["load_label"] for row in rows) == Counter({LOAD: 60}), "load count mismatch", errors)
    if expected_commit:
        for row in rows:
            require(row["git_commit"] == expected_commit, f"{evidence_key(row)} git commit mismatch", errors)

    for row in rows:
        validate_common_row(row, errors)
        snapshot_plan = (row.get("configuration_snapshot") or {}).get("plan") or {}
        require(sha256_fingerprint(snapshot_plan) == plan_fp, f"{evidence_key(row)} plan snapshot fingerprint mismatch", errors)

    grouped_by_seed: Dict[Tuple[str, int, str, int], List[Mapping[str, Any]]] = defaultdict(list)
    grouped_by_algorithm_seed: Dict[Tuple[str, int, str, int, str], List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped_by_seed[seed_key(row)].append(row)
        grouped_by_algorithm_seed[algorithm_seed_key(row)].append(row)
    for scenario, group in grouped_by_seed.items():
        require({(row["algorithm"], row["variant_id"]) for row in group} == {(algorithm, worker) for algorithm in ALGORITHMS for worker in WORKERS}, f"{scenario} missing six-way algorithm/worker records", errors)
        for field in ("topology_fingerprint", "base_flow_fingerprint", "full_instance_fingerprint"):
            require(len({row.get(field) for row in group}) == 1, f"{scenario} {field} mismatch across algorithms/workers", errors)
    for scenario, group in grouped_by_algorithm_seed.items():
        require({row["variant_id"] for row in group} == set(WORKERS), f"{scenario} missing worker variant", errors)
        for field in (
            "pricing_strategy",
            "hybrid_switch_iteration",
            "configured_max_cg_iterations",
            "configured_max_exact_pricing_calls",
            "configured_hard_time_limit_seconds",
            "configured_final_rmp_reserve_seconds",
            "configured_final_rmp_time_cap_seconds",
            "deterministic_mode",
            "logical_pricing_task_count",
            "exact_pricing_budget_used",
            "exact_pricing_call_count",
            "deterministic_work_budget_exhausted",
        ):
            require(len({row.get(field) for row in group}) == 1, f"{scenario} within-algorithm required equivalence field {field} mismatch", errors)
        if scenario[-1] == "dwcg_saginfe_pro":
            for field in (
                "selective_pfe_enabled",
                "selective_pfe_may_run",
                "selective_pfe_call_count",
                "selective_pfe_candidate_count",
                "selective_pfe_round_count",
                "selective_pfe_positive_column_count",
            ):
                require(len({row.get(field) for row in group}) == 1, f"{scenario} PRO PFE equivalence field {field} mismatch", errors)

    return {
        "pass": not errors,
        "errors": errors,
        "record_count": len(rows),
        "new_count": len(rows),
        "reused_count": 0,
        "scenario_count": len(grouped_by_algorithm_seed),
        "seed_scenario_count": len(grouped_by_seed),
        "counts_by_algorithm": dict(Counter(row["algorithm"] for row in rows)),
        "counts_by_worker": dict(Counter(row["variant_id"] for row in rows)),
        "plan_fingerprint": plan_fp,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit V3 parallel-pricing formal evidence.")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--expected-git-commit")
    parser.add_argument("--expected-plan-fingerprint")
    parser.add_argument("--audit-json", type=Path)
    args = parser.parse_args(argv)
    result = audit(args.output_dir, args.config, args.expected_git_commit, args.expected_plan_fingerprint)
    print(json.dumps(result, indent=2, sort_keys=True))
    write_optional(args.audit_json, result)
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
