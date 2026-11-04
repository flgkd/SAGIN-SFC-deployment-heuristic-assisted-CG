"""Read-only audit for V3 fixed-workload temporal scalability formal records."""

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

from calibration_runner import sha256_fingerprint
from paired_temporal_builder import PAIRED_TEMPORAL_TIME_SLOTS


ALGORITHMS = ("saginfe", "dwcg_pure", "dwcg_saginfe", "dwcg_saginfe_pro")
TIME_SLOTS = tuple(PAIRED_TEMPORAL_TIME_SLOTS)
EXPECTED_SLACK_HISTOGRAM = {"2": 48, "3": 64, "4": 48}
EXPECTED_AGGREGATE_SLACK_HISTOGRAM = {"2": 480, "3": 640, "4": 480}
MISSING = object()
METADATA_FIELDS = (
    "physical_base_fingerprint",
    "temporal_prefix_fingerprint",
    "common_temporal_prefix_fingerprints",
    "non_temporal_flow_fingerprint",
    "paired_timing_latent_fingerprint",
    "full_temporal_flow_fingerprint",
    "temporal_canonical_time_slots",
    "temporal_requested_time_slots",
    "temporal_supported_time_slots",
    "temporal_teg_node_count",
    "temporal_storage_edge_count",
    "temporal_communication_edge_count",
    "temporal_arrival_time_histogram",
    "temporal_slack_histogram",
    "temporal_deadline_time_histogram",
    "temporal_minimum_slack",
    "temporal_maximum_slack",
    "temporal_mean_slack",
    "temporal_clipping_count",
    "temporal_horizon_last_slot_count",
    "temporal_fixed_f_average_flows_per_time_slot",
    "deadline_profile",
)


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    path = resolve_input_path(path)
    if not path.exists():
        return []
    return [normalize_record(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def expected_plan_fingerprint(config: Mapping[str, Any]) -> str:
    return sha256_fingerprint(config)


def meta(row: Mapping[str, Any]) -> Mapping[str, Any]:
    if row.get("key", {}).get("algorithm") == "saginfe":
        return row.get("heuristic_metadata") or {}
    return row.get("dwcg_metadata") or {}


def _present(value: Any) -> bool:
    return value is not MISSING and value is not None and value != {} and value != []


def semantic_value(row: Mapping[str, Any], field: str, errors: List[str] | None = None) -> Any:
    found: List[Tuple[str, Any]] = []
    for container_name, container in (("top_level", row), ("metadata", meta(row))):
        if field in container:
            value = container.get(field)
            if _present(value):
                found.append((f"{container_name}.{field}", value))
    if not found:
        return MISSING
    first_name, first_value = found[0]
    for name, value in found[1:]:
        if value != first_value and errors is not None:
            errors.append(f"{display_key(row)} conflicting {field}: {first_name}={first_value!r}, {name}={value!r}")
    return first_value


def normalized_record(row: Mapping[str, Any], errors: List[str] | None = None) -> Dict[str, Any]:
    normalized = dict(row)
    for field in METADATA_FIELDS:
        value = semantic_value(row, field, errors)
        if value is not MISSING:
            normalized[field] = value
    return normalized


def display_key(row: Mapping[str, Any]) -> Tuple[Any, ...]:
    key = row.get("key") or {}
    return (key.get("time_slots"), key.get("scale"), key.get("formal_seed"), key.get("load_label"), key.get("algorithm"))


def record_key(row: Mapping[str, Any]) -> Tuple[int, str, int, str]:
    key = row["key"]
    return int(key["time_slots"]), str(key["load_label"]), int(key["formal_seed"]), str(key["algorithm"])


def scenario_key(row: Mapping[str, Any]) -> Tuple[int, int]:
    key = row["key"]
    return int(key["time_slots"]), int(key["formal_seed"])


def require(condition: bool, message: str, errors: List[str]) -> None:
    if not condition:
        errors.append(message)


def _runtime_accounting_valid(row: Mapping[str, Any]) -> Any:
    return meta(row).get("runtime_accounting_valid", row.get("runtime_accounting_valid"))


def _hard_wall(row: Mapping[str, Any]) -> Any:
    return meta(row).get("hard_wall_clock_limit_reached", row.get("hard_wall_clock_limit_reached"))


def _deterministic_budget(row: Mapping[str, Any]) -> bool:
    row_meta = meta(row)
    return bool(
        row_meta.get("deterministic_work_budget_exhausted")
        or row_meta.get("max_cg_iterations_reached")
        or row.get("termination_status") == "DETERMINISTIC_WORK_BUDGET_EXHAUSTED"
    )


def validate_config(config: Mapping[str, Any], errors: List[str]) -> None:
    scenario = config.get("scenario") or {}
    require(config.get("version") == "v3", "config version is not v3", errors)
    require(config.get("plan_kind") == "formal_temporal_scalability", "config plan_kind mismatch", errors)
    require(config.get("integration_only") is False, "config integration_only is not false", errors)
    require(config.get("smoke") is False, "config smoke is not false", errors)
    require(scenario.get("scales") == ["S40"], "config scale mismatch", errors)
    require(scenario.get("time_slot_values") == list(TIME_SLOTS), "config time_slot_values mismatch", errors)
    require(scenario.get("deadline_profile") == "Balanced", "config deadline profile mismatch", errors)
    require(scenario.get("loads") == [{"label": "Medium", "demand_multiplier": 0.6}], "config Medium load mismatch", errors)
    require(scenario.get("scale_flow_counts") == {"S40": 160}, "config F mismatch", errors)
    require((scenario.get("output_settings") or {}).get("strict_reuse_count") == 0, "config strict reuse count mismatch", errors)


def audit(output_dir: Path, config_path: Path, expected_commit: str | None = None, expected_fingerprint: str | None = None) -> Dict[str, Any]:
    errors: List[str] = []
    config = read_json(config_path)
    validate_config(config, errors)
    expected_fp = expected_fingerprint or expected_plan_fingerprint(config)
    rows = read_jsonl(output_dir / "runs.jsonl")
    manifest = read_json(output_dir / "manifest.json") if (output_dir / "manifest.json").exists() else {}
    normalized_rows = [normalized_record(row, errors) for row in rows]
    keys = [record_key(row) for row in normalized_rows]
    expected_keys = {(time_slots, "Medium", seed, algorithm) for time_slots in TIME_SLOTS for seed in range(10) for algorithm in ALGORITHMS}
    actual_keys = set(keys)

    require(len(rows) == 160, f"expected 160 records, found {len(rows)}", errors)
    require(len({(key[0], key[2]) for key in keys}) == 40, "expected 40 T/seed scenarios", errors)
    duplicates = [key for key, count in Counter(keys).items() if count != 1]
    require(not duplicates, f"duplicate run keys: {duplicates[:10]}", errors)
    require(not sorted(expected_keys - actual_keys), f"missing run keys: {sorted(expected_keys - actual_keys)[:10]}", errors)
    require(not sorted(actual_keys - expected_keys), f"extra run keys: {sorted(actual_keys - expected_keys)[:10]}", errors)
    require(Counter(key[0] for key in keys) == Counter({time_slots: 40 for time_slots in TIME_SLOTS}), "T counts mismatch", errors)
    require(Counter(key[3] for key in keys) == Counter({algorithm: 40 for algorithm in ALGORITHMS}), "algorithm counts mismatch", errors)
    require(manifest.get("plan_fingerprint") == expected_fp, "manifest plan fingerprint mismatch", errors)
    require(manifest.get("run_category") == "formal_paper_or_diagnostic", "manifest is not formal", errors)
    require(manifest.get("integration_only") is False, "manifest integration_only is not false", errors)

    grouped: Dict[Tuple[int, int], List[Mapping[str, Any]]] = defaultdict(list)
    by_seed: Dict[int, List[Mapping[str, Any]]] = defaultdict(list)
    slack_aggregate_by_t: Dict[int, Counter] = {time_slots: Counter() for time_slots in TIME_SLOTS}
    slack_seen_scenarios = set()
    hard_wall_count = 0
    deterministic_budget_count = 0
    for row in normalized_rows:
        key = record_key(row)
        time_slots, load, seed, algorithm = key
        grouped[(time_slots, seed)].append(row)
        by_seed[seed].append(row)
        row_meta = meta(row)
        snapshot_plan = (row.get("configuration_snapshot") or {}).get("plan") or {}
        require(row["key"]["scale"] == "S40", f"{key} scale mismatch", errors)
        require(load == "Medium", f"{key} load mismatch", errors)
        require(row["key"]["demand_multiplier"] == 0.6, f"{key} demand multiplier mismatch", errors)
        require(row.get("total_flow_count") == 160, f"{key} F mismatch", errors)
        require(row.get("integration_only") is False and row.get("run_category") == "formal_paper_or_diagnostic", f"{key} pilot record admitted", errors)
        require(snapshot_plan.get("version") == "v3", f"{key} non-v3 plan snapshot admitted", errors)
        require(snapshot_plan.get("plan_kind") == "formal_temporal_scalability", f"{key} plan kind mismatch", errors)
        require(snapshot_plan.get("integration_only") is False, f"{key} integration snapshot admitted", errors)
        require(snapshot_plan.get("smoke") is False, f"{key} smoke snapshot admitted", errors)
        require(sha256_fingerprint(snapshot_plan) == expected_fp, f"{key} plan snapshot fingerprint mismatch", errors)
        if expected_commit:
            require(row.get("git_commit") == expected_commit, f"{key} git commit mismatch", errors)
        require(row.get("path_validity") is True, f"{key} path invalid", errors)
        require(row.get("capacity_validity") is True, f"{key} capacity invalid", errors)
        require(_runtime_accounting_valid(row) is True, f"{key} runtime accounting invalid", errors)
        require(row.get("deadline_profile") == "Balanced", f"{key} deadline profile mismatch", errors)
        require(row.get("temporal_requested_time_slots") == time_slots, f"{key} temporal requested T mismatch", errors)
        require(row.get("temporal_canonical_time_slots") == 12, f"{key} canonical T mismatch", errors)
        require(tuple(int(value) for value in row.get("temporal_supported_time_slots", ())) == TIME_SLOTS, f"{key} supported T mismatch", errors)
        require(row.get("temporal_teg_node_count") == 40 * time_slots, f"{key} TEG node count mismatch", errors)
        require(row.get("temporal_storage_edge_count") == 40 * (time_slots - 1), f"{key} storage edge count mismatch", errors)
        require(row.get("temporal_communication_edge_count") not in (None, 0), f"{key} communication edge count missing", errors)
        require(row.get("temporal_clipping_count") == 0, f"{key} clipping count nonzero or missing", errors)
        require(row.get("temporal_slack_histogram") == EXPECTED_SLACK_HISTOGRAM, f"{key} slack histogram mismatch", errors)
        require(row.get("physical_base_fingerprint"), f"{key} missing physical base fingerprint", errors)
        require(row.get("temporal_prefix_fingerprint"), f"{key} missing temporal prefix fingerprint", errors)
        require(row.get("non_temporal_flow_fingerprint"), f"{key} missing non-temporal flow fingerprint", errors)
        require(row.get("paired_timing_latent_fingerprint"), f"{key} missing timing latent fingerprint", errors)
        require(row.get("full_temporal_flow_fingerprint"), f"{key} missing full temporal flow fingerprint", errors)
        require(row.get("full_instance_fingerprint"), f"{key} missing full instance fingerprint", errors)
        if time_slots in slack_aggregate_by_t and (time_slots, seed) not in slack_seen_scenarios:
            slack_aggregate_by_t[time_slots].update(row.get("temporal_slack_histogram") or {})
            slack_seen_scenarios.add((time_slots, seed))
        if _hard_wall(row) is True:
            hard_wall_count += 1
        if _deterministic_budget(row):
            deterministic_budget_count += 1
        require(row_meta.get("pricing_worker_count", 1) == 1, f"{key} pricing worker count mismatch", errors)
        require(row_meta.get("parallel_pricing_enabled", False) is False, f"{key} parallel pricing enabled", errors)

    for scenario, group in grouped.items():
        require({row["key"]["algorithm"] for row in group} == set(ALGORITHMS), f"{scenario} missing algorithm", errors)
        for field in ("physical_base_fingerprint", "temporal_prefix_fingerprint", "non_temporal_flow_fingerprint", "paired_timing_latent_fingerprint", "full_temporal_flow_fingerprint", "full_instance_fingerprint"):
            require(len({row.get(field) for row in group}) == 1, f"{scenario} {field} mismatch across algorithms", errors)

    for seed, group in by_seed.items():
        require(len(group) == 16, f"seed {seed} record count mismatch", errors)
        for field in ("physical_base_fingerprint", "non_temporal_flow_fingerprint", "paired_timing_latent_fingerprint"):
            require(len({row.get(field) for row in group}) == 1, f"seed {seed} {field} mismatch across T", errors)
        for prefix in (6, 8, 10):
            values = set()
            for row in group:
                time_slots = int(row["key"]["time_slots"])
                if time_slots < prefix:
                    continue
                common = row.get("common_temporal_prefix_fingerprints") or {}
                values.add(common.get(str(prefix)))
            require(len(values) == 1 and None not in values, f"seed {seed} temporal prefix {prefix} mismatch", errors)
        full_by_t = {
            time_slots: {row.get("full_instance_fingerprint") for row in group if int(row["key"]["time_slots"]) == time_slots}
            for time_slots in TIME_SLOTS
        }
        require(all(len(values) == 1 for values in full_by_t.values()), f"seed {seed} full fingerprint missing by T", errors)
        if all(len(values) == 1 for values in full_by_t.values()):
            require(len({next(iter(values)) for values in full_by_t.values()}) == len(TIME_SLOTS), f"seed {seed} T full fingerprints not distinct", errors)

    return {
        "pass": not errors,
        "errors": errors,
        "record_count": len(rows),
        "scenario_count": len({(key[0], key[2]) for key in keys}),
        "counts_by_time_slots": {str(k): v for k, v in sorted(Counter(key[0] for key in keys).items())},
        "counts_by_algorithm": dict(Counter(key[3] for key in keys)),
        "path_valid_count": sum(row.get("path_validity") is True for row in normalized_rows),
        "capacity_valid_count": sum(row.get("capacity_validity") is True for row in normalized_rows),
        "runtime_accounting_valid_count": sum(_runtime_accounting_valid(row) is True for row in normalized_rows),
        "hard_wall_count": hard_wall_count,
        "deterministic_work_budget_exhausted_count": deterministic_budget_count,
        "aggregate_slack_histogram_by_time_slots": {
            str(time_slots): dict(sorted(counter.items()))
            for time_slots, counter in slack_aggregate_by_t.items()
        },
        "expected_aggregate_slack_histogram": EXPECTED_AGGREGATE_SLACK_HISTOGRAM,
        "plan_fingerprint": expected_fp,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit V3 temporal scalability without Gurobi.")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--expected-git-commit")
    parser.add_argument("--expected-plan-fingerprint")
    parser.add_argument("--summary-json", type=Path)
    args = parser.parse_args(argv)
    summary = audit(args.output_dir, args.config, args.expected_git_commit, args.expected_plan_fingerprint)
    text = json.dumps(summary, indent=2, sort_keys=True)
    print(text)
    if args.summary_json:
        args.summary_json.write_text(text + "\n", encoding="utf-8")
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
