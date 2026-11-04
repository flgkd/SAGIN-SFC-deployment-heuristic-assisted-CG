"""Human-invoked runner for the additive V3 Same-flow Temporal Control HFP extension."""

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
    dwcg_configured_e2e_limit,
    completed_resume_records_by_key,
    default_adapters,
    formal_scenario_id,
    read_jsonl,
    record_to_dict,
    write_json_atomic,
)
from formal_schema import FormalRunKey, formal_algorithm_from_dict, formal_plan_from_dict


ALGORITHM = "dwcg_saginfe"
NEW_TIME_SLOTS = (8, 10, 12)
REUSE_TIME_SLOTS = (6,)
SUPPORTED_TIME_SLOTS = (6, 8, 10, 12)
# Preserve the root-only execution safeguard in any standalone checkout.
EXPECTED_ROOT = ROOT


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def git_info() -> Tuple[str, bool, List[str]]:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()
    status_lines = [
        line.strip()
        for line in subprocess.run(["git", "status", "--short"], capture_output=True, text=True, check=False).stdout.splitlines()
        if line.strip()
    ]
    return commit, bool(status_lines), status_lines


def validate_extension_config(config: Mapping[str, Any]) -> None:
    if Path.cwd().resolve() != EXPECTED_ROOT:
        raise ValueError(f"HFP extension must run from repository root: {EXPECTED_ROOT}")
    scenario = config["scenario"]
    output = scenario.get("output_settings") or {}
    if config.get("version") != "v3":
        raise ValueError("HFP extension requires a V3 config.")
    if config.get("plan_kind") != "formal_same_flow_temporal_control":
        raise ValueError("HFP extension must use the Same-flow Temporal Control plan kind.")
    if config.get("integration_only") or config.get("smoke"):
        raise ValueError("HFP extension config must be formal, not smoke/integration.")
    if scenario.get("scales") != ["S40"]:
        raise ValueError("HFP extension requires S40 only.")
    if tuple(int(value) for value in scenario.get("time_slot_values", [])) != NEW_TIME_SLOTS:
        raise ValueError(f"HFP extension new-run horizons must be {NEW_TIME_SLOTS}; T6 is reuse-only.")
    if scenario.get("loads") != [{"label": "Medium", "demand_multiplier": 0.6}]:
        raise ValueError("HFP extension requires Medium load only.")
    if scenario.get("deadline_profile") != "Balanced":
        raise ValueError("HFP extension requires Balanced deadline profile.")
    if scenario.get("scale_flow_counts") != {"S40": 160}:
        raise ValueError("HFP extension requires F=160 for S40.")
    algorithms = [item for item in scenario.get("algorithms", []) if item.get("enabled", True)]
    if len(algorithms) != 1 or algorithms[0].get("algorithm") != ALGORITHM:
        raise ValueError("HFP extension allows only dwcg_saginfe.")
    algorithm = formal_algorithm_from_dict(algorithms[0])
    if algorithm.use_saginfe_initialization is not True:
        raise ValueError("HFP must use SAGINFE initialization.")
    if algorithm.resolved_pricing_strategy() != "heuristic_then_exact":
        raise ValueError("HFP must use heuristic_then_exact pricing.")
    if not algorithm.disable_hard_time_limit:
        raise ValueError("HFP extension requires disable_hard_time_limit=true.")
    if algorithm.hard_time_limit_seconds is not None:
        raise ValueError("disable_hard_time_limit cannot be combined with hard_time_limit_seconds.")
    if dwcg_configured_e2e_limit(algorithm) is not None:
        raise ValueError("Resolved DWCG E2E hard limit must be disabled/None.")
    if algorithm.max_cg_iterations != 12:
        raise ValueError("HFP max_cg_iterations must remain 12.")
    if algorithm.max_exact_pricing_calls_by_scale.get("S40") != 2080:
        raise ValueError("HFP S40 exact-pricing cap must remain 2080.")
    if algorithm.final_rmp_reserve_seconds != 300.0 or algorithm.final_rmp_time_cap_seconds != 300.0:
        raise ValueError("HFP final RMP reserve/cap must remain 300 seconds.")
    if output.get("flow_anchor_time_slots") != 6 or output.get("topology_anchor_time_slots") != 12:
        raise ValueError("HFP extension requires T6 flow anchor and T12 topology anchor.")
    if output.get("arrival_deadline_remapping_after_T6") is not False:
        raise ValueError("HFP extension forbids arrival/deadline remapping after T6.")
    if output.get("hfp_t6_reuse_only") is not True:
        raise ValueError("T6 HFP must be reuse-only.")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run additive V3 Same-flow Temporal Control HFP extension records.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--time-slots", nargs="*", choices=NEW_TIME_SLOTS, type=int)
    parser.add_argument("--algorithms", nargs="*", choices=(ALGORITHM,))
    parser.add_argument("--seeds", nargs="*", choices=tuple(range(10)), type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    config = read_json(args.config)
    validate_extension_config(config)
    selected_t = selected(NEW_TIME_SLOTS, args.time_slots)
    seeds = selected(config["scenario"]["formal_seeds"], args.seeds)
    algorithm = formal_algorithm_from_dict(config["scenario"]["algorithms"][0])
    algorithms = [algorithm]
    matrix = [(int(time_slots), int(seed), algorithm) for time_slots in selected_t for seed in seeds for algorithm in algorithms]
    expected_fingerprint = sha256_fingerprint(config)

    commit, dirty, status_lines = git_info()
    if dirty:
        raise RuntimeError(
            "Git worktree has changes. This prep task allows dirty state, but formal solver execution must start clean. "
            f"Current status: {status_lines}"
        )
    ensure_output_dir(args.output_dir, resume=args.resume, overwrite=args.overwrite)

    manifest = {
        "formal_framework_version": "v3_formal_framework",
        "run_category": "formal_paper_or_diagnostic",
        "integration_only": False,
        "extension_kind": "same_flow_temporal_control_hfp_extension_v3",
        "git_commit": commit,
        "git_dirty": False,
        "python_version": sys.version,
        "platform": platform.platform(),
        "start_timestamp": utc_now(),
        "finish_timestamp": None,
        "cli_arguments": vars(args),
        "run_filters": {"time_slots": args.time_slots, "algorithms": args.algorithms, "seeds": args.seeds},
        "plan_fingerprint": expected_fingerprint,
        "new_time_slots": list(NEW_TIME_SLOTS),
        "reused_time_slots": list(REUSE_TIME_SLOTS),
        "hfp_t6_reuse_only": True,
        "allow_dirty": False,
        "external_e2e_hard_wall_disabled": True,
    }
    if args.resume and (args.output_dir / "manifest.json").exists():
        old = read_json(args.output_dir / "manifest.json")
        if old.get("plan_fingerprint") != expected_fingerprint:
            raise ValueError("Cannot resume output directory with a different formal plan fingerprint.")
        if old.get("run_category") != manifest["run_category"]:
            raise ValueError("Cannot mix output categories.")
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
            "sgfe_cache": {},
        }
        record = adapters[algorithm.algorithm](instance, algorithm, context)
        record = replace(record, integration_only=False, run_category=manifest["run_category"])
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
