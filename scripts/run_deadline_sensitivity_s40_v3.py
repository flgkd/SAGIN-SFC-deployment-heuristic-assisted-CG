"""Human-invoked runner for the V3 S40 deadline-sensitivity campaign."""

from __future__ import annotations

import argparse
import json
import os
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
from config_schema import (
    FlowGenerationConfig,
    InstanceConfig,
    TopologyConfig,
    get_candidate_deadline_profile,
    get_candidate_scale,
    get_candidate_sfc_profile,
    get_candidate_traffic_mix_profile,
)
from formal_runner import (
    append_jsonl,
    default_adapters,
    formal_scenario_id,
    read_jsonl,
    record_to_dict,
    write_json_atomic,
)
from formal_schema import FormalRunKey, formal_algorithm_from_dict, formal_plan_from_dict
from paired_deadline_builder import build_paired_deadline_instance


ALGORITHMS = ("saginfe", "dwcg_pure", "dwcg_saginfe", "dwcg_saginfe_pro")
PROFILES = ("RealTime", "Balanced", "Flexible")
IGNORED_DIRTY = {"?? scripts/audit_deployment_cost_eligibility_v3.py"}


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_plan_fingerprint(config: Mapping[str, Any]) -> str:
    return sha256_fingerprint(config)


def git_info_allowing_expected_helper() -> Tuple[str, bool, List[str]]:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()
    status_lines = [
        line.strip()
        for line in subprocess.run(["git", "status", "--short"], capture_output=True, text=True, check=False).stdout.splitlines()
        if line.strip()
    ]
    unexpected = [line for line in status_lines if line not in IGNORED_DIRTY]
    return commit, bool(unexpected), status_lines


def selected(items: Iterable[Any], allowed: Iterable[Any] | None) -> List[Any]:
    allowed_set = set(allowed) if allowed is not None else None
    return [item for item in items if allowed_set is None or item in allowed_set]


def make_profile_plan(config: Mapping[str, Any], profile: str, integration_only: bool | None = None):
    data = json.loads(json.dumps(config))
    data["scenario"]["deadline_profile"] = profile
    data["scenario"].pop("deadline_profiles", None)
    if integration_only is not None:
        if data.get("plan_kind") == "formal_deadline_sensitivity_pilot":
            data["plan_kind"] = "formal_deadline_sensitivity"
        data["integration_only"] = bool(integration_only)
        data["smoke"] = False
    return formal_plan_from_dict(data)


def build_instance(config: Mapping[str, Any], profile: str, seed: int):
    scenario = config["scenario"]
    load = scenario["loads"][0]
    topology = TopologyConfig(
        scale=get_candidate_scale("S40"),
        time_slots=int(scenario["time_slots"]),
        seed=0,
        dynamic_topology=bool(scenario["dynamic_topology"]),
    )
    flow = FlowGenerationConfig(
        seed=0,
        num_flows=int(scenario["scale_flow_counts"]["S40"]),
        demand_multiplier=float(load["demand_multiplier"]),
        traffic_mix_profile=get_candidate_traffic_mix_profile(scenario["traffic_profile"]),
        sfc_profile=get_candidate_sfc_profile(scenario["sfc_profile"]),
        deadline_profile=get_candidate_deadline_profile(profile),
        load_profile_name=load["label"],
    )
    return build_paired_deadline_instance(InstanceConfig(topology, flow, scenario_seed=seed), profile).instance


def run_key(profile: str, seed: int, algorithm: str) -> Tuple[str, str, int, str, str]:
    return "S40", profile, seed, "Medium", algorithm


def record_resume_key(row: Mapping[str, Any]) -> Tuple[str, str, int, str, str]:
    key = row["key"]
    return (
        str(key["scale"]),
        str(row.get("deadline_profile")),
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


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run V3 S40 deadline-sensitivity records.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--deadline-profiles", nargs="*", choices=PROFILES)
    parser.add_argument("--algorithms", nargs="*", choices=ALGORITHMS)
    parser.add_argument("--seeds", nargs="*", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    config = read_json(args.config)
    profile_plan = make_profile_plan(config, "Balanced", integration_only=config.get("integration_only"))
    all_algorithms = [formal_algorithm_from_dict(item) for item in config["scenario"]["algorithms"] if item.get("enabled", True)]
    profiles = selected(config["scenario"]["deadline_profiles"], args.deadline_profiles)
    algorithms = [algorithm for algorithm in all_algorithms if algorithm.algorithm in selected(ALGORITHMS, args.algorithms)]
    seeds = selected(config["scenario"]["formal_seeds"], args.seeds)
    matrix = [(profile, int(seed), algorithm) for profile in profiles for seed in seeds for algorithm in algorithms]
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
        "run_filters": {
            "deadline_profiles": args.deadline_profiles,
            "algorithms": args.algorithms,
            "seeds": args.seeds,
        },
        "plan_fingerprint": expected_fingerprint,
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

    existing = {record_resume_key(row): row for row in read_jsonl(args.output_dir / "runs.jsonl")} if args.resume else {}
    adapters = default_adapters()
    completed = 0
    for profile, seed, algorithm in matrix:
        key_tuple = run_key(profile, seed, algorithm.algorithm)
        if key_tuple in existing:
            continue
        temp_plan = make_profile_plan(config, profile, integration_only=config.get("integration_only"))
        instance = build_instance(config, profile, seed)
        load = temp_plan.scenario.loads[0]
        scenario_id = f"{profile}_{formal_scenario_id(temp_plan, 'S40', seed, load)}"
        key = FormalRunKey(
            scenario_id=scenario_id,
            scale="S40",
            time_slots=temp_plan.scenario.time_slots,
            formal_seed=seed,
            load_label=load.label,
            demand_multiplier=load.demand_multiplier,
            algorithm=algorithm.algorithm,
            variant_id=algorithm.variant_id,
        )
        context = {
            "key": key,
            "plan": temp_plan,
            "git_commit": commit,
            "configuration_snapshot": to_jsonable({"plan": config, "algorithm": algorithm}),
            "sgfe_cache": {},
        }
        record = adapters[algorithm.algorithm](instance, algorithm, context)
        record = replace(
            record,
            integration_only=bool(config.get("integration_only")),
            run_category=manifest["run_category"],
        )
        append_jsonl(args.output_dir / "runs.jsonl", record_to_dict(record))
        completed += 1
    rows = read_jsonl(args.output_dir / "runs.jsonl")
    manifest["finish_timestamp"] = utc_now()
    write_json_atomic(args.output_dir / "manifest.json", manifest)
    print(json.dumps({"planned_runs": len(matrix), "completed_this_invocation": completed, "total_records": len(rows)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
