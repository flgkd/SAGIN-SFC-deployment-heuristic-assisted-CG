"""Human-invoked runner for the V3 Same-flow Temporal Control campaign."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calibration_runner import sha256_fingerprint, to_jsonable, utc_now
from formal_runner import (
    _instance_payload,
    _matching_fingerprints,
    append_jsonl,
    build_formal_instance,
    completed_resume_records_by_key,
    default_adapters,
    formal_scenario_id,
    read_jsonl,
    record_to_dict,
    write_json_atomic,
)
from formal_schema import FormalRunKey, formal_algorithm_from_dict, formal_plan_from_dict


ALGORITHMS = ("saginfe", "dwcg_pure", "dwcg_saginfe_pro")
TIME_SLOTS = (6, 8, 10, 12)
NEW_TIME_SLOTS = (8, 10, 12)
IGNORED_DIRTY = {"?? scripts/audit_deployment_cost_eligibility_v3.py"}


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_plan_fingerprint(config: Mapping[str, Any]) -> str:
    return sha256_fingerprint(config)


def git_info_allowing_expected_helper() -> Tuple[str, bool, List[str]]:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()
    status_lines = [
        line.strip()
        for line in subprocess.run(["git", "status", "--short"], capture_output=True, check=False, text=True).stdout.splitlines()
        if line.strip()
    ]
    unexpected = [line for line in status_lines if line not in IGNORED_DIRTY]
    return commit, bool(unexpected), status_lines


def selected(items: Iterable[Any], allowed: Iterable[Any] | None) -> List[Any]:
    allowed_set = set(allowed) if allowed is not None else None
    return [item for item in items if allowed_set is None or item in allowed_set]


def make_time_plan(config: Mapping[str, Any], time_slots: int):
    data = json.loads(json.dumps(config))
    data["scenario"]["time_slots"] = int(time_slots)
    data["scenario"].pop("time_slot_values", None)
    return formal_plan_from_dict(data)


def run_key(time_slots: int, seed: int, algorithm: str) -> Tuple[int, str, int, str, str]:
    return int(time_slots), "S40", int(seed), "Medium", algorithm


def record_resume_key(row: Mapping[str, Any]) -> Tuple[int, str, int, str, str]:
    key = row["key"]
    return (
        int(key["time_slots"]),
        str(key["scale"]),
        int(key["formal_seed"]),
        str(key["load_label"]),
        str(key["algorithm"]),
    )


def ensure_output_dir(path: Path, resume: bool, overwrite: bool) -> None:
    if path.exists() and any(path.iterdir()) and not (resume or overwrite):
        raise ValueError("Output directory is nonempty; use --resume or --overwrite.")
    if overwrite and path.exists() and any(path.iterdir()):
        raise ValueError("This runner does not support destructive overwrite; use a new directory.")
    path.mkdir(parents=True, exist_ok=True)


def validate_config(config: Mapping[str, Any]) -> None:
    scenario = config["scenario"]
    output = scenario.get("output_settings") or {}
    if config.get("version") != "v3":
        raise ValueError("Same-flow temporal control requires a V3 config.")
    if config.get("plan_kind") not in ("formal_same_flow_temporal_control", "formal_same_flow_temporal_control_pilot"):
        raise ValueError("Unexpected plan_kind for Same-flow Temporal Control.")
    values = tuple(int(value) for value in scenario.get("time_slot_values", []))
    allowed = set(TIME_SLOTS)
    if not values or any(value not in allowed for value in values):
        raise ValueError(f"Same-flow temporal control supports T in {TIME_SLOTS}.")
    if not config.get("integration_only") and values != TIME_SLOTS:
        raise ValueError(f"Formal Same-flow Temporal Control requires time_slot_values {TIME_SLOTS}.")
    if scenario.get("scales") != ["S40"]:
        raise ValueError("Same-flow temporal control requires S40 only.")
    if scenario.get("loads") != [{"label": "Medium", "demand_multiplier": 0.6}]:
        raise ValueError("Same-flow temporal control requires Medium load only.")
    if scenario.get("deadline_profile") != "Balanced":
        raise ValueError("Same-flow temporal control requires Balanced deadline profile.")
    if scenario.get("scale_flow_counts") != {"S40": 160}:
        raise ValueError("Same-flow temporal control requires F=160 for S40.")
    algorithms = tuple(item.get("algorithm") for item in scenario.get("algorithms", []) if item.get("enabled", True))
    if algorithms != ALGORITHMS:
        raise ValueError(f"Same-flow temporal control requires algorithms {ALGORITHMS}.")
    if output.get("flow_anchor_time_slots") != 6 or output.get("topology_anchor_time_slots") != 12:
        raise ValueError("Same-flow temporal control requires T6 flow anchor and T12 topology anchor.")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run V3 Same-flow Temporal Control records.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--time-slots", nargs="*", choices=TIME_SLOTS, type=int)
    parser.add_argument("--algorithms", nargs="*", choices=ALGORITHMS)
    parser.add_argument("--seeds", nargs="*", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--include-reused-t6", action="store_true", help="Diagnostic only. Formal T6 evidence is strict reuse.")
    args = parser.parse_args(argv)

    config = read_json(args.config)
    validate_config(config)
    configured_t = tuple(int(value) for value in config["scenario"]["time_slot_values"])
    default_t = configured_t if config.get("integration_only") else NEW_TIME_SLOTS
    selected_t = selected(default_t, args.time_slots)
    if any(value == 6 for value in selected_t) and not args.include_reused_t6 and not config.get("integration_only"):
        raise ValueError("T6 is strict reused evidence; do not rerun it in formal mode.")
    all_algorithms = [formal_algorithm_from_dict(item) for item in config["scenario"]["algorithms"] if item.get("enabled", True)]
    algorithms = [algorithm for algorithm in all_algorithms if algorithm.algorithm in selected(ALGORITHMS, args.algorithms)]
    seeds = selected(config["scenario"]["formal_seeds"], args.seeds)
    matrix = [(int(time_slots), int(seed), algorithm) for time_slots in selected_t for seed in seeds for algorithm in algorithms]
    expected_fingerprint = canonical_plan_fingerprint(config)

    commit, dirty, status_lines = git_info_allowing_expected_helper()
    if dirty:
        raise RuntimeError("Git worktree has unexpected tracked/untracked changes.")
    ensure_output_dir(args.output_dir, resume=args.resume, overwrite=args.overwrite)

    manifest = {
        "formal_framework_version": "v3_formal_framework",
        "run_category": "integration_pilot" if config.get("integration_only") else "formal_paper_or_diagnostic",
        "integration_only": bool(config.get("integration_only")),
        "git_commit": commit,
        "git_dirty": False,
        "ignored_dirty_status_lines": [line for line in status_lines if line in IGNORED_DIRTY],
        "python_version": sys.version,
        "platform": platform.platform(),
        "start_timestamp": utc_now(),
        "finish_timestamp": None,
        "cli_arguments": vars(args),
        "run_filters": {"time_slots": args.time_slots, "algorithms": args.algorithms, "seeds": args.seeds},
        "plan_fingerprint": expected_fingerprint,
        "new_time_slots": list(NEW_TIME_SLOTS),
        "reused_time_slots": [6],
        "allow_dirty": False,
    }
    if args.resume and (args.output_dir / "manifest.json").exists():
        old = read_json(args.output_dir / "manifest.json")
        if old.get("plan_fingerprint") != expected_fingerprint:
            raise ValueError("Cannot resume output directory with a different formal plan fingerprint.")
        if old.get("run_category") != manifest["run_category"]:
            raise ValueError("Cannot mix pilot and formal outputs.")
    write_json_atomic(args.output_dir / "manifest.json", manifest)
    write_json_atomic(args.output_dir / "plan_snapshot.json", config)

    existing = completed_resume_records_by_key(read_jsonl(args.output_dir / "runs.jsonl"), record_resume_key) if args.resume else {}
    existing_instances = {
        row["full_instance_fingerprint"]: row
        for row in read_jsonl(args.output_dir / "instances.jsonl")
    } if args.resume else {}
    adapters = default_adapters()
    completed = 0
    instance_cache: Dict[Tuple[int, int], Any] = {}
    for time_slots, seed, algorithm in matrix:
        key_tuple = run_key(time_slots, seed, algorithm.algorithm)
        if key_tuple in existing:
            continue
        temp_plan = make_time_plan(config, time_slots)
        load = temp_plan.scenario.loads[0]
        if (time_slots, seed) not in instance_cache:
            instance_cache[(time_slots, seed)] = build_formal_instance(temp_plan, "S40", seed, load)
        instance = instance_cache[(time_slots, seed)]
        scenario_id = formal_scenario_id(temp_plan, "S40", seed, load)
        key = FormalRunKey(
            scenario_id=scenario_id,
            scale="S40",
            time_slots=time_slots,
            formal_seed=seed,
            load_label=load.label,
            demand_multiplier=load.demand_multiplier,
            algorithm=algorithm.algorithm,
            variant_id=algorithm.variant_id,
        )
        payload = _instance_payload(key, instance)
        full_fp = payload["full_instance_fingerprint"]
        if full_fp in existing_instances:
            if not _matching_fingerprints(existing_instances[full_fp], instance):
                raise ValueError(f"Existing instance record for {scenario_id} has mismatched fingerprints.")
        else:
            append_jsonl(args.output_dir / "instances.jsonl", payload)
            existing_instances[full_fp] = payload
        context = {
            "key": key,
            "plan": temp_plan,
            "git_commit": commit,
            "configuration_snapshot": to_jsonable({
                "plan": config,
                "execution_subset": {"time_slots": time_slots},
                "algorithm": algorithm,
            }),
            "saginfe_cache": {},
        }
        record = adapters[algorithm.algorithm](instance, algorithm, context)
        record = replace(record, integration_only=bool(config.get("integration_only")), run_category=manifest["run_category"])
        append_jsonl(args.output_dir / "runs.jsonl", record_to_dict(record))
        completed += 1
    rows = read_jsonl(args.output_dir / "runs.jsonl")
    manifest["finish_timestamp"] = utc_now()
    manifest["completed_this_invocation"] = completed
    manifest["planned_runs_this_invocation"] = len(matrix)
    write_json_atomic(args.output_dir / "manifest.json", manifest)
    print(json.dumps({"planned_runs": len(matrix), "completed_this_invocation": completed, "total_records": len(rows)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
