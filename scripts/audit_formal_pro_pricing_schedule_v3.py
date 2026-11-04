"""Read-only audit for the matched V3 DWCG-SAGINFE-PRO pricing-schedule study."""

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
from formal_runner import is_resume_skippable_record


LOADS = ("Medium", "High")
SCHEDULES = ("EX", "ESV", "HYB-S2", "HYB-S5", "HYB-S7")
NEW_VARIANT_IDS = ("EX", "ESV", "HYB_S2", "HYB_S7")
ALGORITHM_ID = "dwcg_saginfe_pro"
SCHEDULE_BY_VARIANT = {
    "EX": "EX",
    "ESV": "ESV",
    "HYB_S2": "HYB-S2",
    "HYB_S5": "HYB-S5",
    "HYB_S7": "HYB-S7",
}
EXPECTED_STRATEGY = {
    "EX": ("exact", None),
    "ESV": ("early_stop", None),
    "HYB-S2": ("hybrid", 2),
    "HYB-S5": ("hybrid", 5),
    "HYB-S7": ("hybrid", 7),
}
EXPECTED_NEW_PHASES_AND_CALLS = {
    "EX": (0, 12, 0, 1920),
    "ESV": (12, 0, 1920, 0),
    "HYB-S2": (2, 10, 320, 1600),
    "HYB-S7": (7, 5, 1120, 800),
}
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


def variant_to_schedule(variant_id: Any, row: Mapping[str, Any]) -> str | None:
    if row.get("pricing_schedule") in SCHEDULES:
        return str(row["pricing_schedule"])
    if meta(row).get("pricing_schedule") in SCHEDULES:
        return str(meta(row)["pricing_schedule"])
    return SCHEDULE_BY_VARIANT.get(str(variant_id))


def normalized_new_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    key = row.get("key") or {}
    row_meta = meta(row)
    schedule = variant_to_schedule(key.get("variant_id"), row)
    return {
        "source_kind": "new",
        "reuse_classification": "new_formal_run",
        "scale": key.get("scale"),
        "time_slots": int(key.get("time_slots", 0)),
        "load_label": key.get("load_label"),
        "formal_seed": int(key.get("formal_seed", -1)),
        "algorithm": key.get("algorithm"),
        "variant_id": key.get("variant_id"),
        "pricing_schedule": schedule,
        "pricing_strategy": row_meta.get("pricing_strategy"),
        "hybrid_switch_iteration": row_meta.get("hybrid_switch_iteration"),
        "topology_fingerprint": row.get("topology_fingerprint"),
        "base_flow_fingerprint": row.get("base_flow_fingerprint"),
        "full_instance_fingerprint": row.get("full_instance_fingerprint"),
        "path_validity": row.get("path_validity"),
        "capacity_validity": row.get("capacity_validity"),
        "runtime_accounting_valid": row_meta.get("runtime_accounting_valid", row.get("runtime_accounting_valid")),
        "hard_wall_clock_limit_reached": row_meta.get("hard_wall_clock_limit_reached", row.get("hard_wall_clock_limit_reached")),
        "integration_only": row.get("integration_only"),
        "run_category": row.get("run_category"),
        "git_commit": row.get("git_commit"),
        "configuration_snapshot": row.get("configuration_snapshot") or {},
        "configured_max_cg_iterations": row_meta.get("configured_max_cg_iterations"),
        "configured_max_exact_pricing_calls": row_meta.get("configured_max_exact_pricing_calls"),
        "configured_hard_time_limit_seconds": row_meta.get("configured_hard_time_limit_seconds"),
        "configured_final_rmp_reserve_seconds": row_meta.get("configured_final_rmp_reserve_seconds"),
        "configured_final_rmp_time_cap_seconds": row_meta.get("configured_final_rmp_time_cap_seconds"),
        "pricing_worker_count": row_meta.get("pricing_worker_count"),
        "parallel_pricing_enabled": row_meta.get("parallel_pricing_enabled"),
        "solver_threads_per_worker": row_meta.get("solver_threads_per_worker"),
        "deterministic_mode": row_meta.get("deterministic_mode"),
        "pricing_strategy_invariant_valid": row_meta.get("pricing_strategy_invariant_valid"),
        "initialization_source": row_meta.get("initialization_source"),
        "saginfe_initialization_used": row_meta.get("saginfe_initialization_used"),
        "pro_family_algorithm": row_meta.get("pro_family_algorithm"),
        "pro_candidate_role": row_meta.get("pro_candidate_role"),
        "selective_pfe_enabled": row_meta.get("selective_pfe_enabled"),
        "selective_pfe_may_run": row_meta.get("selective_pfe_may_run"),
        "selective_pfe_call_count": row_meta.get("selective_pfe_call_count"),
        "selective_pfe_candidate_count": row_meta.get("selective_pfe_candidate_count"),
        "selective_pfe_round_count": row_meta.get("selective_pfe_round_count"),
        "selective_pfe_positive_column_count": row_meta.get("selective_pfe_positive_column_count"),
        "selective_pfe_runtime_seconds": row_meta.get("selective_pfe_runtime_seconds"),
        "early_stop_phase_count": row_meta.get("early_stop_phase_count"),
        "strict_exact_phase_count": row_meta.get("strict_exact_phase_count"),
        "initial_saginfe_accepted_flow_count": row_meta.get("initial_saginfe_accepted_flow_count"),
        "final_acceptance_gain_over_saginfe": row_meta.get("final_acceptance_gain_over_saginfe"),
        "initialization_runtime_seconds": row_meta.get("initialization_runtime_seconds"),
        "initialization_generation_runtime_seconds": row_meta.get("initialization_generation_runtime_seconds"),
        "final_integer_rmp_status": row_meta.get("final_integer_rmp_status"),
        "logical_pricing_task_count": row_meta.get("logical_pricing_task_count"),
        "exact_pricing_budget_used": row_meta.get("exact_pricing_budget_used"),
        "cg_iteration_count": row_meta.get("cg_iteration_count"),
        "generated_column_count": row_meta.get("generated_column_count"),
        "inserted_column_count": row_meta.get("inserted_column_count"),
        "final_column_pool_size": row_meta.get("final_column_pool_size"),
        "final_ordered_column_pool_fingerprint": row_meta.get("final_ordered_column_pool_fingerprint"),
        "exact_pricing_call_count": row_meta.get("exact_pricing_call_count"),
        "early_stop_pricing_call_count": row_meta.get("early_stop_pricing_call_count"),
        "strict_exact_pricing_call_count": row_meta.get("strict_exact_pricing_call_count"),
        "accepted_flow_count": row.get("accepted_flow_count"),
        "acceptance_rate": row.get("acceptance_rate"),
        "total_end_to_end_runtime_seconds": row.get("total_end_to_end_runtime_seconds"),
        "total_pricing_runtime": row_meta.get("total_pricing_runtime"),
        "exact_pricing_runtime": row_meta.get("exact_pricing_runtime"),
        "final_rmp_runtime_seconds": row_meta.get("final_rmp_runtime_seconds"),
        "termination_status": row.get("termination_status"),
        "raw_record": row,
    }


def normalized_reuse_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "source_kind": "reused",
        "reuse_classification": row.get("reuse_classification"),
        "scale": row.get("scale"),
        "time_slots": int(row.get("time_slots", 0)),
        "load_label": row.get("load_label"),
        "formal_seed": int(row.get("formal_seed", -1)),
        "algorithm": row.get("algorithm"),
        "variant_id": row.get("source_variant_id"),
        "pricing_schedule": row.get("normalized_pricing_schedule"),
        "pricing_strategy": row.get("pricing_strategy"),
        "hybrid_switch_iteration": row.get("hybrid_switch_iteration"),
        "topology_fingerprint": row.get("topology_fingerprint"),
        "base_flow_fingerprint": row.get("base_flow_fingerprint"),
        "full_instance_fingerprint": row.get("full_instance_fingerprint"),
        "path_validity": row.get("path_validity"),
        "capacity_validity": row.get("capacity_validity"),
        "runtime_accounting_valid": row.get("runtime_accounting_valid"),
        "hard_wall_clock_limit_reached": row.get("hard_wall_clock_limit_reached"),
        "integration_only": False,
        "run_category": "formal_paper_or_diagnostic",
        "git_commit": row.get("source_commit"),
        "source_commit": row.get("source_commit"),
        "source_tag": row.get("source_tag"),
        "source_path": row.get("source_path"),
        "configured_max_cg_iterations": row.get("configured_max_cg_iterations"),
        "configured_max_exact_pricing_calls": row.get("configured_max_exact_pricing_calls"),
        "configured_hard_time_limit_seconds": row.get("configured_hard_time_limit_seconds"),
        "configured_final_rmp_reserve_seconds": row.get("configured_final_rmp_reserve_seconds"),
        "configured_final_rmp_time_cap_seconds": row.get("configured_final_rmp_time_cap_seconds"),
        "pricing_worker_count": row.get("pricing_worker_count"),
        "parallel_pricing_enabled": row.get("parallel_pricing_enabled"),
        "solver_threads_per_worker": row.get("solver_threads_per_worker"),
        "deterministic_mode": row.get("deterministic_mode"),
        "pricing_strategy_invariant_valid": row.get("pricing_strategy_invariant_valid"),
        "initialization_source": row.get("initialization_source"),
        "saginfe_initialization_used": row.get("saginfe_initialization_used"),
        "pro_family_algorithm": True,
        "pro_candidate_role": "full_selective_pfe",
        "selective_pfe_enabled": row.get("selective_pfe_enabled"),
        "selective_pfe_may_run": row.get("selective_pfe_may_run"),
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
        "logical_pricing_task_count": row.get("logical_pricing_task_count"),
        "exact_pricing_budget_used": row.get("exact_pricing_budget_used"),
        "cg_iteration_count": row.get("cg_iteration_count"),
        "generated_column_count": row.get("generated_column_count"),
        "inserted_column_count": row.get("inserted_column_count"),
        "final_column_pool_size": row.get("final_column_pool_size"),
        "final_ordered_column_pool_fingerprint": row.get("final_ordered_column_pool_fingerprint"),
        "exact_pricing_call_count": row.get("exact_pricing_call_count"),
        "early_stop_pricing_call_count": row.get("early_stop_pricing_call_count"),
        "strict_exact_pricing_call_count": row.get("strict_exact_pricing_call_count"),
        "accepted_flow_count": row.get("accepted_flow_count"),
        "acceptance_rate": row.get("acceptance_rate"),
        "total_end_to_end_runtime_seconds": row.get("total_end_to_end_runtime_seconds"),
        "total_pricing_runtime": row.get("total_pricing_runtime"),
        "exact_pricing_runtime": row.get("exact_pricing_runtime"),
        "final_rmp_runtime_seconds": row.get("final_rmp_runtime_seconds"),
        "termination_status": row.get("termination_status"),
        "raw_record": row,
    }


def evidence_key(row: Mapping[str, Any]) -> Tuple[str, int, str, int, str]:
    return str(row["scale"]), int(row["time_slots"]), str(row["load_label"]), int(row["formal_seed"]), str(row["pricing_schedule"])


def scenario_key(row: Mapping[str, Any]) -> Tuple[str, int, str, int]:
    return str(row["scale"]), int(row["time_slots"]), str(row["load_label"]), int(row["formal_seed"])


def require(condition: bool, message: str, errors: List[str]) -> None:
    if not condition:
        errors.append(message)


def validate_config(config: Mapping[str, Any], expected_fingerprint: str, errors: List[str]) -> None:
    scenario = config.get("scenario") or {}
    output = scenario.get("output_settings") or {}
    require(config.get("version") == "v3", "config version mismatch", errors)
    require(config.get("plan_kind") == "formal_pricing_strategy", "config plan_kind mismatch", errors)
    require(config.get("integration_only") is False, "config integration_only mismatch", errors)
    require(config.get("smoke") is False, "config smoke mismatch", errors)
    require(scenario.get("scales") == ["S40"], "config scale mismatch", errors)
    require(scenario.get("time_slots") == 8, "config T mismatch", errors)
    require(scenario.get("loads") == [{"label": "Medium", "demand_multiplier": 0.6}, {"label": "High", "demand_multiplier": 0.8}], "config loads mismatch", errors)
    require(scenario.get("deadline_profile") == "Balanced", "config deadline mismatch", errors)
    require(scenario.get("scale_flow_counts") == {"S40": 160}, "config F mismatch", errors)
    require(output.get("expected_total_evidence_rows") == 100, "config total evidence count mismatch", errors)
    require(output.get("expected_new_formal_rows") == 80, "config new count mismatch", errors)
    require(output.get("expected_reused_rows") == 20, "config reuse count mismatch", errors)
    require(output.get("algorithm_id") == ALGORITHM_ID, "config algorithm mismatch", errors)
    require(output.get("matched_pure_pricing_schedule_source") == "outputs/pricing_schedule_v3_100_7cee2fe/pricing_schedule_runs.jsonl", "config Pure match source mismatch", errors)
    require(sha256_fingerprint(config) == expected_fingerprint, "computed plan fingerprint mismatch", errors)


def validate_common_row(row: Mapping[str, Any], errors: List[str]) -> None:
    key = evidence_key(row)
    require(row["scale"] == "S40", f"{key} scale mismatch", errors)
    require(row["time_slots"] == 8, f"{key} T mismatch", errors)
    require(row["load_label"] in LOADS, f"{key} load mismatch", errors)
    require(row["algorithm"] == ALGORITHM_ID, f"{key} algorithm mismatch", errors)
    require(row["pricing_schedule"] in SCHEDULES, f"{key} schedule mismatch", errors)
    require(row["path_validity"] is True, f"{key} path invalid", errors)
    require(row["capacity_validity"] is True, f"{key} capacity invalid", errors)
    require(row["runtime_accounting_valid"] is True, f"{key} runtime accounting invalid", errors)
    require(row["integration_only"] is False, f"{key} integration/pilot row admitted", errors)
    require(row["run_category"] == "formal_paper_or_diagnostic", f"{key} run category mismatch", errors)
    require(row["configured_max_cg_iterations"] == 12, f"{key} max CG mismatch", errors)
    require(row["configured_max_exact_pricing_calls"] == 2080, f"{key} exact pricing cap mismatch", errors)
    errors.extend(f"{key} {message}" for message in recorded_runtime_limit_errors(row))
    require(row["configured_final_rmp_reserve_seconds"] == 300.0, f"{key} final RMP reserve mismatch", errors)
    require(row["configured_final_rmp_time_cap_seconds"] == 300.0, f"{key} final RMP cap mismatch", errors)
    require(row["pricing_worker_count"] == 1, f"{key} pricing worker mismatch", errors)
    require(row["parallel_pricing_enabled"] is False, f"{key} parallel pricing enabled", errors)
    require(row["solver_threads_per_worker"] == 1, f"{key} thread mismatch", errors)
    require(row["deterministic_mode"] is True, f"{key} deterministic mode mismatch", errors)
    require(row["pricing_strategy_invariant_valid"] is True, f"{key} pricing invariant invalid", errors)
    require(row["initialization_source"] == "saginfe", f"{key} initialization source mismatch", errors)
    require(row["saginfe_initialization_used"] is True, f"{key} SAGINFE initialization mismatch", errors)
    require(row["pro_family_algorithm"] is True, f"{key} PRO family flag mismatch", errors)
    require(row["pro_candidate_role"] == "full_selective_pfe", f"{key} PRO candidate role mismatch", errors)
    require(row["selective_pfe_enabled"] is True, f"{key} selective PFE enabled mismatch", errors)
    require(row["selective_pfe_may_run"] is True, f"{key} selective PFE may-run mismatch", errors)
    require(row["selective_pfe_call_count"] is not None, f"{key} selective PFE call count missing", errors)
    require(row["selective_pfe_candidate_count"] is not None, f"{key} selective PFE candidate count missing", errors)
    require(row["selective_pfe_round_count"] is not None, f"{key} selective PFE round count missing", errors)
    expected_strategy, expected_switch = EXPECTED_STRATEGY[str(row["pricing_schedule"])]
    require(row["pricing_strategy"] == expected_strategy, f"{key} pricing strategy mismatch", errors)
    require(row["hybrid_switch_iteration"] == expected_switch, f"{key} hybrid switch mismatch", errors)
    if row["pricing_strategy"] == "early_stop":
        require((row["early_stop_pricing_call_count"] or 0) > 0, f"{key} early-stop calls missing", errors)
    if row["pricing_strategy"] == "exact":
        require((row["early_stop_pricing_call_count"] or 0) == 0, f"{key} exact row has early-stop calls", errors)


def validate_new_row_schedule_semantics(row: Mapping[str, Any], errors: List[str]) -> None:
    key = evidence_key(row)
    expected = EXPECTED_NEW_PHASES_AND_CALLS.get(str(row["pricing_schedule"]))
    if expected is None:
        return
    early_phases, strict_phases, early_calls, strict_calls = expected
    require(row["early_stop_phase_count"] == early_phases, f"{key} early phase count mismatch", errors)
    require(row["strict_exact_phase_count"] == strict_phases, f"{key} strict phase count mismatch", errors)
    require(row["early_stop_pricing_call_count"] == early_calls, f"{key} early call count mismatch", errors)
    require(row["strict_exact_pricing_call_count"] == strict_calls, f"{key} strict call count mismatch", errors)
    require(row["logical_pricing_task_count"] == 1920, f"{key} logical pricing task count mismatch", errors)
    require(row["exact_pricing_budget_used"] == 1920, f"{key} exact budget use mismatch", errors)


def pure_reference_rows(path: Path) -> List[Mapping[str, Any]]:
    return [row for row in read_jsonl(path) if row.get("algorithm") == "dwcg_pure"]


def audit(output_dir: Path, config_path: Path, reuse_manifest_path: Path, expected_commit: str | None, expected_fingerprint: str | None, pure_reference_path: Path | None = None) -> Dict[str, Any]:
    errors: List[str] = []
    config = read_json(config_path)
    plan_fp = expected_fingerprint or sha256_fingerprint(config)
    validate_config(config, plan_fp, errors)
    manifest = read_json(output_dir / "manifest.json") if (output_dir / "manifest.json").exists() else {}
    raw_new_rows = read_jsonl(output_dir / "runs.jsonl")
    ignored_diagnostic_rows = [row for row in raw_new_rows if not is_resume_skippable_record(row)]
    new_rows = [normalized_new_row(row) for row in raw_new_rows if is_resume_skippable_record(row)]
    reuse_manifest = read_json(reuse_manifest_path)
    reused_rows = [normalized_reuse_row(row) for row in reuse_manifest.get("rows", [])]
    rows = new_rows + reused_rows
    keys = [evidence_key(row) for row in rows]
    expected_keys = {("S40", 8, load, seed, schedule) for load in LOADS for seed in range(10) for schedule in SCHEDULES}
    duplicates = [key for key, count in Counter(keys).items() if count != 1]
    actual_keys = set(keys)

    require(manifest.get("plan_fingerprint") == plan_fp, "new-run manifest plan fingerprint mismatch", errors)
    require(manifest.get("run_category") == "formal_paper_or_diagnostic", "new-run manifest is not formal", errors)
    require(manifest.get("integration_only") is False, "new-run manifest integration_only mismatch", errors)
    require(len(new_rows) == 80, f"expected 80 new rows, found {len(new_rows)}", errors)
    require(len(reused_rows) == 20, f"expected 20 reused rows, found {len(reused_rows)}", errors)
    require(len(rows) == 100, f"expected 100 evidence rows, found {len(rows)}", errors)
    require(not duplicates, f"duplicate evidence keys: {duplicates[:10]}", errors)
    require(not sorted(expected_keys - actual_keys), f"missing evidence keys: {sorted(expected_keys - actual_keys)[:10]}", errors)
    require(not sorted(actual_keys - expected_keys), f"extra evidence keys: {sorted(actual_keys - expected_keys)[:10]}", errors)
    require(Counter(row["load_label"] for row in rows) == Counter({"Medium": 50, "High": 50}), "load counts mismatch", errors)
    require(Counter(row["pricing_schedule"] for row in rows) == Counter({schedule: 20 for schedule in SCHEDULES}), "schedule counts mismatch", errors)
    require(Counter(row["source_kind"] for row in rows) == Counter({"new": 80, "reused": 20}), "source-kind counts mismatch", errors)
    require(all(row["pricing_schedule"] != "HYB-S5" for row in new_rows), "HYB-S5 was rerun as new formal evidence", errors)
    require(all(row["pricing_schedule"] == "HYB-S5" and row["reuse_classification"] == "strict_exact_reuse" for row in reused_rows), "reuse classification mismatch", errors)
    require(all(row["algorithm"] == ALGORITHM_ID for row in rows), "non-PRO row admitted", errors)
    if expected_commit:
        for row in new_rows:
            require(row["git_commit"] == expected_commit, f"{evidence_key(row)} git commit mismatch", errors)

    for row in rows:
        validate_common_row(row, errors)
        if row["source_kind"] == "new":
            validate_new_row_schedule_semantics(row, errors)
            snapshot_plan = (row.get("configuration_snapshot") or {}).get("plan") or {}
            require(sha256_fingerprint(snapshot_plan) == plan_fp, f"{evidence_key(row)} plan snapshot fingerprint mismatch", errors)
            require((row.get("configuration_snapshot") or {}).get("execution_subset", {}).get("pricing_schedule") == row["pricing_schedule"], f"{evidence_key(row)} execution subset schedule mismatch", errors)

    grouped: Dict[Tuple[str, int, str, int], List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[scenario_key(row)].append(row)
    for scenario, group in grouped.items():
        require({row["pricing_schedule"] for row in group} == set(SCHEDULES), f"{scenario} missing schedule", errors)
        for field in ("topology_fingerprint", "base_flow_fingerprint", "full_instance_fingerprint"):
            require(len({row.get(field) for row in group}) == 1, f"{scenario} {field} mismatch across schedules", errors)
        for field in (
            "initialization_source",
            "saginfe_initialization_used",
            "selective_pfe_enabled",
            "selective_pfe_may_run",
            "pricing_worker_count",
            "parallel_pricing_enabled",
            "solver_threads_per_worker",
        ):
            require(len({row.get(field) for row in group}) == 1, f"{scenario} fixed PRO field {field} mismatch across schedules", errors)

    pure_path = pure_reference_path or Path("outputs/pricing_schedule_v3_100_7cee2fe/pricing_schedule_runs.jsonl")
    if pure_path.exists():
        pure_rows = pure_reference_rows(pure_path)
        pure_grouped: Dict[Tuple[str, int, str, int], List[Mapping[str, Any]]] = defaultdict(list)
        for row in pure_rows:
            pure_grouped[scenario_key(row)].append(row)
        for scenario, group in grouped.items():
            pure_group = pure_grouped.get(scenario, [])
            require(len(pure_group) == 5, f"{scenario} missing frozen Pure reference rows", errors)
            for field in ("topology_fingerprint", "base_flow_fingerprint", "full_instance_fingerprint"):
                expected = {row.get(field) for row in pure_group}
                actual = {row.get(field) for row in group}
                require(len(expected) == 1 and len(actual) == 1 and expected == actual, f"{scenario} Pure/PRO {field} mismatch", errors)
    else:
        errors.append(f"Pure reference path missing: {pure_path}")

    return {
        "pass": not errors,
        "errors": errors,
        "record_count": len(rows),
        "raw_new_record_count": len(raw_new_rows),
        "ignored_diagnostic_new_record_count": len(ignored_diagnostic_rows),
        "new_count": len(new_rows),
        "reused_count": len(reused_rows),
        "scenario_count": len(grouped),
        "counts_by_load": dict(Counter(row["load_label"] for row in rows)),
        "counts_by_schedule": dict(Counter(row["pricing_schedule"] for row in rows)),
        "plan_fingerprint": plan_fp,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit V3 pricing-schedule formal evidence.")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--reuse-manifest-json", required=True, type=Path)
    parser.add_argument("--expected-git-commit")
    parser.add_argument("--expected-plan-fingerprint")
    parser.add_argument("--pure-reference-runs", type=Path, default=Path("outputs/pricing_schedule_v3_100_7cee2fe/pricing_schedule_runs.jsonl"))
    parser.add_argument("--audit-json", type=Path)
    args = parser.parse_args(argv)
    result = audit(args.output_dir, args.config, args.reuse_manifest_json, args.expected_git_commit, args.expected_plan_fingerprint, args.pure_reference_runs)
    print(json.dumps(result, indent=2, sort_keys=True))
    write_optional(args.audit_json, result)
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
