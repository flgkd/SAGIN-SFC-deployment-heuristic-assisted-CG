"""Calibration execution framework for V3 SAGIN instances.

This module builds calibration instances, verifies paired-load structure, and
records standardized run records. It does not freeze candidate parameters.
"""

from dataclasses import asdict, is_dataclass, replace
import csv
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path, PurePath
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from calibration_schema import (
    FRAMEWORK_VERSION,
    CalibrationManifest,
    CalibrationPlan,
    CalibrationRunKey,
    CalibrationRunRecord,
    SolverCalibrationConfig,
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
from instance_builder import SaginInstance, build_instance
from objective import ObjectiveSpec, compute_path_physical_cost, objective_spec_from_network
from data_model import Path as SfcPath
from sgfe_solver import sgfe
from validation import validate_path, validate_solution_capacity


ACCEPTANCE_CERTIFICATION_TOLERANCE = 1e-7


Adapter = Callable[[SaginInstance, SolverCalibrationConfig, Dict[str, Any]], CalibrationRunRecord]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, Enum):
        return to_jsonable(value.value)
    if hasattr(value, "tolist") and callable(value.tolist):
        return to_jsonable(value.tolist())
    if hasattr(value, "item") and callable(value.item) and value.__class__.__module__.startswith("numpy"):
        return to_jsonable(value.item())
    if isinstance(value, Mapping):
        converted = {}
        for key, val in sorted(value.items(), key=lambda item: str(item[0])):
            json_key = to_jsonable(key)
            if not isinstance(json_key, (str, int, float, bool)) and json_key is not None:
                json_key = str(json_key)
            converted[json_key] = to_jsonable(val)
        return converted
    if isinstance(value, (set, frozenset)):
        return [to_jsonable(item) for item in sorted(value, key=str)]
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return float(format(value, ".12g"))
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def scenario_id(plan: CalibrationPlan, seed: int, multiplier: float) -> str:
    scenario = plan.scenario
    source = {
        "scale": scenario.scale_name,
        "time_slots": scenario.time_slots,
        "seed": seed,
        "traffic_profile": scenario.traffic_profile,
        "sfc_profile": scenario.sfc_profile,
        "deadline_profile": scenario.deadline_profile,
        "demand_multiplier": multiplier,
        "dynamic_topology": scenario.dynamic_topology,
    }
    short = sha256_fingerprint(source)[:12]
    return f"{scenario.scale_name}_T{scenario.time_slots}_seed{seed}_load{multiplier:g}_{short}"


def topology_fingerprint(instance: SaginInstance) -> str:
    network = instance.network
    payload = {
        "topology_seed": instance.instance_metadata["topology_seed"],
        "nodes": list(network.nodes),
        "physical_links": instance.topology_metadata["physical_topology"]["base_physical_links"],
        "time_node_capacity": sorted((list(key), value) for key, value in network.time_node_capacity.items()),
        "active_snapshot_links": [
            {"time_slot": snap["time_slot"], "active_edges": snap["active_edges"]}
            for snap in instance.topology_metadata["snapshots"]
        ],
        "communication_capacity": sorted(
            (list(edge[0]), list(edge[1]), cap)
            for edge, cap in network.time_edge_capacity.items()
            if cap != math.inf and edge[0][1] == edge[1][1]
        ),
    }
    return sha256_fingerprint(payload)


def base_flow_fingerprint(instance: SaginInstance) -> str:
    rows = []
    for flow in sorted(instance.flows, key=lambda item: item.flow_id):
        meta = instance.flow_metadata["per_flow"][flow.flow_id]
        rows.append({
            "flow_id": flow.flow_id,
            "source": flow.flow_src_time_node[0],
            "arrival": flow.flow_src_time_node[1],
            "destination": flow.flow_dst_node,
            "deadline": flow.flow_dst_max_time,
            "stage_labels": [stage.vnf_type for stage in flow.processing_stages],
            "processing_coefficients": [stage.processing_coefficient for stage in flow.processing_stages],
            "base_demand": meta["base_demand"],
        })
    return sha256_fingerprint(rows)


def full_instance_fingerprint(instance: SaginInstance) -> str:
    rows = []
    for flow in sorted(instance.flows, key=lambda item: item.flow_id):
        rows.append({"flow_id": flow.flow_id, "actual_demand": flow.flow_demand})
    return sha256_fingerprint({
        "topology": topology_fingerprint(instance),
        "base_flow": base_flow_fingerprint(instance),
        "demand_multiplier": instance.instance_metadata["demand_multiplier"],
        "actual_demands": rows,
    })


def instance_metrics(instance: SaginInstance) -> Dict[str, Any]:
    network = instance.network
    finite_edges = [
        edge for edge, cap in network.time_edge_capacity.items()
        if cap != math.inf and edge[0][1] == edge[1][1]
    ]
    proxies = instance.instance_metadata["offered_load_proxies"]
    total_node_capacity = proxies["total_time_node_capacity"]
    total_comm_capacity = proxies["total_finite_communication_capacity"]
    return {
        "scale": instance.instance_metadata["scale"],
        "physical_node_count": len(network.nodes),
        "time_node_count": len(network.time_nodes),
        "physical_link_count": instance.instance_metadata["physical_link_count"],
        "finite_communication_edge_count": len(finite_edges),
        "flow_count": len(instance.flows),
        "flow_per_node_ratio": instance.instance_metadata["flow_per_node_ratio"],
        "total_node_capacity": total_node_capacity,
        "total_finite_communication_capacity": total_comm_capacity,
        "processing_offered_load_proxy": proxies["total_processing_demand_proxy"],
        "virtual_link_offered_load_proxy": proxies["total_virtual_link_demand_proxy"],
        "processing_offered_load_ratio_proxy": (
            proxies["total_processing_demand_proxy"] / total_node_capacity if total_node_capacity else None
        ),
        "communication_offered_load_ratio_proxy": (
            proxies["total_virtual_link_demand_proxy"] / total_comm_capacity if total_comm_capacity else None
        ),
        "deadline_distribution": instance.flow_metadata["aggregate"]["deadline"]["realized_slack_distribution"],
        "average_stage_count": instance.flow_metadata["aggregate"]["sfc"]["average_processing_stage_count"],
        "actual_traffic_category_ratios": instance.flow_metadata["aggregate"]["traffic"]["realized_category_ratios"],
        "topology_churn": instance.topology_metadata["aggregate_dynamics"]["snapshot_to_snapshot_link_churn_rate"],
        "mean_realized_link_survival_by_type": instance.topology_metadata["aggregate_dynamics"]["mean_realized_survival_by_type"],
    }


def build_calibration_instance(plan: CalibrationPlan, seed: int, multiplier: float) -> SaginInstance:
    scenario = plan.scenario
    topology = TopologyConfig(
        scale=get_candidate_scale(scenario.scale_name),
        time_slots=scenario.time_slots,
        seed=0,
        dynamic_topology=scenario.dynamic_topology,
    )
    flow = FlowGenerationConfig(
        seed=0,
        demand_multiplier=multiplier,
        traffic_mix_profile=get_candidate_traffic_mix_profile(scenario.traffic_profile),
        sfc_profile=get_candidate_sfc_profile(scenario.sfc_profile),
        deadline_profile=get_candidate_deadline_profile(scenario.deadline_profile),
        load_profile_name=f"multiplier_{multiplier:g}",
    )
    return build_instance(InstanceConfig(topology, flow, scenario_seed=seed))


def calibration_matrix(plan: CalibrationPlan, mode: str, algorithms: Optional[Iterable[str]] = None) -> List[Tuple[int, float, SolverCalibrationConfig]]:
    plan.validate()
    scenario = plan.scenario
    seeds = scenario.calibration_seeds
    multipliers = scenario.demand_multipliers
    if mode == "smoke":
        seeds = (scenario.calibration_seeds[0],)
        smoke_multipliers = scenario.output_settings.get("smoke_demand_multipliers", (0.8, 1.2))
        multipliers = tuple(mult for mult in scenario.demand_multipliers if mult in tuple(smoke_multipliers))
    elif mode != "full":
        raise ValueError("mode must be 'smoke' or 'full'.")
    selected_algorithms = set(algorithms) if algorithms else None
    enabled = [
        algorithm for algorithm in scenario.algorithms
        if algorithm.enabled and (selected_algorithms is None or algorithm.algorithm in selected_algorithms)
    ]
    order = {"saginfe": 0, "direct_ilp": 1}
    enabled = sorted(enabled, key=lambda algorithm: order.get(algorithm.algorithm, 99))
    if mode == "smoke":
        enabled = [
            replace(algorithm, time_limit=60.0) if algorithm.algorithm == "direct_ilp" else algorithm
            for algorithm in enabled
        ]
    return [(seed, multiplier, algorithm) for seed in seeds for multiplier in multipliers for algorithm in enabled]


def verify_paired_instances(instances: List[SaginInstance]) -> None:
    if not instances:
        return
    topo = topology_fingerprint(instances[0])
    base = base_flow_fingerprint(instances[0])
    for instance in instances[1:]:
        if topology_fingerprint(instance) != topo:
            raise ValueError("Paired calibration instances do not share the same topology fingerprint.")
        if base_flow_fingerprint(instance) != base:
            raise ValueError("Paired calibration instances do not share the same base-flow fingerprint.")


def build_instances_by_seed_and_multiplier(plan: CalibrationPlan, mode: str) -> Dict[Tuple[int, float], SaginInstance]:
    pairs = sorted({(seed, multiplier) for seed, multiplier, _ in calibration_matrix(plan, mode)})
    instances = {(seed, multiplier): build_calibration_instance(plan, seed, multiplier) for seed, multiplier in pairs}
    for seed in sorted({seed for seed, _ in pairs}):
        verify_paired_instances([instances[(pair_seed, multiplier)] for pair_seed, multiplier in pairs if pair_seed == seed])
    return instances


def run_direct_ilp_adapter(instance: SaginInstance, solver_config: SolverCalibrationConfig, context: Dict[str, Any]) -> CalibrationRunRecord:
    from ILP_solver import solve_ilp

    start = time.perf_counter()
    timestamp = utc_now()
    key = context["key"]
    try:
        warm_start_paths = context.get("warm_start_paths")
        solution = solve_ilp(
            instance.network,
            list(instance.flows),
            objective_spec=objective_spec_from_network(instance.network),
            time_limit=solver_config.time_limit,
            mip_gap=solver_config.mip_gap,
            verbose=solver_config.verbose,
            threads=solver_config.threads,
            warm_start_paths=warm_start_paths,
            stop_when_acceptance_certified=True,
        )
        runtime = time.perf_counter() - start
        accepted = solution.accepted_count if solution.has_incumbent else None
        acceptance = accepted / len(instance.flows) if accepted is not None and instance.flows else None
        certification = certify_acceptance_count(
            accepted_count=accepted,
            total_flow_count=len(instance.flows),
            objective_globally_optimal=solution.global_original_integer_optimal,
            objective_upper_bound=solution.best_bound,
        )
        certified = acceptance if certification["acceptance_count_certified"] else None
        warm_meta = solution.result.metadata.get("warm_start", {}) if solution.result is not None else {}
        early_meta = solution.result.metadata.get("acceptance_early_stop", {}) if solution.result is not None else {}
        runtime_meta = solution.result.metadata.get("runtime_breakdown", {}) if solution.result is not None else {}
        return _record(
            key, timestamp, runtime, solution.status_name or str(solution.status),
            success=solution.has_incumbent or solution.global_original_integer_optimal,
            feasible=solution.has_incumbent,
            accepted=accepted,
            total=len(instance.flows),
            acceptance=acceptance,
            certified=certified,
            incumbent=acceptance if solution.has_incumbent and not solution.global_original_integer_optimal else None,
            heuristic=None,
            objective=solution.scalarized_objective,
            physical_cost=solution.normalized_physical_cost,
            objective_globally_optimal=solution.global_original_integer_optimal,
            acceptance_count_certified=certification["acceptance_count_certified"],
            acceptance_certification_method=certification["acceptance_certification_method"],
            error_type=None,
            error_message=None,
            instance=instance,
            metadata={
                "solver_status_code": solution.status,
                "solver_status_name": solution.status_name,
                "optimality_scope": "global_original_integer" if solution.global_original_integer_optimal else "not_certified",
                "globally_optimal": solution.global_original_integer_optimal,
                "proven_optimal": solution.global_original_integer_optimal,
                "mip_gap": solution.final_mip_gap,
                "best_bound": solution.best_bound,
                "node_count": solution.node_count,
                "simplex_iterations": getattr(solution.model, "IterCount", None) if solution.model is not None else None,
                "root_relaxation_objective": None,
                "timeout": solution.hit_time_limit,
                "has_incumbent": solution.has_incumbent,
                "accepted_count_from_incumbent": accepted,
                "solver_invoked": early_meta.get("solver_invoked", True),
                "solver_skipped_reason": early_meta.get("solver_skipped_reason"),
                "optimization_runtime": solution.runtime,
                "adapter_total_runtime": runtime,
                "model_construction_runtime": runtime_meta.get("model_build_runtime_seconds"),
                "model_build_runtime_seconds": runtime_meta.get("model_build_runtime_seconds"),
                "optimization_runtime_seconds": runtime_meta.get("optimization_runtime_seconds", solution.runtime),
                "result_extraction_runtime_seconds": runtime_meta.get("result_extraction_runtime_seconds"),
                "total_adapter_runtime_seconds": runtime,
                "root_node_count": solution.node_count,
                "simplex_iterations": getattr(solution.model, "IterCount", None) if solution.model is not None else None,
                "thread_count": solver_config.threads,
                "configured_time_limit": solver_config.time_limit,
                "solver_seed": solver_config.solver_seed,
                "warm_start_supplied": warm_meta.get("supplied", False),
                "warm_start_complete": warm_meta.get("complete", False),
                "warm_start_accepted_count": warm_meta.get("accepted_count"),
                "warm_start_gurobi_status": warm_meta.get("gurobi_start_status"),
                "warm_start_gurobi_status_reason": warm_meta.get("gurobi_start_status_reason"),
            },
        )
    except Exception as exc:
        return _error_record(key, timestamp, time.perf_counter() - start, instance, exc)


def run_saginfe_adapter(instance: SaginInstance, solver_config: SolverCalibrationConfig, context: Dict[str, Any]) -> CalibrationRunRecord:
    start = time.perf_counter()
    timestamp = utc_now()
    key = context["key"]
    try:
        results = sgfe(instance.network, list(instance.flows), instance.network.nodes, instance.network.nodes, verbose=solver_config.verbose)
        selected = {}
        normalized_cost = 0.0
        objective_spec: ObjectiveSpec = objective_spec_from_network(instance.network)
        accepted = 0
        path_validation_ok = True
        for flow in instance.flows:
            eta, phi = results.get(flow.flow_id, (None, None))
            if eta is None or phi is None:
                continue
            path = SfcPath(flow.flow_id, -1, False, eta, phi, 0.0)
            path.finalize_usage(flow=flow)
            path.path_cost = compute_path_physical_cost(instance.network, flow, path, objective_spec)
            validate_path(instance.network, flow, path)
            selected[flow.flow_id] = path
            normalized_cost += path.path_cost
            accepted += 1
        try:
            validate_solution_capacity(instance.network, instance.flows, selected)
            capacity_ok = True
        except ValueError:
            capacity_ok = False
        if capacity_ok and "warm_start_cache" in context:
            context["warm_start_cache"][key.scenario_id] = dict(selected)
        acceptance = accepted / len(instance.flows) if instance.flows else None
        return _record(
            key, timestamp, time.perf_counter() - start, "COMPLETED",
            success=True,
            feasible=capacity_ok,
            accepted=accepted,
            total=len(instance.flows),
            acceptance=acceptance,
            certified=None,
            incumbent=None,
            heuristic=acceptance,
            objective=objective_spec.acceptance_reward * accepted - normalized_cost,
            physical_cost=normalized_cost,
            objective_globally_optimal=False,
            acceptance_count_certified=False,
            acceptance_certification_method="NOT_CERTIFIED",
            error_type=None,
            error_message=None,
            instance=instance,
            metadata={
                "heuristic_feasibility": capacity_ok,
                "path_validation_result": path_validation_ok,
                "capacity_validation_result": capacity_ok,
                "internal_statistics": None,
                "unavailable_statistics_reason": "SGFE exposes embeddings but no branch-and-bound statistics.",
            },
        )
    except Exception as exc:
        return _error_record(key, timestamp, time.perf_counter() - start, instance, exc)


def _record(
    key: CalibrationRunKey,
    timestamp: str,
    runtime: float,
    status: str,
    success: bool,
    feasible: bool,
    accepted: Optional[int],
    total: int,
    acceptance: Optional[float],
    certified: Optional[float],
    incumbent: Optional[float],
    heuristic: Optional[float],
    objective: Optional[float],
    physical_cost: Optional[float],
    objective_globally_optimal: bool,
    acceptance_count_certified: bool,
    acceptance_certification_method: str,
    error_type: Optional[str],
    error_message: Optional[str],
    instance: SaginInstance,
    metadata: Dict[str, Any],
) -> CalibrationRunRecord:
    return CalibrationRunRecord(
        key=key,
        start_timestamp=timestamp,
        runtime_seconds=runtime,
        termination_status=status,
        success=success,
        feasible_solution=feasible,
        accepted_flow_count=accepted,
        total_flow_count=total,
        acceptance_rate=acceptance,
        certified_acceptance_rate=certified,
        incumbent_acceptance_rate=incumbent,
        heuristic_acceptance_rate=heuristic,
        canonical_objective_value=objective,
        normalized_physical_cost=physical_cost,
        objective_globally_optimal=objective_globally_optimal,
        acceptance_count_certified=acceptance_count_certified,
        acceptance_certification_method=acceptance_certification_method,
        error_type=error_type,
        error_message=error_message,
        topology_fingerprint=topology_fingerprint(instance),
        base_flow_fingerprint=base_flow_fingerprint(instance),
        full_instance_fingerprint=full_instance_fingerprint(instance),
        instance_metrics=instance_metrics(instance),
        solver_metadata=metadata,
    )


def _error_record(key: CalibrationRunKey, timestamp: str, runtime: float, instance: SaginInstance, exc: Exception) -> CalibrationRunRecord:
    return _record(
        key, timestamp, runtime, "ERROR", False, False, None, len(instance.flows),
        None, None, None, None, None, None, False, False, "NOT_CERTIFIED",
        exc.__class__.__name__, str(exc), instance, {}
    )


def certify_acceptance_count(
    accepted_count: Optional[int],
    total_flow_count: int,
    objective_globally_optimal: bool,
    objective_upper_bound: Optional[float],
    tolerance: float = ACCEPTANCE_CERTIFICATION_TOLERANCE,
    acceptance_reward: float = 2.0,
) -> Dict[str, Any]:
    """Certify accepted-flow count without requiring physical-cost optimality.

    The canonical objective is z = 2A - C with 0 <= C <= 1. Any solution
    accepting at least A_inc + 1 flows has objective at least
    2 * (A_inc + 1) - 1. If the global objective upper bound is below that
    threshold, no better accepted count is possible.
    """
    if accepted_count is None:
        return {"acceptance_count_certified": False, "acceptance_certification_method": "NOT_CERTIFIED"}
    if objective_globally_optimal:
        return {"acceptance_count_certified": True, "acceptance_certification_method": "FULL_OBJECTIVE_OPTIMAL"}
    if accepted_count >= total_flow_count:
        return {"acceptance_count_certified": True, "acceptance_certification_method": "ALL_FLOWS_ACCEPTED"}
    if objective_upper_bound is not None:
        threshold = acceptance_reward * (accepted_count + 1) - 1.0
        if objective_upper_bound < threshold - tolerance:
            return {"acceptance_count_certified": True, "acceptance_certification_method": "OBJECTIVE_BOUND"}
    return {"acceptance_count_certified": False, "acceptance_certification_method": "NOT_CERTIFIED"}


def record_to_dict(record: CalibrationRunRecord) -> Dict[str, Any]:
    return to_jsonable(record)


def append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(payload) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(to_jsonable(payload), handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp_name, path)


def read_existing_run_records(path: Path) -> Dict[Tuple[str, int, float, str], Dict[str, Any]]:
    records = {}
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                key = record["key"]
                run_key = (key["scenario_id"], int(key["calibration_seed"]), float(key["demand_multiplier"]), key["algorithm"])
                records[run_key] = record
            except Exception as exc:
                raise ValueError(f"Malformed runs.jsonl line {line_number}: {exc}") from exc
    return records


def read_existing_instance_fingerprints(path: Path) -> set:
    fingerprints = set()
    if not path.exists():
        return fingerprints
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                fingerprints.add(record["full_instance_fingerprint"])
            except Exception as exc:
                raise ValueError(f"Malformed instances.jsonl line {line_number}: {exc}") from exc
    return fingerprints


def is_matching_existing_record(record: Mapping[str, Any], instance: SaginInstance) -> bool:
    return (
        record.get("topology_fingerprint") == topology_fingerprint(instance)
        and record.get("base_flow_fingerprint") == base_flow_fingerprint(instance)
        and record.get("full_instance_fingerprint") == full_instance_fingerprint(instance)
    )


def git_info() -> Tuple[str, bool]:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--short"], capture_output=True, text=True, check=False).stdout.strip())
    return commit, dirty


def ensure_output_dir(path: Path, overwrite: bool, mode: str) -> None:
    if path.exists() and any(path.iterdir()):
        if not overwrite:
            existing_manifest = path / "manifest.json"
            if existing_manifest.exists():
                try:
                    existing_mode = json.loads(existing_manifest.read_text(encoding="utf-8")).get("mode")
                except Exception:
                    existing_mode = None
                if existing_mode and existing_mode != mode:
                    raise ValueError("Cannot mix smoke and full records in one output directory.")
            raise ValueError("Output directory is nonempty; use --overwrite or --resume.")
        for child in path.iterdir():
            if child.is_file():
                child.unlink()
    path.mkdir(parents=True, exist_ok=True)


def run_calibration_plan(
    plan: CalibrationPlan,
    output_dir: Path,
    mode: str,
    algorithms: Optional[Iterable[str]] = None,
    resume: bool = False,
    allow_dirty: bool = False,
    overwrite: bool = False,
    adapters: Optional[Dict[str, Adapter]] = None,
    cli_arguments: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    plan.validate()
    commit, dirty = git_info()
    if dirty and not allow_dirty:
        raise RuntimeError("Git worktree is dirty; pass --allow-dirty for debugging runs.")
    if not resume:
        ensure_output_dir(output_dir, overwrite, mode)
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        existing_manifest = output_dir / "manifest.json"
        if existing_manifest.exists():
            existing_mode = json.loads(existing_manifest.read_text(encoding="utf-8")).get("mode")
            if existing_mode and existing_mode != mode:
                raise ValueError("Cannot mix smoke and full records in one output directory.")

    matrix = calibration_matrix(plan, mode, algorithms)
    manifest = CalibrationManifest(
        FRAMEWORK_VERSION, commit, dirty, sys.version, platform.platform(),
        utc_now(), None, cli_arguments or {}, mode, sha256_fingerprint(plan), allow_dirty
    )
    write_json_atomic(output_dir / "manifest.json", manifest)
    write_json_atomic(output_dir / "plan_snapshot.json", plan)

    adapter_map = adapters or {"direct_ilp": run_direct_ilp_adapter, "saginfe": run_saginfe_adapter}
    existing = read_existing_run_records(output_dir / "runs.jsonl") if resume else {}
    existing_instances = read_existing_instance_fingerprints(output_dir / "instances.jsonl") if resume else set()
    completed = []
    instance_cache: Dict[Tuple[int, float], SaginInstance] = {}
    warm_start_cache: Dict[str, Dict[int, SfcPath]] = {}
    for seed, multiplier, solver_config in matrix:
        instance_key = (seed, multiplier)
        if instance_key not in instance_cache:
            instance_cache[instance_key] = build_calibration_instance(plan, seed, multiplier)
        instance = instance_cache[instance_key]
        key = CalibrationRunKey(scenario_id(plan, seed, multiplier), seed, multiplier, solver_config.algorithm)
        existing_record = existing.get(key.as_tuple())
        if existing_record is not None:
            if is_matching_existing_record(existing_record, instance):
                continue
            raise ValueError(f"Existing run record for {key.as_tuple()} has mismatched fingerprints.")
        instance_payload = {
            "scenario_id": key.scenario_id,
            "calibration_seed": seed,
            "demand_multiplier": multiplier,
            "topology_fingerprint": topology_fingerprint(instance),
            "base_flow_fingerprint": base_flow_fingerprint(instance),
            "full_instance_fingerprint": full_instance_fingerprint(instance),
            "instance_metrics": instance_metrics(instance),
        }
        full_fp = instance_payload["full_instance_fingerprint"]
        if full_fp not in existing_instances:
            append_jsonl(output_dir / "instances.jsonl", instance_payload)
            existing_instances.add(full_fp)
        adapter = adapter_map[solver_config.algorithm]
        context = {"key": key, "warm_start_cache": warm_start_cache}
        if solver_config.algorithm == "direct_ilp":
            context["warm_start_paths"] = warm_start_cache.get(key.scenario_id)
        record = adapter(instance, solver_config, context)
        append_jsonl(output_dir / "runs.jsonl", record_to_dict(record))
        completed.append(record)

    from calibration_analysis import analyze_calibration_records, write_reports

    records = list(read_existing_run_records(output_dir / "runs.jsonl").values())
    transfer_validation_only = (
        plan.plan_type == "transfer_validation"
        or bool(plan.scenario.output_settings.get("transfer_validation_only", False))
    )
    analysis = analyze_calibration_records(
        records,
        plan.thresholds,
        smoke_mode=(mode == "smoke"),
        transfer_validation_only=transfer_validation_only,
    )
    write_summary_csv(output_dir / "summary.csv", analysis)
    write_reports(output_dir, analysis)
    finished = replace(manifest, finish_timestamp=utc_now())
    write_json_atomic(output_dir / "manifest.json", finished)
    return {"planned_runs": len(matrix), "completed_this_invocation": len(completed), "analysis": analysis}


def write_summary_csv(path: Path, analysis: Mapping[str, Any]) -> None:
    rows = analysis.get("summary_rows", [])
    fields = [
        "demand_multiplier", "algorithm", "planned_seeds", "completed_seeds",
        "certified_seeds", "success_count", "error_count", "timeout_count",
        "median_acceptance", "median_certified_acceptance", "full_acceptance_fraction",
        "mean_runtime", "median_runtime", "median_ilp_node_count",
        "root_solve_fraction", "regime_label", "classification_sufficient",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})

