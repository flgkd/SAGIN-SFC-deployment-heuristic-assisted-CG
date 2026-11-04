"""Human-invoked runner for the V3 DWCG-Pure pricing-schedule study."""

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
    default_adapters,
    formal_scenario_id,
    read_jsonl,
    record_to_dict,
    write_json_atomic,
)
from formal_schema import FormalRunKey, formal_algorithm_from_dict, formal_plan_from_dict


LOADS = ("Medium", "High")
NEW_VARIANT_IDS = ("EX", "ESV", "HYB_S2", "HYB_S7")
REUSED_VARIANT_IDS = ("HYB_S5",)
ALL_VARIANT_IDS = ("EX", "ESV", "HYB_S2", "HYB_S5", "HYB_S7")
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


def validate_config(config: Mapping[str, Any]) -> None:
    scenario = config["scenario"]
    output = scenario.get("output_settings") or {}
    if config.get("version") != "v3":
        raise ValueError("Pricing schedule requires a V3 config.")
    if config.get("plan_kind") not in ("formal_pricing_strategy", "formal_pricing_strategy_pilot"):
        raise ValueError("Unexpected plan_kind for pricing schedule.")
    if scenario.get("scales") != ["S40"] or scenario.get("time_slots") != 8:
        raise ValueError("Pricing schedule requires S40 T=8.")
    if scenario.get("loads") != [{"label": "Medium", "demand_multiplier": 0.6}, {"label": "High", "demand_multiplier": 0.8}]:
        raise ValueError("Pricing schedule requires Medium and High loads.")
    if scenario.get("deadline_profile") != "Balanced":
        raise ValueError("Pricing schedule requires Balanced deadline profile.")
    if scenario.get("scale_flow_counts") != {"S40": 160}:
        raise ValueError("Pricing schedule requires F=160 for S40.")
    if config.get("integration_only"):
        return
    if output.get("expected_total_evidence_rows") != 100:
        raise ValueError("Formal pricing schedule config must describe 100 evidence rows.")
    if output.get("expected_new_formal_rows") != 80 or output.get("expected_reused_rows") != 20:
        raise ValueError("Formal pricing schedule config must distinguish 80 new and 20 reused rows.")


def variant_label(variant_id: str) -> str:
    return {"HYB_S5": "HYB-S5", "HYB_S2": "HYB-S2", "HYB_S7": "HYB-S7"}.get(variant_id, variant_id)


def run_key(load_label: str, seed: int, variant_id: str) -> Tuple[str, str, int, str, str]:
    return "S40", load_label, int(seed), "dwcg_pure", variant_id


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
        raise ValueError("Output directory is nonempty; use --resume or --overwrite.")
    if overwrite and path.exists() and any(path.iterdir()):
        raise ValueError("This runner does not support destructive overwrite; use a new directory.")
    path.mkdir(parents=True, exist_ok=True)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run V3 DWCG-Pure pricing-schedule records.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--loads", nargs="*", choices=LOADS)
    parser.add_argument("--seeds", nargs="*", type=int)
    parser.add_argument("--pricing-schedules", nargs="*", choices=ALL_VARIANT_IDS)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--include-reused-reference", action="store_true", help="Diagnostic-only: permit HYB-S5 execution. Not formal evidence.")
    args = parser.parse_args(argv)

    config = read_json(args.config)
    validate_config(config)
    expected_fingerprint = canonical_plan_fingerprint(config)
    all_algorithms = [formal_algorithm_from_dict(item) for item in config["scenario"]["algorithms"] if item.get("enabled", True)]
    allowed_variants = set(args.pricing_schedules) if args.pricing_schedules else set(NEW_VARIANT_IDS)
    if "HYB_S5" in allowed_variants and not args.include_reused_reference:
        raise ValueError("HYB-S5 is approved reused evidence; do not rerun it in formal mode.")
    if args.include_reused_reference and not config.get("integration_only"):
        raise ValueError("HYB-S5 diagnostic execution is allowed only from an integration/pilot config.")
    allowed_variants &= set(ALL_VARIANT_IDS)
    algorithms = [algorithm for algorithm in all_algorithms if algorithm.variant_id in allowed_variants]
    loads = [load for load in formal_plan_from_dict(config).scenario.loads if load.label in selected(LOADS, args.loads)]
    seeds = selected(config["scenario"]["formal_seeds"], args.seeds)
    matrix = [(load, int(seed), algorithm) for load in loads for seed in seeds for algorithm in algorithms]

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
            "loads": args.loads,
            "seeds": args.seeds,
            "pricing_schedules": args.pricing_schedules,
        },
        "plan_fingerprint": expected_fingerprint,
        "new_variant_ids": list(NEW_VARIANT_IDS),
        "reused_variant_ids": list(REUSED_VARIANT_IDS),
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
    plan = formal_plan_from_dict(config)
    completed = 0
    instance_cache: Dict[Tuple[str, int], Any] = {}
    for load, seed, algorithm in matrix:
        key_tuple = run_key(load.label, seed, str(algorithm.variant_id))
        if key_tuple in existing:
            continue
        if (load.label, seed) not in instance_cache:
            instance_cache[(load.label, seed)] = build_formal_instance(plan, "S40", seed, load)
        instance = instance_cache[(load.label, seed)]
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
                "execution_subset": {"load": load.label, "pricing_schedule": variant_label(str(algorithm.variant_id))},
                "algorithm": algorithm,
            }),
            "pricing_schedule": variant_label(str(algorithm.variant_id)),
            "sgfe_cache": {},
        }
        record = adapters[algorithm.algorithm](instance, algorithm, context)
        record_dict = record_to_dict(replace(record, integration_only=bool(config.get("integration_only"))))
        record_dict["pricing_schedule"] = variant_label(str(algorithm.variant_id))
        if record_dict.get("dwcg_metadata") is not None:
            record_dict["dwcg_metadata"]["pricing_schedule"] = variant_label(str(algorithm.variant_id))
            record_dict["dwcg_metadata"]["reuse_classification"] = "new_formal_run"
        append_jsonl(args.output_dir / "runs.jsonl", record_dict)
        completed += 1

    manifest["finish_timestamp"] = utc_now()
    manifest["completed_this_invocation"] = completed
    manifest["planned_runs_this_invocation"] = len(matrix)
    write_json_atomic(args.output_dir / "manifest.json", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
