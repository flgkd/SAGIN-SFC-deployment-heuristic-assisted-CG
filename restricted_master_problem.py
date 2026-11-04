# restricted_master_problem.py

from typing import Dict, Optional

from gurobipy import Model, GRB, quicksum
import math
from objective import (
    ObjectiveSpec,
    accepted_path_value,
    compute_capacity_denominators,
    empty_path_value,
    objective_spec_from_network,
    validate_objective_spec,
)
from result_schema import OptimalityClaims, RMPResult, RuntimeBreakdown
from validation import validate_path
from deterministic_seeds import normalize_gurobi_seed


def solve_rmp(
    paths,
    flows,
    network,
    objective_spec: Optional[ObjectiveSpec] = None,
    is_integer=False,
    time_limit: Optional[float] = None,
    mip_gap: Optional[float] = None,
    threads: Optional[int] = None,
    verbose: bool = False,
    model_name: Optional[str] = None,
    gurobi_seed: Optional[int] = None,
    collect_mip_progress: bool = False,
    mip_start_values: Optional[Dict[int, float]] = None,
):
    """
    Solve the Restricted Master Problem.

    :param is_integer: Boolean flag.
                       If False (default), relax w[p] to CONTINUOUS (LP) and return duals.
                       If True, constrain w[p] to BINARY (ILP) and skip duals extraction.
    """
    objective_spec = objective_spec or objective_spec_from_network(network)
    denominators = compute_capacity_denominators(network)
    validate_objective_spec(objective_spec, denominators)

    flow_dict = {flow.flow_id: flow for flow in flows}
    for p in paths:
        if p.flow_id not in flow_dict:
            raise ValueError(
                f"Path {p.path_id} references unknown flow_id {p.flow_id}."
            )
        if p.path_node_usage is None or p.path_edge_usage is None:
            raise ValueError(f"Path {p.path_id} for flow {p.flow_id} has missing finalized usage.")
        validate_path(network, flow_dict[p.flow_id], p)

    model = Model(model_name or "RMP")

    # =========================
    # 1. Variables
    # =========================
    # w[p] corresponds to w^r_p in the formula
    w = {}

    # Determine variable type based on the boolean flag
    var_type = GRB.BINARY if is_integer else GRB.CONTINUOUS

    for p in paths:
        # LP column-generation variables must not have an explicit upper bound.
        # The flow convexity equality already implies w <= 1, and adding the
        # redundant variable bound can move the selected column's marginal value
        # from the flow row Pi into the variable reduced cost, breaking pricing.
        upper_bound = 1.0 if is_integer else GRB.INFINITY
        w[p.path_id] = model.addVar(lb=0.0, ub=upper_bound,
                                    vtype=var_type,
                                    name=f"w_{p.path_id}")

    model.update()

    # =========================
    # 2. Objective
    # =========================
    # Real path: accepted-flow reward minus physical cost.
    # Empty path: rejected flow, zero objective contribution.
    obj_coeff = {}
    for p in paths:
        if p.path_is_empty:
            obj_coeff[p.path_id] = empty_path_value(objective_spec)
        else:
            obj_coeff[p.path_id] = accepted_path_value(p.path_cost, objective_spec)

    model.setObjective(
        quicksum(
            obj_coeff[p.path_id] * w[p.path_id]
            for p in paths
        ),
        GRB.MAXIMIZE
    )

    # =========================
    # 3. Flow constraint
    # =========================
    # Constraint 1: Each flow r must select exactly one path (including the empty path).
    # sum(w^r_p) = 1, for all r
    flow_constraints = {}

    for flow in flows:
        flow_constraints[flow.flow_id] = model.addConstr(
            quicksum(
                w[p.path_id] for p in paths if p.flow_id == flow.flow_id
            ) == 1,
            name=f"flow_{flow.flow_id}"
        )

    # =========================
    # 4. Node capacity constraint
    # =========================
    # Constraint 2: Need to aggregate the resource usage of all paths on node n according to the logic of b^{p,v}_{n}
    # sum(f_r * delta * b * w) <= c(n^t)
    node_constraints = {}

    for (n, t) in network.time_nodes:
        expr = quicksum(
            flow_dict[p.flow_id].flow_demand *
            p.path_node_usage.get((n, t), 0.0) *
            w[p.path_id]
            for p in paths if not p.path_is_empty
        )

        node_constraints[(n, t)] = model.addConstr(
            expr <= network.time_node_capacity[(n, t)],
            name=f"node_{n}_{t}"
        )

    # =========================
    # 5. Edge capacity constraint
    # =========================
    # Constraint 3: Need to aggregate the resource usage of all paths on edge e according to the logic of c^{p,h}_{e}
    # sum(f_r * c_p * w) <= d(e^t)
    edge_constraints = {}

    for edge in network.time_edges:
        capacity = network.time_edge_capacity[edge]

        if capacity == math.inf:
            continue

        (u, t1), (v, t2) = edge
        t = t1

        expr = quicksum(
            flow_dict[p.flow_id].flow_demand *
            p.path_edge_usage.get((u, v, t), 0.0) *
            w[p.path_id]
            for p in paths if not p.path_is_empty
        )

        edge_constraints[(u, v, t)] = model.addConstr(
            expr <= capacity,
            name=f"edge_{u}_{v}_{t}"
        )

    # =========================
    # 6. Solve
    # =========================
    model.Params.OutputFlag = 1 if verbose else 0
    if time_limit is not None and time_limit > 0:
        model.Params.TimeLimit = time_limit
    if threads is not None:
        model.Params.Threads = threads
    if gurobi_seed is not None:
        model.Params.Seed = normalize_gurobi_seed(gurobi_seed)
    if is_integer and mip_gap is not None:
        model.Params.MIPGap = mip_gap
    if is_integer and mip_start_values:
        for path_id, value in mip_start_values.items():
            if path_id in w:
                w[path_id].Start = float(value)
    mip_incumbent_events = []

    def _mip_progress_callback(model, where):
        if where == GRB.Callback.MIPSOL:
            mip_incumbent_events.append({
                "runtime_seconds": model.cbGet(GRB.Callback.RUNTIME),
                "objective": model.cbGet(GRB.Callback.MIPSOL_OBJ),
            })

    if is_integer and collect_mip_progress:
        model.optimize(_mip_progress_callback)
    else:
        model.optimize()

    status_name = _status_name(model.Status)
    has_incumbent = model.SolCount > 0
    accepted_count = 0
    normalized_physical_cost = None
    scalarized_objective = model.ObjVal if has_incumbent else None
    if has_incumbent:
        normalized_physical_cost = 0.0
        for p in paths:
            if w[p.path_id].X > 0.5 and not p.path_is_empty:
                accepted_count += 1
                normalized_physical_cost += p.path_cost
    rejected_count = len(flows) - accepted_count

    if is_integer:
        first_event = mip_incumbent_events[0] if mip_incumbent_events else None
        if mip_incumbent_events:
            best_event = max(mip_incumbent_events, key=lambda event: event["objective"])
            final_event = mip_incumbent_events[-1]
        else:
            best_event = None
            final_event = None
        return RMPResult(
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            normalized_physical_cost=normalized_physical_cost,
            scalarized_objective=scalarized_objective,
            has_incumbent=has_incumbent,
            hit_time_limit=model.Status == GRB.TIME_LIMIT,
            solver_status=model.Status,
            solver_status_name=status_name,
            termination_reason=status_name or "",
            runtime=RuntimeBreakdown(total_runtime=model.Runtime, final_rmp_runtime=model.Runtime),
            optimality=OptimalityClaims(
                global_original_integer_optimal=False,
                restricted_pool_integer_optimal=model.Status == GRB.OPTIMAL,
            ),
            current_restricted_master_optimal=model.Status == GRB.OPTIMAL,
            metadata={
                "mip_incumbent_events": mip_incumbent_events,
                "first_incumbent_runtime_seconds": first_event["runtime_seconds"] if first_event else None,
                "first_incumbent_objective": first_event["objective"] if first_event else None,
                "best_incumbent_runtime_seconds": best_event["runtime_seconds"] if best_event else None,
                "best_incumbent_objective": best_event["objective"] if best_event else None,
                "final_incumbent_runtime_seconds": final_event["runtime_seconds"] if final_event else None,
                "final_incumbent_objective": final_event["objective"] if final_event else None,
            },
            model=model,
            variables=w,
        )

    # Check the LP solver status before accessing .Pi. Dual values are valid
    # only for an optimal LP relaxation.
    if model.status != GRB.OPTIMAL:
        if verbose:
            print(f"Warning: RMP LP is not optimal! Status code: {model.status}")
        return RMPResult(
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            normalized_physical_cost=normalized_physical_cost,
            scalarized_objective=scalarized_objective,
            has_incumbent=has_incumbent,
            hit_time_limit=model.Status == GRB.TIME_LIMIT,
            solver_status=model.Status,
            solver_status_name=status_name,
            termination_reason=f"LP RMP did not solve to optimality: {status_name}",
            runtime=RuntimeBreakdown(total_runtime=model.Runtime, rmp_lp_runtime=model.Runtime),
            optimality=OptimalityClaims(),
            lp_restricted_master_optimal=False,
            current_restricted_master_optimal=False,
            model=model,
            variables=w,
            duals=None,
        )

    duals = {
        "flow": {},
        "node": {},
        "edge": {}
    }

    for flow_id, constr in flow_constraints.items():
        duals["flow"][flow_id] = constr.Pi

    missing_flow_duals = [
        flow.flow_id for flow in flows if flow.flow_id not in duals["flow"]
    ]
    if missing_flow_duals:
        raise ValueError(f"Missing RMP flow convexity duals for flow IDs {missing_flow_duals}.")

    for key, constr in node_constraints.items():
        duals["node"][key] = constr.Pi

    for key, constr in edge_constraints.items():
        duals["edge"][key] = constr.Pi

    return RMPResult(
        accepted_count=accepted_count,
        rejected_count=rejected_count,
        normalized_physical_cost=normalized_physical_cost,
        scalarized_objective=scalarized_objective,
        has_incumbent=has_incumbent,
        hit_time_limit=False,
        solver_status=model.Status,
        solver_status_name=status_name,
        termination_reason="LP restricted master optimal.",
        runtime=RuntimeBreakdown(total_runtime=model.Runtime, rmp_lp_runtime=model.Runtime),
        optimality=OptimalityClaims(
            global_original_integer_optimal=False,
            lp_column_generation_converged=False,
            lp_column_generation_certified=False,
            restricted_pool_integer_optimal=False,
        ),
        lp_restricted_master_optimal=True,
        current_restricted_master_optimal=True,
        model=model,
        variables=w,
        duals=duals,
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

