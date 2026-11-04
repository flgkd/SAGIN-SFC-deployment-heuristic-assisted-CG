"""Formal benchmark runner for SAGIN experiments.

The runner reuses the shared instance builder and existing solver
implementations. It does not run automatically; CLI users choose smoke or
formal plans explicitly.
"""

from dataclasses import asdict, is_dataclass, replace
import csv
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from calibration_runner import (
    base_flow_fingerprint,
    canonical_json,
    certify_acceptance_count,
    full_instance_fingerprint,
    instance_metrics,
    sha256_fingerprint,
    to_jsonable,
    topology_fingerprint,
    utc_now,
)
from config_schema import (
    FlowGenerationConfig,
    InstanceConfig,
    TopologyConfig,
    get_candidate_deadline_profile,
    get_candidate_scale,
    get_candidate_sfc_profile,
    get_candidate_traffic_mix_profile,
)
from data_model import Path as SfcPath
from formal_schema import (
    FORMAL_FRAMEWORK_VERSION,
    FORMAL_DWCG_DISPLAY_NAMES,
    FormalAlgorithmConfig,
    FormalPlan,
    FormalRunKey,
    FormalRunRecord,
    LoadRegime,
)
from dwcg_saginfe_pro import (
    is_pro_algorithm,
    pro_metadata_defaults,
    selective_pfe_enabled_for_algorithm,
)
from instance_builder import SaginInstance, build_instance
from objective import ObjectiveSpec, compute_path_physical_cost, objective_spec_from_network
from saginfe_solver import build_saginfe_initial_paths_from_results, saginfe
from validation import validate_path, validate_solution_capacity
from deterministic_seeds import seed_bundle
from record_compatibility import algorithm_seed_namespace, normalize_record


FormalAdapter = Callable[[SaginInstance, FormalAlgorithmConfig, Dict[str, Any]], FormalRunRecord]


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def formal_scenario_id(plan: FormalPlan, scale: str, seed: int, load: LoadRegime) -> str:
    payload = {
        "plan": plan.name,
        "scale": scale,
        "time_slots": plan.scenario.time_slots,
        "seed": seed,
        "load_label": load.label,
        "demand_multiplier": load.demand_multiplier,
        "dynamic_topology": plan.scenario.dynamic_topology,
        "traffic_profile": plan.scenario.traffic_profile,
        "sfc_profile": plan.scenario.sfc_profile,
        "deadline_profile": plan.scenario.deadline_profile,
    }
    return f"{scale}_T{plan.scenario.time_slots}_seed{seed}_{load.label}_{sha256_fingerprint(payload)[:12]}"


def formal_matrix(
    plan: FormalPlan,
    algorithms: Optional[Iterable[str]] = None,
    scales: Optional[Iterable[str]] = None,
    loads: Optional[Iterable[str]] = None,
    seeds: Optional[Iterable[int]] = None,
    variants: Optional[Iterable[str]] = None,
) -> List[Tuple[str, int, LoadRegime, FormalAlgorithmConfig]]:
    plan.validate()
    selected = set(algorithms) if algorithms else None
    selected_scales = set(scales) if scales else None
    selected_loads = set(loads) if loads else None
    selected_seeds = {int(seed) for seed in seeds} if seeds else None
    selected_variants = set(variants) if variants else None
    order = {
        "saginfe": 0,
        "direct_ilp": 1,
        "direct_ilp_acceptance_cold": 2,
        "dwcg_pure": 3,
        "dwcg_warm": 4,
        "dwcg_pure_hp": 5,
        "dwcg_saginfe": 6,
        "dwcg_saginfe_pro_no_pfe": 7,
        "dwcg_saginfe_pro": 8,
    }
    enabled = [
        algorithm for algorithm in plan.scenario.algorithms
        if algorithm.enabled and (selected is None or algorithm.algorithm in selected)
        and (selected_variants is None or algorithm.variant_id in selected_variants)
    ]
    variant_order = {
        "EX": 1,
        "ESV": 2,
        "HYB_S5": 3,
        "HYB_S2": 4,
        "HYB_S7": 5,
        "W1": 11,
        "W2": 12,
        "W4": 14,
        "W8": 18,
        "CG8": 108,
        "CG12": 112,
        "CG16": 116,
    }
    enabled = sorted(enabled, key=lambda item: (order[item.algorithm], variant_order.get(item.variant_id, 0), item.variant_id or ""))
    load_by_label = {load.label: load for load in plan.scenario.loads}
    if plan.scenario.run_matrix:
        algorithm_by_key = {
            (algorithm.algorithm, algorithm.variant_id): algorithm
            for algorithm in enabled
        }
        algorithms_by_name: Dict[str, List[FormalAlgorithmConfig]] = {}
        for algorithm in enabled:
            algorithms_by_name.setdefault(algorithm.algorithm, []).append(algorithm)
        rows = []
        for run in plan.scenario.run_matrix:
            scale = str(run["scale"])
            seed = int(run["formal_seed"])
            load = load_by_label[str(run["load_label"])]
            algorithm_name = str(run["algorithm"])
            variant_id = run.get("variant_id")
            variant_key = None if variant_id is None else str(variant_id)
            if selected_scales is not None and scale not in selected_scales:
                continue
            if selected_loads is not None and load.label not in selected_loads:
                continue
            if selected_seeds is not None and seed not in selected_seeds:
                continue
            if selected is not None and algorithm_name not in selected:
                continue
            if selected_variants is not None and variant_key not in selected_variants:
                continue
            if variant_key is None:
                candidates = algorithms_by_name.get(algorithm_name, [])
                if len(candidates) != 1:
                    raise ValueError(
                        f"run_matrix row for {algorithm_name!r} must specify variant_id "
                        "when multiple enabled variants exist after filtering."
                    )
                algorithm = candidates[0]
            else:
                algorithm = algorithm_by_key.get((algorithm_name, variant_key))
                if algorithm is None:
                    continue
            rows.append((scale, seed, load, algorithm))
        return rows
    scenarios = (
        [(scale, load_by_label[load_label]) for scale, load_label in plan.scenario.scenario_pairs]
        if plan.scenario.scenario_pairs
        else [(scale, load) for scale in plan.scenario.scales for load in plan.scenario.loads]
    )
    scenarios = [
        (scale, load)
        for scale, load in scenarios
        if (selected_scales is None or scale in selected_scales)
        and (selected_loads is None or load.label in selected_loads)
    ]
    formal_seeds = [
        seed for seed in plan.scenario.formal_seeds
        if selected_seeds is None or seed in selected_seeds
    ]
    return [
        (scale, seed, load, algorithm)
        for scale, load in scenarios
        for seed in formal_seeds
        for algorithm in enabled
    ]


def build_formal_instance(plan: FormalPlan, scale: str, seed: int, load: LoadRegime) -> SaginInstance:
    scenario = plan.scenario
    if plan.plan_kind in ("formal_deadline_sensitivity_pilot", "formal_deadline_sensitivity"):
        from paired_deadline_builder import build_paired_deadline_instance

        topology = TopologyConfig(
            scale=get_candidate_scale(scale),
            time_slots=scenario.time_slots,
            seed=0,
            dynamic_topology=scenario.dynamic_topology,
        )
        flow = FlowGenerationConfig(
            seed=0,
            num_flows=scenario.scale_flow_counts.get(scale),
            demand_multiplier=load.demand_multiplier,
            traffic_mix_profile=get_candidate_traffic_mix_profile(scenario.traffic_profile),
            sfc_profile=get_candidate_sfc_profile(scenario.sfc_profile),
            deadline_profile=get_candidate_deadline_profile(scenario.deadline_profile),
            load_profile_name=load.label,
        )
        return build_paired_deadline_instance(
            InstanceConfig(topology, flow, scenario_seed=seed),
            scenario.deadline_profile,
        ).instance
    if plan.plan_kind in ("formal_same_flow_temporal_control_pilot", "formal_same_flow_temporal_control"):
        from same_flow_temporal_control_builder import build_same_flow_temporal_instance

        topology = TopologyConfig(
            scale=get_candidate_scale(scale),
            time_slots=scenario.time_slots,
            seed=0,
            dynamic_topology=scenario.dynamic_topology,
        )
        flow = FlowGenerationConfig(
            seed=0,
            demand_multiplier=load.demand_multiplier,
            traffic_mix_profile=get_candidate_traffic_mix_profile(scenario.traffic_profile),
            sfc_profile=get_candidate_sfc_profile(scenario.sfc_profile),
            deadline_profile=get_candidate_deadline_profile(scenario.deadline_profile),
            load_profile_name=load.label,
        )
        return build_same_flow_temporal_instance(
            InstanceConfig(topology, flow, scenario_seed=seed),
        ).instance
    if plan.plan_kind in ("formal_temporal_scalability_pilot", "formal_temporal_scalability"):
        from paired_temporal_builder import build_paired_temporal_instance

        topology = TopologyConfig(
            scale=get_candidate_scale(scale),
            time_slots=scenario.time_slots,
            seed=0,
            dynamic_topology=scenario.dynamic_topology,
        )
        flow = FlowGenerationConfig(
            seed=0,
            num_flows=scenario.scale_flow_counts.get(scale),
            demand_multiplier=load.demand_multiplier,
            traffic_mix_profile=get_candidate_traffic_mix_profile(scenario.traffic_profile),
            sfc_profile=get_candidate_sfc_profile(scenario.sfc_profile),
            deadline_profile=get_candidate_deadline_profile(scenario.deadline_profile),
            load_profile_name=load.label,
        )
        return build_paired_temporal_instance(
            InstanceConfig(topology, flow, scenario_seed=seed),
        ).instance
    topology = TopologyConfig(
        scale=get_candidate_scale(scale),
        time_slots=scenario.time_slots,
        seed=0,
        dynamic_topology=scenario.dynamic_topology,
    )
    flow = FlowGenerationConfig(
        seed=0,
        num_flows=scenario.scale_flow_counts.get(scale),
        demand_multiplier=load.demand_multiplier,
        traffic_mix_profile=get_candidate_traffic_mix_profile(scenario.traffic_profile),
        sfc_profile=get_candidate_sfc_profile(scenario.sfc_profile),
        deadline_profile=get_candidate_deadline_profile(scenario.deadline_profile),
        load_profile_name=load.label,
    )
    return build_instance(InstanceConfig(topology, flow, scenario_seed=seed))


def _run_git_command(args: List[str]) -> Tuple[str, int, str]:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    return (result.stdout or "").strip(), int(result.returncode), (result.stderr or "").strip()


def git_info() -> Tuple[str, bool]:
    commit, commit_returncode, commit_stderr = _run_git_command(["git", "rev-parse", "HEAD"])
    status, _status_returncode, _status_stderr = _run_git_command(["git", "status", "--short"])
    if commit_returncode != 0:
        detail = commit_stderr or f"git rev-parse HEAD exited {commit_returncode}"
        commit = f"GIT_COMMIT_UNAVAILABLE:{detail}"
    elif not commit:
        commit = "GIT_COMMIT_UNAVAILABLE:git rev-parse HEAD returned empty stdout"
    return commit, bool(status)


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(to_jsonable(payload), handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp_name, path)


def append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(payload) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(normalize_record(json.loads(line)))
            except Exception as exc:
                raise ValueError(f"Malformed {path.name} line {line_number}: {exc}") from exc
    return rows


def record_to_dict(record: FormalRunRecord) -> Dict[str, Any]:
    return to_jsonable(record)


def formal_run_key(plan: FormalPlan, scale: str, seed: int, load: LoadRegime, algorithm_config: FormalAlgorithmConfig) -> FormalRunKey:
    return FormalRunKey(
        scenario_id=formal_scenario_id(plan, scale, seed, load),
        scale=scale,
        time_slots=plan.scenario.time_slots,
        formal_seed=seed,
        load_label=load.label,
        demand_multiplier=load.demand_multiplier,
        algorithm=algorithm_config.algorithm,
        variant_id=algorithm_config.variant_id,
    )


def _instance_payload(key: FormalRunKey, instance: SaginInstance) -> Dict[str, Any]:
    payload = {
        "scenario_id": key.scenario_id,
        "scale": key.scale,
        "time_slots": key.time_slots,
        "formal_seed": key.formal_seed,
        "load_label": key.load_label,
        "demand_multiplier": key.demand_multiplier,
        "topology_fingerprint": topology_fingerprint(instance),
        "base_flow_fingerprint": base_flow_fingerprint(instance),
        "full_instance_fingerprint": full_instance_fingerprint(instance),
        "instance_metrics": instance_metrics(instance),
    }
    deadline_metadata = _deadline_sensitivity_metadata(instance)
    if deadline_metadata:
        payload.update(deadline_metadata)
    temporal_control_metadata = _same_flow_temporal_control_metadata(instance)
    if temporal_control_metadata:
        payload.update(temporal_control_metadata)
    paired_temporal_metadata = _paired_temporal_scalability_metadata(instance)
    if paired_temporal_metadata:
        payload.update(paired_temporal_metadata)
    return payload


def _matching_fingerprints(record: Mapping[str, Any], instance: SaginInstance) -> bool:
    return (
        record.get("topology_fingerprint") == topology_fingerprint(instance)
        and record.get("base_flow_fingerprint") == base_flow_fingerprint(instance)
        and record.get("full_instance_fingerprint") == full_instance_fingerprint(instance)
    )


def _deadline_sensitivity_metadata(instance: SaginInstance) -> Dict[str, Any]:
    metadata = instance.instance_metadata.get("paired_deadline_sensitivity") if hasattr(instance, "instance_metadata") else None
    if not metadata:
        return {}
    from paired_deadline_builder import deadline_invariant_flow_fingerprint, full_flow_fingerprint
    per_flow = instance.flow_metadata.get("per_flow", {})
    aggregate = instance.flow_metadata.get("aggregate", {})
    deadline_summary = aggregate.get("deadline", {})
    last_slot = instance.network.time_slots_num - 1

    return {
        "deadline_profile": metadata["deadline_profile"],
        "deadline_invariant_flow_fingerprint": deadline_invariant_flow_fingerprint(instance),
        "full_flow_fingerprint": full_flow_fingerprint(instance),
        "deadline_uniform_fingerprint": metadata["deadline_uniform_fingerprint"],
        "deadline_clipping_count": 0,
        "latest_source_time": metadata["latest_source_time"],
        "deadline_slack_histogram": {
            str(key): value for key, value in (deadline_summary.get("realized_slack_counts") or {}).items()
        },
        "deadline_arrival_time_histogram": {
            str(key): value for key, value in _count_values(meta["arrival_time"] for meta in per_flow.values()).items()
        },
        "deadline_time_histogram": {
            str(key): value for key, value in _count_values(meta["deadline_time"] for meta in per_flow.values()).items()
        },
        "deadline_minimum_slack": deadline_summary.get("minimum_slack"),
        "deadline_maximum_slack": deadline_summary.get("maximum_slack"),
        "deadline_mean_slack": deadline_summary.get("mean_slack"),
        "deadline_horizon_last_slot_count": sum(
            1 for meta in per_flow.values() if meta.get("deadline_time") == last_slot
        ),
        "deadline_saturation_count": sum(
            1 for meta in per_flow.values() if meta.get("deadline_time") == last_slot
        ),
    }


def _count_values(values: Iterable[Any]) -> Dict[Any, int]:
    counts: Dict[Any, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _same_flow_temporal_control_metadata(instance: SaginInstance) -> Dict[str, Any]:
    metadata = instance.instance_metadata.get("same_flow_temporal_control") if hasattr(instance, "instance_metadata") else None
    if not metadata:
        return {}
    from same_flow_temporal_control_builder import (
        common_prefix_fingerprint,
        full_temporal_flow_fingerprint,
        strict_same_flow_fingerprint,
        temporal_prefix_fingerprint,
    )

    aggregate = instance.flow_metadata.get("aggregate", {})
    deadline_summary = aggregate.get("deadline", {})
    supported = tuple(int(value) for value in metadata.get("supported_time_slots", (metadata["requested_time_slots"],)))
    requested_t = int(metadata["requested_time_slots"])
    common_prefixes = {
        str(prefix): common_prefix_fingerprint(instance, prefix)
        for prefix in supported
        if int(prefix) <= requested_t
    }
    return {
        "deadline_profile": metadata["deadline_profile"],
        "strict_same_flow_fingerprint": strict_same_flow_fingerprint(instance),
        "canonical_t12_topology_fingerprint": metadata["canonical_t12_topology_fingerprint"],
        "temporal_prefix_fingerprint": temporal_prefix_fingerprint(instance),
        "common_prefix_fingerprint": common_prefix_fingerprint(instance, instance.network.time_slots_num),
        "common_temporal_prefix_fingerprints": common_prefixes,
        "physical_base_fingerprint": metadata.get("physical_base_fingerprint"),
        "non_temporal_flow_fingerprint": metadata.get("non_temporal_flow_fingerprint", strict_same_flow_fingerprint(instance)),
        "paired_timing_latent_fingerprint": metadata.get("paired_timing_latent_fingerprint"),
        "full_temporal_flow_fingerprint": full_temporal_flow_fingerprint(instance),
        "source_time_counts": metadata["source_time_counts"],
        "source_time_support": metadata["source_time_support"],
        "deadline_clipping_count": metadata["deadline_clipping_count"],
        "canonical_time_slots": metadata["canonical_time_slots"],
        "requested_time_slots": requested_t,
        "temporal_canonical_time_slots": metadata["canonical_time_slots"],
        "temporal_requested_time_slots": requested_t,
        "temporal_supported_time_slots": supported,
        "temporal_flow_anchor_time_slots": metadata.get("flow_anchor_time_slots"),
        "temporal_teg_node_count": len(instance.network.time_node_capacity),
        "temporal_storage_edge_count": sum(1 for edge in instance.network.time_edge_capacity if edge[0][0] == edge[1][0]),
        "temporal_communication_edge_count": sum(1 for edge in instance.network.time_edge_capacity if edge[0][0] != edge[1][0]),
        "temporal_arrival_time_histogram": {
            str(key): value for key, value in (deadline_summary.get("arrival_time_counts") or {}).items()
        },
        "temporal_slack_histogram": {
            str(key): value for key, value in (deadline_summary.get("realized_slack_counts") or {}).items()
        },
        "temporal_deadline_time_histogram": {
            str(key): value for key, value in (deadline_summary.get("deadline_time_counts") or {}).items()
        },
        "temporal_minimum_slack": deadline_summary.get("minimum_slack"),
        "temporal_maximum_slack": deadline_summary.get("maximum_slack"),
        "temporal_mean_slack": deadline_summary.get("mean_slack"),
        "temporal_clipping_count": deadline_summary.get("clipping_count"),
        "temporal_horizon_last_slot_count": deadline_summary.get("horizon_last_slot_count"),
        "temporal_fixed_f_average_flows_per_time_slot": len(instance.flows) / requested_t if requested_t else None,
    }


def _paired_temporal_scalability_metadata(instance: SaginInstance) -> Dict[str, Any]:
    metadata = instance.instance_metadata.get("paired_temporal_scalability") if hasattr(instance, "instance_metadata") else None
    if not metadata:
        return {}
    from paired_temporal_builder import (
        common_temporal_prefix_fingerprint,
        full_temporal_flow_fingerprint,
        non_temporal_flow_fingerprint,
        paired_timing_latent_fingerprint,
        physical_base_fingerprint,
        temporal_prefix_fingerprint,
    )

    aggregate = instance.flow_metadata.get("aggregate", {})
    deadline_summary = aggregate.get("deadline", {})
    supported = tuple(int(value) for value in metadata.get("supported_time_slots", ()))
    requested_t = int(metadata["requested_time_slots"])
    common_prefixes = {
        str(prefix): common_temporal_prefix_fingerprint(instance, prefix)
        for prefix in supported
        if int(prefix) <= requested_t
    }
    return {
        "deadline_profile": metadata["deadline_profile"],
        "physical_base_fingerprint": physical_base_fingerprint(instance),
        "temporal_prefix_fingerprint": temporal_prefix_fingerprint(instance, requested_t),
        "common_temporal_prefix_fingerprints": common_prefixes,
        "non_temporal_flow_fingerprint": non_temporal_flow_fingerprint(instance),
        "paired_timing_latent_fingerprint": paired_timing_latent_fingerprint(instance),
        "full_temporal_flow_fingerprint": full_temporal_flow_fingerprint(instance),
        "temporal_canonical_time_slots": metadata["canonical_time_slots"],
        "temporal_requested_time_slots": requested_t,
        "temporal_supported_time_slots": supported,
        "temporal_teg_node_count": len(instance.network.time_node_capacity),
        "temporal_storage_edge_count": sum(1 for edge in instance.network.time_edge_capacity if edge[0][0] == edge[1][0]),
        "temporal_communication_edge_count": sum(1 for edge in instance.network.time_edge_capacity if edge[0][0] != edge[1][0]),
        "temporal_arrival_time_histogram": {
            str(key): value for key, value in (deadline_summary.get("arrival_time_counts") or {}).items()
        },
        "temporal_slack_histogram": {
            str(key): value for key, value in (deadline_summary.get("realized_slack_counts") or {}).items()
        },
        "temporal_deadline_time_histogram": {
            str(key): value for key, value in (deadline_summary.get("deadline_time_counts") or {}).items()
        },
        "temporal_minimum_slack": deadline_summary.get("minimum_slack"),
        "temporal_maximum_slack": deadline_summary.get("maximum_slack"),
        "temporal_mean_slack": deadline_summary.get("mean_slack"),
        "temporal_clipping_count": deadline_summary.get("clipping_count"),
        "temporal_horizon_last_slot_count": deadline_summary.get("horizon_last_slot_count"),
        "temporal_fixed_f_average_flows_per_time_slot": len(instance.flows) / requested_t if requested_t else None,
    }


def _record_resume_key(row: Mapping[str, Any]) -> Tuple:
    key = row["key"]
    base = (
        key["scenario_id"],
        key["scale"],
        int(key["formal_seed"]),
        key["load_label"],
        key["algorithm"],
    )
    variant_id = key.get("variant_id")
    return base if variant_id is None else (*base, variant_id)


_NON_RESUMABLE_TERMINATION_STATUSES = frozenset(("ERROR", "FAILED", "INCOMPLETE", "INTERRUPTED"))


def is_resume_skippable_record(row: Mapping[str, Any]) -> bool:
    status = row.get("termination_status")
    if status is None or str(status).strip().upper() in _NON_RESUMABLE_TERMINATION_STATUSES:
        return False
    if row.get("error_type") or row.get("error_message"):
        return False
    return True


def completed_resume_records_by_key(
    rows: Iterable[Mapping[str, Any]],
    key_func: Callable[[Mapping[str, Any]], Tuple],
) -> Dict[Tuple, Mapping[str, Any]]:
    completed: Dict[Tuple, Mapping[str, Any]] = {}
    duplicates: List[Tuple] = []
    for row in rows:
        if not is_resume_skippable_record(row):
            continue
        key = key_func(row)
        if key in completed:
            duplicates.append(key)
        else:
            completed[key] = row
    if duplicates:
        raise ValueError(f"Duplicate completed resume records: {duplicates[:10]}")
    return completed


def ensure_output_dir(path: Path, overwrite: bool, resume: bool) -> None:
    if path.exists() and any(path.iterdir()) and not (overwrite or resume):
        raise ValueError("Output directory is nonempty; use --overwrite or --resume.")
    if overwrite and path.exists():
        for child in path.iterdir():
            if child.is_file():
                child.unlink()
    path.mkdir(parents=True, exist_ok=True)


def run_formal_plan(
    plan: FormalPlan,
    output_dir: Path,
    algorithms: Optional[Iterable[str]] = None,
    scales: Optional[Iterable[str]] = None,
    loads: Optional[Iterable[str]] = None,
    seeds: Optional[Iterable[int]] = None,
    variants: Optional[Iterable[str]] = None,
    resume: bool = False,
    allow_dirty: bool = False,
    overwrite: bool = False,
    adapters: Optional[Dict[str, FormalAdapter]] = None,
    cli_arguments: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    plan.validate()
    commit, dirty = git_info()
    if dirty and not allow_dirty:
        raise RuntimeError("Git worktree is dirty; pass --allow-dirty for debugging only.")
    integration_only = bool(plan.integration_only or plan.smoke)
    if plan.smoke:
        run_category = "integration_smoke"
    elif integration_only:
        run_category = "integration_pilot"
    else:
        run_category = "formal_paper_or_diagnostic"
    plan_fingerprint = sha256_fingerprint(plan)
    if resume and (output_dir / "manifest.json").exists():
        existing_manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
        existing_category = existing_manifest.get("run_category")
        if existing_category is not None and existing_category != run_category:
            raise ValueError("Cannot mix smoke/integration output with formal paper output.")
        existing_plan_fingerprint = existing_manifest.get("plan_fingerprint")
        if existing_plan_fingerprint is not None and existing_plan_fingerprint != plan_fingerprint:
            raise ValueError("Cannot resume output directory with a different formal plan fingerprint.")
    ensure_output_dir(output_dir, overwrite=overwrite, resume=resume)
    matrix = formal_matrix(plan, algorithms, scales=scales, loads=loads, seeds=seeds, variants=variants)
    manifest = {
        "formal_framework_version": FORMAL_FRAMEWORK_VERSION,
        "run_category": run_category,
        "integration_only": integration_only,
        "git_commit": commit,
        "git_dirty": dirty,
        "python_version": sys.version,
        "platform": platform.platform(),
        "start_timestamp": utc_now(),
        "finish_timestamp": None,
        "cli_arguments": cli_arguments or {},
        "run_filters": {
            "algorithms": list(algorithms) if algorithms else None,
            "scales": list(scales) if scales else None,
            "loads": list(loads) if loads else None,
            "seeds": list(seeds) if seeds else None,
            "variants": list(variants) if variants else None,
        },
        "plan_fingerprint": plan_fingerprint,
        "allow_dirty": allow_dirty,
    }
    write_json_atomic(output_dir / "manifest.json", manifest)
    write_json_atomic(output_dir / "plan_snapshot.json", plan)

    adapter_map = adapters or default_adapters()
    existing_runs = completed_resume_records_by_key(
        read_jsonl(output_dir / "runs.jsonl"),
        _record_resume_key,
    ) if resume else {}
    existing_instances = {
        row["full_instance_fingerprint"]: row
        for row in read_jsonl(output_dir / "instances.jsonl")
    } if resume else {}
    completed = []
    instance_cache: Dict[Tuple[str, int, str], SaginInstance] = {}
    saginfe_cache: Dict[str, Dict[int, SfcPath]] = {}

    for scale, seed, load, algorithm_config in matrix:
        instance_key = (scale, seed, load.label)
        if instance_key not in instance_cache:
            instance_cache[instance_key] = build_formal_instance(plan, scale, seed, load)
        instance = instance_cache[instance_key]
        key = formal_run_key(plan, scale, seed, load, algorithm_config)
        existing = existing_runs.get(key.as_tuple())
        if existing is not None:
            if _matching_fingerprints(existing, instance):
                continue
            raise ValueError(f"Existing formal run {key.as_tuple()} has mismatched fingerprints.")

        payload = _instance_payload(key, instance)
        full_fp = payload["full_instance_fingerprint"]
        if full_fp in existing_instances:
            if not _matching_fingerprints(existing_instances[full_fp], instance):
                raise ValueError(f"Existing instance record for {key.scenario_id} has mismatched fingerprints.")
        else:
            append_jsonl(output_dir / "instances.jsonl", payload)
            existing_instances[full_fp] = payload

        context = {
            "key": key,
            "plan": plan,
            "git_commit": commit,
            "configuration_snapshot": to_jsonable({"plan": plan, "algorithm": algorithm_config}),
            "saginfe_cache": saginfe_cache,
        }
        record = adapter_map[algorithm_config.algorithm](instance, algorithm_config, context)
        record = replace(
            record,
            integration_only=integration_only,
            run_category=run_category,
        )
        append_jsonl(output_dir / "runs.jsonl", record_to_dict(record))
        completed.append(record)

    rows = read_jsonl(output_dir / "runs.jsonl")
    from formal_analysis import analyze_formal_records, write_formal_reports

    analysis = analyze_formal_records(rows)
    write_formal_summary_csv(output_dir / "summary.csv", analysis)
    write_formal_reports(output_dir, analysis)
    manifest["finish_timestamp"] = utc_now()
    write_json_atomic(output_dir / "manifest.json", manifest)
    return {"planned_runs": len(matrix), "completed_this_invocation": len(completed), "analysis": analysis}


def default_adapters() -> Dict[str, FormalAdapter]:
    return {
        "direct_ilp": run_direct_ilp_formal_adapter,
        "direct_ilp_acceptance_cold": run_direct_ilp_acceptance_cold_formal_adapter,
        "saginfe": run_saginfe_formal_adapter,
        "dwcg_pure": run_dwcg_formal_adapter,
        "dwcg_warm": run_dwcg_formal_adapter,
        "dwcg_pure_hp": run_dwcg_formal_adapter,
        "dwcg_saginfe": run_dwcg_formal_adapter,
        "dwcg_saginfe_pro_no_pfe": run_dwcg_formal_adapter,
        "dwcg_saginfe_pro": run_dwcg_formal_adapter,
    }


def _build_paths_from_saginfe(instance: SaginInstance, results) -> Dict[int, SfcPath]:
    selected = {}
    objective_spec: ObjectiveSpec = objective_spec_from_network(instance.network)
    for flow in instance.flows:
        eta, phi = results.get(flow.flow_id, (None, None))
        if eta is None or phi is None:
            continue
        path = SfcPath(flow.flow_id, -1, False, eta, phi, 0.0)
        path.finalize_usage(flow=flow)
        path.path_cost = compute_path_physical_cost(instance.network, flow, path, objective_spec)
        validate_path(instance.network, flow, path)
        selected[flow.flow_id] = path
    validate_solution_capacity(instance.network, instance.flows, selected)
    return selected


def _saginfe_initialization_fingerprint(instance: SaginInstance) -> str:
    return sha256_fingerprint({
        "topology": topology_fingerprint(instance),
        "base_flow": base_flow_fingerprint(instance),
        "full_instance": full_instance_fingerprint(instance),
        "source": "saginfe",
    })


_PRIMARY_PRICING_PHASE_NAMES = frozenset(("Pricing", "Exact Gurobi fallback"))


def _primary_pricing_phases(phase_history: List[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    return [
        phase for phase in phase_history
        if phase.get("phase") in _PRIMARY_PRICING_PHASE_NAMES
    ]


def _pricing_strategy_execution_invariant(
    pricing_strategy: str,
    phase_history: List[Mapping[str, Any]],
    convergence_certified: bool,
    exact_iteration_certified: bool,
    hybrid_switch_iteration: int,
) -> bool:
    exact_phases = _primary_pricing_phases(phase_history)
    if pricing_strategy == "exact":
        if any(phase.get("stop_early") for phase in exact_phases):
            return False
    elif pricing_strategy == "early_stop":
        if exact_phases and not exact_phases[0].get("stop_early"):
            return False
    elif pricing_strategy == "hybrid":
        for phase in exact_phases:
            iteration = phase.get("iteration")
            if iteration is None:
                continue
            if iteration > hybrid_switch_iteration and phase.get("stop_early"):
                return False
    elif pricing_strategy == "heuristic_then_exact":
        pass
    else:
        return False
    if convergence_certified and not exact_iteration_certified:
        return False
    return True


def derive_formal_algorithm_seed(instance: SaginInstance, key: FormalRunKey, algorithm_config: FormalAlgorithmConfig) -> int:
    return derive_formal_algorithm_seed_bundle(instance, key, algorithm_config)["c_long_seed"]


def derive_formal_algorithm_seed_bundle(instance: SaginInstance, key: FormalRunKey, algorithm_config: FormalAlgorithmConfig) -> Dict[str, Any]:
    if algorithm_config.algorithm_seed is not None:
        return seed_bundle("configured", int(algorithm_config.algorithm_seed))
    return seed_bundle(
        full_instance_fingerprint(instance),
        algorithm_seed_namespace(algorithm_config.algorithm),
        key.formal_seed,
        key.load_label,
    )


def _compute_saginfe_initialization(instance: SaginInstance, verbose: bool = False) -> Dict[str, Any]:
    start = time.perf_counter()
    results = saginfe(instance.network, list(instance.flows), instance.network.nodes, instance.network.nodes, verbose=verbose)
    paths = _build_paths_from_saginfe(instance, results)
    return {
        "paths": paths,
        "generation_runtime_seconds": time.perf_counter() - start,
        "scenario_fingerprint": _saginfe_initialization_fingerprint(instance),
        "solution_validated": True,
        "accepted_flow_count": len(paths),
    }


def _get_or_run_saginfe_initialization(
    instance: SaginInstance,
    context: Dict[str, Any],
    verbose: bool = False,
    allow_solution_cache: bool = False,
) -> Dict[str, Any]:
    if not allow_solution_cache:
        return {
            **_compute_saginfe_initialization(instance, verbose=verbose),
            "cache_reused": False,
        }
    key = context["key"].scenario_id
    cache = context.setdefault("saginfe_cache", {})
    if key not in cache:
        cache[key] = _compute_saginfe_initialization(instance, verbose=verbose)
        reused = False
    else:
        reused = True
    cached = cache[key]
    return {
        **cached,
        "cache_reused": reused,
    }


def _get_or_run_saginfe_paths(instance: SaginInstance, context: Dict[str, Any], verbose: bool = False) -> Dict[int, SfcPath]:
    return _get_or_run_saginfe_initialization(instance, context, verbose=verbose)["paths"]


def _base_record(
    instance: SaginInstance,
    context: Dict[str, Any],
    status: str,
    runtime: float,
    accepted: Optional[int],
    physical_cost: Optional[float],
    objective: Optional[float],
    path_validity: Optional[bool],
    capacity_validity: Optional[bool],
    error: Optional[Exception] = None,
    **kwargs,
) -> FormalRunRecord:
    key = context["key"]
    total = len(instance.flows)
    plan = context.get("plan")
    integration_only = bool(plan is not None and (plan.integration_only or plan.smoke))
    if plan is not None and plan.smoke:
        run_category = "integration_smoke"
    elif integration_only:
        run_category = "integration_pilot"
    else:
        run_category = "formal_paper_or_diagnostic"
    return FormalRunRecord(
        key=key,
        git_commit=context.get("git_commit", ""),
        total_flow_count=total,
        accepted_flow_count=accepted,
        acceptance_rate=accepted / total if accepted is not None and total else None,
        normalized_physical_cost=physical_cost,
        canonical_objective_value=objective,
        path_validity=path_validity,
        capacity_validity=capacity_validity,
        termination_status=status,
        total_end_to_end_runtime_seconds=runtime,
        error_type=error.__class__.__name__ if error is not None else None,
        error_message=str(error) if error is not None else None,
        topology_fingerprint=topology_fingerprint(instance),
        base_flow_fingerprint=base_flow_fingerprint(instance),
        full_instance_fingerprint=full_instance_fingerprint(instance),
        configuration_snapshot=context.get("configuration_snapshot", {}),
        integration_only=integration_only,
        run_category=run_category,
        **kwargs,
    )


def run_saginfe_formal_adapter(instance: SaginInstance, algorithm_config: FormalAlgorithmConfig, context: Dict[str, Any]) -> FormalRunRecord:
    start = time.perf_counter()
    try:
        initialization = _get_or_run_saginfe_initialization(
            instance,
            context,
            verbose=False,
            allow_solution_cache=False,
        )
        paths = initialization["paths"]
        objective_spec = objective_spec_from_network(instance.network)
        physical = sum(path.path_cost for path in paths.values())
        accepted = len(paths)
        objective = objective_spec.acceptance_reward * accepted - physical
        runtime = time.perf_counter() - start
        return _base_record(
            instance, context, "COMPLETED", runtime, accepted, physical, objective,
            path_validity=True, capacity_validity=True,
            heuristic_metadata={
                "standalone_saginfe": True,
                "initialization_source": "none",
                "algorithm_core_runtime_seconds": runtime,
                "total_end_to_end_runtime_seconds": runtime,
                "runtime_accounting_valid": True,
                "initialization_cache_reused": initialization["cache_reused"],
                "initialization_scenario_fingerprint": initialization["scenario_fingerprint"],
                "initialization_solution_validated": initialization["solution_validated"],
                "initialization_accepted_flow_count": initialization["accepted_flow_count"],
                **_deadline_sensitivity_metadata(instance),
                **_same_flow_temporal_control_metadata(instance),
                **_paired_temporal_scalability_metadata(instance),
            },
            **_deadline_sensitivity_metadata(instance),
            **_paired_temporal_scalability_metadata(instance),
        )
    except Exception as exc:
        return _base_record(instance, context, "ERROR", time.perf_counter() - start, None, None, None, None, None, exc)


def run_direct_ilp_formal_adapter(instance: SaginInstance, algorithm_config: FormalAlgorithmConfig, context: Dict[str, Any]) -> FormalRunRecord:
    start = time.perf_counter()
    try:
        warm_paths = None
        initialization = None
        if algorithm_config.direct_ilp_warm_start:
            initialization = _get_or_run_saginfe_initialization(
                instance,
                context,
                verbose=False,
                allow_solution_cache=False,
            )
            warm_paths = initialization["paths"]
        from ILP_solver import solve_ilp

        core_start = time.perf_counter()
        solution = solve_ilp(
            instance.network,
            list(instance.flows),
            objective_spec=objective_spec_from_network(instance.network),
            time_limit=algorithm_config.time_limit,
            mip_gap=algorithm_config.mip_gap,
            mip_gap_abs=algorithm_config.mip_gap_abs,
            threads=algorithm_config.threads,
            gurobi_seed=algorithm_config.gurobi_seed,
            verbose=False,
            warm_start_paths=warm_paths,
        )
        algorithm_core_runtime = time.perf_counter() - core_start
        initialization_generation_runtime = (
            initialization["generation_runtime_seconds"] if initialization is not None else 0.0
        )
        total_end_to_end_runtime = initialization_generation_runtime + algorithm_core_runtime
        certification = certify_acceptance_count(
            solution.accepted_count if solution.has_incumbent else None,
            len(instance.flows),
            solution.global_original_integer_optimal,
            solution.best_bound,
        )
        runtime_meta = solution.result.metadata.get("runtime_breakdown", {}) if solution.result else {}
        direct_meta = solution.result.metadata if solution.result else {}
        warm_meta = solution.result.metadata.get("warm_start", {}) if solution.result else {}
        return _base_record(
            instance, context, solution.status_name or str(solution.status), total_end_to_end_runtime,
            solution.accepted_count if solution.has_incumbent else None,
            solution.normalized_physical_cost,
            solution.scalarized_objective,
            path_validity=solution.has_incumbent,
            capacity_validity=solution.has_incumbent,
            objective_globally_optimal=solution.global_original_integer_optimal,
            acceptance_count_certified=certification["acceptance_count_certified"],
            certification_method=certification["acceptance_certification_method"],
            best_bound=solution.best_bound,
            mip_gap=solution.final_mip_gap,
            optimality_scope="global_original_integer" if solution.global_original_integer_optimal else "not_certified",
            direct_ilp_metadata={
                "identifier": "direct_ilp",
                "campaign": context["plan"].scenario.output_settings.get("campaign"),
                "display_name": algorithm_config.display_name,
                "objective_mode": direct_meta.get("objective_mode", "scalarized"),
                "objective_sense": "maximize",
                "objective_description": "maximize acceptance_reward * accepted flows - normalized physical cost",
                "physical_cost_in_objective": True,
                "cold_start": warm_paths is None,
                "mip_start_mode": "none" if warm_paths is None else "saginfe",
                "external_mip_start_used": warm_paths is not None,
                "initialization_source": "saginfe" if warm_paths is not None else "none",
                "initialization_generation_runtime_seconds": initialization_generation_runtime,
                "initialization_application_runtime_seconds": runtime_meta.get("warm_start_application_runtime_seconds"),
                "algorithm_core_runtime_seconds": algorithm_core_runtime,
                "total_end_to_end_runtime_seconds": total_end_to_end_runtime,
                "initialization_cache_reused": initialization["cache_reused"] if initialization is not None else False,
                "initialization_scenario_fingerprint": initialization["scenario_fingerprint"] if initialization is not None else None,
                "initialization_solution_validated": initialization["solution_validated"] if initialization is not None else None,
                "initialization_accepted_flow_count": initialization["accepted_flow_count"] if initialization is not None else None,
                "configured_threads": direct_meta.get("configured_threads"),
                "threads": algorithm_config.threads,
                "solver_seed": direct_meta.get("configured_gurobi_seed"),
                "configured_gurobi_seed": direct_meta.get("configured_gurobi_seed"),
                "configured_mip_gap": direct_meta.get("configured_mip_gap"),
                "configured_mip_gap_abs": direct_meta.get("configured_mip_gap_abs"),
                "best_bound_scope": "global_original_ilp",
                "mip_gap_scope": "global_original_ilp",
                "model_build_runtime_seconds": runtime_meta.get("model_build_runtime_seconds"),
                "optimization_runtime_seconds": runtime_meta.get("optimization_runtime_seconds", solution.runtime),
                "extraction_runtime_seconds": runtime_meta.get("result_extraction_runtime_seconds"),
                "initialization_runtime_seconds": initialization_generation_runtime,
                "end_to_end_runtime_seconds": total_end_to_end_runtime,
                "hard_time_limit_enabled": False,
                "configured_end_to_end_limit_seconds": None,
                "effective_solver_time_limit_seconds": direct_meta.get("effective_solver_time_limit_seconds"),
                "hard_wall_clock_limit_reached": False,
                "solver_time_limit_reached": solution.status_name == "TIME_LIMIT",
                "solver_status": solution.status,
                "solver_status_name": solution.status_name,
                "sol_count": solution.sol_count,
                "accepted_flow_incumbent_count": solution.accepted_count if solution.has_incumbent else None,
                "raw_objective_value": solution.objective,
                "raw_objective_bound": solution.best_bound,
                "global_full_objective_optimal": solution.status_name == "OPTIMAL",
                "optimality_certification_reason": solution.status_name,
                "mip_gap": solution.final_mip_gap,
                "node_count": solution.node_count,
                "simplex_iterations": getattr(solution.model, "IterCount", None) if solution.model is not None else None,
                "warm_start_supplied": warm_paths is not None,
                "warm_start_accepted_count": len(warm_paths) if warm_paths is not None else None,
                "warm_start_complete": warm_meta.get("complete"),
            },
        )
    except Exception as exc:
        return _base_record(instance, context, "ERROR", time.perf_counter() - start, None, None, None, None, None, exc)


def _safe_integer_acceptance_upper_bound(raw_bound: Optional[float], flow_count: int, tolerance: float = 1e-7) -> Optional[int]:
    if raw_bound is None or not math.isfinite(raw_bound):
        return None
    return max(0, min(flow_count, int(math.ceil(float(raw_bound) - tolerance))))


def _direct_ilp_acceptance_termination_status(
    solver_status_name: Optional[str],
    has_incumbent: bool,
    certified: bool,
    hard_wall_reached: bool,
) -> str:
    if solver_status_name == "OPTIMAL":
        return "OPTIMAL"
    if certified:
        return "BOUND_CERTIFIED"
    if hard_wall_reached:
        return "E2E_HARD_WALL_WITH_INCUMBENT" if has_incumbent else "E2E_HARD_WALL_NO_INCUMBENT"
    if solver_status_name == "TIME_LIMIT":
        return "TIME_LIMIT_WITH_INCUMBENT" if has_incumbent else "TIME_LIMIT_NO_INCUMBENT"
    if solver_status_name == "INFEASIBLE":
        return "INFEASIBLE"
    return solver_status_name or "ERROR"


def _direct_ilp_acceptance_certification(
    solver_status_name: Optional[str],
    incumbent: Optional[int],
    integer_upper_bound: Optional[int],
) -> Tuple[bool, str]:
    if solver_status_name == "OPTIMAL":
        return True, "GUROBI_OPTIMAL"
    if incumbent is not None and integer_upper_bound is not None and integer_upper_bound == incumbent:
        return True, "INTEGER_BOUND_MATCH"
    if incumbent is None:
        return False, "NO_VALID_INCUMBENT"
    if integer_upper_bound is None:
        return False, "NO_FINITE_INTEGER_UPPER_BOUND"
    return False, "INTEGER_BOUND_EXCEEDS_INCUMBENT"


def direct_ilp_acceptance_configured_e2e_limit(
    algorithm_config: FormalAlgorithmConfig,
) -> Optional[float]:
    if algorithm_config.disable_hard_time_limit:
        return None
    return algorithm_config.hard_time_limit_seconds


def dwcg_configured_e2e_limit(
    algorithm_config: FormalAlgorithmConfig,
) -> Optional[float]:
    if algorithm_config.disable_hard_time_limit:
        return None
    return algorithm_config.hard_time_limit_seconds or algorithm_config.time_limit


def run_direct_ilp_acceptance_cold_formal_adapter(
    instance: SaginInstance,
    algorithm_config: FormalAlgorithmConfig,
    context: Dict[str, Any],
) -> FormalRunRecord:
    start = time.perf_counter()
    configured_limit = direct_ilp_acceptance_configured_e2e_limit(algorithm_config)
    try:
        if algorithm_config.direct_ilp_warm_start:
            raise ValueError("direct_ilp_acceptance_cold forbids direct_ilp_warm_start.")
        if algorithm_config.time_limit is not None:
            raise ValueError("direct_ilp_acceptance_cold uses hard_time_limit_seconds, not time_limit.")
        if algorithm_config.disable_hard_time_limit and algorithm_config.hard_time_limit_seconds is not None:
            raise ValueError("direct_ilp_acceptance_cold unlimited mode forbids hard_time_limit_seconds.")
        if int(algorithm_config.threads) != 1:
            raise ValueError("direct_ilp_acceptance_cold requires Threads=1.")
        if algorithm_config.mip_gap != 0.0:
            raise ValueError("direct_ilp_acceptance_cold requires MIPGap=0.")

        from ILP_solver import solve_ilp

        solution = solve_ilp(
            instance.network,
            list(instance.flows),
            objective_spec=objective_spec_from_network(instance.network),
            time_limit=None,
            mip_gap=0.0,
            mip_gap_abs=0.0,
            threads=1,
            gurobi_seed=0,
            verbose=False,
            warm_start_paths=None,
            objective_mode="acceptance_only",
            enforce_simple_routes=True,
            e2e_start_time=start,
            e2e_time_limit_seconds=configured_limit,
        )
        total_end_to_end_runtime = time.perf_counter() - start
        runtime_meta = solution.result.metadata.get("runtime_breakdown", {}) if solution.result else {}
        direct_meta = solution.result.metadata if solution.result else {}
        raw_bound = solution.best_bound
        incumbent = solution.accepted_count if solution.has_incumbent else None
        safe_upper = _safe_integer_acceptance_upper_bound(raw_bound, len(instance.flows))
        safe_lower = incumbent
        hard_wall_reached = (
            False
            if configured_limit is None
            else bool(total_end_to_end_runtime > configured_limit + 1.0)
        )
        certified, certification_reason = _direct_ilp_acceptance_certification(
            solution.status_name,
            incumbent,
            safe_upper,
        )
        termination_status = _direct_ilp_acceptance_termination_status(
            solution.status_name,
            solution.has_incumbent,
            certified,
            hard_wall_reached,
        )
        solver_runtime = runtime_meta.get("optimization_runtime_seconds", solution.runtime)
        model_build_runtime = runtime_meta.get("model_build_runtime_seconds")
        extraction_runtime = runtime_meta.get("result_extraction_runtime_seconds")
        runtime_accounting_total = sum(
            value for value in (model_build_runtime, solver_runtime, extraction_runtime)
            if value is not None
        )
        accounting_residual = total_end_to_end_runtime - runtime_accounting_total
        runtime_accounting_valid = (
            (configured_limit is None or total_end_to_end_runtime <= configured_limit + 5.0)
            and accounting_residual >= -1e-6
        )
        return _base_record(
            instance,
            context,
            termination_status,
            total_end_to_end_runtime,
            incumbent,
            solution.normalized_physical_cost,
            solution.objective,
            path_validity=solution.has_incumbent,
            capacity_validity=solution.has_incumbent,
            objective_globally_optimal=solution.status_name == "OPTIMAL",
            acceptance_count_certified=certified,
            certification_method=certification_reason if certified else "NOT_CERTIFIED",
            best_bound=raw_bound,
            mip_gap=solution.final_mip_gap,
            optimality_scope="acceptance_integer" if certified else "not_certified",
            direct_ilp_metadata={
                "identifier": "direct_ilp_acceptance_cold",
                "display_name": "Direct ILP (Cold)",
                "objective_mode": "admission_only",
                "objective_sense": "maximize",
                "objective_description": "maximize total accepted flows",
                "physical_cost_in_objective": False,
                "cold_start": True,
                "mip_start_mode": "none",
                "external_mip_start_used": False,
                "initialization_cache_reused": False,
                "initialization_source": "none",
                "initialization_generation_runtime_seconds": 0.0,
                "initialization_application_runtime_seconds": runtime_meta.get("warm_start_application_runtime_seconds"),
                "initialization_scenario_fingerprint": _saginfe_initialization_fingerprint(instance),
                "initialization_solution_validated": None,
                "initialization_accepted_flow_count": None,
                "configured_threads": 1,
                "threads": 1,
                "solver_seed": 0,
                "configured_gurobi_seed": 0,
                "configured_mip_gap": 0.0,
                "configured_mip_gap_abs": 0.0,
                "model_build_runtime_seconds": model_build_runtime,
                "solver_runtime_seconds": solver_runtime,
                "optimization_runtime_seconds": solver_runtime,
                "solution_extraction_runtime_seconds": extraction_runtime,
                "extraction_runtime_seconds": extraction_runtime,
                "algorithm_core_runtime_seconds": total_end_to_end_runtime,
                "total_end_to_end_runtime_seconds": total_end_to_end_runtime,
                "hard_time_limit_enabled": configured_limit is not None,
                "disable_hard_time_limit": algorithm_config.disable_hard_time_limit,
                "configured_end_to_end_limit_seconds": configured_limit,
                "effective_solver_time_limit_seconds": direct_meta.get("effective_solver_time_limit_seconds"),
                "hard_wall_clock_limit_reached": hard_wall_reached,
                "solver_time_limit_reached": solution.status_name == "TIME_LIMIT",
                "runtime_accounting_valid": runtime_accounting_valid,
                "runtime_accounting_residual_seconds": accounting_residual,
                "solver_status": solution.status,
                "solver_status_name": solution.status_name,
                "sol_count": solution.sol_count,
                "accepted_flow_incumbent_count": incumbent,
                "raw_objective_value": solution.objective,
                "raw_objective_bound": raw_bound,
                "safe_integer_acceptance_lower_bound": safe_lower,
                "safe_integer_acceptance_upper_bound": safe_upper,
                "acceptance_optimality_certified": certified,
                "optimality_certification_reason": certification_reason,
                "mip_gap": solution.final_mip_gap,
                "node_count": solution.node_count,
                "termination_status": termination_status,
                "time_to_first_feasible_incumbent_seconds": direct_meta.get("time_to_first_feasible_incumbent_seconds"),
                "time_to_best_incumbent_seconds": direct_meta.get("time_to_best_incumbent_seconds"),
                "time_to_optimality_certification_seconds": (
                    total_end_to_end_runtime if certified else None
                ),
                "path_validity": solution.has_incumbent,
                "capacity_validity": solution.has_incumbent,
                "best_bound_scope": "global_acceptance_ilp",
                "mip_gap_scope": "global_acceptance_ilp",
            },
        )
    except Exception as exc:
        diagnostics = getattr(exc, "diagnostics", None)
        return _base_record(
            instance,
            context,
            "ERROR",
            time.perf_counter() - start,
            None,
            None,
            None,
            None,
            None,
            exc,
            direct_ilp_metadata={
                "identifier": "direct_ilp_acceptance_cold",
                "display_name": "Direct ILP (Cold)",
                "objective_mode": "admission_only",
                "physical_cost_in_objective": False,
                "cold_start": True,
                "mip_start_mode": "none",
                "external_mip_start_used": False,
                "initialization_cache_reused": False,
                "hard_time_limit_enabled": configured_limit is not None,
                "disable_hard_time_limit": algorithm_config.disable_hard_time_limit,
                "configured_end_to_end_limit_seconds": configured_limit,
                "runtime_accounting_valid": False,
                "validation_failure_diagnostics": diagnostics,
            },
        )


def run_dwcg_formal_adapter(instance: SaginInstance, algorithm_config: FormalAlgorithmConfig, context: Dict[str, Any]) -> FormalRunRecord:
    start = time.perf_counter()
    try:
        from main_solver import GBSolver
        from saginfe_solver import build_saginfe_initial_paths_from_results, solve_sp_with_pfe

        algorithm_seed_bundle = derive_formal_algorithm_seed_bundle(instance, context["key"], algorithm_config)
        algorithm_seed = algorithm_seed_bundle["c_long_seed"]
        custom_initial_paths = None
        initialization = None
        initialization_generation_runtime = 0.0
        initial_column_pool_size = len(instance.flows)
        if algorithm_config.use_saginfe_initialization:
            initialization = _get_or_run_saginfe_initialization(
                instance,
                context,
                verbose=False,
                allow_solution_cache=False,
            )
            saginfe_paths = initialization["paths"]
            saginfe_results = {
                flow_id: (path.path_stage_placement, path.path_segment_routing)
                for flow_id, path in saginfe_paths.items()
            }
            custom_initial_paths = build_saginfe_initial_paths_from_results(
                instance.network,
                list(instance.flows),
                saginfe_results,
                objective_spec_from_network(instance.network),
            )
            initialization_generation_runtime = initialization["generation_runtime_seconds"]
            initial_column_pool_size += len(custom_initial_paths)
        solver = GBSolver(instance.network, list(instance.flows), objective_spec_from_network(instance.network))
        pricing_strategy = algorithm_config.resolved_pricing_strategy()
        heuristic_func = solve_sp_with_pfe if pricing_strategy == "heuristic_then_exact" else None
        solver_sp_strategy = pricing_strategy
        hybrid_switch_iteration = algorithm_config.hybrid_switch_iteration or 5
        parallel_pricing_enabled = algorithm_config.pricing_worker_count > 1
        exact_pricing_call_cap = algorithm_config.max_exact_pricing_calls_by_scale.get(
            context["key"].scale,
            algorithm_config.max_exact_pricing_calls,
        )
        configured_hard_limit = dwcg_configured_e2e_limit(algorithm_config)
        overall_deadline = start + configured_hard_limit if configured_hard_limit is not None else None
        core_start = time.perf_counter()
        pre_solver_setup_runtime = max(0.0, core_start - start - initialization_generation_runtime)
        model, w_vars = solver.solve(
            use_parallel=parallel_pricing_enabled,
            custom_initial_paths=custom_initial_paths,
            sp_strategy=solver_sp_strategy,
            hybrid_switch_iters=hybrid_switch_iteration,
            heuristic_sp_func=heuristic_func,
            global_time_limit=configured_hard_limit,
            solver_threads=algorithm_config.threads,
            parallel_workers=algorithm_config.pricing_worker_count,
            exact_pricing_mip_gap=algorithm_config.mip_gap or 0.0,
            final_rmp_reserve_ratio=(
                algorithm_config.final_rmp_reserve_ratio
                if algorithm_config.final_rmp_reserve_ratio is not None
                else 0.20
            ),
            final_rmp_reserve_seconds=algorithm_config.final_rmp_reserve_seconds,
            final_rmp_time_cap_seconds=algorithm_config.final_rmp_time_cap_seconds,
            overall_start_time=start,
            overall_deadline=overall_deadline,
            external_initialization_runtime=initialization_generation_runtime,
            hard_time_limit_scope="end_to_end_algorithm_runtime_seconds",
            deterministic_mode=algorithm_config.deterministic_mode,
            algorithm_seed=algorithm_seed,
            pricing_worker_count=algorithm_config.pricing_worker_count,
            max_iterations=algorithm_config.max_cg_iterations,
            max_exact_pricing_calls=exact_pricing_call_cap,
            selective_pfe_enabled=selective_pfe_enabled_for_algorithm(algorithm_config.algorithm),
            selective_pfe_func=solve_sp_with_pfe if selective_pfe_enabled_for_algorithm(algorithm_config.algorithm) else None,
            initial_saginfe_accepted_flow_count=(
                initialization["accepted_flow_count"] if initialization is not None else None
            ),
            final_rmp_mip_start_from_initial_columns=is_pro_algorithm(algorithm_config.algorithm),
        )
        solver_call_runtime = time.perf_counter() - core_start
        total_end_to_end_runtime = time.perf_counter() - start
        algorithm_core_runtime = solver_call_runtime
        selected_paths = _selected_pool_paths(solver, w_vars)
        path_valid = capacity_valid = None
        if selected_paths is not None:
            path_valid = True
            for flow in instance.flows:
                path = selected_paths.get(flow.flow_id)
                if path is not None and not path.path_is_empty:
                    validate_path(instance.network, flow, path)
            validate_solution_capacity(instance.network, instance.flows, {
                flow_id: path for flow_id, path in selected_paths.items() if not path.path_is_empty
            })
            capacity_valid = True
        accepted = sum(1 for path in selected_paths.values() if not path.path_is_empty) if selected_paths is not None else None
        physical = sum(path.path_cost for path in selected_paths.values() if not path.path_is_empty) if selected_paths is not None else None
        objective = (
            objective_spec_from_network(instance.network).acceptance_reward * accepted - physical
            if accepted is not None and physical is not None else None
        )
        dominance_returned_initial = False
        dominance_solver_accepted = accepted
        dominance_solver_physical = physical
        dominance_returned_source = None
        if is_pro_algorithm(algorithm_config.algorithm) and initialization is not None:
            initial_accepted = initialization["accepted_flow_count"]
            initial_physical = sum(path.path_cost for path in initialization["paths"].values())
            if (
                accepted is None
                or accepted < initial_accepted
                or (accepted == initial_accepted and physical is not None and physical > initial_physical)
            ):
                accepted = initial_accepted
                physical = initial_physical
                objective = objective_spec_from_network(instance.network).acceptance_reward * accepted - physical
                path_valid = True
                capacity_valid = True
                dominance_returned_initial = True
                dominance_returned_source = "SAGINFE_INITIAL_INCUMBENT"
        stats = solver.stats
        runtime_accounting_valid = abs(
            total_end_to_end_runtime - (
                initialization_generation_runtime
                + pre_solver_setup_runtime
                + algorithm_core_runtime
            )
        ) <= 1e-3
        pricing_strategy_invariant_valid = pricing_strategy == solver_sp_strategy
        if not pricing_strategy_invariant_valid:
            raise RuntimeError(
                "Configured DWCG pricing_strategy does not match actual solver sp_strategy: "
                f"{pricing_strategy!r} != {solver_sp_strategy!r}."
            )
        phase_history = stats.get("pricing_phase_history") or []
        early_stop_phases = [
            phase for phase in _primary_pricing_phases(phase_history)
            if phase.get("stop_early")
        ]
        strict_exact_phases = [
            phase for phase in _primary_pricing_phases(phase_history)
            if not phase.get("stop_early")
        ]
        exact_iteration_certified = any(
            getattr(record, "certification_possible", False)
            for record in (stats.get("pricing_iteration_stats") or [])
        )
        pricing_strategy_invariant_valid = pricing_strategy_invariant_valid and _pricing_strategy_execution_invariant(
            pricing_strategy,
            phase_history,
            bool(stats.get("pricing_convergence_certified")),
            exact_iteration_certified,
            hybrid_switch_iteration,
        )
        if not pricing_strategy_invariant_valid:
            raise RuntimeError(
                f"DWCG pricing_strategy execution invariant failed for {pricing_strategy!r}."
            )
        restricted_optimal = bool(stats.get("final_rmp_is_optimal"))
        strict_exact_positive_column_count = int(stats.get("strict_exact_positive_column_count") or 0)
        early_stop_positive_column_count = int(stats.get("early_stop_positive_column_count") or 0)
        total_positive_column_count = strict_exact_positive_column_count + early_stop_positive_column_count
        exact_pricing_call_count = int(stats.get("exact_sp_calls") or 0)
        early_stop_pricing_call_count = int(stats.get("early_stop_pricing_call_count") or 0)
        strict_exact_pricing_call_count = exact_pricing_call_count - early_stop_pricing_call_count
        if strict_exact_pricing_call_count < 0:
            raise RuntimeError("DWCG pricing call counters are inconsistent.")
        exact_pricing_runtime = stats.get("exact_pricing_runtime")
        early_stop_pricing_runtime = stats.get("early_stop_pricing_runtime")
        strict_exact_pricing_runtime = (
            None
            if exact_pricing_runtime is None or early_stop_pricing_runtime is None
            else max(0.0, exact_pricing_runtime - early_stop_pricing_runtime)
        )
        pro_metadata = {}
        if is_pro_algorithm(algorithm_config.algorithm):
            selective_positive_count = int(stats.get("selective_pfe_positive_column_count") or 0)
            selective_unique_count = int(stats.get("selective_pfe_unique_candidate_count") or 0)
            selective_duplicate_count = int(stats.get("selective_pfe_duplicate_count") or 0)
            selective_invalid_count = int(stats.get("selective_pfe_invalid_candidate_count") or 0)
            selective_runtime = float(stats.get("selective_pfe_runtime_seconds") or 0.0)
            primary_unique_count = max(0, int(stats.get("columns_added_total") or 0) - selective_unique_count)
            primary_duplicate_count = max(0, int(stats.get("duplicate_rejection_count") or 0) - selective_duplicate_count)
            primary_invalid_count = max(0, int(stats.get("invalid_column_count") or 0) - selective_invalid_count)
            primary_runtime = float(stats.get("pricing_runtime") or 0.0)
            pro_metadata = {
                **pro_metadata_defaults(algorithm_config.algorithm, len(instance.flows)),
                "initialization_runtime_seconds": initialization_generation_runtime,
                "initial_saginfe_accepted_flow_count": initialization["accepted_flow_count"] if initialization is not None else None,
                "initial_saginfe_acceptance_rate": (
                    initialization["accepted_flow_count"] / len(instance.flows)
                    if initialization is not None and instance.flows else None
                ),
                "initial_column_count": initial_column_pool_size,
                "initial_incumbent_fingerprint": initialization["scenario_fingerprint"] if initialization is not None else None,
                "initial_incumbent_objective": None,
                "initial_incumbent_path_valid": initialization["solution_validated"] if initialization is not None else None,
                "initial_incumbent_capacity_valid": initialization["solution_validated"] if initialization is not None else None,
                "pure_primary_pricing_runtime_seconds": primary_runtime,
                "primary_pricing_call_count": exact_pricing_call_count,
                "primary_pricing_positive_column_count": total_positive_column_count,
                "primary_pricing_unique_column_count": primary_unique_count,
                "primary_pricing_duplicate_count": primary_duplicate_count,
                "primary_pricing_invalid_candidate_count": primary_invalid_count,
                "primary_pricing_non_improving_candidate_count": None,
                "selective_pfe_runtime_seconds": selective_runtime,
                "selective_pfe_call_count": stats.get("selective_pfe_call_count"),
                "selective_pfe_round_count": stats.get("selective_pfe_round_count"),
                "selective_pfe_selected_flow_count": stats.get("selective_pfe_selected_flow_count"),
                "selective_pfe_candidate_count": stats.get("selective_pfe_candidate_count"),
                "selective_pfe_unique_candidate_count": selective_unique_count,
                "selective_pfe_positive_column_count": selective_positive_count,
                "selective_pfe_duplicate_count": selective_duplicate_count,
                "selective_pfe_invalid_candidate_count": selective_invalid_count,
                "selective_pfe_non_improving_candidate_count": stats.get("selective_pfe_non_improving_candidate_count"),
                "selective_pfe_seconds_per_positive_column": (
                    None
                    if not selective_positive_count
                    else selective_runtime / selective_positive_count
                ),
                "combined_pricing_positive_column_count": total_positive_column_count + selective_positive_count,
                "combined_pricing_unique_column_count": primary_unique_count + selective_unique_count,
                "combined_pricing_runtime_seconds": primary_runtime + selective_runtime,
                "selective_pfe_global_cap_reached": stats.get("selective_pfe_global_cap_reached"),
                "selective_pfe_per_round_cap_reached": stats.get("selective_pfe_per_round_cap_reached"),
                "selective_pfe_skipped_full_acceptance": stats.get("selective_pfe_skipped_full_acceptance"),
                "selective_pfe_skipped_no_eligible_flow": stats.get("selective_pfe_skipped_no_eligible_flow"),
                "selective_pfe_skipped_reserve_protection": stats.get("selective_pfe_skipped_reserve_protection"),
                "selective_pfe_skipped_deadline_protection": stats.get("selective_pfe_skipped_deadline_protection"),
                "final_rmp_mip_start_used": stats.get("final_rmp_mip_start_used"),
                "final_rmp_mip_start_valid": stats.get("final_rmp_mip_start_valid"),
                "final_rmp_mip_start_skip_reason": stats.get("final_rmp_mip_start_skip_reason"),
                "final_rmp_mip_start_accepted_flow_count": stats.get("final_rmp_mip_start_accepted_flow_count"),
                "final_rmp_mip_start_objective": stats.get("final_rmp_mip_start_objective"),
                "incumbent_safeguard_used": True,
                "initial_incumbent_accepted_flow_count": initialization["accepted_flow_count"] if initialization is not None else None,
                "initial_incumbent_normalized_physical_cost": (
                    sum(path.path_cost for path in initialization["paths"].values())
                    if initialization is not None else None
                ),
                "final_solver_incumbent_accepted_flow_count": dominance_solver_accepted,
                "final_solver_incumbent_normalized_physical_cost": dominance_solver_physical,
                "returned_incumbent_accepted_flow_count": accepted,
                "returned_incumbent_normalized_physical_cost": physical,
                "final_acceptance_gain_over_saginfe": (
                    accepted - initialization["accepted_flow_count"]
                    if accepted is not None and initialization is not None else None
                ),
                "returned_initial_incumbent_due_to_dominance": dominance_returned_initial,
                "returned_incumbent_source": dominance_returned_source or stats.get("final_solution_source"),
                "setup_runtime_seconds": pre_solver_setup_runtime,
                "lp_rmp_runtime_seconds": stats.get("rmp_lp_runtime"),
                "final_restricted_rmp_runtime_seconds": stats.get("final_rmp_runtime"),
                "extraction_and_finalization_runtime_seconds": max(
                    0.0,
                    total_end_to_end_runtime
                    - initialization_generation_runtime
                    - pre_solver_setup_runtime
                    - algorithm_core_runtime,
                ),
                "runtime_accounting_residual_seconds": total_end_to_end_runtime - (
                    initialization_generation_runtime + pre_solver_setup_runtime + algorithm_core_runtime
                ),
                "final_rmp_reserve_seconds": stats.get("final_rmp_reserved_seconds"),
                "final_rmp_effective_time_limit_seconds": stats.get("actual_final_rmp_time_limit_seconds"),
                "selective_pfe_may_run": selective_pfe_enabled_for_algorithm(algorithm_config.algorithm),
            }
        return _base_record(
            instance, context, stats.get("cg_status") or stats.get("termination_reason") or "COMPLETED",
            total_end_to_end_runtime, accepted, physical, objective, path_valid, capacity_valid,
            objective_globally_optimal=False,
            acceptance_count_certified=False,
            certification_method="NOT_CERTIFIED",
            best_bound=stats.get("final_rmp_best_bound"),
            mip_gap=stats.get("final_rmp_gap"),
            optimality_scope="restricted_pool_integer" if restricted_optimal else "not_certified",
            dwcg_metadata={
                "base_algorithm": algorithm_config.algorithm,
                "variant": algorithm_config.variant_id or algorithm_config.algorithm,
                "variant_id": algorithm_config.variant_id,
                "display_name": algorithm_config.display_name,
                "pricing_strategy": pricing_strategy,
                "configured_pricing_strategy": pricing_strategy,
                "heuristic_pricing_enabled": pricing_strategy == "heuristic_then_exact",
                "solver_pricing_strategy": solver_sp_strategy,
                "effective_solver_pricing_strategy": solver_sp_strategy,
                "hybrid_switch_iteration": hybrid_switch_iteration if pricing_strategy == "hybrid" else None,
                "pricing_strategy_invariant_valid": pricing_strategy_invariant_valid,
                "exact_pricing_completeness_required_for_certification": True,
                "deterministic_mode": algorithm_config.deterministic_mode,
                "algorithm_seed": algorithm_seed,
                "algorithm_seed_digest": algorithm_seed_bundle["seed_digest"],
                "python_random_seed": stats.get("python_random_seed"),
                "numpy_seed": stats.get("numpy_seed"),
                "gurobi_seed": stats.get("gurobi_seed"),
                "pricing_worker_count": algorithm_config.pricing_worker_count,
                "parallel_pricing_enabled": parallel_pricing_enabled,
                "solver_threads_per_worker": algorithm_config.threads,
                "pricing_worker_threads": 1,
                "pricing_task_ordering_policy": stats.get("pricing_task_ordering_policy"),
                "column_insertion_ordering_policy": stats.get("column_insertion_ordering_policy"),
                "logical_pricing_task_count": stats.get("logical_pricing_task_count"),
                "exact_pricing_budget_used": stats.get("exact_pricing_budget_used"),
                "exact_pricing_budget_policy": stats.get("exact_pricing_budget_policy"),
                "initialization_source": "saginfe" if algorithm_config.use_saginfe_initialization else "pure",
                "saginfe_initialization_used": bool(algorithm_config.use_saginfe_initialization),
                "initialization_generation_runtime_seconds": initialization_generation_runtime,
                "initialization_application_runtime_seconds": None,
                "pre_solver_setup_runtime_seconds": pre_solver_setup_runtime,
                "dwcg_solver_call_runtime_seconds": solver_call_runtime,
                "algorithm_core_runtime_seconds": algorithm_core_runtime,
                "total_end_to_end_runtime_seconds": total_end_to_end_runtime,
                "runtime_accounting_valid": runtime_accounting_valid,
                "initialization_cache_reused": initialization["cache_reused"] if initialization is not None else False,
                "initialization_scenario_fingerprint": initialization["scenario_fingerprint"] if initialization is not None else _saginfe_initialization_fingerprint(instance),
                "initialization_solution_validated": initialization["solution_validated"] if initialization is not None else None,
                "initialization_accepted_flow_count": initialization["accepted_flow_count"] if initialization is not None else None,
                "initial_column_pool_size": initial_column_pool_size,
                "cg_iteration_count": stats.get("iterations"),
                "completed_cg_iteration_count": stats.get("completed_cg_iteration_count"),
                "interrupted_iteration": stats.get("interrupted_iteration"),
                "max_cg_iterations_reached": stats.get("max_cg_iterations_reached"),
                "configured_max_cg_iterations": algorithm_config.max_cg_iterations,
                "configured_max_exact_pricing_calls": exact_pricing_call_cap,
                "configured_max_exact_pricing_calls_by_scale": algorithm_config.max_exact_pricing_calls_by_scale,
                "max_exact_pricing_calls_reached": stats.get("max_exact_pricing_calls_reached"),
                "deterministic_work_budget_exhausted": stats.get("deterministic_work_budget_exhausted"),
                "configured_hard_time_limit_seconds": stats.get("configured_hard_time_limit_seconds"),
                "configured_final_rmp_reserve_ratio": algorithm_config.final_rmp_reserve_ratio,
                "configured_final_rmp_reserve_seconds": stats.get("configured_final_rmp_reserve_seconds"),
                "configured_final_rmp_time_cap_seconds": stats.get("configured_final_rmp_time_cap_seconds"),
                "actual_final_rmp_time_limit_seconds": stats.get("actual_final_rmp_time_limit_seconds"),
                "hard_time_limit_scope": stats.get("hard_time_limit_scope"),
                "elapsed_seconds_in_hard_limit_scope": stats.get("elapsed_seconds_in_hard_limit_scope"),
                "hard_wall_clock_limit_reached": stats.get("hard_wall_clock_limit_reached"),
                "run_hard_wall_clock_limit_reached": stats.get("hard_wall_clock_limit_reached"),
                "hard_limit_overrun_seconds": stats.get("hard_limit_overrun_seconds"),
                "termination_trigger": stats.get("termination_trigger"),
                "cg_termination_trigger": stats.get("termination_trigger"),
                "internal_phase_limit_reached": stats.get("internal_phase_limit_reached"),
                "internal_phase_name": stats.get("internal_phase_name"),
                "internal_phase_limit_seconds": stats.get("internal_phase_limit_seconds"),
                "cg_phase_budget_seconds": stats.get("cg_phase_budget"),
                "final_rmp_reserved_seconds": stats.get("final_rmp_reserved_seconds"),
                "final_rmp_remaining_seconds_at_start": stats.get("final_rmp_remaining_seconds_at_start"),
                "last_completed_checkpoint_column_pool_size": stats.get("last_completed_checkpoint_column_pool_size"),
                "rmp_solve_count": stats.get("rmp_lp_calls"),
                "generated_column_count": stats.get("columns_added_total"),
                "inserted_column_count": stats.get("columns_added_total"),
                "positive_column_count": total_positive_column_count,
                "strict_exact_positive_column_count": strict_exact_positive_column_count,
                "early_stop_positive_column_count": early_stop_positive_column_count,
                "total_positive_column_count": total_positive_column_count,
                "rejected_duplicate_column_count": stats.get("duplicate_rejection_count"),
                "duplicate_column_count": stats.get("duplicate_rejection_count"),
                "invalid_column_count": stats.get("invalid_column_count"),
                "final_column_pool_size": len(solver.pool),
                "final_ordered_column_pool_fingerprint": stats.get("final_ordered_column_pool_fingerprint"),
                "total_rmp_runtime": stats.get("rmp_lp_runtime", 0.0) + stats.get("final_rmp_runtime", 0.0),
                "rmp_lp_runtime_seconds": stats.get("rmp_lp_runtime"),
                "final_rmp_runtime_seconds": stats.get("final_rmp_runtime"),
                "final_rmp_first_incumbent_runtime_seconds": stats.get("final_rmp_first_incumbent_runtime_seconds"),
                "final_rmp_first_incumbent_objective": stats.get("final_rmp_first_incumbent_objective"),
                "final_rmp_best_incumbent_runtime_seconds": stats.get("final_rmp_best_incumbent_runtime_seconds"),
                "final_rmp_best_incumbent_objective": stats.get("final_rmp_best_incumbent_objective"),
                "final_rmp_final_incumbent_runtime_seconds": stats.get("final_rmp_final_incumbent_runtime_seconds"),
                "final_rmp_final_incumbent_objective": stats.get("final_rmp_final_incumbent_objective"),
                "final_rmp_incumbent_events": stats.get("final_rmp_incumbent_events"),
                "total_pricing_runtime": stats.get("pricing_runtime"),
                "heuristic_pricing_runtime": stats.get("heuristic_pricing_runtime"),
                "early_stop_pricing_runtime": early_stop_pricing_runtime,
                "strict_exact_pricing_runtime": strict_exact_pricing_runtime,
                "exact_pricing_runtime": exact_pricing_runtime,
                "exact_pricing_call_count": exact_pricing_call_count,
                "early_stop_pricing_call_count": early_stop_pricing_call_count,
                "strict_exact_pricing_call_count": strict_exact_pricing_call_count,
                "pricing_calls_proven_optimal": stats.get("exact_pricing_optimal_calls"),
                "incomplete_pricing_call_count": stats.get("exact_pricing_incomplete_calls"),
                "early_stop_phase_count": len(early_stop_phases),
                "strict_exact_phase_count": len(strict_exact_phases),
                "pricing_timeout_failure_count": stats.get("exact_pricing_time_limit_calls", 0) + stats.get("exact_pricing_incomplete_calls", 0),
                "convergence_certification": stats.get("pricing_convergence_certified"),
                "final_integer_rmp_status": stats.get("final_rmp_status_name"),
                "restricted_pool_integer_optimal": restricted_optimal,
                "final_rmp_mip_gap": stats.get("final_rmp_gap"),
                "final_rmp_best_bound": stats.get("final_rmp_best_bound"),
                "global_original_integer_optimal": False,
                "path_validity": path_valid,
                "capacity_validity": capacity_valid,
                "best_bound_scope": "restricted_column_pool",
                "mip_gap_scope": "restricted_column_pool",
                **pro_metadata,
                **_deadline_sensitivity_metadata(instance),
                **_same_flow_temporal_control_metadata(instance),
                **_paired_temporal_scalability_metadata(instance),
            },
        )
    except Exception as exc:
        return _base_record(instance, context, "ERROR", time.perf_counter() - start, None, None, None, None, None, exc)


def _selected_pool_paths(solver, w_vars) -> Optional[Dict[int, SfcPath]]:
    if w_vars is None:
        return None
    by_id = {path.path_id: path for path in solver.pool.get_all_paths()}
    selected = {}
    for path_id, var in w_vars.items():
        if getattr(var, "X", 0.0) > 0.5:
            path = by_id[path_id]
            selected[path.flow_id] = path
    return selected


def write_formal_summary_csv(path: Path, analysis: Mapping[str, Any]) -> None:
    fields = [
        "scale", "load_label", "algorithm", "variant_id", "variant", "display_name", "pricing_strategy",
        "pricing_worker_count", "parallel_pricing_enabled", "record_count", "median_accepted_flow_count",
        "median_acceptance_rate", "mean_acceptance_rate", "median_algorithm_core_runtime_seconds",
        "median_total_end_to_end_runtime_seconds", "median_runtime_seconds", "termination_statuses",
        "deterministic_work_budget_exhausted_count", "hard_wall_clock_limit_count",
        "internal_phase_limit_count", "max_cg_iterations_reached_count",
        "max_exact_pricing_calls_reached_count", "runtime_accounting_valid_count",
        "median_completed_cg_iterations", "median_exact_pricing_calls",
        "median_strict_exact_pricing_calls", "median_early_stop_pricing_calls",
        "median_total_positive_column_count", "median_strict_exact_positive_column_count",
        "median_early_stop_positive_column_count",
        "median_primary_pricing_runtime_seconds", "median_selective_pfe_runtime_seconds",
        "median_combined_pricing_runtime_seconds", "median_primary_pricing_unique_column_count",
        "median_selective_pfe_unique_candidate_count", "median_combined_pricing_unique_column_count",
        "median_final_column_pool_size", "final_ordered_column_pool_fingerprint_count",
        "path_valid_count", "capacity_valid_count", "error_count", "error_messages",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in analysis.get("summary_rows", []):
            writer.writerow({field: row.get(field, "") for field in fields})

