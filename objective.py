"""Canonical objective helpers.

The scalar objective is

    acceptance_reward * accepted_count - normalized_physical_cost

where normalized physical cost is the convex combination of total processing
usage divided by total processing capacity and total communication usage
divided by total finite communication capacity. Storage edges have zero
communication physical cost and are excluded from the denominator.
"""

import math
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple

from data_model import Flow, Network, Path, TimeEdge, TimeNode


@dataclass(frozen=True)
class CapacityDenominators:
    """Positive denominators for normalized physical resource cost."""

    total_processing_capacity: float
    total_communication_capacity: float


@dataclass(frozen=True)
class ObjectiveSpec:
    """Canonical scalarized lexicographic objective specification.

    With feasible aggregate capacity constraints,
    processing_usage / processing_capacity <= 1 and
    communication_usage / communication_capacity <= 1. If
    ``resource_weight`` is in [0, 1], the weighted sum is therefore at most 1.
    The default acceptance reward 2.0 is strictly larger than the maximum
    possible physical-cost difference, so accepting one additional flow always
    dominates physical-cost savings.
    """

    resource_weight: float
    acceptance_reward: float = 2.0


def objective_spec_from_network(network: Network) -> ObjectiveSpec:
    """Build the default objective spec from the network resource weight."""

    return ObjectiveSpec(resource_weight=network.resource_weight)


def compute_capacity_denominators(network: Network) -> CapacityDenominators:
    """Compute positive finite denominators for normalized physical cost.

    Raises ValueError when finite processing or communication capacity is
    nonpositive. Storage edges are recognized by infinite capacity and excluded
    from the communication denominator and communication physical cost.
    """

    total_processing = sum(network.time_node_capacity.values())
    total_communication = 0.0
    for edge in network.time_edges:
        capacity = network.time_edge_capacity[edge]
        if capacity == math.inf:
            continue
        if edge[0][1] != edge[1][1]:
            continue
        total_communication += capacity

    if total_processing <= 0.0:
        raise ValueError(
            "Total finite processing capacity must be positive for normalized objective."
        )
    if total_communication <= 0.0:
        raise ValueError(
            "Total finite communication capacity must be positive for normalized objective."
        )

    network.total_time_node_capacity = total_processing
    network.total_comm_edge_capacity = total_communication
    return CapacityDenominators(total_processing, total_communication)


def validate_objective_spec(
    objective_spec: ObjectiveSpec,
    denominators: Optional[CapacityDenominators] = None,
) -> None:
    """Validate objective parameters and, when provided, denominators."""

    if not 0.0 <= objective_spec.resource_weight <= 1.0:
        raise ValueError("Objective resource_weight must be in [0, 1].")
    if objective_spec.acceptance_reward <= 1.0:
        raise ValueError(
            "acceptance_reward must be greater than 1.0 to dominate feasible "
            "normalized physical-cost differences."
        )
    if denominators is not None:
        if denominators.total_processing_capacity <= 0.0:
            raise ValueError("Processing capacity denominator must be positive.")
        if denominators.total_communication_capacity <= 0.0:
            raise ValueError("Communication capacity denominator must be positive.")


def accepted_path_value(path_cost: float, objective_spec: ObjectiveSpec) -> float:
    """Objective value contribution of one accepted path."""

    validate_objective_spec(objective_spec)
    return objective_spec.acceptance_reward - path_cost


def empty_path_value(objective_spec: Optional[ObjectiveSpec] = None) -> float:
    """Rejected-flow path contribution. Rejection always contributes zero."""

    if objective_spec is not None:
        validate_objective_spec(objective_spec)
    return 0.0


def compute_usage_physical_cost(
    node_usage: Mapping[TimeNode, float],
    edge_usage: Mapping[Tuple[int, int, int], float],
    flow_demand: float,
    denominators: CapacityDenominators,
    objective_spec: ObjectiveSpec,
) -> float:
    """Compute canonical normalized cost from raw path usage coefficients.

    ``node_usage`` stores processing coefficients, not demand-scaled usage.
    ``edge_usage`` stores communication traversal counts keyed by
    ``(u, v, departure_time)``. Callers with full path usage must filter out
    storage edges before calling this helper.
    """

    validate_objective_spec(objective_spec, denominators)
    processing_usage = flow_demand * sum(node_usage.values())
    communication_usage = flow_demand * sum(edge_usage.values())
    return (
        objective_spec.resource_weight
        * processing_usage
        / denominators.total_processing_capacity
        + (1.0 - objective_spec.resource_weight)
        * communication_usage
        / denominators.total_communication_capacity
    )


def compute_path_physical_cost(
    network: Network,
    flow: Flow,
    path: Path,
    objective_spec: ObjectiveSpec,
    denominators: Optional[CapacityDenominators] = None,
) -> float:
    """Compute one path's canonical normalized physical resource cost."""

    if path.path_is_empty:
        return 0.0
    denominators = denominators or compute_capacity_denominators(network)
    if path.path_node_usage is None or path.path_edge_usage is None:
        path.finalize_usage(flow=flow)
    communication_usage = {
        key: usage
        for key, usage in path.path_edge_usage.items()
        if ((key[0], key[2]), (key[1], key[2])) in network.time_edge_capacity
        and network.time_edge_capacity[((key[0], key[2]), (key[1], key[2]))] != math.inf
    }
    return compute_usage_physical_cost(
        path.path_node_usage,
        communication_usage,
        flow.flow_demand,
        denominators,
        objective_spec,
    )


def compute_reduced_cost_from_usage(
    flow: Flow,
    path_cost: float,
    node_usage: Mapping[TimeNode, float],
    edge_usage: Mapping[Tuple[int, int, int], float],
    duals: Dict,
    objective_spec: ObjectiveSpec,
) -> float:
    """Compute maximization reduced cost for a non-empty path.

    RMP capacity rows are ``<=`` constraints. This helper follows the existing
    convention used by the Gurobi max RMP: positive reduced cost means the
    column can improve the current LP relaxation.
    """

    validate_objective_spec(objective_spec)
    if "flow" not in duals or flow.flow_id not in duals["flow"]:
        raise ValueError(f"Missing required flow convexity dual for flow ID {flow.flow_id}.")
    reduced_cost = objective_spec.acceptance_reward - path_cost - duals["flow"][flow.flow_id]
    for time_node, usage in node_usage.items():
        pi = duals["node"].get(time_node, 0.0)
        reduced_cost -= pi * flow.flow_demand * usage
    for (u, v, t), usage in edge_usage.items():
        mu = duals["edge"].get((u, v, t), 0.0)
        reduced_cost -= mu * flow.flow_demand * usage
    return reduced_cost


def prove_normalized_cost_bound() -> str:
    """Return the normalized-cost bound proof used for documentation."""

    return (
        "For any feasible solution, aggregate processing usage is bounded by "
        "total processing capacity and aggregate communication usage on finite "
        "same-time communication edges is bounded by total communication "
        "capacity. Storage edges have infinite capacity and zero physical "
        "communication cost, so they are excluded. With resource_weight in "
        "[0, 1], the normalized physical cost is a convex combination of two "
        "ratios in [0, 1], hence it is in [0, 1]."
    )

