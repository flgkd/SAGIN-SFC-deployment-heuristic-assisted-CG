# ILP_solver.py

import math
import time
from typing import Dict, List, Mapping, Optional, Tuple

from gurobipy import GRB, Model, quicksum

from data_model import Flow, Network, Path, TimeEdge, TimeNode, reconstruct_ordered_route
from objective import (
    ObjectiveSpec,
    compute_capacity_denominators,
    compute_path_physical_cost,
    objective_spec_from_network,
    validate_objective_spec,
)
from result_schema import ILPResult, OptimalityClaims, RuntimeBreakdown
from validation import validate_path, validate_solution_capacity


class ILPSolution:
    def __init__(
        self,
        objective: Optional[float],
        accepted: int,
        placements: Dict[int, Dict[int, TimeNode]],
        routings: Dict[int, List[TimeEdge]],
        accepted_flows: Dict[int, int],
        runtime: float,
        status: int,
        model: Optional[Model],
        status_name: Optional[str] = None,
        has_incumbent: bool = False,
        global_original_integer_optimal: bool = False,
        hit_time_limit: bool = False,
        best_bound: Optional[float] = None,
        final_mip_gap: Optional[float] = None,
        node_count: Optional[float] = None,
        num_variables: Optional[int] = None,
        num_constraints: Optional[int] = None,
        sol_count: Optional[int] = None,
        physical_cost: Optional[float] = None,
        result: Optional[ILPResult] = None,
    ):
        self.objective = objective
        self.accepted = accepted
        self.placements = placements
        self.routings = routings
        self.accepted_flows = accepted_flows
        self.runtime = runtime
        self.status = status
        self.model = model
        self.status_name = status_name
        self.has_incumbent = has_incumbent
        self.global_original_integer_optimal = global_original_integer_optimal
        self.hit_time_limit = hit_time_limit
        self.best_bound = best_bound
        self.final_mip_gap = final_mip_gap
        self.node_count = node_count
        self.num_variables = num_variables
        self.num_constraints = num_constraints
        self.sol_count = sol_count
        self.physical_cost = physical_cost
        self.accepted_count = accepted
        self.rejected_count = len(accepted_flows) - accepted
        self.normalized_physical_cost = physical_cost
        self.scalarized_objective = objective
        self.result = result

    def as_sgfe_results(self) -> Dict[int, Tuple[Optional[Dict[int, TimeNode]], Optional[List[TimeEdge]]]]:
        results: Dict[int, Tuple[Optional[Dict[int, TimeNode]], Optional[List[TimeEdge]]]] = {}
        for flow_id, is_accepted in self.accepted_flows.items():
            if is_accepted:
                results[flow_id] = (self.placements.get(flow_id, {}), self.routings.get(flow_id, []))
            else:
                results[flow_id] = (None, None)
        return results


class DirectILPExtractionError(ValueError):
    def __init__(self, message: str, diagnostics: Dict):
        super().__init__(message)
        self.diagnostics = diagnostics


def solve_ilp(
    network: Network,
    flows: List[Flow],
    objective_spec: Optional[ObjectiveSpec] = None,
    time_limit: Optional[float] = None,
    mip_gap: Optional[float] = None,
    mip_gap_abs: Optional[float] = None,
    verbose: bool = False,
    threads: Optional[int] = None,
    gurobi_seed: Optional[int] = None,
    warm_start_paths: Optional[Mapping[int, Path]] = None,
    stop_when_acceptance_certified: bool = False,
    acceptance_certification_tolerance: float = 1e-7,
    objective_mode: str = "scalarized",
    enforce_simple_routes: bool = False,
    e2e_start_time: Optional[float] = None,
    e2e_time_limit_seconds: Optional[float] = None,
    solver_finalization_reserve_seconds: float = 0.0,
) -> ILPSolution:
    """
    Solve the original ILP directly with Gurobi.

    By default, objective follows the v2 scalarized lexicographic model:
        max acceptance_reward * sum_r x_r - normalized_physical_resource_cost

    The ``acceptance_only`` objective mode preserves the same feasible region
    but maximizes only the integer accepted-flow count.

    x_r = 1 means flow r is accepted. If x_r = 0, all placement and routing
    variables for that flow are forced to zero by the placement and flow
    conservation constraints.
    """
    objective_spec = objective_spec or objective_spec_from_network(network)
    denominators = compute_capacity_denominators(network)
    validate_objective_spec(objective_spec, denominators)
    if objective_mode not in ("scalarized", "acceptance_only"):
        raise ValueError(f"Unsupported Direct ILP objective_mode {objective_mode!r}.")
    if solver_finalization_reserve_seconds < 0.0:
        raise ValueError("solver_finalization_reserve_seconds must be nonnegative.")

    model_build_start = time.perf_counter()
    model = Model("Original_ILP")
    model.Params.OutputFlag = 1 if verbose else 0
    if time_limit is not None:
        model.Params.TimeLimit = time_limit
    if mip_gap is not None:
        model.Params.MIPGap = mip_gap
    if mip_gap_abs is not None:
        model.Params.MIPGapAbs = mip_gap_abs
    if threads is not None:
        model.Params.Threads = threads
    if gurobi_seed is not None:
        model.Params.Seed = gurobi_seed

    # =========================
    # 1. Variables
    # =========================
    x = {}
    y = {}
    z = {}
    route_order = {}

    for flow in flows:
        r = flow.flow_id
        x[r] = model.addVar(vtype=GRB.BINARY, name=f"x_{r}")

        for (n, t) in network.time_nodes:
            for k in range(len(flow.flow_extended_sfc)):
                y[r, n, t, k] = model.addVar(vtype=GRB.BINARY, name=f"y_{r}_{n}_{t}_{k}")

        for (u_tn, v_tn) in network.time_edges:
            for l in range(len(flow.flow_virtual_links)):
                (u, t1), (v, t2) = u_tn, v_tn
                z[r, u, t1, v, t2, l] = model.addVar(
                    vtype=GRB.BINARY,
                    name=f"z_{r}_{u}_{t1}_{v}_{t2}_{l}",
                )
        if enforce_simple_routes:
            for l in range(len(flow.flow_virtual_links)):
                for (n, t) in network.time_nodes:
                    route_order[r, n, t, l] = model.addVar(
                        lb=0.0,
                        ub=float(len(network.time_nodes)),
                        vtype=GRB.CONTINUOUS,
                        name=f"route_order_{r}_{n}_{t}_{l}",
                    )

    model.update()

    warm_start_application_start = time.perf_counter()
    warm_start_metadata = _apply_warm_start(
        model, x, y, z, network, flows, warm_start_paths
    )
    warm_start_application_runtime = time.perf_counter() - warm_start_application_start
    warm_start_metadata["application_runtime_seconds"] = warm_start_application_runtime

    # =========================
    # 2. Objective
    # =========================
    accepted_count_expr = quicksum(x[flow.flow_id] for flow in flows)
    accepted_reward = objective_spec.acceptance_reward * accepted_count_expr

    node_cost = quicksum(
        objective_spec.resource_weight
        * flow.flow_demand
        * flow.flow_extended_vnf_cost[k]
        / denominators.total_processing_capacity
        * y[flow.flow_id, n, t, k]
        for flow in flows
        for (n, t) in network.time_nodes
        for k in range(len(flow.flow_extended_sfc))
    )

    edge_cost = quicksum(
        (1.0 - objective_spec.resource_weight)
        * flow.flow_demand
        / denominators.total_communication_capacity
        * z[flow.flow_id, u, t1, v, t2, l]
        for flow in flows
        for ((u, t1), (v, t2)) in network.time_edges
        if t1 == t2
        for l in range(len(flow.flow_virtual_links))
    )

    if objective_mode == "acceptance_only":
        model.setObjective(accepted_count_expr, GRB.MAXIMIZE)
    else:
        model.setObjective(accepted_reward - node_cost - edge_cost, GRB.MAXIMIZE)

    # =========================
    # 3. Flow acceptance and placement constraints
    # =========================
    for flow in flows:
        r = flow.flow_id
        src_n, src_t = flow.flow_src_time_node
        dst_n = flow.flow_dst_node
        dst_mt = flow.flow_dst_max_time
        last_k = len(flow.flow_extended_sfc) - 1

        # Source virtual node is placed at the required source time-node iff accepted.
        if flow.flow_src_time_node in network.time_node_capacity:
            model.addConstr(y[r, src_n, src_t, 0] == x[r], name=f"source_{r}")
        else:
            model.addConstr(x[r] == 0, name=f"invalid_source_{r}")

        # Destination virtual node is placed at the destination node within the time window iff accepted.
        dst_terms = [
            y[r, dst_n, t, last_k]
            for t in network.time_slots
            if src_t <= t <= dst_mt and (dst_n, t) in network.time_node_capacity
        ]
        model.addConstr(quicksum(dst_terms) == x[r], name=f"destination_{r}")

        # Every extended SFC element is placed exactly once when accepted, zero times otherwise.
        for k in range(len(flow.flow_extended_sfc)):
            model.addConstr(
                quicksum(y[r, n, t, k] for (n, t) in network.time_nodes) == x[r],
                name=f"place_once_{r}_{k}",
            )

        # Tighten the time window for all virtual nodes of this flow.
        for (n, t) in network.time_nodes:
            if t < src_t or t > dst_mt:
                for k in range(len(flow.flow_extended_sfc)):
                    model.addConstr(y[r, n, t, k] == 0, name=f"time_window_{r}_{n}_{t}_{k}")

    # =========================
    # 4. Flow conservation and no-splitting constraints
    # =========================
    for flow in flows:
        r = flow.flow_id
        for l in range(len(flow.flow_virtual_links)):
            for (n, t) in network.time_nodes:
                incoming = quicksum(
                    z[r, m, tt, n, t, l]
                    for (m, tt) in network.in_time_edges.get((n, t), [])
                )
                outgoing = quicksum(
                    z[r, n, t, m, tt, l]
                    for (m, tt) in network.out_time_edges.get((n, t), [])
                )

                model.addConstr(outgoing <= 1, name=f"out_split_{r}_{l}_{n}_{t}")
                model.addConstr(incoming <= 1, name=f"in_split_{r}_{l}_{n}_{t}")
                model.addConstr(
                    incoming - outgoing
                    == y[r, n, t, l + 1] - y[r, n, t, l],
                    name=f"flow_conservation_{r}_{l}_{n}_{t}",
                )
            if enforce_simple_routes:
                big_m = float(len(network.time_nodes) + 1)
                for ((u, t1), (v, t2)) in network.time_edges:
                    model.addConstr(
                        route_order[r, v, t2, l]
                        >= route_order[r, u, t1, l] + 1.0
                        - big_m * (1.0 - z[r, u, t1, v, t2, l]),
                        name=f"simple_route_order_{r}_{l}_{u}_{t1}_{v}_{t2}",
                    )

    # =========================
    # 5. Resource capacity constraints
    # =========================
    for (n, t) in network.time_nodes:
        model.addConstr(
            quicksum(
                flow.flow_demand
                * flow.flow_extended_vnf_cost[k]
                * y[flow.flow_id, n, t, k]
                for flow in flows
                for k in range(len(flow.flow_extended_sfc))
            )
            <= network.time_node_capacity[(n, t)],
            name=f"node_capacity_{n}_{t}",
        )

    for ((u, t1), (v, t2)) in network.time_edges:
        capacity = network.time_edge_capacity[((u, t1), (v, t2))]
        if capacity == math.inf:
            continue
        model.addConstr(
            quicksum(
                flow.flow_demand
                * z[flow.flow_id, u, t1, v, t2, l]
                for flow in flows
                for l in range(len(flow.flow_virtual_links))
            )
            <= capacity,
            name=f"edge_capacity_{u}_{v}_{t1}",
        )

    model.update()
    model_build_runtime = time.perf_counter() - model_build_start
    effective_solver_time_limit = time_limit
    if e2e_start_time is not None and e2e_time_limit_seconds is not None:
        remaining = e2e_time_limit_seconds - (time.perf_counter() - e2e_start_time)
        remaining -= solver_finalization_reserve_seconds
        effective_solver_time_limit = max(0.0, remaining)
        if time_limit is not None:
            effective_solver_time_limit = min(float(time_limit), effective_solver_time_limit)
        model.Params.TimeLimit = effective_solver_time_limit
    early_stop_metadata = {
        "enabled": stop_when_acceptance_certified,
        "terminated_by_acceptance_certification": False,
        "method": None,
        "solver_invoked": True,
        "solver_skipped_reason": None,
        "best_incumbent_accepted_count": None,
        "bound_at_termination": None,
        "tolerance": acceptance_certification_tolerance,
    }
    if (
        stop_when_acceptance_certified
        and warm_start_metadata["supplied"]
        and warm_start_metadata["accepted_count"] == len(flows)
    ):
        early_stop_metadata.update({
            "terminated_by_acceptance_certification": True,
            "method": "ALL_FLOWS_ACCEPTED",
            "solver_invoked": False,
            "solver_skipped_reason": "Validated warm start accepts every flow; no solution can accept more.",
            "best_incumbent_accepted_count": len(flows),
            "model_build_runtime_seconds": model_build_runtime,
        })
        return _build_warm_start_all_accepted_solution(
            network,
            flows,
            objective_spec,
            warm_start_paths,
            warm_start_metadata,
            early_stop_metadata,
            model,
        )

    callback_state = {
        "best_incumbent_accepted_count": None,
        "bound_at_termination": None,
        "method": None,
        "time_to_first_feasible_incumbent_seconds": None,
        "time_to_best_incumbent_seconds": None,
    }

    def acceptance_certification_callback(model_obj, where):
        if where == GRB.Callback.MIPSOL:
            accepted_count = 0
            for flow in flows:
                value = model_obj.cbGetSolution(x[flow.flow_id])
                if value > 0.5:
                    accepted_count += 1
            current = callback_state["best_incumbent_accepted_count"]
            if current is None or accepted_count > current:
                callback_state["best_incumbent_accepted_count"] = accepted_count
                callback_state["time_to_best_incumbent_seconds"] = model_obj.cbGet(GRB.Callback.RUNTIME)
            if callback_state["time_to_first_feasible_incumbent_seconds"] is None:
                callback_state["time_to_first_feasible_incumbent_seconds"] = model_obj.cbGet(GRB.Callback.RUNTIME)
            if stop_when_acceptance_certified and accepted_count >= len(flows):
                callback_state["method"] = "ALL_FLOWS_ACCEPTED"
                model_obj.terminate()
        elif where == GRB.Callback.MIP:
            if not stop_when_acceptance_certified:
                return
            incumbent = callback_state["best_incumbent_accepted_count"]
            if incumbent is None or incumbent >= len(flows):
                return
            bound = model_obj.cbGet(GRB.Callback.MIP_OBJBND)
            threshold = objective_spec.acceptance_reward * (incumbent + 1) - 1.0
            if bound < threshold - acceptance_certification_tolerance:
                callback_state["bound_at_termination"] = bound
                callback_state["method"] = "OBJECTIVE_BOUND"
                model_obj.terminate()

    model.optimize(acceptance_certification_callback)
    result_extraction_start = time.perf_counter()
    if stop_when_acceptance_certified and callback_state["method"] is not None:
        early_stop_metadata.update({
            "terminated_by_acceptance_certification": True,
            "method": callback_state["method"],
            "best_incumbent_accepted_count": callback_state["best_incumbent_accepted_count"],
            "bound_at_termination": callback_state["bound_at_termination"],
        })
    sol_count = model.SolCount
    has_incumbent = sol_count > 0

    objective: Optional[float] = None
    accepted = 0
    placements: Dict[int, Dict[int, TimeNode]] = {}
    routings: Dict[int, List[TimeEdge]] = {}
    segment_routings: Dict[int, Dict[int, List[TimeEdge]]] = {}
    accepted_flows: Dict[int, int] = {flow.flow_id: 0 for flow in flows}

    best_bound: Optional[float] = None
    final_mip_gap: Optional[float] = None
    physical_cost: Optional[float] = None
    extracted_paths: Dict[int, Path] = {}
    try:
        best_bound = model.ObjBound
    except Exception:
        best_bound = None

    if has_incumbent:
        objective = model.ObjVal
        final_mip_gap = model.MIPGap

        for flow in flows:
            r = flow.flow_id
            is_accepted = 1 if x[r].X > 0.5 else 0
            accepted_flows[r] = is_accepted
            if not is_accepted:
                continue

            accepted += 1
            placement: Dict[int, TimeNode] = {}
            dst_time_node: Optional[TimeNode] = None
            for (n, t) in network.time_nodes:
                for k, vnf_name in enumerate(flow.flow_extended_sfc):
                    if y[r, n, t, k].X > 0.5 and vnf_name not in ("SRC", "DST"):
                        stage_index = flow.stage_index_for_extended_index(k)
                        if stage_index is not None:
                            placement[stage_index] = (n, t)
                    elif y[r, n, t, k].X > 0.5 and vnf_name == "DST":
                        dst_time_node = (n, t)

            routing: List[TimeEdge] = []
            segment_routing: Dict[int, List[TimeEdge]] = {}
            milestones = [flow.flow_src_time_node]
            for stage in flow.processing_stages:
                milestones.append(placement[stage.stage_index])
            if dst_time_node is None:
                raise DirectILPExtractionError(
                    f"Accepted flow {r} has no selected destination placement.",
                    {"flow_id": r, "placement": placement},
                )
            milestones.append(dst_time_node)
            for l in range(len(flow.flow_virtual_links)):
                segment_edges = [
                    ((u, t1), (v, t2))
                    for ((u, t1), (v, t2)) in network.time_edges
                    if z[r, u, t1, v, t2, l].X > 0.5
                ]
                try:
                    ordered_segment = reconstruct_ordered_route(
                        segment_edges,
                        milestones[l],
                        milestones[l + 1],
                    )
                except Exception as exc:
                    raise DirectILPExtractionError(
                        f"Flow {r} segment index {l} cannot be reconstructed as a simple route: {exc}",
                        _segment_diagnostics(
                            flow,
                            l,
                            milestones[l],
                            milestones[l + 1],
                            segment_edges,
                            placement,
                            dst_time_node,
                        ),
                    ) from exc
                segment_routing[l] = ordered_segment
                routing.extend(ordered_segment)

            placements[r] = placement
            routings[r] = routing
            segment_routings[r] = segment_routing

            extracted_path = Path(
                flow_id=r,
                path_id=-1,
                path_is_empty=False,
                path_stage_placement=placement,
                path_segment_routing=segment_routing,
                path_cost=0.0,
            )
            extracted_path.finalize_usage(flow=flow)
            extracted_path.path_cost = compute_path_physical_cost(
                network, flow, extracted_path, objective_spec
            )
            try:
                validate_path(network, flow, extracted_path)
            except Exception as exc:
                raise DirectILPExtractionError(
                    str(exc),
                    _path_diagnostics(flow, placement, dst_time_node, segment_routing),
                ) from exc
            extracted_paths[r] = extracted_path

        if objective_mode == "acceptance_only":
            physical_cost = sum(path.path_cost for path in extracted_paths.values())
        else:
            physical_cost = objective_spec.acceptance_reward * accepted - objective
        if physical_cost < 0.0 and physical_cost > -1e-6:
            physical_cost = 0.0
    result_extraction_runtime = time.perf_counter() - result_extraction_start

    status_name = _status_name(model.Status)
    global_original_integer_optimal = model.Status == GRB.OPTIMAL
    explicit_result = ILPResult(
        accepted_count=accepted,
        rejected_count=len(flows) - accepted,
        normalized_physical_cost=physical_cost,
        scalarized_objective=objective,
        has_incumbent=has_incumbent,
        hit_time_limit=model.Status == GRB.TIME_LIMIT,
        solver_status=model.Status,
        solver_status_name=status_name,
        termination_reason=status_name,
        runtime=RuntimeBreakdown(total_runtime=model.Runtime),
        optimality=OptimalityClaims(
            global_original_integer_optimal=global_original_integer_optimal,
        ),
        metadata={
            "best_bound": best_bound,
            "final_mip_gap": final_mip_gap,
            "node_count": model.NodeCount,
            "num_variables": model.NumVars,
            "num_constraints": model.NumConstrs,
            "sol_count": sol_count,
            "warm_start": warm_start_metadata,
            "objective_mode": objective_mode,
            "enforce_simple_routes": enforce_simple_routes,
            "effective_solver_time_limit_seconds": effective_solver_time_limit,
            "configured_threads": threads,
            "configured_gurobi_seed": gurobi_seed,
            "configured_mip_gap": mip_gap,
            "configured_mip_gap_abs": mip_gap_abs,
            "time_to_first_feasible_incumbent_seconds": callback_state["time_to_first_feasible_incumbent_seconds"],
            "time_to_best_incumbent_seconds": callback_state["time_to_best_incumbent_seconds"],
            "acceptance_early_stop": early_stop_metadata,
            "runtime_breakdown": {
                "model_build_runtime_seconds": model_build_runtime,
                "optimization_runtime_seconds": model.Runtime,
                "result_extraction_runtime_seconds": result_extraction_runtime,
                "warm_start_application_runtime_seconds": warm_start_application_runtime,
            },
        },
    )

    return ILPSolution(
        objective=objective,
        accepted=accepted,
        placements=placements,
        routings=routings,
        accepted_flows=accepted_flows,
        runtime=model.Runtime,
        status=model.Status,
        model=model,
        status_name=status_name,
        has_incumbent=has_incumbent,
        global_original_integer_optimal=global_original_integer_optimal,
        hit_time_limit=model.Status == GRB.TIME_LIMIT,
        best_bound=best_bound,
        final_mip_gap=final_mip_gap,
        node_count=model.NodeCount,
        num_variables=model.NumVars,
        num_constraints=model.NumConstrs,
        sol_count=sol_count,
        physical_cost=physical_cost,
        result=explicit_result,
    )


def _status_name(status_code: int) -> str:
    for name in (
        "LOADED", "OPTIMAL", "INFEASIBLE", "INF_OR_UNBD", "UNBOUNDED",
        "CUTOFF", "ITERATION_LIMIT", "NODE_LIMIT", "TIME_LIMIT",
        "SOLUTION_LIMIT", "INTERRUPTED", "NUMERIC", "SUBOPTIMAL",
        "INPROGRESS", "USER_OBJ_LIMIT", "WORK_LIMIT", "MEM_LIMIT",
    ):
        if getattr(GRB, name, None) == status_code:
            return name
    return str(status_code)


def _segment_diagnostics(
    flow: Flow,
    segment_index: int,
    start: TimeNode,
    end: TimeNode,
    selected_edges: List[TimeEdge],
    placement: Mapping[int, TimeNode],
    dst_time_node: TimeNode,
) -> Dict:
    outgoing: Dict[TimeNode, List[TimeEdge]] = {}
    incoming: Dict[TimeNode, List[TimeEdge]] = {}
    nodes = set()
    for edge in selected_edges:
        outgoing.setdefault(edge[0], []).append(edge)
        incoming.setdefault(edge[1], []).append(edge)
        nodes.add(edge[0])
        nodes.add(edge[1])
    branches = {
        str(node): {
            "outdegree": len(outgoing.get(node, [])),
            "indegree": len(incoming.get(node, [])),
        }
        for node in sorted(nodes)
        if len(outgoing.get(node, [])) > 1 or len(incoming.get(node, [])) > 1
    }
    return {
        "flow_id": flow.flow_id,
        "source": flow.flow_src_time_node,
        "destination_node": flow.flow_dst_node,
        "arrival": flow.flow_src_time_node[1],
        "deadline": flow.flow_dst_max_time,
        "flow_sfc": list(flow.flow_sfc),
        "flow_extended_sfc": list(flow.flow_extended_sfc),
        "flow_virtual_links": list(flow.flow_virtual_links),
        "segment_index": segment_index,
        "segment_start": start,
        "segment_end": end,
        "stage_placement": dict(placement),
        "dst_time_node": dst_time_node,
        "selected_edges": list(selected_edges),
        "node_degrees": {
            str(node): {
                "outdegree": len(outgoing.get(node, [])),
                "indegree": len(incoming.get(node, [])),
            }
            for node in sorted(nodes)
        },
        "branches": branches,
    }


def _path_diagnostics(
    flow: Flow,
    placement: Mapping[int, TimeNode],
    dst_time_node: TimeNode,
    segment_routing: Mapping[int, List[TimeEdge]],
) -> Dict:
    diagnostics = {
        "flow_id": flow.flow_id,
        "source": flow.flow_src_time_node,
        "destination_node": flow.flow_dst_node,
        "arrival": flow.flow_src_time_node[1],
        "deadline": flow.flow_dst_max_time,
        "flow_sfc": list(flow.flow_sfc),
        "flow_extended_sfc": list(flow.flow_extended_sfc),
        "flow_virtual_links": list(flow.flow_virtual_links),
        "stage_placement": dict(placement),
        "dst_time_node": dst_time_node,
        "segments": {},
    }
    milestones = [flow.flow_src_time_node]
    for stage in flow.processing_stages:
        milestones.append(placement.get(stage.stage_index))
    milestones.append(dst_time_node)
    for segment_index, edges in segment_routing.items():
        start = milestones[segment_index] if segment_index < len(milestones) else None
        end = milestones[segment_index + 1] if segment_index + 1 < len(milestones) else None
        diagnostics["segments"][segment_index] = _segment_diagnostics(
            flow,
            segment_index,
            start,
            end,
            list(edges),
            placement,
            dst_time_node,
        )
    return diagnostics


def _build_warm_start_all_accepted_solution(
    network: Network,
    flows: List[Flow],
    objective_spec: ObjectiveSpec,
    warm_start_paths: Mapping[int, Path],
    warm_start_metadata: Dict,
    early_stop_metadata: Dict,
    model: Model,
) -> ILPSolution:
    accepted_flows = {flow.flow_id: 1 for flow in flows}
    placements: Dict[int, Dict[int, TimeNode]] = {}
    routings: Dict[int, List[TimeEdge]] = {}
    physical_cost = 0.0
    for flow in flows:
        path = warm_start_paths[flow.flow_id]
        placements[flow.flow_id] = dict(path.path_stage_placement)
        routing = []
        for segment_index in sorted(path.path_segment_routing):
            routing.extend(path.path_segment_routing[segment_index])
        routings[flow.flow_id] = routing
        physical_cost += path.path_cost
    accepted = len(flows)
    objective = objective_spec.acceptance_reward * accepted - physical_cost
    metadata = {
        "best_bound": None,
        "final_mip_gap": None,
        "node_count": 0,
        "num_variables": model.NumVars,
        "num_constraints": model.NumConstrs,
        "sol_count": 0,
        "warm_start": warm_start_metadata,
        "acceptance_early_stop": early_stop_metadata,
        "runtime_breakdown": {
            "model_build_runtime_seconds": early_stop_metadata.get("model_build_runtime_seconds"),
            "optimization_runtime_seconds": 0.0,
            "result_extraction_runtime_seconds": 0.0,
            "warm_start_application_runtime_seconds": warm_start_metadata.get("application_runtime_seconds"),
        },
    }
    explicit_result = ILPResult(
        accepted_count=accepted,
        rejected_count=0,
        normalized_physical_cost=physical_cost,
        scalarized_objective=objective,
        has_incumbent=True,
        hit_time_limit=False,
        solver_status=None,
        solver_status_name="ACCEPTANCE_CERTIFIED",
        termination_reason="ALL_FLOWS_ACCEPTED_WARM_START",
        runtime=RuntimeBreakdown(total_runtime=0.0),
        optimality=OptimalityClaims(global_original_integer_optimal=False),
        metadata=metadata,
    )
    return ILPSolution(
        objective=objective,
        accepted=accepted,
        placements=placements,
        routings=routings,
        accepted_flows=accepted_flows,
        runtime=0.0,
        status=None,
        model=model,
        status_name="ACCEPTANCE_CERTIFIED",
        has_incumbent=True,
        global_original_integer_optimal=False,
        hit_time_limit=False,
        best_bound=None,
        final_mip_gap=None,
        node_count=0,
        num_variables=model.NumVars,
        num_constraints=model.NumConstrs,
        sol_count=0,
        physical_cost=physical_cost,
        result=explicit_result,
    )


def _apply_warm_start(model, x, y, z, network: Network, flows: List[Flow], warm_start_paths: Optional[Mapping[int, Path]]) -> Dict:
    metadata = {
        "supplied": warm_start_paths is not None,
        "complete": False,
        "accepted_count": 0,
        "gurobi_start_status": None,
        "gurobi_start_status_reason": "Gurobi does not expose a simple accepted/rejected start flag before optimize.",
    }
    if warm_start_paths is None:
        return metadata

    flow_by_id = {flow.flow_id: flow for flow in flows}
    selected_paths = {}
    for flow_id, path in warm_start_paths.items():
        if flow_id not in flow_by_id:
            raise ValueError(f"Warm start references unknown flow ID {flow_id}.")
        if path.path_is_empty:
            continue
        validate_path(network, flow_by_id[flow_id], path)
        selected_paths[flow_id] = path
    validate_solution_capacity(network, flows, selected_paths)

    for var in x.values():
        var.Start = 0.0
    for var in y.values():
        var.Start = 0.0
    for var in z.values():
        var.Start = 0.0

    warm_by_flow = dict(warm_start_paths)
    complete = True
    for flow in flows:
        r = flow.flow_id
        path = warm_by_flow.get(r)
        if path is None or path.path_is_empty:
            x[r].Start = 0.0
            continue
        complete = complete and path.path_node_usage is not None and path.path_edge_usage is not None
        x[r].Start = 1.0
        metadata["accepted_count"] += 1
        src_n, src_t = flow.flow_src_time_node
        y[r, src_n, src_t, 0].Start = 1.0
        for stage in flow.processing_stages:
            n, t = path.path_stage_placement[stage.stage_index]
            y[r, n, t, stage.stage_index + 1].Start = 1.0
        dst_time_node = _warm_start_destination_time_node(flow, path)
        if dst_time_node is not None:
            y[r, dst_time_node[0], dst_time_node[1], len(flow.flow_extended_sfc) - 1].Start = 1.0
        else:
            complete = False
        for segment_index, edges in path.path_segment_routing.items():
            for edge in edges:
                (u, t1), (v, t2) = edge
                if (r, u, t1, v, t2, segment_index) in z:
                    z[r, u, t1, v, t2, segment_index].Start = 1.0
                else:
                    complete = False
    metadata["complete"] = complete
    return metadata


def _warm_start_destination_time_node(flow: Flow, path: Path) -> Optional[TimeNode]:
    final_segment = len(flow.processing_stages)
    route = path.path_segment_routing.get(final_segment, [])
    if route:
        return route[-1][1]
    if flow.processing_stages:
        return path.path_stage_placement.get(len(flow.processing_stages) - 1)
    return flow.flow_src_time_node


def _order_segment_edges(edges: List[TimeEdge]) -> List[TimeEdge]:
    if len(edges) <= 1:
        return edges

    successors = {edge[0]: edge for edge in edges}
    end_nodes = {edge[1] for edge in edges}
    starts = [edge[0] for edge in edges if edge[0] not in end_nodes]
    if not starts:
        return sorted(edges)

    ordered: List[TimeEdge] = []
    current = starts[0]
    while current in successors:
        edge = successors[current]
        ordered.append(edge)
        current = edge[1]

    if len(ordered) != len(edges):
        remaining = [edge for edge in edges if edge not in ordered]
        ordered.extend(sorted(remaining))
    return ordered
