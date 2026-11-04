# main_solver.py

import concurrent.futures
import inspect
import random
import time
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Tuple

from gurobipy import GRB

from data_model import Flow, Network, Path
from column_pool import ColumnPool
from restricted_master_problem import solve_rmp
from sub_problem import solve_sp_detailed, extract_path_from_sp
from objective import (
    ObjectiveSpec,
    compute_path_physical_cost,
    compute_reduced_cost_from_usage,
    objective_spec_from_network,
)
from result_schema import PricingIterationStats, evaluate_exact_pricing_certification
from validation import validate_path
from deterministic_seeds import seed_bundle, stable_seed_digest
from dwcg_saginfe_pro import FlowRepairFeatures, SelectivePFECounters, select_critical_flows
from parallel_pricing_schedule import plan_pricing_tasks


REDUCED_COST_TOLERANCE = 1e-6


def stable_int_seed(*parts) -> int:
    """Return a backend-safe deterministic integer seed for legacy callers."""
    return seed_bundle(*parts)["c_long_seed"]


def stable_gurobi_seed(*parts) -> int:
    return seed_bundle(*parts)["gurobi_seed"]


def pricing_result_sort_key(item):
    """Stable ordering for main-process column insertion."""
    flow_obj, phase_name, obj_val, y_res, z_res, metadata = item
    signature = metadata.get("path_signature") if isinstance(metadata, dict) else None
    if signature is None:
        signature = (repr(y_res), repr(z_res))
    fallback = metadata.get("fallback_key") if isinstance(metadata, dict) else None
    if fallback is None:
        fallback = repr((obj_val, metadata.get("status_name") if isinstance(metadata, dict) else None))
    return (flow_obj.flow_id, phase_name, repr(signature), fallback)


def sort_pricing_results_deterministically(items):
    return sorted(items, key=pricing_result_sort_key)


def canonical_column_signature(path: Path):
    if path.signature is None:
        path.build_signature()
    return repr(path.signature)


def column_pool_fingerprint(paths) -> str:
    payload = "\n".join(sorted(canonical_column_signature(path) for path in paths))
    return stable_seed_digest(payload)


def apply_hard_limit_metadata(
    stats: Dict[str, Any],
    elapsed_seconds: float,
    configured_hard_time_limit_seconds: Optional[float],
) -> None:
    """Record global hard-limit metadata for the declared solver-core scope."""
    stats["elapsed_seconds_in_hard_limit_scope"] = elapsed_seconds
    if configured_hard_time_limit_seconds is None or configured_hard_time_limit_seconds <= 0:
        stats["hard_limit_overrun_seconds"] = 0.0
        stats["hard_wall_clock_limit_reached"] = False
        return
    overrun = max(0.0, elapsed_seconds - configured_hard_time_limit_seconds)
    stats["hard_limit_overrun_seconds"] = overrun
    stats["hard_wall_clock_limit_reached"] = elapsed_seconds >= configured_hard_time_limit_seconds
    if stats["hard_wall_clock_limit_reached"]:
        stats["stopped_by_global_time_limit"] = True
        if stats.get("termination_trigger") is None:
            stats["termination_trigger"] = "GLOBAL_HARD_TIME_LIMIT"


def iteration_metadata_invariant_holds(stats: Dict[str, Any]) -> bool:
    iterations = stats.get("iterations")
    completed = stats.get("completed_cg_iteration_count")
    if iterations is None or completed is None:
        return False
    if stats.get("interrupted_iteration"):
        return iterations == completed + 1
    return iterations == completed


def deterministic_work_budget_flags(
    iteration: int,
    exact_pricing_calls: int,
    max_iterations: Optional[int],
    max_exact_pricing_calls: Optional[int],
) -> Dict[str, Any]:
    max_cg_reached = max_iterations is not None and iteration >= max_iterations
    max_exact_reached = (
        max_exact_pricing_calls is not None
        and exact_pricing_calls >= max_exact_pricing_calls
    )
    if max_exact_reached:
        trigger = "MAX_EXACT_PRICING_CALLS"
    elif max_cg_reached:
        trigger = "MAX_CG_ITERATIONS"
    else:
        trigger = None
    return {
        "max_cg_iterations_reached": max_cg_reached,
        "max_exact_pricing_calls_reached": max_exact_reached,
        "deterministic_work_budget_exhausted": max_cg_reached or max_exact_reached,
        "termination_trigger": trigger,
    }


def latest_exact_iteration_certification(stats: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    records = stats.get("pricing_iteration_stats") or []
    if not records:
        return False, "no exact-pricing iteration statistics are available"
    latest = records[-1]
    return latest.certification_possible, latest.certification_reason


def exact_iteration_had_rejected_positive_column(stats: Dict[str, Any]) -> bool:
    return latest_rejected_positive_reason(stats) is not None


def latest_rejected_positive_reason(stats: Dict[str, Any]) -> Optional[str]:
    records = stats.get("pricing_iteration_stats") or []
    if not records:
        return None
    reason = records[-1].certification_reason or ""
    for code in (
        "DUPLICATE_POSITIVE_COLUMN_INCONSISTENCY",
        "POSITIVE_COLUMN_EXTRACTION_FAILED",
        "POSITIVE_COLUMN_VALIDATION_FAILED",
        "POSITIVE_COLUMN_COST_MISMATCH",
    ):
        if code in reason:
            return code
    if "a positive column was rejected" in reason:
        return "POSITIVE_COLUMN_EXTRACTION_FAILED"
    return None


class _FixedValueVar:
    def __init__(self, value: float):
        self.X = value


class GBSolver:
    def __init__(
        self,
        network: Network,
        flows: List[Flow],
        objective_spec: Optional[ObjectiveSpec] = None,
    ):
        self.network = network
        self.flows = flows
        self.flow_dict = {f.flow_id: f for f in flows}
        self.pool = ColumnPool()
        self.objective_spec = objective_spec or objective_spec_from_network(network)
        self.stats = self._new_stats()

    @staticmethod
    def _new_stats() -> Dict[str, Any]:
        return {
            "iterations": 0,
            "initial_empty_paths": 0,
            "initial_custom_paths": 0,
            "columns_added_total": 0,
            "columns_added_by_phase": {},
            "pricing_phase_history": [],
            "strict_exact_positive_column_count": 0,
            "early_stop_positive_column_count": 0,
            "total_positive_column_count": 0,
            "duplicate_rejections": 0,
            "heuristic_miss_count": 0,
            "heuristic_disabled": False,
            "heuristic_disable_after_misses": None,
            "run_exact_after_heuristic_success": False,
            "converged": False,
            "stopped_by_max_iterations": False,
            "initialization_runtime": 0.0,
            "rmp_lp_runtime": 0.0,
            "heuristic_pricing_runtime": 0.0,
            "early_stop_pricing_runtime": 0.0,
            "exact_pricing_runtime": 0.0,
            "pricing_runtime": 0.0,
            "final_rmp_runtime": 0.0,
            "total_runtime": 0.0,
            "other_runtime": 0.0,
            "rmp_lp_calls": 0,
            "pricing_sp_calls": 0,
            "heuristic_sp_calls": 0,
            "early_stop_pricing_call_count": 0,
            "exact_sp_calls": 0,
            "exact_pricing_optimal_calls": 0,
            "exact_pricing_time_limit_calls": 0,
            "exact_pricing_incomplete_calls": 0,
            "cg_status": "NOT_STARTED",
            "termination_reason": "",
            "stopped_by_global_time_limit": False,
            "pricing_convergence_certified": False,
            "final_rmp_required": False,
            "final_rmp_status": None,
            "final_rmp_status_name": None,
            "final_rmp_has_incumbent": False,
            "final_rmp_is_optimal": False,
            "final_rmp_hit_time_limit": False,
            "final_rmp_gap": None,
            "final_rmp_best_bound": None,
            "final_rmp_binary_variables": 0,
            "final_rmp_incumbent_events": [],
            "final_rmp_first_incumbent_runtime_seconds": None,
            "final_rmp_first_incumbent_objective": None,
            "final_rmp_best_incumbent_runtime_seconds": None,
            "final_rmp_best_incumbent_objective": None,
            "final_rmp_final_incumbent_runtime_seconds": None,
            "final_rmp_final_incumbent_objective": None,
            "final_solution_source": "NONE",
            "deadline_overrun_runtime": 0.0,
            "cg_phase_budget": None,
            "cg_phase_runtime": 0.0,
            "final_rmp_reserved_seconds": None,
            "configured_final_rmp_reserve_seconds": None,
            "final_rmp_remaining_seconds_at_start": None,
            "configured_final_rmp_time_cap_seconds": None,
            "actual_final_rmp_time_limit_seconds": None,
            "pricing_phase_incomplete": False,
            "pricing_iteration_stats": [],
            "deterministic_parallel_ordering_applied": False,
            "invalid_column_count": 0,
            "cost_mismatch_count": 0,
            "duplicate_rejection_count": 0,
            "extraction_failure_count": 0,
            "pricing_failures": [],
            "heuristic_overrun_count": 0,
            "no_progress_termination_count": 0,
            "last_rejected_positive_column": None,
            "deterministic_mode": False,
            "algorithm_seed": None,
            "python_random_seed": None,
            "numpy_seed": None,
            "gurobi_seed": None,
            "pricing_worker_count": None,
            "pricing_task_ordering_policy": None,
            "column_insertion_ordering_policy": None,
            "logical_pricing_task_count": 0,
            "exact_pricing_budget_used": 0,
            "exact_pricing_budget_policy": None,
            "completed_cg_iteration_count": 0,
            "interrupted_iteration": False,
            "deterministic_work_budget_exhausted": False,
            "max_cg_iterations_reached": False,
            "max_exact_pricing_calls_reached": False,
            "hard_wall_clock_limit_reached": False,
            "configured_hard_time_limit_seconds": None,
            "hard_time_limit_scope": None,
            "elapsed_seconds_in_hard_limit_scope": 0.0,
            "hard_limit_overrun_seconds": 0.0,
            "termination_trigger": None,
            "internal_phase_limit_reached": False,
            "internal_phase_name": None,
            "internal_phase_limit_seconds": None,
            "last_completed_checkpoint_column_pool_size": 0,
            "final_ordered_column_pool_fingerprint": None,
            "selective_pfe_runtime_seconds": 0.0,
            "selective_pfe_call_count": 0,
            "selective_pfe_round_count": 0,
            "selective_pfe_selected_flow_count": 0,
            "selective_pfe_candidate_count": 0,
            "selective_pfe_unique_candidate_count": 0,
            "selective_pfe_positive_column_count": 0,
            "selective_pfe_duplicate_count": 0,
            "selective_pfe_invalid_candidate_count": 0,
            "selective_pfe_non_improving_candidate_count": 0,
            "selective_pfe_global_cap_reached": False,
            "selective_pfe_per_round_cap_reached": False,
            "selective_pfe_skipped_full_acceptance": False,
            "selective_pfe_skipped_no_eligible_flow": False,
            "selective_pfe_skipped_reserve_protection": False,
            "selective_pfe_skipped_deadline_protection": False,
            "initial_custom_path_signatures_by_flow": {},
            "final_rmp_mip_start_used": False,
            "final_rmp_mip_start_valid": False,
            "final_rmp_mip_start_skip_reason": None,
            "final_rmp_mip_start_accepted_flow_count": None,
            "final_rmp_mip_start_objective": None,
        }

    def _add_custom_initial_paths(self, custom_data: List[Dict]) -> int:
        added_count = 0
        for data in custom_data:
            flow_id = data["flow_id"]
            if flow_id not in self.flow_dict:
                raise ValueError(f"Initial custom path references unknown flow_id {flow_id}.")
            if "stage_placement" not in data or "segment_routing" not in data:
                raise ValueError(
                    f"Initial custom path for flow_id {flow_id} must use canonical "
                    "stage_placement and segment_routing fields."
                )
            path = Path(
                flow_id=flow_id,
                path_id=self.pool.generate_path_id(),
                path_is_empty=False,
                path_stage_placement=data["stage_placement"],
                path_segment_routing=data["segment_routing"],
                path_cost=data.get("physical_cost", 0.0),
            )
            path.finalize_usage(flow=self.flow_dict[flow_id])
            supplied_cost = path.path_cost
            recomputed_cost = compute_path_physical_cost(
                self.network,
                self.flow_dict[flow_id],
                path,
                self.objective_spec,
            )
            if "physical_cost" in data and abs(supplied_cost - recomputed_cost) > 1e-7:
                self.stats["cost_mismatch_count"] += 1
                self.stats["invalid_column_count"] += 1
                continue
            path.path_cost = recomputed_cost
            try:
                validate_path(self.network, self.flow_dict[flow_id], path)
            except ValueError:
                self.stats["invalid_column_count"] += 1
                raise
            if self.pool.add_path(path):
                added_count += 1
                self.stats["initial_custom_path_signatures_by_flow"][flow_id] = canonical_column_signature(path)
            else:
                self.stats["duplicate_rejection_count"] += 1
        return added_count

    def _build_initial_column_solution(self) -> Dict[int, _FixedValueVar]:
        selected_path_ids = set()
        for flow in self.flows:
            paths = self.pool.get_paths_by_flow(flow.flow_id)
            non_empty = [path for path in paths if not path.path_is_empty]
            if non_empty:
                selected_path_ids.add(non_empty[0].path_id)
            elif paths:
                selected_path_ids.add(paths[0].path_id)
        return {
            path.path_id: _FixedValueVar(1.0 if path.path_id in selected_path_ids else 0.0)
            for path in self.pool.get_all_paths()
        }

    @staticmethod
    def _status_name(status_code: Optional[int]) -> Optional[str]:
        if status_code is None:
            return None
        for name in (
            "LOADED", "OPTIMAL", "INFEASIBLE", "INF_OR_UNBD", "UNBOUNDED",
            "CUTOFF", "ITERATION_LIMIT", "NODE_LIMIT", "TIME_LIMIT",
            "SOLUTION_LIMIT", "INTERRUPTED", "NUMERIC", "SUBOPTIMAL",
            "INPROGRESS", "USER_OBJ_LIMIT", "WORK_LIMIT", "MEM_LIMIT",
        ):
            if getattr(GRB, name, None) == status_code:
                return name
        return str(status_code)

    @staticmethod
    def _supported_kwargs(func: Callable, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        signature = inspect.signature(func)
        params = signature.parameters
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
            return kwargs
        return {key: value for key, value in kwargs.items() if key in params}

    def _call_sp(
        self,
        sp_func: Callable,
        flow: Flow,
        duals: Dict,
        stop_early: bool,
        kwargs: Dict[str, Any],
    ) -> Tuple[Any, Any, Any, Dict[str, Any]]:
        call_kwargs = self._supported_kwargs(sp_func, kwargs)
        try:
            result = sp_func(
                flow,
                self.network,
                duals,
                self.objective_spec,
                stop_early,
                **call_kwargs,
            )
        except Exception as exc:
            return None, None, None, {
                "status_code": None,
                "status_name": "EXCEPTION",
                "runtime": None,
                "sol_count": None,
                "objective": None,
                "has_solution": False,
                "exact_pricing_optimal": False,
                "hit_time_limit": False,
                "stop_early": stop_early,
                "positive_reduced_cost_found": False,
                "exact_pricing_call_certified": False,
                "failed": True,
                "flow_id": flow.flow_id,
                "exception_class": exc.__class__.__name__,
                "exception_message": str(exc),
            }
        if not isinstance(result, tuple) or len(result) not in (3, 4):
            return None, None, None, {
                "status_name": "INVALID_RESULT",
                "failed": True,
                "flow_id": flow.flow_id,
                "exception_class": "ValueError",
                "exception_message": "Pricing solver must return a 3-tuple or 4-tuple.",
                "exact_pricing_optimal": False,
                "hit_time_limit": False,
                "positive_reduced_cost_found": False,
            }
        if len(result) == 3:
            obj, y_res, z_res = result
            metadata = {
                "status_code": None,
                "status_name": None,
                "runtime": None,
                "sol_count": None,
                "objective": obj,
                "has_solution": obj is not None,
                "exact_pricing_optimal": False,
                "hit_time_limit": False,
                "stop_early": stop_early,
                "positive_reduced_cost_found": obj is not None and obj > 1e-6,
                "exact_pricing_call_certified": False,
            }
            return obj, y_res, z_res, metadata
        obj, y_res, z_res, metadata = result
        return obj, y_res, z_res, metadata or {}

    @staticmethod
    def _parallel_call_sp(
        sp_func: Callable,
        flow: Flow,
        network: Network,
        duals: Dict,
        objective_spec: ObjectiveSpec,
        stop_early: bool,
        kwargs: Dict[str, Any],
    ) -> Tuple[Any, Any, Any, Dict[str, Any]]:
        absolute_deadline = kwargs.pop("_absolute_deadline", None)
        use_absolute_deadline_time_limit = kwargs.pop("_use_absolute_deadline_time_limit", False)
        if absolute_deadline is not None:
            remaining = absolute_deadline - time.perf_counter()
            if remaining <= 0:
                return None, None, None, {
                    "status_name": "DEADLINE_EXPIRED_BEFORE_START",
                    "failed": False,
                    "flow_id": flow.flow_id,
                    "exact_pricing_optimal": False,
                    "hit_time_limit": True,
                    "positive_reduced_cost_found": False,
                }
            if use_absolute_deadline_time_limit:
                kwargs["time_limit"] = remaining
        call_kwargs = GBSolver._supported_kwargs(sp_func, kwargs)
        try:
            result = sp_func(
                flow,
                network,
                duals,
                objective_spec,
                stop_early,
                **call_kwargs,
            )
        except Exception as exc:
            return None, None, None, {
                "status_name": "EXCEPTION",
                "failed": True,
                "flow_id": flow.flow_id,
                "exception_class": exc.__class__.__name__,
                "exception_message": str(exc),
                "exact_pricing_optimal": False,
                "hit_time_limit": False,
                "positive_reduced_cost_found": False,
            }
        if not isinstance(result, tuple) or len(result) not in (3, 4):
            return None, None, None, {
                "status_name": "INVALID_RESULT",
                "failed": True,
                "flow_id": flow.flow_id,
                "exception_class": "ValueError",
                "exception_message": "Pricing solver must return a 3-tuple or 4-tuple.",
                "exact_pricing_optimal": False,
                "hit_time_limit": False,
                "positive_reduced_cost_found": False,
            }
        if len(result) == 3:
            obj, y_res, z_res = result
            return obj, y_res, z_res, {
                "status_code": None,
                "status_name": None,
                "runtime": None,
                "sol_count": None,
                "objective": obj,
                "has_solution": obj is not None,
                "exact_pricing_optimal": False,
                "hit_time_limit": False,
                "stop_early": stop_early,
                "positive_reduced_cost_found": obj is not None and obj > 1e-6,
                "exact_pricing_call_certified": False,
            }
        obj, y_res, z_res, metadata = result
        return obj, y_res, z_res, metadata or {}

    def solve(
        self,
        use_parallel: bool = True,
        use_artificial_start: bool = True,
        custom_initial_paths: List[Dict] = None,
        sp_strategy: str = "hybrid",
        hybrid_switch_iters: int = 5,
        custom_sp_func: Callable = None,
        selective_pfe_func: Callable = None,
        selective_pfe_enabled: bool = False,
        initial_saginfe_accepted_flow_count: Optional[int] = None,
        final_rmp_mip_start_from_initial_columns: bool = False,
        max_iterations: Optional[int] = None,
        heuristic_sp_func: Callable = None,
        exact_sp_func: Callable = solve_sp_detailed,
        heuristic_disable_after_misses: Optional[int] = None,
        run_exact_after_heuristic_success: bool = False,
        global_time_limit: Optional[float] = None,
        final_rmp_reserve_ratio: float = 0.20,
        final_rmp_reserve_seconds: Optional[float] = None,
        final_rmp_time_cap_seconds: Optional[float] = None,
        final_rmp_mip_gap: float = 1e-4,
        solver_threads: int = 1,
        parallel_workers: Optional[int] = None,
        exact_pricing_mip_gap: float = 0.0,
        exact_pricing_mip_gap_abs: float = 1e-7,
        overall_start_time: Optional[float] = None,
        overall_deadline: Optional[float] = None,
        cg_phase_deadline: Optional[float] = None,
        external_initialization_runtime: float = 0.0,
        hard_time_limit_scope: str = "algorithm_core_runtime_seconds",
        deterministic_mode: bool = False,
        algorithm_seed: Optional[int] = None,
        pricing_worker_count: Optional[int] = None,
        max_exact_pricing_calls: Optional[int] = None,
    ):
        self.stats = self._new_stats()
        algorithm_seed_values = seed_bundle("gbsolver", algorithm_seed) if algorithm_seed is not None else None
        if deterministic_mode:
            self.flows = sorted(self.flows, key=lambda flow: flow.flow_id)
            self.flow_dict = {f.flow_id: f for f in self.flows}
            if algorithm_seed_values is not None:
                random.seed(algorithm_seed_values["python_random_seed"])
        self.stats["deterministic_mode"] = bool(deterministic_mode)
        self.stats["algorithm_seed"] = algorithm_seed
        self.stats["algorithm_seed_digest"] = algorithm_seed_values["seed_digest"] if algorithm_seed_values is not None else None
        self.stats["python_random_seed"] = algorithm_seed_values["python_random_seed"] if algorithm_seed_values is not None else None
        self.stats["numpy_seed"] = algorithm_seed_values["numpy_seed"] if algorithm_seed_values is not None else None
        self.stats["gurobi_seed"] = algorithm_seed_values["gurobi_seed"] if algorithm_seed_values is not None else None
        self.stats["pricing_worker_count"] = pricing_worker_count if pricing_worker_count is not None else parallel_workers
        self.stats["pricing_task_ordering_policy"] = "flow_id_ascending" if deterministic_mode else "solver_default"
        self.stats["column_insertion_ordering_policy"] = "flow_id_reduced_cost_signature" if deterministic_mode else "solver_default"
        self.stats["exact_pricing_budget_policy"] = "pre_submit_logical_task_budget"
        self.stats["heuristic_disable_after_misses"] = heuristic_disable_after_misses
        self.stats["run_exact_after_heuristic_success"] = run_exact_after_heuristic_success
        total_start = overall_start_time if overall_start_time is not None else time.perf_counter()
        effective_global_time_limit = global_time_limit
        self.stats["configured_hard_time_limit_seconds"] = effective_global_time_limit
        self.stats["hard_time_limit_scope"] = hard_time_limit_scope
        self.stats["configured_final_rmp_reserve_seconds"] = final_rmp_reserve_seconds
        self.stats["configured_final_rmp_time_cap_seconds"] = final_rmp_time_cap_seconds
        if overall_deadline is None and global_time_limit is not None and global_time_limit > 0:
            overall_deadline = total_start + global_time_limit
        elif overall_deadline is not None and effective_global_time_limit is None:
            effective_global_time_limit = max(0.0, overall_deadline - total_start)
        self.stats["configured_hard_time_limit_seconds"] = effective_global_time_limit

        if overall_deadline is not None:
            if cg_phase_deadline is None:
                if final_rmp_reserve_seconds is not None:
                    final_reserved = final_rmp_reserve_seconds
                else:
                    final_reserved = (
                        effective_global_time_limit * final_rmp_reserve_ratio
                        if effective_global_time_limit is not None
                        else 0.0
                    )
                cg_phase_deadline = overall_deadline - final_reserved
            else:
                final_reserved = max(0.0, overall_deadline - cg_phase_deadline)
            self.stats["cg_phase_budget"] = max(0.0, cg_phase_deadline - total_start)
            self.stats["final_rmp_reserved_seconds"] = final_reserved

        def mark_termination(trigger: str) -> None:
            if self.stats.get("termination_trigger") is None:
                self.stats["termination_trigger"] = trigger

        def update_deterministic_work_budget_flags() -> None:
            flags = deterministic_work_budget_flags(
                iteration,
                exact_pricing_call_budget_used,
                max_iterations,
                max_exact_pricing_calls,
            )
            self.stats["max_cg_iterations_reached"] = flags["max_cg_iterations_reached"]
            self.stats["max_exact_pricing_calls_reached"] = flags["max_exact_pricing_calls_reached"]
            self.stats["deterministic_work_budget_exhausted"] = flags["deterministic_work_budget_exhausted"]

        def deterministic_work_budget_trigger() -> str:
            return (
                deterministic_work_budget_flags(
                    iteration,
                    exact_pricing_call_budget_used,
                    max_iterations,
                    max_exact_pricing_calls,
                )["termination_trigger"]
                or "DETERMINISTIC_WORK_BUDGET"
            )

        def remaining_time(deadline: Optional[float]) -> Optional[float]:
            if deadline is None:
                return None
            return max(0.0, deadline - time.perf_counter())

        def deadline_reached(deadline: Optional[float]) -> bool:
            return deadline is not None and time.perf_counter() >= deadline

        def mark_cg_deadline(reason: str) -> None:
            self.stats["internal_phase_limit_reached"] = True
            self.stats["internal_phase_name"] = "column_generation"
            self.stats["internal_phase_limit_seconds"] = self.stats.get("cg_phase_budget")
            self.stats["pricing_convergence_certified"] = False
            self.stats["converged"] = False
            self.stats["cg_status"] = "TIME_LIMIT_NOT_CONVERGED"
            self.stats["termination_reason"] = reason
            mark_termination("INTERNAL_PHASE_LIMIT")

        def mark_pricing_incomplete(reason: str) -> None:
            self.stats["pricing_phase_incomplete"] = True
            self.stats["interrupted_iteration"] = True
            mark_cg_deadline(reason)

        def restore_checkpoint() -> None:
            self.pool.paths = list(checkpoint_paths)
            self.pool._signatures = set(checkpoint_signatures)
            self.pool._path_id_counter = checkpoint_path_id_counter
            self.stats["last_completed_checkpoint_column_pool_size"] = len(self.pool)

        init_start = time.perf_counter()
        if use_artificial_start:
            before = len(self.pool)
            self.pool.initialize_empty_paths(self.flows)
            self.stats["initial_empty_paths"] = len(self.pool) - before

        if custom_initial_paths:
            if deterministic_mode:
                custom_initial_paths = sorted(
                    custom_initial_paths,
                    key=lambda data: (
                        data.get("flow_id"),
                        repr(data.get("stage_placement")),
                        repr(data.get("segment_routing")),
                    ),
                )
            self.stats["initial_custom_paths"] = self._add_custom_initial_paths(custom_initial_paths)
        self.stats["duplicate_rejections"] = self.pool.duplicate_rejections
        self.stats["initialization_runtime"] = external_initialization_runtime + (time.perf_counter() - init_start)

        if deadline_reached(cg_phase_deadline):
            mark_cg_deadline("CG phase deadline reached after initialization.")
            fixed_w_vars = self._build_initial_column_solution()
            self.stats["final_solution_source"] = "INITIAL_COLUMNS"
            self.stats["final_rmp_required"] = False
            self.stats["final_rmp_status"] = "NOT_LAUNCHED"
            self.stats["final_rmp_status_name"] = "NOT_LAUNCHED"
            self.stats["final_rmp_has_incumbent"] = None
            self.stats["final_rmp_gap"] = None
            self.stats["final_rmp_best_bound"] = None
            self.stats["final_rmp_runtime"] = 0.0
            self.stats["final_rmp_binary_variables"] = len(fixed_w_vars)
            self.stats["final_rmp_remaining_seconds_at_start"] = remaining_time(overall_deadline)
            self._finish_runtime_stats(total_start, effective_global_time_limit)
            return None, fixed_w_vars

        sp_solver = custom_sp_func if custom_sp_func else exact_sp_func
        if sp_strategy == "heuristic_then_exact":
            if heuristic_sp_func is None:
                heuristic_sp_func = custom_sp_func
            if heuristic_sp_func is None:
                raise ValueError("sp_strategy='heuristic_then_exact' requires heuristic_sp_func or custom_sp_func.")

        effective_parallel = bool(use_parallel)
        if deterministic_mode:
            parallel_workers = pricing_worker_count or 1
        if parallel_workers == 1:
            effective_parallel = False

        iteration = 0
        heuristic_miss_count = 0
        heuristic_disabled = False
        rmp_model = None
        w_vars = None
        exact_pricing_call_budget_used = 0
        selective_pfe_call_budget_used = 0
        last_phase_added_flow_ids = set()
        checkpoint_paths = list(self.pool.paths)
        checkpoint_signatures = set(self.pool._signatures)
        checkpoint_path_id_counter = self.pool._path_id_counter
        def mark_iteration_complete() -> None:
            nonlocal checkpoint_paths, checkpoint_signatures, checkpoint_path_id_counter
            if deterministic_mode and not self.stats.get("pricing_phase_incomplete"):
                checkpoint_paths = list(self.pool.paths)
                checkpoint_signatures = set(self.pool._signatures)
                checkpoint_path_id_counter = self.pool._path_id_counter
                self.stats["completed_cg_iteration_count"] = iteration
                self.stats["last_completed_checkpoint_column_pool_size"] = len(self.pool)

        def mark_rejected_positive(exact_iteration_stats, reason: str, flow_obj: Flow, obj_val, recomputed_rc=None, signature=None):
            self.stats["last_rejected_positive_column"] = {
                "flow_id": flow_obj.flow_id,
                "reason": reason,
                "sp_objective": obj_val,
                "recomputed_reduced_cost": recomputed_rc,
                "signature": repr(signature),
                "already_in_active_rmp": signature in self.pool._signatures if signature is not None else None,
            }
            if exact_iteration_stats is not None:
                exact_iteration_stats["positive_column_rejected"] = True
                exact_iteration_stats["rejection_reasons"].add(reason)

        def process_sp_result(obj_val, y_res, z_res, flow_obj, dual_values, exact_iteration_stats=None):
            if obj_val is not None and obj_val > REDUCED_COST_TOLERANCE:
                recomputed_rc = None
                candidate_signature = None
                try:
                    new_path = extract_path_from_sp(
                        flow_obj,
                        self.network,
                        y_res,
                        z_res,
                        self.pool.generate_path_id(),
                        self.objective_spec,
                    )
                    new_path.finalize_usage(flow=flow_obj)
                    if new_path.signature is None:
                        new_path.build_signature()
                    candidate_signature = new_path.signature
                    recomputed_rc = compute_reduced_cost_from_usage(
                        flow_obj,
                        new_path.path_cost,
                        new_path.path_node_usage,
                        new_path.path_edge_usage,
                        dual_values,
                        self.objective_spec,
                    )
                    if recomputed_rc <= REDUCED_COST_TOLERANCE:
                        return False
                    supplied_cost = new_path.path_cost
                    recomputed_cost = compute_path_physical_cost(
                        self.network, flow_obj, new_path, self.objective_spec
                    )
                    if abs(supplied_cost - recomputed_cost) > 1e-7:
                        self.stats["cost_mismatch_count"] += 1
                        mark_rejected_positive(
                            exact_iteration_stats,
                            "POSITIVE_COLUMN_COST_MISMATCH",
                            flow_obj,
                            obj_val,
                            recomputed_rc,
                            candidate_signature,
                        )
                        return False
                    try:
                        validate_path(self.network, flow_obj, new_path)
                    except Exception:
                        self.stats["invalid_column_count"] += 1
                        mark_rejected_positive(
                            exact_iteration_stats,
                            "POSITIVE_COLUMN_VALIDATION_FAILED",
                            flow_obj,
                            obj_val,
                            recomputed_rc,
                            candidate_signature,
                        )
                        return False
                    added = self.pool.add_path(new_path)
                    if not added:
                        self.stats["duplicate_rejection_count"] += 1
                        mark_rejected_positive(
                            exact_iteration_stats,
                            "DUPLICATE_POSITIVE_COLUMN_INCONSISTENCY",
                            flow_obj,
                            obj_val,
                            recomputed_rc,
                            candidate_signature,
                        )
                    return added
                except Exception as exc:
                    self.stats["extraction_failure_count"] += 1
                    self.stats["pricing_failures"].append({
                        "flow_id": flow_obj.flow_id,
                        "exception_class": exc.__class__.__name__,
                        "exception_message": str(exc),
                    })
                    mark_rejected_positive(
                        exact_iteration_stats,
                        "POSITIVE_COLUMN_EXTRACTION_FAILED",
                        flow_obj,
                        obj_val,
                        recomputed_rc,
                        candidate_signature,
                    )
                    return False
            return False

        def record_phase(phase_name: str, added_count: int, stop_early: bool, duration: float) -> None:
            self.stats["columns_added_total"] += added_count
            self.stats["duplicate_rejections"] = self.pool.duplicate_rejections
            by_phase = self.stats["columns_added_by_phase"]
            by_phase[phase_name] = by_phase.get(phase_name, 0) + added_count
            self.stats["pricing_phase_history"].append({
                "iteration": iteration,
                "phase": phase_name,
                "stop_early": stop_early,
                "columns_added": added_count,
                "pricing_phase_incomplete": bool(self.stats.get("pricing_phase_incomplete")),
                "duplicate_rejections": self.pool.duplicate_rejections,
                "total_columns": len(self.pool),
                "runtime": duration,
            })
            if phase_name != "SGFE heuristic" and not self.stats.get("pricing_phase_incomplete"):
                if stop_early:
                    self.stats["early_stop_positive_column_count"] += added_count
                else:
                    self.stats["strict_exact_positive_column_count"] += added_count
                self.stats["total_positive_column_count"] = (
                    self.stats["strict_exact_positive_column_count"]
                    + self.stats["early_stop_positive_column_count"]
                )

        def run_pricing_phase(sp_phase_solver, dual_values, stop_early: bool, phase_use_parallel: bool, phase_name: str) -> int:
            nonlocal exact_pricing_call_budget_used, last_phase_added_flow_ids
            phase_start = time.perf_counter()
            new_paths = 0
            phase_added_flow_ids = set()
            last_phase_added_flow_ids = set()
            is_exact_phase = phase_name != "SGFE heuristic"
            exact_iteration_stats = None
            if is_exact_phase and not stop_early:
                exact_iteration_stats = {
                    "expected_exact_calls": len(self.flows),
                    "launched_exact_calls": 0,
                    "completed_exact_calls": 0,
                    "optimal_exact_calls": 0,
                    "positive_reduced_cost_calls": 0,
                    "time_limit_calls": 0,
                    "failed_calls": 0,
                    "columns_added": 0,
                    "positive_column_rejected": False,
                    "rejection_reasons": set(),
                    "pool_signatures_before": set(self.pool._signatures),
                    "pool_signatures_after": None,
                }
            if deadline_reached(cg_phase_deadline):
                mark_pricing_incomplete(f"CG phase deadline reached before pricing phase {phase_name}.")
                return 0

            remaining_exact_budget = None
            if is_exact_phase and max_exact_pricing_calls is not None:
                remaining_exact_budget = max(0, max_exact_pricing_calls - exact_pricing_call_budget_used)
            planned_tasks = plan_pricing_tasks(
                self.flows,
                deterministic_mode,
                remaining_exact_budget if is_exact_phase else None,
            )
            if is_exact_phase and not planned_tasks:
                update_deterministic_work_budget_flags()
                self.stats["max_exact_pricing_calls_reached"] = True
                self.stats["deterministic_work_budget_exhausted"] = True
                self.stats["cg_status"] = "DETERMINISTIC_WORK_BUDGET_EXHAUSTED"
                trigger = deterministic_work_budget_trigger()
                self.stats["termination_reason"] = f"Reached deterministic work budget: {trigger}."
                mark_termination(trigger)
                return 0
            if phase_use_parallel:
                phase_remaining = remaining_time(cg_phase_deadline)
                if phase_remaining is not None and phase_remaining <= 0:
                    mark_pricing_incomplete(f"CG phase deadline reached before parallel pricing phase {phase_name}.")
                    return 0
                sp_kwargs = {"threads": 1}
                if is_exact_phase:
                    sp_kwargs.update({
                        "time_limit": phase_remaining,
                        "exact_mip_gap": exact_pricing_mip_gap,
                        "exact_mip_gap_abs": exact_pricing_mip_gap_abs,
                    })
                with concurrent.futures.ProcessPoolExecutor(max_workers=parallel_workers) as executor:
                    futures = {}
                    completed_items = []
                    next_flow_index = 0
                    submitted_count = 0

                    def submit_available_flows() -> None:
                        nonlocal next_flow_index, submitted_count, exact_pricing_call_budget_used
                        while len(futures) < parallel_workers and next_flow_index < len(planned_tasks):
                            if deadline_reached(cg_phase_deadline):
                                return
                            task = planned_tasks[next_flow_index]
                            flow = task.flow
                            next_flow_index += 1
                            flow_kwargs = dict(sp_kwargs)
                            if cg_phase_deadline is not None:
                                flow_kwargs["_absolute_deadline"] = cg_phase_deadline
                                flow_kwargs["_use_absolute_deadline_time_limit"] = True
                            if is_exact_phase and deterministic_mode and algorithm_seed is not None:
                                flow_kwargs["gurobi_seed"] = stable_gurobi_seed(
                                    algorithm_seed, iteration, phase_name, flow.flow_id
                                )
                            if is_exact_phase:
                                exact_pricing_call_budget_used += 1
                                self.stats["exact_pricing_budget_used"] = exact_pricing_call_budget_used
                            self.stats["logical_pricing_task_count"] += 1
                            futures[
                                executor.submit(
                                    GBSolver._parallel_call_sp,
                                    sp_phase_solver,
                                    flow,
                                    self.network,
                                    dual_values,
                                    self.objective_spec,
                                    stop_early,
                                    flow_kwargs,
                                )
                            ] = task
                            submitted_count += 1

                    submit_available_flows()
                    while futures:
                        phase_remaining = remaining_time(cg_phase_deadline)
                        if phase_remaining is not None and phase_remaining <= 0:
                            for future in futures:
                                future.cancel()
                            mark_pricing_incomplete(
                                f"CG phase deadline reached during parallel pricing phase {phase_name}."
                            )
                            break
                        done, _ = concurrent.futures.wait(
                            futures,
                            timeout=phase_remaining,
                            return_when=concurrent.futures.FIRST_COMPLETED,
                        )
                        if not done:
                            for future in futures:
                                future.cancel()
                            mark_pricing_incomplete(
                                f"CG phase deadline reached during parallel pricing phase {phase_name}."
                            )
                            break
                        for future in done:
                            task = futures.pop(future)
                            flow_obj = task.flow
                            try:
                                obj_val, y_res, z_res, metadata = future.result()
                            except Exception as exc:
                                obj_val, y_res, z_res = None, None, None
                                metadata = {
                                    "status_name": "EXCEPTION",
                                    "failed": True,
                                    "flow_id": flow_obj.flow_id,
                                    "exception_class": exc.__class__.__name__,
                                    "exception_message": str(exc),
                                    "exact_pricing_optimal": False,
                                    "hit_time_limit": False,
                                    "positive_reduced_cost_found": False,
                                }
                            completed_items.append((task.ordinal, flow_obj, phase_name, obj_val, y_res, z_res, metadata))
                        submit_available_flows()
                    if futures:
                        for future in futures:
                            future.cancel()
                    if exact_iteration_stats is not None:
                        exact_iteration_stats["launched_exact_calls"] = submitted_count
                    if next_flow_index < len(planned_tasks) and deadline_reached(cg_phase_deadline):
                        mark_pricing_incomplete(
                            f"CG phase deadline reached before all flows were submitted in {phase_name}."
                        )
                completed_items = [
                    item[1:]
                    for item in sorted(
                        completed_items,
                        key=lambda item: (item[0], item[1].flow_id),
                    )
                ]
                self.stats["deterministic_parallel_ordering_applied"] = True
                for flow_obj, _, obj_val, y_res, z_res, metadata in completed_items:
                    update_sp_stats(metadata, is_exact_phase, stop_early)
                    update_exact_iteration_stats(exact_iteration_stats, metadata)
                    if process_sp_result(obj_val, y_res, z_res, flow_obj, dual_values, exact_iteration_stats):
                        new_paths += 1
                        phase_added_flow_ids.add(flow_obj.flow_id)
                        if exact_iteration_stats is not None:
                            exact_iteration_stats["columns_added"] += 1
            else:
                for task in planned_tasks:
                    flow = task.flow
                    phase_remaining = remaining_time(cg_phase_deadline)
                    if phase_remaining is not None and phase_remaining <= 0:
                        mark_pricing_incomplete(f"CG phase deadline reached before launching another SP in {phase_name}.")
                        break
                    sp_kwargs = {"threads": solver_threads}
                    if is_exact_phase:
                        sp_kwargs.update({
                            "time_limit": phase_remaining,
                            "exact_mip_gap": exact_pricing_mip_gap,
                            "exact_mip_gap_abs": exact_pricing_mip_gap_abs,
                        })
                        if deterministic_mode and algorithm_seed is not None:
                            sp_kwargs["gurobi_seed"] = stable_gurobi_seed(algorithm_seed, iteration, phase_name, flow.flow_id)
                    else:
                        sp_kwargs.update({
                            "time_limit": phase_remaining,
                            "deadline": cg_phase_deadline,
                        })
                    if exact_iteration_stats is not None:
                        exact_iteration_stats["launched_exact_calls"] += 1
                    if is_exact_phase:
                        exact_pricing_call_budget_used += 1
                        self.stats["exact_pricing_budget_used"] = exact_pricing_call_budget_used
                    self.stats["logical_pricing_task_count"] += 1
                    call_started = time.perf_counter()
                    obj_val, y_res, z_res, metadata = self._call_sp(
                        sp_phase_solver, flow, dual_values, stop_early, sp_kwargs
                    )
                    call_elapsed = time.perf_counter() - call_started
                    if not is_exact_phase and phase_remaining is not None and call_elapsed > phase_remaining + 1e-6:
                        self.stats["heuristic_overrun_count"] += 1
                    update_sp_stats(metadata, is_exact_phase, stop_early)
                    update_exact_iteration_stats(exact_iteration_stats, metadata)
                    if process_sp_result(obj_val, y_res, z_res, flow, dual_values, exact_iteration_stats):
                        new_paths += 1
                        phase_added_flow_ids.add(flow.flow_id)
                        if exact_iteration_stats is not None:
                            exact_iteration_stats["columns_added"] += 1
                    if deadline_reached(cg_phase_deadline):
                        mark_pricing_incomplete(f"CG phase deadline reached after one SP in {phase_name}.")
                        break

                if is_exact_phase and max_exact_pricing_calls is not None and exact_pricing_call_budget_used >= max_exact_pricing_calls:
                    self.stats["max_exact_pricing_calls_reached"] = True
                    self.stats["deterministic_work_budget_exhausted"] = True

            duration = time.perf_counter() - phase_start
            if is_exact_phase:
                self.stats["exact_pricing_runtime"] += duration
                if stop_early:
                    self.stats["early_stop_pricing_runtime"] += duration
            else:
                self.stats["heuristic_pricing_runtime"] += duration
            self.stats["pricing_runtime"] += duration
            record_phase(phase_name, new_paths, stop_early, duration)
            last_phase_added_flow_ids = phase_added_flow_ids
            update_deterministic_work_budget_flags()
            if exact_iteration_stats is not None:
                exact_iteration_stats["pool_signatures_after"] = set(self.pool._signatures)
                raw_stats = PricingIterationStats(
                    iteration=iteration,
                    expected_exact_calls=exact_iteration_stats["expected_exact_calls"],
                    launched_exact_calls=exact_iteration_stats["launched_exact_calls"],
                    completed_exact_calls=exact_iteration_stats["completed_exact_calls"],
                    optimal_exact_calls=exact_iteration_stats["optimal_exact_calls"],
                    positive_reduced_cost_calls=exact_iteration_stats["positive_reduced_cost_calls"],
                    time_limit_calls=exact_iteration_stats["time_limit_calls"],
                    failed_calls=exact_iteration_stats["failed_calls"],
                    columns_added=exact_iteration_stats["columns_added"],
                )
                certified_stats = evaluate_exact_pricing_certification(
                    raw_stats,
                    len(self.flows),
                    positive_column_rejected=exact_iteration_stats["positive_column_rejected"],
                    rejection_reasons=sorted(exact_iteration_stats["rejection_reasons"]),
                )
                self.stats["pricing_iteration_stats"].append(certified_stats)
            return new_paths

        def update_sp_stats(metadata: Dict[str, Any], is_exact_phase: bool, stop_early: bool) -> None:
            self.stats["pricing_sp_calls"] += 1
            if metadata.get("failed"):
                self.stats["pricing_failures"].append({
                    "flow_id": metadata.get("flow_id"),
                    "exception_class": metadata.get("exception_class"),
                    "exception_message": metadata.get("exception_message"),
                })
            if is_exact_phase:
                self.stats["exact_sp_calls"] += 1
                if stop_early:
                    self.stats["early_stop_pricing_call_count"] += 1
                if metadata.get("exact_pricing_optimal"):
                    self.stats["exact_pricing_optimal_calls"] += 1
                elif metadata.get("hit_time_limit"):
                    self.stats["exact_pricing_time_limit_calls"] += 1
                    self.stats["exact_pricing_incomplete_calls"] += 1
                elif not stop_early or metadata.get("failed"):
                    self.stats["exact_pricing_incomplete_calls"] += 1
            else:
                self.stats["heuristic_sp_calls"] += 1

        def update_exact_iteration_stats(exact_iteration_stats, metadata: Dict[str, Any]) -> None:
            if exact_iteration_stats is None:
                return
            exact_iteration_stats["completed_exact_calls"] += 1
            if metadata.get("exact_pricing_optimal"):
                exact_iteration_stats["optimal_exact_calls"] += 1
            if metadata.get("positive_reduced_cost_found"):
                exact_iteration_stats["positive_reduced_cost_calls"] += 1
            if metadata.get("hit_time_limit"):
                exact_iteration_stats["time_limit_calls"] += 1
            if metadata.get("failed"):
                exact_iteration_stats["failed_calls"] += 1

        def _lp_fractionality_by_flow(current_w_vars) -> Dict[int, float]:
            fractionality = {flow.flow_id: 0.0 for flow in self.flows}
            if current_w_vars is None:
                return fractionality
            by_id = {path.path_id: path for path in self.pool.get_all_paths()}
            for path_id, variable in current_w_vars.items():
                value = getattr(variable, "X", 0.0)
                path = by_id.get(path_id)
                if path is not None:
                    fractionality[path.flow_id] += min(value, 1.0 - value)
            return fractionality

        def _lp_rejection_weight_by_flow(current_w_vars) -> Dict[int, float]:
            weights = {flow.flow_id: 0.0 for flow in self.flows}
            if current_w_vars is None:
                return weights
            by_id = {path.path_id: path for path in self.pool.get_all_paths()}
            for path_id, variable in current_w_vars.items():
                path = by_id.get(path_id)
                if path is not None and path.path_is_empty:
                    weights[path.flow_id] = getattr(variable, "X", 0.0)
            return weights

        def run_selective_pfe_phase(dual_values, current_w_vars, primary_added_flow_ids) -> int:
            nonlocal selective_pfe_call_budget_used
            if not selective_pfe_enabled or selective_pfe_func is None:
                return 0
            counters = SelectivePFECounters()
            full_initial_acceptance = (
                initial_saginfe_accepted_flow_count is not None
                and initial_saginfe_accepted_flow_count >= len(self.flows)
            )
            reserve_protected = (
                cg_phase_deadline is not None
                and remaining_time(cg_phase_deadline) is not None
                and remaining_time(cg_phase_deadline) <= 5.0
            )
            deadline_protected = (
                overall_deadline is not None
                and remaining_time(overall_deadline) is not None
                and remaining_time(overall_deadline) <= 5.0
            )
            rejection_weights = _lp_rejection_weight_by_flow(current_w_vars)
            fractionality = _lp_fractionality_by_flow(current_w_vars)
            features = []
            for flow in self.flows:
                non_rejection_count = sum(
                    1 for path in self.pool.get_paths_by_flow(flow.flow_id)
                    if not path.path_is_empty
                )
                features.append(FlowRepairFeatures(
                    flow_id=flow.flow_id,
                    lp_rejection_weight=rejection_weights.get(flow.flow_id, 0.0),
                    represented_by_rejection=rejection_weights.get(flow.flow_id, 0.0) >= 0.5,
                    primary_pricing_generated=flow.flow_id in primary_added_flow_ids,
                    non_rejection_column_count=non_rejection_count,
                    lp_fractionality=fractionality.get(flow.flow_id, 0.0),
                    dual_bottleneck_exposure=0.0,
                ))
            selected_features, selection_counters = select_critical_flows(
                features,
                flow_count=len(self.flows),
                already_used_global_calls=selective_pfe_call_budget_used,
                initial_acceptance_is_full=full_initial_acceptance,
                reserve_protected=reserve_protected,
                deadline_protected=deadline_protected,
            )
            for key, value in selection_counters.as_metadata().items():
                if key.startswith("selective_pfe_skipped") or key.endswith("_cap_reached"):
                    self.stats[key] = bool(self.stats.get(key)) or bool(value)
            if not selected_features:
                return 0

            phase_start = time.perf_counter()
            new_paths = 0
            for feature in selected_features:
                if deadline_reached(cg_phase_deadline):
                    self.stats["selective_pfe_skipped_deadline_protection"] = True
                    break
                flow = self.flow_dict[feature.flow_id]
                sp_kwargs = {
                    "threads": solver_threads,
                    "time_limit": remaining_time(cg_phase_deadline),
                    "deadline": cg_phase_deadline,
                }
                call_start = time.perf_counter()
                obj_val, y_res, z_res, metadata = self._call_sp(
                    selective_pfe_func, flow, dual_values, True, sp_kwargs
                )
                counters.runtime_seconds += time.perf_counter() - call_start
                counters.call_count += 1
                counters.candidate_count += 1
                selective_pfe_call_budget_used += 1
                before_duplicate = self.stats["duplicate_rejection_count"]
                before_invalid = self.stats["invalid_column_count"]
                before_len = len(self.pool)
                added = process_sp_result(obj_val, y_res, z_res, flow, dual_values, None)
                if added:
                    new_paths += 1
                    counters.unique_candidate_count += 1
                    counters.positive_column_count += 1
                elif self.stats["invalid_column_count"] > before_invalid:
                    counters.invalid_candidate_count += 1
                elif self.stats["duplicate_rejection_count"] > before_duplicate or len(self.pool) == before_len:
                    if obj_val is not None and obj_val > REDUCED_COST_TOLERANCE:
                        counters.duplicate_count += 1
                    else:
                        counters.non_improving_candidate_count += 1
                else:
                    counters.non_improving_candidate_count += 1
            counters.round_count = 1
            counters.selected_flow_count = len(selected_features)
            counters.runtime_seconds = time.perf_counter() - phase_start
            counters.global_cap_reached = selection_counters.global_cap_reached
            counters.per_round_cap_reached = selection_counters.per_round_cap_reached
            metadata = counters.as_metadata()
            self.stats["selective_pfe_runtime_seconds"] += metadata["selective_pfe_runtime_seconds"]
            for key in (
                "selective_pfe_call_count",
                "selective_pfe_round_count",
                "selective_pfe_selected_flow_count",
                "selective_pfe_candidate_count",
                "selective_pfe_unique_candidate_count",
                "selective_pfe_positive_column_count",
                "selective_pfe_duplicate_count",
                "selective_pfe_invalid_candidate_count",
                "selective_pfe_non_improving_candidate_count",
            ):
                self.stats[key] += metadata[key]
            self.stats["selective_pfe_global_cap_reached"] = (
                self.stats["selective_pfe_global_cap_reached"] or metadata["selective_pfe_global_cap_reached"]
            )
            self.stats["selective_pfe_per_round_cap_reached"] = (
                self.stats["selective_pfe_per_round_cap_reached"] or metadata["selective_pfe_per_round_cap_reached"]
            )
            if new_paths:
                self.stats["columns_added_total"] += new_paths
                self.stats["columns_added_by_phase"]["Selective PFE"] = (
                    self.stats["columns_added_by_phase"].get("Selective PFE", 0) + new_paths
                )
            self.stats["pricing_phase_history"].append({
                "iteration": iteration,
                "phase": "Selective PFE",
                "stop_early": True,
                "columns_added": new_paths,
                "pricing_phase_incomplete": bool(self.stats.get("pricing_phase_incomplete")),
                "duplicate_rejections": self.pool.duplicate_rejections,
                "total_columns": len(self.pool),
                "runtime": counters.runtime_seconds,
            })
            return new_paths

        def build_final_rmp_mip_start(final_paths) -> Optional[Dict[int, float]]:
            if not final_rmp_mip_start_from_initial_columns:
                self.stats["final_rmp_mip_start_skip_reason"] = "not_configured"
                return None
            initial_signatures = self.stats.get("initial_custom_path_signatures_by_flow") or {}
            if not initial_signatures:
                self.stats["final_rmp_mip_start_skip_reason"] = "no_initial_custom_columns"
                return None
            by_flow: Dict[int, List[Path]] = {}
            for path in final_paths:
                by_flow.setdefault(path.flow_id, []).append(path)
            values: Dict[int, float] = {}
            accepted = 0
            objective = 0.0
            for flow in self.flows:
                paths_for_flow = by_flow.get(flow.flow_id, [])
                if not paths_for_flow:
                    self.stats["final_rmp_mip_start_skip_reason"] = f"flow {flow.flow_id} has no final-RMP column"
                    return None
                selected_path = None
                expected_signature = initial_signatures.get(flow.flow_id)
                if expected_signature is not None:
                    for path in paths_for_flow:
                        if canonical_column_signature(path) == expected_signature:
                            selected_path = path
                            break
                    if selected_path is None:
                        self.stats["final_rmp_mip_start_skip_reason"] = (
                            f"flow {flow.flow_id} initial SAGINFE column absent from final pool"
                        )
                        return None
                else:
                    empty_paths = [path for path in paths_for_flow if path.path_is_empty]
                    if not empty_paths:
                        self.stats["final_rmp_mip_start_skip_reason"] = (
                            f"flow {flow.flow_id} has no rejection column for complete MIP start"
                        )
                        return None
                    selected_path = sorted(empty_paths, key=lambda path: path.path_id)[0]
                for path in paths_for_flow:
                    values[path.path_id] = 1.0 if path.path_id == selected_path.path_id else 0.0
                if not selected_path.path_is_empty:
                    accepted += 1
                    objective += self.objective_spec.acceptance_reward - selected_path.path_cost
            self.stats["final_rmp_mip_start_used"] = True
            self.stats["final_rmp_mip_start_valid"] = True
            self.stats["final_rmp_mip_start_skip_reason"] = None
            self.stats["final_rmp_mip_start_accepted_flow_count"] = accepted
            self.stats["final_rmp_mip_start_objective"] = objective
            return values

        try:
            while True:
                if cg_phase_deadline is not None and time.perf_counter() >= cg_phase_deadline:
                    mark_cg_deadline("CG phase deadline reached before starting a new iteration.")
                    break

                iteration += 1
                self.stats["iterations"] = iteration

                if deadline_reached(cg_phase_deadline):
                    mark_cg_deadline("CG phase deadline reached before RMP LP solve.")
                    break
                rmp_start = time.perf_counter()
                rmp_model, w_vars, duals = solve_rmp(
                    sorted(self.pool.get_all_paths(), key=lambda path: (path.flow_id, path.path_id, canonical_column_signature(path))) if deterministic_mode else self.pool.get_all_paths(),
                    self.flows,
                    self.network,
                    self.objective_spec,
                    is_integer=False,
                    time_limit=remaining_time(cg_phase_deadline),
                    threads=solver_threads,
                    model_name="RMP_LP",
                    gurobi_seed=stable_gurobi_seed(algorithm_seed, iteration, "rmp_lp") if deterministic_mode and algorithm_seed is not None else None,
                )
                self.stats["rmp_lp_runtime"] += time.perf_counter() - rmp_start
                self.stats["rmp_lp_calls"] += 1

                if deadline_reached(cg_phase_deadline):
                    mark_cg_deadline("CG phase deadline reached after RMP LP solve.")
                    break

                if duals is None:
                    if deadline_reached(cg_phase_deadline) or getattr(rmp_model, "Status", None) == GRB.TIME_LIMIT:
                        mark_cg_deadline("RMP LP did not provide duals before the CG phase deadline.")
                        break
                    self.stats["cg_status"] = "RMP_NOT_OPTIMAL"
                    self.stats["termination_reason"] = f"LP RMP did not solve to optimality: {self._status_name(rmp_model.Status)}"
                    return None, None

                current_stop_early = False
                if sp_strategy == "early_stop":
                    current_stop_early = True
                elif sp_strategy == "hybrid" and iteration <= hybrid_switch_iters:
                    current_stop_early = True

                if sp_strategy == "heuristic_then_exact":
                    new_paths_this_iter = 0
                    heuristic_added = 0
                    should_run_exact = False

                    if heuristic_disabled:
                        should_run_exact = True
                    else:
                        if deadline_reached(cg_phase_deadline):
                            mark_pricing_incomplete("CG phase deadline reached before heuristic pricing.")
                            break
                        heuristic_added = run_pricing_phase(
                            heuristic_sp_func, duals, True, False, "SGFE heuristic"
                        )
                        new_paths_this_iter += heuristic_added
                        if self.stats["pricing_phase_incomplete"]:
                            if deterministic_mode:
                                restore_checkpoint()
                            break
                        if heuristic_added == 0:
                            heuristic_miss_count += 1
                            self.stats["heuristic_miss_count"] = heuristic_miss_count
                            if (
                                heuristic_disable_after_misses is not None
                                and heuristic_miss_count >= heuristic_disable_after_misses
                            ):
                                heuristic_disabled = True
                                self.stats["heuristic_disabled"] = True
                            should_run_exact = True
                        else:
                            heuristic_miss_count = 0
                            self.stats["heuristic_miss_count"] = 0
                            if run_exact_after_heuristic_success:
                                should_run_exact = True

                    if should_run_exact:
                        if deadline_reached(cg_phase_deadline):
                            mark_pricing_incomplete("CG phase deadline reached before exact fallback.")
                            break
                        before_incomplete = self.stats["exact_pricing_incomplete_calls"]
                        exact_added = run_pricing_phase(
                            exact_sp_func, duals, False, effective_parallel, "Exact Gurobi fallback"
                        )
                        new_paths_this_iter += exact_added
                        if self.stats["pricing_phase_incomplete"]:
                            if deterministic_mode:
                                restore_checkpoint()
                            break
                        if self.stats["deterministic_work_budget_exhausted"]:
                            update_deterministic_work_budget_flags()
                            trigger = deterministic_work_budget_trigger()
                            if not self.stats.get("cg_status") or self.stats.get("cg_status") == "NOT_STARTED":
                                self.stats["cg_status"] = "DETERMINISTIC_WORK_BUDGET_EXHAUSTED"
                                self.stats["termination_reason"] = f"Reached deterministic work budget: {trigger}."
                            mark_termination(trigger)
                            mark_iteration_complete()
                            break
                        exact_incomplete = self.stats["exact_pricing_incomplete_calls"] > before_incomplete
                        last_cert, _ = latest_exact_iteration_certification(self.stats)
                        if heuristic_added == 0 and exact_added == 0 and not exact_incomplete and last_cert:
                            self.stats["converged"] = True
                            self.stats["pricing_convergence_certified"] = True
                            self.stats["cg_status"] = "CONVERGED"
                            self.stats["termination_reason"] = "Exact fallback found no positive reduced-cost columns."
                            mark_iteration_complete()
                            break
                        if heuristic_added == 0 and exact_added == 0 and not exact_incomplete:
                            reason = latest_rejected_positive_reason(self.stats)
                            if reason is not None:
                                self.stats["pricing_convergence_certified"] = False
                                self.stats["converged"] = False
                                self.stats["cg_status"] = reason
                                self.stats["termination_reason"] = (
                                    "Exact pricing produced positive reduced-cost results, "
                                    "but no valid positive column was inserted and the RMP pool did not change."
                                )
                                self.stats["no_progress_termination_count"] += 1
                                mark_iteration_complete()
                                break
                        if (exact_incomplete or not last_cert) and exact_added == 0:
                            self.stats["pricing_convergence_certified"] = False
                else:
                    if deadline_reached(cg_phase_deadline):
                        mark_pricing_incomplete("CG phase deadline reached before pricing.")
                        break
                    before_incomplete = self.stats["exact_pricing_incomplete_calls"]
                    new_paths_this_iter = run_pricing_phase(
                        sp_solver, duals, current_stop_early, effective_parallel, "Pricing"
                    )
                    if self.stats["pricing_phase_incomplete"]:
                        if deterministic_mode:
                            restore_checkpoint()
                        break
                    if self.stats["deterministic_work_budget_exhausted"]:
                        update_deterministic_work_budget_flags()
                        trigger = deterministic_work_budget_trigger()
                        if not self.stats.get("cg_status") or self.stats.get("cg_status") == "NOT_STARTED":
                            self.stats["cg_status"] = "DETERMINISTIC_WORK_BUDGET_EXHAUSTED"
                            self.stats["termination_reason"] = f"Reached deterministic work budget: {trigger}."
                        mark_termination(trigger)
                        mark_iteration_complete()
                        break
                    exact_incomplete = (
                        not current_stop_early
                        and self.stats["exact_pricing_incomplete_calls"] > before_incomplete
                    )
                    if (
                        selective_pfe_enabled
                        and not self.stats["pricing_phase_incomplete"]
                        and not self.stats["deterministic_work_budget_exhausted"]
                    ):
                        new_paths_this_iter += run_selective_pfe_phase(
                            duals,
                            w_vars,
                            last_phase_added_flow_ids,
                        )

                    if new_paths_this_iter == 0:
                        if current_stop_early:
                            sp_strategy = "exact"
                            continue
                        last_cert, _ = latest_exact_iteration_certification(self.stats)
                        if exact_incomplete:
                            self.stats["pricing_convergence_certified"] = False
                            self.stats["cg_status"] = "TIME_LIMIT_NOT_CONVERGED"
                            self.stats["termination_reason"] = "Exact pricing did not certify convergence."
                            mark_iteration_complete()
                            break
                        reason = latest_rejected_positive_reason(self.stats)
                        if reason is not None:
                            self.stats["pricing_convergence_certified"] = False
                            self.stats["converged"] = False
                            self.stats["cg_status"] = reason
                            self.stats["termination_reason"] = (
                                "Exact pricing produced positive reduced-cost results, "
                                "but no valid positive column was inserted and the RMP pool did not change."
                            )
                            self.stats["no_progress_termination_count"] += 1
                            mark_iteration_complete()
                            break
                        if not last_cert:
                            self.stats["pricing_convergence_certified"] = False
                            self.stats["cg_status"] = "INCOMPLETE_NOT_CONVERGED"
                            self.stats["termination_reason"] = "Exact pricing iteration did not satisfy certification conditions."
                            mark_iteration_complete()
                            break
                        self.stats["converged"] = True
                        self.stats["pricing_convergence_certified"] = True
                        self.stats["cg_status"] = "CONVERGED"
                        self.stats["termination_reason"] = "Exact pricing found no positive reduced-cost columns."
                        mark_iteration_complete()
                        break

                if max_iterations is not None and iteration >= max_iterations:
                    update_deterministic_work_budget_flags()
                    self.stats["stopped_by_max_iterations"] = True
                    trigger = deterministic_work_budget_trigger()
                    self.stats["cg_status"] = "MAX_ITERATIONS" if trigger == "MAX_CG_ITERATIONS" else "DETERMINISTIC_WORK_BUDGET_EXHAUSTED"
                    self.stats["termination_reason"] = f"Reached deterministic work budget: {trigger}."
                    mark_termination(trigger)
                    mark_iteration_complete()
                    break

                mark_iteration_complete()

        finally:
            pass

        if rmp_model is None or w_vars is None:
            if (
                self.stats.get("stopped_by_global_time_limit")
                or self.stats.get("internal_phase_limit_reached")
            ) and len(self.pool) > 0:
                fixed_w_vars = self._build_initial_column_solution()
                self.stats["final_solution_source"] = "INITIAL_COLUMNS"
                self.stats["final_rmp_required"] = False
                self.stats["final_rmp_status"] = "NOT_LAUNCHED"
                self.stats["final_rmp_status_name"] = "NOT_LAUNCHED"
                self.stats["final_rmp_has_incumbent"] = None
                self.stats["final_rmp_binary_variables"] = len(fixed_w_vars)
                self._finish_runtime_stats(total_start, effective_global_time_limit)
                return None, fixed_w_vars
            self._finish_runtime_stats(total_start, effective_global_time_limit)
            return None, None

        if getattr(rmp_model, "SolCount", 0) <= 0:
            self.stats["final_rmp_required"] = False
            self.stats["final_rmp_status"] = "NOT_LAUNCHED"
            self.stats["final_rmp_status_name"] = "NOT_LAUNCHED"
            self.stats["final_rmp_has_incumbent"] = None
            self.stats["final_rmp_gap"] = None
            self.stats["final_rmp_best_bound"] = None
            self.stats["final_rmp_runtime"] = 0.0
            self.stats["final_solution_source"] = "NONE"
            if (
                self.stats.get("stopped_by_global_time_limit")
                or self.stats.get("internal_phase_limit_reached")
            ) and len(self.pool) > 0:
                fixed_w_vars = self._build_initial_column_solution()
                self.stats["final_solution_source"] = "INITIAL_COLUMNS"
                self.stats["final_rmp_binary_variables"] = len(fixed_w_vars)
                self._finish_runtime_stats(total_start, effective_global_time_limit)
                return None, fixed_w_vars
            self.stats["final_rmp_binary_variables"] = len(w_vars)
            self._finish_runtime_stats(total_start, effective_global_time_limit)
            return None, None

        is_already_integer = True
        for var in w_vars.values():
            val = var.X
            if not (val < 1e-6 or val > 1 - 1e-6):
                is_already_integer = False
                break

        if is_already_integer:
            self.stats["final_rmp_required"] = False
            self.stats["final_rmp_binary_variables"] = len(w_vars)
            self.stats["final_rmp_status"] = "NOT_REQUIRED"
            self.stats["final_rmp_status_name"] = "NOT_REQUIRED"
            self.stats["final_rmp_has_incumbent"] = None
            self.stats["final_rmp_is_optimal"] = False
            self.stats["final_rmp_hit_time_limit"] = False
            self.stats["final_rmp_gap"] = None
            self.stats["final_rmp_best_bound"] = None
            self.stats["final_rmp_runtime"] = 0.0
            self.stats["final_solution_source"] = "INTEGRAL_RMP_LP"
            self._finish_runtime_stats(total_start, effective_global_time_limit)
            return rmp_model, w_vars

        self.stats["final_rmp_required"] = True
        final_remaining = remaining_time(overall_deadline)
        self.stats["final_rmp_remaining_seconds_at_start"] = final_remaining
        if final_remaining is None:
            actual_final_time_limit = final_rmp_time_cap_seconds
        elif final_rmp_time_cap_seconds is None:
            actual_final_time_limit = final_remaining
        else:
            actual_final_time_limit = min(final_remaining, final_rmp_time_cap_seconds)
        self.stats["actual_final_rmp_time_limit_seconds"] = actual_final_time_limit
        if final_remaining is not None and final_remaining <= 0:
            self.stats["stopped_by_global_time_limit"] = True
            self.stats["termination_reason"] = "No global wall-clock time remained for the final integer RMP."
            self.stats["cg_status"] = self.stats["cg_status"] or "TIME_LIMIT_NOT_CONVERGED"
            self.stats["final_rmp_status"] = "NOT_LAUNCHED"
            self.stats["final_rmp_status_name"] = "NOT_LAUNCHED"
            self.stats["final_rmp_has_incumbent"] = None
            self.stats["final_rmp_gap"] = None
            self.stats["final_rmp_best_bound"] = None
            self.stats["final_solution_source"] = "NONE"
            self._finish_runtime_stats(total_start, effective_global_time_limit)
            return None, None

        if deadline_reached(overall_deadline):
            self.stats["stopped_by_global_time_limit"] = True
            self.stats["termination_reason"] = "Global wall-clock deadline reached before final integer RMP."
            self.stats["final_rmp_status"] = "NOT_LAUNCHED"
            self.stats["final_rmp_status_name"] = "NOT_LAUNCHED"
            self.stats["final_rmp_has_incumbent"] = None
            self.stats["final_solution_source"] = "NONE"
            self._finish_runtime_stats(total_start, effective_global_time_limit)
            return None, None

        final_start = time.perf_counter()
        final_paths = (
            sorted(self.pool.get_all_paths(), key=lambda path: (path.flow_id, path.path_id, canonical_column_signature(path)))
            if deterministic_mode else self.pool.get_all_paths()
        )
        final_rmp_mip_start_values = build_final_rmp_mip_start(final_paths)
        final_result = solve_rmp(
            final_paths,
            self.flows,
            self.network,
            self.objective_spec,
            is_integer=True,
            time_limit=actual_final_time_limit,
            mip_gap=final_rmp_mip_gap,
            threads=solver_threads,
            model_name="Final_Integer_RMP",
            gurobi_seed=stable_gurobi_seed(algorithm_seed, "final_rmp") if deterministic_mode and algorithm_seed is not None else None,
            collect_mip_progress=True,
            mip_start_values=final_rmp_mip_start_values,
        )
        final_model, final_w_vars, _ = final_result
        self.stats["final_rmp_runtime"] = time.perf_counter() - final_start
        self.stats["final_rmp_incumbent_events"] = final_result.metadata.get("mip_incumbent_events", [])
        self.stats["final_rmp_first_incumbent_runtime_seconds"] = final_result.metadata.get("first_incumbent_runtime_seconds")
        self.stats["final_rmp_first_incumbent_objective"] = final_result.metadata.get("first_incumbent_objective")
        self.stats["final_rmp_best_incumbent_runtime_seconds"] = final_result.metadata.get("best_incumbent_runtime_seconds")
        self.stats["final_rmp_best_incumbent_objective"] = final_result.metadata.get("best_incumbent_objective")
        self.stats["final_rmp_final_incumbent_runtime_seconds"] = final_result.metadata.get("final_incumbent_runtime_seconds")
        self.stats["final_rmp_final_incumbent_objective"] = final_result.metadata.get("final_incumbent_objective")
        self._record_final_rmp_stats(final_model, final_w_vars)
        self.stats["final_solution_source"] = "FINAL_INTEGER_RMP" if final_model.SolCount > 0 else "NONE"
        if final_model.Status == GRB.TIME_LIMIT:
            self.stats["stopped_by_global_time_limit"] = True
            previous_reason = self.stats.get("termination_reason")
            final_reason = (
                "Final restricted integer RMP reached the time limit "
                f"with {'an incumbent' if final_model.SolCount > 0 else 'no incumbent'}."
            )
            if previous_reason:
                self.stats["termination_reason"] = f"{previous_reason} Final phase: {final_reason}"
            else:
                self.stats["termination_reason"] = final_reason
        self._finish_runtime_stats(total_start, effective_global_time_limit)
        self.stats["final_ordered_column_pool_fingerprint"] = column_pool_fingerprint(self.pool.get_all_paths())
        return final_model, final_w_vars

    def _record_final_rmp_stats(self, model, w_vars) -> None:
        self.stats["final_rmp_status"] = model.Status
        self.stats["final_rmp_status_name"] = self._status_name(model.Status)
        self.stats["final_rmp_has_incumbent"] = model.SolCount > 0
        self.stats["final_rmp_is_optimal"] = model.Status == GRB.OPTIMAL
        self.stats["final_rmp_hit_time_limit"] = model.Status == GRB.TIME_LIMIT
        self.stats["final_rmp_binary_variables"] = len(w_vars)
        if model.SolCount > 0:
            self.stats["final_rmp_gap"] = model.MIPGap
            self.stats["final_rmp_best_bound"] = model.ObjBound

    def _finish_runtime_stats(self, total_start: float, global_time_limit: Optional[float] = None) -> None:
        total_runtime = time.perf_counter() - total_start
        self.stats["total_runtime"] = total_runtime
        self.stats["final_ordered_column_pool_fingerprint"] = column_pool_fingerprint(self.pool.get_all_paths())
        apply_hard_limit_metadata(self.stats, total_runtime, global_time_limit)
        self.stats["deadline_overrun_runtime"] = self.stats.get("hard_limit_overrun_seconds", 0.0)
        accounted = (
            self.stats["initialization_runtime"]
            + self.stats["rmp_lp_runtime"]
            + self.stats["heuristic_pricing_runtime"]
            + self.stats["exact_pricing_runtime"]
            + self.stats["final_rmp_runtime"]
        )
        self.stats["other_runtime"] = max(0.0, total_runtime - accounted)
        self.stats["cg_phase_runtime"] = (
            self.stats["initialization_runtime"]
            + self.stats["rmp_lp_runtime"]
            + self.stats["pricing_runtime"]
        )
