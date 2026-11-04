# sub_problem.py

from typing import Dict, List, Optional

from gurobipy import Model, GRB, quicksum
import math
from data_model import Path, TimeEdge, TimeNode, reconstruct_ordered_route
from objective import (
    ObjectiveSpec,
    compute_capacity_denominators,
    compute_path_physical_cost,
    objective_spec_from_network,
    validate_objective_spec,
)
from deterministic_seeds import normalize_gurobi_seed


def _status_name(status_code):
    for name in (
        "LOADED", "OPTIMAL", "INFEASIBLE", "INF_OR_UNBD", "UNBOUNDED",
        "CUTOFF", "ITERATION_LIMIT", "NODE_LIMIT", "TIME_LIMIT",
        "SOLUTION_LIMIT", "INTERRUPTED", "NUMERIC", "SUBOPTIMAL",
        "INPROGRESS", "USER_OBJ_LIMIT", "WORK_LIMIT", "MEM_LIMIT",
    ):
        if getattr(GRB, name, None) == status_code:
            return name
    return str(status_code)


def _build_metadata(model, stop_early, reduced_cost_tolerance):
    status_code = model.Status
    sol_count = model.SolCount
    has_solution = sol_count > 0
    objective = model.ObjVal if has_solution else None
    metadata = {
        "status_code": status_code,
        "status_name": _status_name(status_code),
        "runtime": model.Runtime,
        "sol_count": sol_count,
        "objective": objective,
        "has_solution": has_solution,
        "exact_pricing_optimal": status_code == GRB.OPTIMAL,
        "hit_time_limit": status_code == GRB.TIME_LIMIT,
        "stop_early": stop_early,
        "positive_reduced_cost_found": (
            objective is not None and objective > reduced_cost_tolerance
        ),
        "exact_pricing_call_certified": (not stop_early and status_code == GRB.OPTIMAL),
    }
    try:
        metadata["objective_bound"] = model.ObjBound
    except Exception:
        metadata["objective_bound"] = None
    return metadata


def solve_sp(
    flow,
    network,
    duals,
    objective_spec=None,
    stop_early=False,
    enforce_individual_capacity=True,
    time_limit: Optional[float] = None,
    threads: Optional[int] = None,
    exact_mip_gap: float = 0.0,
    exact_mip_gap_abs: float = 1e-7,
    reduced_cost_tolerance: float = 1e-6,
    gurobi_seed: Optional[int] = None,
):
    obj_val, y_results, z_results, _ = solve_sp_detailed(
        flow=flow,
        network=network,
        duals=duals,
        objective_spec=objective_spec,
        stop_early=stop_early,
        enforce_individual_capacity=enforce_individual_capacity,
        time_limit=time_limit,
        threads=threads,
        exact_mip_gap=exact_mip_gap,
        exact_mip_gap_abs=exact_mip_gap_abs,
        reduced_cost_tolerance=reduced_cost_tolerance,
        gurobi_seed=gurobi_seed,
    )
    return obj_val, y_results, z_results


def solve_sp_detailed(
    flow,
    network,
    duals,
    objective_spec=None,
    stop_early=False,
    enforce_individual_capacity=True,
    time_limit: Optional[float] = None,
    threads: Optional[int] = None,
    exact_mip_gap: float = 0.0,
    exact_mip_gap_abs: float = 1e-7,
    reduced_cost_tolerance: float = 1e-6,
    gurobi_seed: Optional[int] = None,
):
    """
    Pricing problem for a single flow r.
    Goal: find a new path with positive reduced cost.
    stop_early: If set to True, Gurobi will stop as soon as it finds the first column with reduced cost > 0.
    enforce_individual_capacity: If True, prune paths that cannot fit this
                                 single flow on individual node/edge capacity.
    """
    objective_spec = objective_spec or objective_spec_from_network(network)
    denominators = compute_capacity_denominators(network)
    validate_objective_spec(objective_spec, denominators)

    model = Model("SP")
    model.Params.OutputFlag = 0  # Keep the console clean.
    if threads is not None:
        model.Params.Threads = threads
    if time_limit is not None and time_limit > 0:
        model.Params.TimeLimit = time_limit
    if gurobi_seed is not None:
        model.Params.Seed = normalize_gurobi_seed(gurobi_seed)

    if stop_early:
        # Stop as soon as one feasible path is found (no need to maximize the reduced cost of the path)
        model.Params.SolutionLimit = 1
        # Optional: set a relatively large MIP gap tolerance to further speed up solving
        model.Params.MIPGap = 0.1
    else:
        model.Params.MIPGap = exact_mip_gap
        model.Params.MIPGapAbs = exact_mip_gap_abs

    # =========================
    # 1. Variables
    # =========================

    # y[n,t,k] : whether the k-th VNF is at time node (n, t)
    y = {}
    for (n, t) in network.time_nodes:
        for k in range(len(flow.flow_extended_sfc)):
            y[n, t, k] = model.addVar(vtype=GRB.BINARY,
                                      name=f"y_{n}_{t}_{k}")

    # z[(u, t1), (v, t2), l] : whether the l-th virtual link is at time edge (u, t1), (v, t2)
    z = {}
    for (u, t1), (v, t2) in network.time_edges:
        for l in range(len(flow.flow_virtual_links)):
            z[(u, t1), (v, t2), l] = model.addVar(
                vtype=GRB.BINARY,
                name=f"z_{u}_{t1}_{v}_{t2}_{l}"
            )

    model.update()

    # =========================
    # 2. Objective (reduced cost)
    # =========================

    if "flow" not in duals or flow.flow_id not in duals["flow"]:
        raise ValueError(f"Missing required flow convexity dual for flow ID {flow.flow_id}.")
    dual_flow = duals["flow"][flow.flow_id]

    obj = []

    # constant part
    obj.append(objective_spec.acceptance_reward - dual_flow)

    # node part
    for (n, t) in network.time_nodes:
        pi = duals["node"].get((n, t), 0.0)

        for k in range(len(flow.flow_extended_sfc)):
            base = flow.flow_demand * flow.flow_extended_vnf_cost[k]

            cost = (
                    objective_spec.resource_weight
                    * base
                    / denominators.total_processing_capacity
                    + pi * base
            )

            obj.append(-cost * y[n, t, k])

    # edge part
    for (u, t1), (v, t2) in network.time_edges:
        t = t1
        mu = duals["edge"].get((u, v, t), 0.0)

        for l in range(len(flow.flow_virtual_links)):
            base = flow.flow_demand

            if t1 == t2:
                # Communication edge: includes bandwidth-related physical cost + dual penalty
                cost = (
                    (1 - objective_spec.resource_weight)
                    * base
                    / denominators.total_communication_capacity
                    + mu * base
                )
            else:
                # Storage edge: physical cost is 0, only dual penalty
                cost = mu * base

            obj.append(-cost * z[(u, t1), (v, t2), l])

    model.setObjective(quicksum(obj), GRB.MAXIMIZE)

    # =========================
    # 3. Constraints
    # =========================

    # (1) source constraint
    src_n, src_t = flow.flow_src_time_node
    if (src_n, src_t, 0) not in y:
        raise ValueError(f"Flow {flow.flow_id} source time-node is not in the pricing graph.")
    model.addConstr(y[src_n, src_t, 0] == 1)

    # (2) destination constraint
    dst_n = flow.flow_dst_node
    dst_mt = flow.flow_dst_max_time
    dst_terms = [
        y[dst_n, t, len(flow.flow_extended_sfc) - 1]
        for t in range(src_t, dst_mt + 1)
        if (dst_n, t, len(flow.flow_extended_sfc) - 1) in y
    ]
    if not dst_terms:
        raise ValueError(f"Flow {flow.flow_id} has no destination candidate in the pricing graph.")
    model.addConstr(quicksum(dst_terms) == 1)

    # (3) placement constraint
    for k in range(len(flow.flow_extended_sfc)):
        model.addConstr(
            quicksum(y[n, t, k]
                     for (n, t) in network.time_nodes
                     ) == 1
        )

    # # (4) flow split avoid constraint
    # for l in range(len(flow.flow_virtual_links)):
    #     for (n, t) in network.time_nodes:
    #         expr = quicksum(
    #             z[(n, t), (m, tt), l]
    #             for (m, tt) in network.out_time_edges.get((n, t), [])
    #             if (m != n and tt == t) or (m == n and tt == t + 1)
    #         )
    #
    #         model.addConstr(expr <= 1)
    #
    # for l in range(len(flow.flow_virtual_links)):
    #     for (n, t) in network.time_nodes:
    #         expr = quicksum(
    #             z[(m, tt), (n, t), l]
    #             for (m, tt) in network.in_time_edges.get((n, t), [])
    #             if (m != n and tt == t) or (m == n and tt == t - 1)
    #         )
    #
    #         model.addConstr(expr <= 1)
    #
    # # (5) flow conservation constraint
    # for l in range(len(flow.flow_virtual_links)):
    #     for (n, t) in network.time_nodes:
    #         # incoming
    #         incoming = quicksum(
    #             z[(m, tt), (n, t), l]
    #             for (m, tt) in network.in_time_edges.get((n, t), [])
    #             if (m != n and tt == t) or (m == n and tt == t - 1)
    #         )
    #
    #         # outgoing
    #         outgoing = quicksum(
    #             z[(n, t), (m, tt), l]
    #             for (m, tt) in network.out_time_edges.get((n, t), [])
    #             if (m != n and tt == t) or (m == n and tt == t + 1)
    #         )
    #
    #         model.addConstr(
    #             incoming - outgoing == y[n, t, l + 1] - y[n, t, l]
    #         )

    # (4) flow split avoid & (5) flow conservation constraints
    for l in range(len(flow.flow_virtual_links)):
        for (n, t) in network.time_nodes:
            # incoming edges (already valid)
            incoming_edges = network.in_time_edges.get((n, t), [])

            # outgoing edges (already valid)
            outgoing_edges = network.out_time_edges.get((n, t), [])

            incoming = quicksum(z[(m, tt), (n, t), l] for (m, tt) in incoming_edges)
            outgoing = quicksum(z[(n, t), (m, tt), l] for (m, tt) in outgoing_edges)

            # flow split avoidance
            model.addConstr(outgoing <= 1)
            model.addConstr(incoming <= 1)

            # flow conservation
            model.addConstr(incoming - outgoing == y[n, t, l + 1] - y[n, t, l])

    # (6) Optional per-flow feasibility pruning. The RMP duals still drive the
    # reduced-cost objective; these constraints only avoid individually
    # impossible columns.
    if enforce_individual_capacity:
        for (n, t) in network.time_nodes:
            model.addConstr(
                quicksum(
                    flow.flow_demand *
                    flow.flow_extended_vnf_cost[k] *
                    y[n, t, k]
                    for k in range(len(flow.flow_extended_sfc))
                ) <= network.time_node_capacity[(n, t)]
            )

        for (u, t1), (v, t2) in network.time_edges:
            cap = network.time_edge_capacity[((u, t1), (v, t2))]
            if cap != math.inf:
                model.addConstr(
                    quicksum(
                        flow.flow_demand *
                        z[(u, t1), (v, t2), l]
                        for l in range(len(flow.flow_virtual_links))
                    ) <= cap
                )

    model.optimize()

    # =========================
    # 4. return new column if positive reduced cost
    # =========================

    metadata = _build_metadata(model, stop_early, reduced_cost_tolerance)

    # As long as the model has a solution (whether OPTIMAL or stopped due to SolutionLimit)
    if model.SolCount > 0:
        # Get the current best (or first found) objective value
        current_obj = model.ObjVal

        # The reduced cost must be greater than 0 to be meaningful (use 1e-6 tolerance for floating-point errors)
        if current_obj > reduced_cost_tolerance:
            # Only extract variables with X close to 1. Without this filtering,
            # transmitting dictionaries containing tens of thousands of 0.0 values
            # will make multiprocessing serialization (pickle) extremely slow.
            y_results = {k: 1.0 for k, v in y.items() if v.X > 0.5}
            z_results = {k: 1.0 for k, v in z.items() if v.X > 0.5}

            return current_obj, y_results, z_results, metadata

    # If no solution is found, or reduced cost <= 0
    return None, None, None, metadata


def _extract_milestones(flow, y_results) -> List[TimeNode]:
    milestones: List[Optional[TimeNode]] = [None] * len(flow.flow_extended_sfc)
    for (n, t, k), val in y_results.items():
        if val <= 0.5:
            continue
        if milestones[k] is not None:
            raise ValueError(f"Multiple placements selected for extended index {k}.")
        milestones[k] = (n, t)
    missing = [k for k, tn in enumerate(milestones) if tn is None]
    if missing:
        raise ValueError(f"Missing placements for extended indices {missing}.")
    return [tn for tn in milestones if tn is not None]


def extract_path_from_sp(
    flow,
    network,
    y_results,
    z_results,
    path_id,
    objective_spec=None,
):
    """
    At this point, the inputs are y_results and z_results (Dict[tuple, float])
    """
    objective_spec = objective_spec or objective_spec_from_network(network)
    placement: Dict[int, TimeNode] = {}
    segment_routing: Dict[int, List[TimeEdge]] = {}
    milestones = _extract_milestones(flow, y_results)

    for (n, t, k), val in y_results.items():
        if val > 0.5:
            stage_index = flow.stage_index_for_extended_index(k)
            if stage_index is not None:
                placement[stage_index] = (n, t)

    for segment_index in range(len(flow.flow_virtual_links)):
        selected_edges = [
            (u_t1, v_t2)
            for (u_t1, v_t2, l), val in z_results.items()
            if l == segment_index and val > 0.5
        ]
        segment_routing[segment_index] = reconstruct_ordered_route(
            selected_edges,
            milestones[segment_index],
            milestones[segment_index + 1],
        )

    # 3. Create Path object
    new_path = Path(
        flow_id=flow.flow_id,
        path_id=path_id,
        path_is_empty=False,
        path_stage_placement=placement,
        path_segment_routing=segment_routing,
        path_cost=0.0,
    )
    new_path.finalize_usage(flow=flow)
    new_path.path_cost = compute_path_physical_cost(
        network, flow, new_path, objective_spec
    )

    return new_path

