"""Read-only audit for V3 Same-flow Temporal Control evidence."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from record_compatibility import normalize_record, resolve_input_path

from calibration_runner import sha256_fingerprint, write_json_atomic
from formal_runner import is_resume_skippable_record


ALGORITHMS = ("saginfe", "dwcg_pure", "dwcg_saginfe_pro")
TIME_SLOTS = (6, 8, 10, 12)
NEW_TIME_SLOTS = (8, 10, 12)
LOAD = "Medium"
EXPECTED_SLACK_HISTOGRAM = {"2": 48, "3": 64, "4": 48}


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    path = resolve_input_path(path)
    if not path.exists():
        return []
    return [normalize_record(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def meta(row: Mapping[str, Any]) -> Mapping[str, Any]:
    if row.get("algorithm") == "saginfe" or row.get("key", {}).get("algorithm") == "saginfe":
        return row.get("heuristic_metadata") or {}
    return row.get("dwcg_metadata") or {}


def row_algorithm(row: Mapping[str, Any]) -> str:
    return str(row.get("algorithm") or row.get("key", {}).get("algorithm"))


def normalized_new_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    key = row.get("key") or {}
    m = meta(row)
    return {
        "source_kind": "new",
        "scale": key.get("scale"),
        "time_slots": int(key.get("time_slots", 0)),
        "load_label": key.get("load_label"),
        "formal_seed": int(key.get("formal_seed", -1)),
        "algorithm": key.get("algorithm"),
        "git_commit": row.get("git_commit"),
        "total_flow_count": row.get("total_flow_count"),
        "accepted_flow_count": row.get("accepted_flow_count"),
        "acceptance_rate": row.get("acceptance_rate"),
        "total_end_to_end_runtime_seconds": row.get("total_end_to_end_runtime_seconds"),
        "termination_status": row.get("termination_status"),
        "path_validity": row.get("path_validity"),
        "capacity_validity": row.get("capacity_validity"),
        "runtime_accounting_valid": m.get("runtime_accounting_valid", row.get("runtime_accounting_valid")),
        "hard_wall_clock_limit_reached": m.get("hard_wall_clock_limit_reached", row.get("hard_wall_clock_limit_reached")),
        "integration_only": row.get("integration_only"),
        "run_category": row.get("run_category"),
        "configuration_snapshot": row.get("configuration_snapshot") or {},
        "topology_fingerprint": row.get("topology_fingerprint"),
        "base_flow_fingerprint": row.get("base_flow_fingerprint"),
        "full_instance_fingerprint": row.get("full_instance_fingerprint"),
        "physical_base_fingerprint": row.get("physical_base_fingerprint") or m.get("physical_base_fingerprint"),
        "temporal_prefix_fingerprint": row.get("temporal_prefix_fingerprint") or m.get("temporal_prefix_fingerprint"),
        "common_temporal_prefix_fingerprints": row.get("common_temporal_prefix_fingerprints") or m.get("common_temporal_prefix_fingerprints"),
        "non_temporal_flow_fingerprint": row.get("non_temporal_flow_fingerprint") or m.get("non_temporal_flow_fingerprint"),
        "paired_timing_latent_fingerprint": row.get("paired_timing_latent_fingerprint") or m.get("paired_timing_latent_fingerprint"),
        "full_temporal_flow_fingerprint": row.get("full_temporal_flow_fingerprint") or m.get("full_temporal_flow_fingerprint"),
        "strict_same_flow_fingerprint": row.get("strict_same_flow_fingerprint") or m.get("strict_same_flow_fingerprint"),
        "temporal_canonical_time_slots": row.get("temporal_canonical_time_slots") or m.get("temporal_canonical_time_slots"),
        "temporal_requested_time_slots": row.get("temporal_requested_time_slots") or m.get("temporal_requested_time_slots"),
        "temporal_supported_time_slots": row.get("temporal_supported_time_slots") or m.get("temporal_supported_time_slots"),
        "temporal_flow_anchor_time_slots": row.get("temporal_flow_anchor_time_slots") or m.get("temporal_flow_anchor_time_slots"),
        "temporal_teg_node_count": row.get("temporal_teg_node_count") or m.get("temporal_teg_node_count"),
        "temporal_storage_edge_count": row.get("temporal_storage_edge_count") or m.get("temporal_storage_edge_count"),
        "temporal_communication_edge_count": row.get("temporal_communication_edge_count") or m.get("temporal_communication_edge_count"),
        "temporal_arrival_time_histogram": row.get("temporal_arrival_time_histogram") or m.get("temporal_arrival_time_histogram"),
        "temporal_slack_histogram": row.get("temporal_slack_histogram") or m.get("temporal_slack_histogram"),
        "temporal_deadline_time_histogram": row.get("temporal_deadline_time_histogram") or m.get("temporal_deadline_time_histogram"),
        "temporal_minimum_slack": row.get("temporal_minimum_slack") or m.get("temporal_minimum_slack"),
        "temporal_maximum_slack": row.get("temporal_maximum_slack") or m.get("temporal_maximum_slack"),
        "temporal_mean_slack": row.get("temporal_mean_slack") or m.get("temporal_mean_slack"),
        "temporal_clipping_count": row.get("temporal_clipping_count") if row.get("temporal_clipping_count") is not None else m.get("temporal_clipping_count"),
        "deadline_profile": row.get("deadline_profile") or m.get("deadline_profile"),
        "pricing_worker_count": m.get("pricing_worker_count", 1),
        "parallel_pricing_enabled": m.get("parallel_pricing_enabled", False),
        "solver_threads_per_worker": m.get("solver_threads_per_worker", 1),
        "configured_max_cg_iterations": m.get("configured_max_cg_iterations"),
        "configured_max_exact_pricing_calls": m.get("configured_max_exact_pricing_calls"),
        "configured_final_rmp_reserve_seconds": m.get("configured_final_rmp_reserve_seconds"),
        "configured_final_rmp_time_cap_seconds": m.get("configured_final_rmp_time_cap_seconds"),
        "raw_record": row,
    }


def normalized_reuse_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    data = dict(row)
    m = meta(row)
    data["source_kind"] = "reused"
    data["git_commit"] = row.get("source_execution_commit")
    data["integration_only"] = False
    data["run_category"] = "formal_paper_or_diagnostic"
    data["temporal_flow_anchor_time_slots"] = row.get("temporal_flow_anchor_time_slots", 6)
    data["pricing_worker_count"] = m.get("pricing_worker_count", row.get("pricing_worker_count"))
    data["parallel_pricing_enabled"] = m.get("parallel_pricing_enabled", row.get("parallel_pricing_enabled"))
    data["solver_threads_per_worker"] = m.get("solver_threads_per_worker", row.get("solver_threads_per_worker"))
    data["configured_max_cg_iterations"] = m.get("configured_max_cg_iterations", row.get("configured_max_cg_iterations"))
    data["configured_max_exact_pricing_calls"] = m.get("configured_max_exact_pricing_calls", row.get("configured_max_exact_pricing_calls"))
    data["configured_final_rmp_reserve_seconds"] = m.get("configured_final_rmp_reserve_seconds", row.get("configured_final_rmp_reserve_seconds"))
    data["configured_final_rmp_time_cap_seconds"] = m.get("configured_final_rmp_time_cap_seconds", row.get("configured_final_rmp_time_cap_seconds"))
    return data


def evidence_key(row: Mapping[str, Any]) -> Tuple[int, str, int, str]:
    return int(row["time_slots"]), str(row["load_label"]), int(row["formal_seed"]), str(row["algorithm"])


def scenario_key(row: Mapping[str, Any]) -> Tuple[int, int]:
    return int(row["time_slots"]), int(row["formal_seed"])


def require(condition: bool, message: str, errors: List[str]) -> None:
    if not condition:
        errors.append(message)


def validate_config(config: Mapping[str, Any], expected_fingerprint: str, errors: List[str]) -> None:
    scenario = config.get("scenario") or {}
    output = scenario.get("output_settings") or {}
    require(config.get("version") == "v3", "config version mismatch", errors)
    require(config.get("plan_kind") == "formal_same_flow_temporal_control", "config plan_kind mismatch", errors)
    require(config.get("integration_only") is False, "config integration_only mismatch", errors)
    require(config.get("smoke") is False, "config smoke mismatch", errors)
    require(scenario.get("scales") == ["S40"], "config scale mismatch", errors)
    require(scenario.get("time_slot_values") == list(TIME_SLOTS), "config T values mismatch", errors)
    require(scenario.get("loads") == [{"label": "Medium", "demand_multiplier": 0.6}], "config load mismatch", errors)
    require(scenario.get("deadline_profile") == "Balanced", "config deadline mismatch", errors)
    require(scenario.get("scale_flow_counts") == {"S40": 160}, "config F mismatch", errors)
    require([item.get("algorithm") for item in scenario.get("algorithms", []) if item.get("enabled", True)] == list(ALGORITHMS), "config algorithm set mismatch", errors)
    require(output.get("topology_anchor_time_slots") == 12, "config topology anchor mismatch", errors)
    require(output.get("flow_anchor_time_slots") == 6, "config flow anchor mismatch", errors)
    require(output.get("expected_total_evidence_rows") == 120, "config total count mismatch", errors)
    require(output.get("expected_new_formal_rows") == 90, "config new count mismatch", errors)
    require(output.get("expected_reused_rows") == 30, "config reuse count mismatch", errors)
    require(sha256_fingerprint(config) == expected_fingerprint, "computed plan fingerprint mismatch", errors)


def validate_row(row: Mapping[str, Any], errors: List[str]) -> None:
    key = evidence_key(row)
    require(row["scale"] == "S40", f"{key} scale mismatch", errors)
    require(row["time_slots"] in TIME_SLOTS, f"{key} T mismatch", errors)
    require(row["load_label"] == LOAD, f"{key} load mismatch", errors)
    require(row["algorithm"] in ALGORITHMS, f"{key} algorithm mismatch", errors)
    require(row["total_flow_count"] == 160, f"{key} F mismatch", errors)
    require(row["path_validity"] is True, f"{key} path invalid", errors)
    require(row["capacity_validity"] is True, f"{key} capacity invalid", errors)
    require(row["runtime_accounting_valid"] is True, f"{key} runtime accounting invalid", errors)
    require(row["integration_only"] is False and row["run_category"] == "formal_paper_or_diagnostic", f"{key} pilot admitted", errors)
    require(row["deadline_profile"] == "Balanced", f"{key} deadline mismatch", errors)
    require(row["temporal_canonical_time_slots"] == 12, f"{key} canonical T mismatch", errors)
    require(row["temporal_requested_time_slots"] == row["time_slots"], f"{key} requested T mismatch", errors)
    require(tuple(int(v) for v in (row["temporal_supported_time_slots"] or ())) == TIME_SLOTS, f"{key} supported T mismatch", errors)
    require(row.get("temporal_flow_anchor_time_slots") in (None, 6), f"{key} flow anchor mismatch", errors)
    require(row["temporal_teg_node_count"] == 40 * int(row["time_slots"]), f"{key} TEG node count mismatch", errors)
    require(row["temporal_storage_edge_count"] == 40 * (int(row["time_slots"]) - 1), f"{key} storage edge count mismatch", errors)
    require(row["temporal_communication_edge_count"] not in (None, 0), f"{key} communication edge missing", errors)
    require(row["temporal_slack_histogram"] == EXPECTED_SLACK_HISTOGRAM, f"{key} slack histogram mismatch", errors)
    require(row["temporal_clipping_count"] == 0, f"{key} clipping mismatch", errors)
    require(row["temporal_maximum_slack"] == 4 and row["temporal_minimum_slack"] == 2, f"{key} slack support mismatch", errors)
    deadline_histogram = row.get("temporal_deadline_time_histogram")
    if isinstance(deadline_histogram, Mapping):
        require(max(int(k) for k, v in deadline_histogram.items() if v) <= 5, f"{key} deadline exceeds T6 anchor", errors)
    else:
        require(False, f"{key} deadline histogram missing", errors)
    for field in (
        "physical_base_fingerprint",
        "temporal_prefix_fingerprint",
        "non_temporal_flow_fingerprint",
        "paired_timing_latent_fingerprint",
        "full_temporal_flow_fingerprint",
        "full_instance_fingerprint",
    ):
        require(bool(row.get(field)), f"{key} missing {field}", errors)
    if row["algorithm"] != "saginfe":
        require(row.get("pricing_worker_count") == 1, f"{key} pricing worker mismatch", errors)
        require(row.get("parallel_pricing_enabled") is False, f"{key} parallel pricing enabled", errors)
        require(row.get("solver_threads_per_worker") == 1, f"{key} threads mismatch", errors)
        require(row.get("configured_max_cg_iterations") == 12, f"{key} CG cap mismatch", errors)
        require(row.get("configured_max_exact_pricing_calls") == 2080, f"{key} exact pricing cap mismatch", errors)
        if row.get("source_kind") == "new":
            raw_meta = meta(row.get("raw_record") or {})
            require(raw_meta.get("completed_cg_iteration_count") == 12, f"{key} completed CG iteration mismatch", errors)
            require(raw_meta.get("logical_pricing_task_count") == 1920, f"{key} logical pricing task mismatch", errors)
            require(raw_meta.get("exact_pricing_call_count") == 1920, f"{key} exact pricing call mismatch", errors)
            require(raw_meta.get("exact_pricing_budget_used") == 1920, f"{key} exact pricing budget mismatch", errors)
            require(raw_meta.get("pricing_strategy") == "hybrid", f"{key} pricing strategy mismatch", errors)
            require(raw_meta.get("hybrid_switch_iteration") == 5, f"{key} hybrid switch mismatch", errors)
            require(raw_meta.get("pricing_strategy_invariant_valid") is True, f"{key} pricing invariant invalid", errors)
            require(raw_meta.get("hard_wall_clock_limit_reached") is False, f"{key} execution interrupted", errors)
            if row["algorithm"] == "dwcg_saginfe_pro":
                require(raw_meta.get("saginfe_initialization_used") is True, f"{key} PRO initialization missing", errors)
                require(raw_meta.get("selective_pfe_enabled") is True, f"{key} PRO PFE disabled", errors)
                require(raw_meta.get("selective_pfe_call_count") == 32, f"{key} PRO PFE call mismatch", errors)
                require(raw_meta.get("selective_pfe_round_count") == 4, f"{key} PRO PFE round mismatch", errors)
                require(raw_meta.get("incumbent_safeguard_used") in (True, False), f"{key} incumbent safeguard metadata missing", errors)


def audit(output_dir: Path, config_path: Path, reuse_manifest_path: Path, expected_commit: str | None = None, expected_fingerprint: str | None = None) -> Dict[str, Any]:
    errors: List[str] = []
    config = read_json(config_path)
    plan_fp = expected_fingerprint or sha256_fingerprint(config)
    validate_config(config, plan_fp, errors)
    manifest = read_json(output_dir / "manifest.json") if (output_dir / "manifest.json").exists() else {}
    raw_new = read_jsonl(output_dir / "runs.jsonl")
    ignored = [row for row in raw_new if not is_resume_skippable_record(row)]
    new_rows = [normalized_new_row(row) for row in raw_new if is_resume_skippable_record(row)]
    reuse_manifest = read_json(reuse_manifest_path)
    reused_rows = [normalized_reuse_row(row) for row in reuse_manifest.get("rows", [])]
    rows = new_rows + reused_rows
    keys = [evidence_key(row) for row in rows]
    expected_keys = {(t, LOAD, seed, alg) for t in TIME_SLOTS for seed in range(10) for alg in ALGORITHMS}
    actual_keys = set(keys)
    duplicates = [key for key, count in Counter(keys).items() if count != 1]

    require(manifest.get("plan_fingerprint") in (None, plan_fp), "new-run manifest plan fingerprint mismatch", errors)
    require(len(new_rows) == 90, f"expected 90 new rows, found {len(new_rows)}", errors)
    require(len(reused_rows) == 30, f"expected 30 reused rows, found {len(reused_rows)}", errors)
    require(len(rows) == 120, f"expected 120 rows, found {len(rows)}", errors)
    require(not duplicates, f"duplicate evidence keys: {duplicates[:10]}", errors)
    require(not sorted(expected_keys - actual_keys), f"missing evidence keys: {sorted(expected_keys - actual_keys)[:10]}", errors)
    require(not sorted(actual_keys - expected_keys), f"extra evidence keys: {sorted(actual_keys - expected_keys)[:10]}", errors)
    require(Counter(row["time_slots"] for row in rows) == Counter({t: 30 for t in TIME_SLOTS}), "T counts mismatch", errors)
    require(Counter(row["algorithm"] for row in rows) == Counter({alg: 40 for alg in ALGORITHMS}), "algorithm counts mismatch", errors)
    require(all(row["time_slots"] == 6 for row in reused_rows), "reuse contains non-T6 row", errors)
    require(all(row["time_slots"] in NEW_TIME_SLOTS for row in new_rows), "new rows contain reused T6 horizon", errors)
    if expected_commit:
        for row in new_rows:
            require(row["git_commit"] == expected_commit, f"{evidence_key(row)} git commit mismatch", errors)
    for row in rows:
        validate_row(row, errors)
        if row["source_kind"] == "new":
            snapshot = (row.get("configuration_snapshot") or {}).get("plan") or {}
            require(sha256_fingerprint(snapshot) == plan_fp, f"{evidence_key(row)} plan snapshot fingerprint mismatch", errors)
        else:
            require(row.get("source_tag") == "temporal-scalability-v3-final", f"{evidence_key(row)} reuse tag mismatch", errors)
            require(row.get("source_commit") == "78d6e3343ac739eb3e06228b53b48a4d72f56b3a", f"{evidence_key(row)} reuse commit mismatch", errors)

    grouped: Dict[Tuple[int, int], List[Mapping[str, Any]]] = defaultdict(list)
    by_seed: Dict[int, List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[scenario_key(row)].append(row)
        by_seed[int(row["formal_seed"])].append(row)
    for scenario, group in grouped.items():
        require({row["algorithm"] for row in group} == set(ALGORITHMS), f"{scenario} missing algorithms", errors)
        for field in ("physical_base_fingerprint", "temporal_prefix_fingerprint", "non_temporal_flow_fingerprint", "paired_timing_latent_fingerprint", "full_temporal_flow_fingerprint", "full_instance_fingerprint"):
            require(len({row.get(field) for row in group}) == 1, f"{scenario} {field} mismatch across algorithms", errors)
    for seed, group in by_seed.items():
        require(len(group) == 12, f"seed {seed} record count mismatch", errors)
        for field in ("physical_base_fingerprint", "non_temporal_flow_fingerprint", "paired_timing_latent_fingerprint", "full_temporal_flow_fingerprint"):
            require(len({row.get(field) for row in group}) == 1, f"seed {seed} {field} mismatch across T", errors)
        for prefix in (6, 8, 10):
            values = set()
            for row in group:
                if int(row["time_slots"]) < prefix:
                    continue
                common = row.get("common_temporal_prefix_fingerprints") or {}
                values.add(common.get(str(prefix)))
            require(len(values) == 1 and None not in values, f"seed {seed} common prefix {prefix} mismatch", errors)

    return {
        "pass": not errors,
        "errors": errors,
        "record_count": len(rows),
        "new_count": len(new_rows),
        "reused_count": len(reused_rows),
        "ignored_diagnostic_new_record_count": len(ignored),
        "scenario_count": len(grouped),
        "counts_by_time_slots": {str(k): v for k, v in sorted(Counter(row["time_slots"] for row in rows).items())},
        "counts_by_algorithm": dict(Counter(row["algorithm"] for row in rows)),
        "plan_fingerprint": plan_fp,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit V3 Same-flow Temporal Control evidence without Gurobi.")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--reuse-manifest-json", required=True, type=Path)
    parser.add_argument("--expected-git-commit")
    parser.add_argument("--expected-plan-fingerprint")
    parser.add_argument("--audit-json", type=Path)
    args = parser.parse_args()
    result = audit(args.output_dir, args.config, args.reuse_manifest_json, args.expected_git_commit, args.expected_plan_fingerprint)
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.audit_json:
        write_json_atomic(args.audit_json, result)
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
