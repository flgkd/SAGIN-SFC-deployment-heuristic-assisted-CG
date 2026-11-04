"""Human-invoked runner for the V3 DWCG-Pure parallel-pricing study."""

from __future__ import annotations

import argparse
import json
import multiprocessing
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
from formal_runner import (
    _instance_payload,
    _matching_fingerprints,
    append_jsonl,
    build_formal_instance,
    default_adapters,
    formal_scenario_id,
    read_jsonl,
    record_to_dict,
    write_json_atomic,
)
from formal_schema import FormalRunKey, formal_algorithm_from_dict, formal_plan_from_dict


LOAD = "High"
ALGORITHMS = ("dwcg_pure", "dwcg_saginfe_pro")
WORKER_VARIANTS = ("W1", "W2", "W4")
WORKER_COUNT_BY_VARIANT = {"W1": 1, "W2": 2, "W4": 4}
IGNORED_DIRTY = {"?? scripts/audit_deployment_cost_eligibility_v3.py"}


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_plan_fingerprint(config: Mapping[str, Any]) -> str:
    return sha256_fingerprint(config)


def git_info_allowing_expected_helper() -> Tuple[str, str, bool, List[str]]:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()
    short = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()
    status_lines = [
        line.strip()
        for line in subprocess.run(["git", "status", "--short"], capture_output=True, check=False, text=True).stdout.splitlines()
        if line.strip()
    ]
    unexpected = [line for line in status_lines if line not in IGNORED_DIRTY]
    return commit, short, bool(unexpected), status_lines


def hardware_context() -> Dict[str, Any]:
    return {
        "logical_cpu_count": os.cpu_count(),
        "platform": platform.platform(),
        "python_version": sys.version,
        "multiprocessing_start_method": multiprocessing.get_start_method(allow_none=True),
    }


def selected(items: Iterable[Any], allowed: Iterable[Any] | None) -> List[Any]:
    allowed_set = set(allowed) if allowed is not None else None
    return [item for item in items if allowed_set is None or item in allowed_set]


def validate_config(config: Mapping[str, Any]) -> None:
    scenario = config["scenario"]
    output = scenario.get("output_settings") or {}
    if config.get("version") != "v3":
        raise ValueError("Parallel pricing requires a V3 config.")
    if config.get("plan_kind") not in ("formal_parallel_pricing", "formal_parallel_pricing_pilot"):
        raise ValueError("Unexpected plan_kind for parallel pricing.")
    if scenario.get("scales") != ["S40"] or scenario.get("time_slots") != 8:
        raise ValueError("Parallel pricing requires S40 T=8.")
    if scenario.get("loads") != [{"label": "High", "demand_multiplier": 0.8}]:
        raise ValueError("Parallel pricing requires High load only.")
    if scenario.get("deadline_profile") != "Balanced":
        raise ValueError("Parallel pricing requires Balanced deadline profile.")
    if scenario.get("scale_flow_counts") != {"S40": 160}:
        raise ValueError("Parallel pricing requires F=160 for S40.")
    algorithms = [item for item in scenario.get("algorithms", []) if item.get("enabled", True)]
    expected_pairs = [(algorithm, worker) for algorithm in ALGORITHMS for worker in WORKER_VARIANTS]
    actual_pairs = [(item.get("algorithm"), item.get("variant_id")) for item in algorithms]
    if actual_pairs != expected_pairs:
        raise ValueError("Parallel pricing requires exactly Pure/PRO x W1/W2/W4 variants in order.")
    for item in algorithms:
        if item.get("algorithm") not in ALGORITHMS:
            raise ValueError(f"Unexpected algorithm {item.get('algorithm')!r}.")
        variant = item["variant_id"]
        if item.get("pricing_worker_count") != WORKER_COUNT_BY_VARIANT[variant]:
            raise ValueError(f"{variant} pricing_worker_count mismatch.")
        if item.get("threads") != 1:
            raise ValueError(f"{variant} must use Threads=1.")
        if item.get("pricing_strategy") != "hybrid" or item.get("hybrid_switch_iteration") != 5:
            raise ValueError(f"{variant} must use the frozen hybrid S5 pricing schedule.")
        if item.get("max_cg_iterations") != 12:
            raise ValueError(f"{variant} max_cg_iterations mismatch.")
        if item.get("max_exact_pricing_calls_by_scale", {}).get("S40") != 2080:
            raise ValueError(f"{variant} exact-pricing cap mismatch.")
    if config.get("integration_only"):
        return
    if output.get("expected_total_evidence_rows") != 60 or output.get("expected_reused_rows") != 0:
        raise ValueError("Formal parallel pricing config must describe 60 new rows and zero reuse.")


def run_key(seed: int, algorithm: str, variant_id: str) -> Tuple[str, str, int, str, str]:
    return "S40", LOAD, int(seed), algorithm, variant_id


def record_resume_key(row: Mapping[str, Any]) -> Tuple[str, str, int, str, str]:
    key = row["key"]
    return (
        str(key["scale"]),
        str(key["load_label"]),
        int(key["formal_seed"]),
        str(key["algorithm"]),
        str(key.get("variant_id")),
    )


def ensure_output_dir(path: Path, resume: bool, overwrite: bool) -> None:
    if path.exists() and any(path.iterdir()) and not (resume or overwrite):
        raise ValueError("Output directory is nonempty; use --resume or a new directory.")
    if overwrite and path.exists() and any(path.iterdir()):
        raise ValueError("This runner does not support destructive overwrite; use a new directory.")
    path.mkdir(parents=True, exist_ok=True)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run V3 DWCG-Pure parallel-pricing records.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--algorithms", nargs="*", choices=ALGORITHMS)
    parser.add_argument("--workers", nargs="*", choices=WORKER_VARIANTS)
    parser.add_argument("--seeds", nargs="*", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    config = read_json(args.config)
    validate_config(config)
    expected_fingerprint = canonical_plan_fingerprint(config)
    commit, short_commit, dirty, status_lines = git_info_allowing_expected_helper()
    if dirty:
        raise RuntimeError("Git worktree has unexpected tracked/untracked changes.")
    if args.output_dir is None:
        base = "pilot_outputs/parallel_pricing_smoke_v3" if config.get("integration_only") else "formal_outputs/parallel_pricing_v3"
        args.output_dir = Path(f"{base}_{short_commit}")
    ensure_output_dir(args.output_dir, resume=args.resume, overwrite=args.overwrite)

    all_algorithms = [formal_algorithm_from_dict(item) for item in config["scenario"]["algorithms"] if item.get("enabled", True)]
    allowed_algorithms = set(args.algorithms) if args.algorithms else set(ALGORITHMS)
    allowed_workers = set(args.workers) if args.workers else set(WORKER_VARIANTS)
    algorithms = [
        algorithm
        for algorithm in all_algorithms
        if algorithm.algorithm in allowed_algorithms and algorithm.variant_id in allowed_workers
    ]
    plan = formal_plan_from_dict(config)
    load = plan.scenario.loads[0]
    seeds = selected(config["scenario"]["formal_seeds"], args.seeds)
    matrix = [(int(seed), algorithm) for seed in seeds for algorithm in algorithms]

    manifest = {
        "formal_framework_version": "v3_formal_framework",
        "run_category": "integration_pilot" if config.get("integration_only") else "formal_paper_or_diagnostic",
        "integration_only": bool(config.get("integration_only")),
        "git_commit": commit,
        "git_dirty": False,
        "ignored_dirty_status_lines": [line for line in status_lines if line in IGNORED_DIRTY],
        "hardware_context": hardware_context(),
        "start_timestamp": utc_now(),
        "finish_timestamp": None,
        "cli_arguments": {**vars(args), "output_dir": str(args.output_dir)},
        "run_filters": {"algorithms": args.algorithms, "workers": args.workers, "seeds": args.seeds},
        "plan_fingerprint": expected_fingerprint,
        "worker_variant_ids": list(WORKER_VARIANTS),
        "reuse_count": 0,
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
    existing_instances = {
        row["full_instance_fingerprint"]: row
        for row in read_jsonl(args.output_dir / "instances.jsonl")
    } if args.resume else {}
    adapters = default_adapters()
    completed = 0
    instance_cache: Dict[int, Any] = {}
    for seed, algorithm in matrix:
        key_tuple = run_key(seed, algorithm.algorithm, str(algorithm.variant_id))
        if key_tuple in existing:
            continue
        if seed not in instance_cache:
            instance_cache[seed] = build_formal_instance(plan, "S40", seed, load)
        instance = instance_cache[seed]
        scenario_id = formal_scenario_id(plan, "S40", seed, load)
        key = FormalRunKey(
            scenario_id=scenario_id,
            scale="S40",
            time_slots=8,
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
            "plan": plan,
            "git_commit": commit,
            "configuration_snapshot": to_jsonable({
                "plan": config,
                "execution_subset": {"load": load.label, "algorithm": algorithm.algorithm, "worker_variant": algorithm.variant_id},
                "algorithm": algorithm,
                "parallel_execution_context": {
                    "pricing_worker_count": algorithm.pricing_worker_count,
                    "parallel_pricing_enabled": algorithm.pricing_worker_count > 1,
                },
            }),
            "sgfe_cache": {},
        }
        record = adapters[algorithm.algorithm](instance, algorithm, context)
        append_jsonl(args.output_dir / "runs.jsonl", record_to_dict(replace(record, integration_only=bool(config.get("integration_only")))))
        completed += 1

    manifest["finish_timestamp"] = utc_now()
    manifest["completed_this_invocation"] = completed
    manifest["planned_runs_this_invocation"] = len(matrix)
    write_json_atomic(args.output_dir / "manifest.json", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
