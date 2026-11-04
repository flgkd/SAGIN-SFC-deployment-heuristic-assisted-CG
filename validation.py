"""Solver-independent validation for v2 networks, flows, paths, and solutions."""

import math
from types import SimpleNamespace
from typing import Dict, Iterable, List, Mapping, Optional

from data_model import Flow, Network, Path, TimeEdge, TimeNode
from objective import (
    ObjectiveSpec,
    accepted_path_value,
    compute_capacity_denominators,
    compute_path_physical_cost,
    objective_spec_from_network,
    validate_objective_spec,
)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _flow_by_id(flows: Iterable[Flow]) -> Dict[int, Flow]:
    return {flow.flow_id: flow for flow in flows}


def validate_network(network: Network) -> None:
    if not 0.0 <= network.resource_weight <= 1.0:
        raise ValueError(f"resource_weight {network.resource_weight} must be in [0, 1].")

    time_nodes = set(network.time_nodes)
    for time_node in network.time_nodes:
        capacity = network.time_node_capacity.get(time_node)
        if capacity is None:
            raise ValueError(f"Time-node {time_node} has no capacity.")
        if not _is_number(capacity) or not math.isfinite(capacity) or capacity < 0.0:
            raise ValueError(f"Time-node {time_node} capacity {capacity} must be finite and nonnegative.")

    time_edges = set(network.time_edges)
    for edge in network.time_edges:
        if edge[0] not in time_nodes or edge[1] not in time_nodes:
            raise ValueError(f"Time edge {edge} refers to a missing time node.")
        capacity = network.time_edge_capacity.get(edge)
        if capacity is None:
            raise ValueError(f"Time edge {edge} has no capacity.")
        (u, t1), (v, t2) = edge
        is_storage = u == v and t2 == t1 + 1
        is_comm = u != v and t1 == t2
        if is_storage:
            if capacity != math.inf:
                raise ValueError(f"Storage edge {edge} capacity {capacity} must be infinite.")
        elif is_comm:
            if not _is_number(capacity) or not math.isfinite(capacity) or capacity < 0.0:
                raise ValueError(f"Communication edge {edge} capacity {capacity} must be finite and nonnegative.")
        else:
            raise ValueError(f"Time edge {edge} is neither communication nor storage.")

    expected_out: Dict[TimeNode, set] = {}
    expected_in: Dict[TimeNode, set] = {}
    for edge in network.time_edges:
        expected_out.setdefault(edge[0], set()).add(edge[1])
        expected_in.setdefault(edge[1], set()).add(edge[0])
    for node in time_nodes:
        actual_out = set(network.out_time_edges.get(node, []))
        actual_in = set(network.in_time_edges.get(node, []))
        if actual_out != expected_out.get(node, set()):
            raise ValueError(f"out_time_edges adjacency mismatch at node {node}.")
        if actual_in != expected_in.get(node, set()):
            raise ValueError(f"in_time_edges adjacency mismatch at node {node}.")

    denominators = compute_capacity_denominators(network)
    validate_objective_spec(objective_spec_from_network(network), denominators)


def validate_unique_flow_ids(flows: Iterable[Flow]) -> None:
    seen = set()
    for flow in flows:
        if flow.flow_id in seen:
            raise ValueError(f"Duplicate flow ID {flow.flow_id}.")
        seen.add(flow.flow_id)


def validate_flows(flows: Iterable[Flow]) -> None:
    flows = list(flows)
    validate_unique_flow_ids(flows)
    for flow in flows:
        validate_flow(flow)


def validate_flow(flow: Flow, network: Optional[Network] = None) -> None:
    if network is not None:
        src_node, _ = flow.flow_src_time_node
        if src_node not in network.nodes:
            raise ValueError(f"Flow {flow.flow_id} source node {src_node} is not in network.")
        if flow.flow_dst_node not in network.nodes:
            raise ValueError(f"Flow {flow.flow_id} destination node {flow.flow_dst_node} is not in network.")
        if flow.flow_src_time_node not in network.time_nodes:
            raise ValueError(f"Flow {flow.flow_id} source time-node {flow.flow_src_time_node} is not in network.")

    src_time = flow.flow_src_time_node[1]
    if src_time > flow.flow_dst_max_time:
        raise ValueError(
            f"Flow {flow.flow_id} source time {src_time} is after deadline {flow.flow_dst_max_time}."
        )
    expected_indices = list(range(len(flow.processing_stages)))
    actual_indices = [stage.stage_index for stage in flow.processing_stages]
    if actual_indices != expected_indices:
        raise ValueError(f"Flow {flow.flow_id} processing stage indices {actual_indices} are not contiguous.")
    for stage in flow.processing_stages:
        if stage.processing_coefficient < 0.0:
            raise ValueError(
                f"Flow {flow.flow_id} stage index {stage.stage_index} has negative coefficient "
                f"{stage.processing_coefficient}."
            )
    if len(flow.virtual_segments) != len(flow.processing_stages) + 1:
        raise ValueError(
            f"Flow {flow.flow_id} has {len(flow.virtual_segments)} virtual segments for "
            f"{len(flow.processing_stages)} processing stages."
        )


def validate_path(network: Network, flow: Flow, path: Path) -> None:
    validate_flow(flow, network)
    if path.flow_id != flow.flow_id:
        raise ValueError(f"Path {path.path_id} flow ID {path.flow_id} does not match flow {flow.flow_id}.")

    if path.path_is_empty:
        if path.path_stage_placement:
            raise ValueError(f"Flow {flow.flow_id} empty path {path.path_id} has stage placement.")
        if path.path_segment_routing:
            raise ValueError(f"Flow {flow.flow_id} empty path {path.path_id} has segment routing.")
        if path.path_node_usage not in (None, {}) or path.path_edge_usage not in (None, {}):
            raise ValueError(f"Flow {flow.flow_id} empty path {path.path_id} has nonempty stored usage.")
        if abs(path.path_cost) > 1e-8:
            raise ValueError(f"Flow {flow.flow_id} empty path {path.path_id} has cost {path.path_cost}.")
        return

    expected_stages = set(range(len(flow.processing_stages)))
    actual_stages = set(path.path_stage_placement)
    if actual_stages != expected_stages:
        missing = sorted(expected_stages - actual_stages)
        unknown = sorted(actual_stages - expected_stages)
        raise ValueError(
            f"Flow {flow.flow_id} path {path.path_id} stage indices invalid; "
            f"missing={missing}, unknown={unknown}."
        )

    for stage_index, time_node in path.path_stage_placement.items():
        if time_node not in network.time_nodes:
            raise ValueError(f"Flow {flow.flow_id} stage index {stage_index} uses unknown node {time_node}.")
        time = time_node[1]
        if time < flow.flow_src_time_node[1] or time > flow.flow_dst_max_time:
            raise ValueError(
                f"Flow {flow.flow_id} stage index {stage_index} placement {time_node} "
                f"lies outside deadline window."
            )

    expected_segments = set(range(len(flow.processing_stages) + 1))
    actual_segments = set(path.path_segment_routing)
    if actual_segments != expected_segments:
        missing = sorted(expected_segments - actual_segments)
        unknown = sorted(actual_segments - expected_segments)
        raise ValueError(
            f"Flow {flow.flow_id} path {path.path_id} segment indices invalid; "
            f"missing={missing}, unknown={unknown}."
        )

    milestones: List[TimeNode] = [flow.flow_src_time_node]
    for stage in flow.processing_stages:
        milestones.append(path.path_stage_placement[stage.stage_index])
    dst_candidates = []

    for segment_index in sorted(path.path_segment_routing):
        route = path.path_segment_routing[segment_index]
        start = milestones[segment_index]
        expected_end = (
            milestones[segment_index + 1]
            if segment_index + 1 < len(milestones)
            else None
        )
        _validate_segment_route(network, flow, path.path_id, segment_index, start, expected_end, route)
        if segment_index == len(flow.processing_stages):
            end = route[-1][1] if route else start
            dst_candidates.append(end)
            if end[0] != flow.flow_dst_node or end[1] > flow.flow_dst_max_time:
                raise ValueError(
                    f"Flow {flow.flow_id} segment index {segment_index} ends at {end}, "
                    f"not destination node {flow.flow_dst_node} by deadline {flow.flow_dst_max_time}."
                )
    if not dst_candidates:
        raise ValueError(f"Flow {flow.flow_id} path {path.path_id} has no destination segment.")

    validate_path_usage(network, flow, path)
    spec = objective_spec_from_network(network)
    expected_cost = compute_path_physical_cost(network, flow, path, spec)
    if abs(expected_cost - path.path_cost) > 1e-7:
        raise ValueError(
            f"Flow {flow.flow_id} path {path.path_id} cost mismatch: "
            f"stored {path.path_cost}, recomputed {expected_cost}."
        )


def _validate_segment_route(
    network: Network,
    flow: Flow,
    path_id: int,
    segment_index: int,
    start: TimeNode,
    expected_end: Optional[TimeNode],
    route: List[TimeEdge],
) -> None:
    current = start
    previous_time = start[1]
    if not route and expected_end is not None and start != expected_end:
        raise ValueError(
            f"Flow {flow.flow_id} segment index {segment_index} is disconnected from {start} to {expected_end}."
        )
    for edge in route:
        if edge not in network.time_edges:
            raise ValueError(f"Flow {flow.flow_id} segment index {segment_index} uses unknown edge {edge}.")
        if edge[0] != current:
            raise ValueError(
                f"Flow {flow.flow_id} segment index {segment_index} is not continuous at edge {edge}."
            )
        (u, t1), (v, t2) = edge
        if t2 < t1 or t1 < previous_time:
            raise ValueError(f"Flow {flow.flow_id} segment index {segment_index} moves backward on edge {edge}.")
        cap = network.time_edge_capacity[edge]
        is_storage = u == v and t2 == t1 + 1 and cap == math.inf
        is_comm = u != v and t1 == t2 and cap != math.inf
        if not (is_storage or is_comm):
            raise ValueError(
                f"Flow {flow.flow_id} segment index {segment_index} edge {edge} violates communication/storage semantics."
            )
        current = edge[1]
        previous_time = current[1]
    if expected_end is not None and current != expected_end:
        raise ValueError(
            f"Flow {flow.flow_id} segment index {segment_index} ends at {current}, expected {expected_end}."
        )


def validate_path_usage(network: Network, flow: Flow, path: Path) -> None:
    expected = Path(
        flow_id=path.flow_id,
        path_id=path.path_id,
        path_is_empty=path.path_is_empty,
        path_stage_placement=dict(path.path_stage_placement),
        path_segment_routing={idx: list(edges) for idx, edges in path.path_segment_routing.items()},
        path_cost=path.path_cost,
    )
    expected.finalize_usage(flow=flow)
    if path.path_node_usage is None or path.path_edge_usage is None:
        raise ValueError(f"Flow {flow.flow_id} path {path.path_id} has missing finalized usage.")
    if path.path_node_usage != expected.path_node_usage:
        raise ValueError(
            f"Flow {flow.flow_id} path {path.path_id} stored node usage disagrees with recomputed usage."
        )
    if path.path_edge_usage != expected.path_edge_usage:
        raise ValueError(
            f"Flow {flow.flow_id} path {path.path_id} stored edge usage disagrees with recomputed usage."
        )


def validate_selected_paths(network: Network, flows: Iterable[Flow], selected_paths: Mapping[int, Path]) -> None:
    flow_dict = _flow_by_id(flows)
    for flow_id, path in selected_paths.items():
        if flow_id not in flow_dict:
            raise ValueError(f"Selected path references unknown flow ID {flow_id}.")
        validate_path(network, flow_dict[flow_id], path)


def validate_solution_capacity(network: Network, flows: Iterable[Flow], selected_paths: Mapping[int, Path]) -> None:
    validate_selected_paths(network, flows, selected_paths)
    flow_dict = _flow_by_id(flows)
    node_usage: Dict[TimeNode, float] = {}
    edge_usage: Dict[TimeEdge, float] = {}
    for flow_id, path in selected_paths.items():
        if path.path_is_empty:
            continue
        flow = flow_dict[flow_id]
        for time_node, usage in path.path_node_usage.items():
            node_usage[time_node] = node_usage.get(time_node, 0.0) + flow.flow_demand * usage
        for (u, v, t), usage in path.path_edge_usage.items():
            edge = ((u, t), (v, t))
            if edge in network.time_edge_capacity and network.time_edge_capacity[edge] != math.inf:
                edge_usage[edge] = edge_usage.get(edge, 0.0) + flow.flow_demand * usage

    for time_node, used in node_usage.items():
        capacity = network.time_node_capacity[time_node]
        if used > capacity + 1e-8:
            raise ValueError(f"Node {time_node} capacity {capacity} exceeded by usage {used}.")
    for edge, used in edge_usage.items():
        capacity = network.time_edge_capacity[edge]
        if used > capacity + 1e-8:
            raise ValueError(f"Edge {edge} capacity {capacity} exceeded by usage {used}.")


def validate_solution_objective(
    network: Network,
    flows: Iterable[Flow],
    selected_paths: Mapping[int, Path],
    objective_spec: ObjectiveSpec,
    reported_cost: float,
) -> None:
    validate_solution_capacity(network, flows, selected_paths)
    flow_dict = _flow_by_id(flows)
    accepted_count = 0
    normalized_physical_cost = 0.0
    for flow_id, path in selected_paths.items():
        if path.path_is_empty:
            continue
        accepted_count += 1
        normalized_physical_cost += compute_path_physical_cost(
            network, flow_dict[flow_id], path, objective_spec
        )
    expected = objective_spec.acceptance_reward * accepted_count - normalized_physical_cost
    if abs(expected - reported_cost) > 1e-7:
        raise ValueError(f"Solution objective mismatch: reported {reported_cost}, recomputed {expected}.")


def validate_flow_generation_result(
    flow_result,
    network: Network,
    flow_config=None,
    topology_metadata: Optional[Mapping] = None,
) -> None:
    flows = list(flow_result.flows)
    validate_flows(flows)
    flow_by_id = _flow_by_id(flows)
    if set(flow_by_id) != set(flow_result.per_flow_metadata):
        raise ValueError("Per-flow metadata keys do not match generated flow IDs.")
    aggregate = flow_result.aggregate_metadata
    if aggregate["configuration"]["actual_flow_count"] != len(flows):
        raise ValueError("Aggregate actual flow count does not match generated flows.")
    if aggregate["deadline"].get("clipping_count") != 0:
        raise ValueError("Generated flow metadata reports deadline clipping.")

    layer_map = None
    region_map = None
    if topology_metadata is not None:
        layer_map = topology_metadata["node_structure"]["node_layer_map"]
        region_map = topology_metadata["node_structure"]["ground_region_map"]

    category_counts: Dict[str, int] = {}
    stage_counts: Dict[int, int] = {}
    slack_counts: Dict[int, int] = {}
    total_base = 0.0
    total_actual = 0.0
    for flow in flows:
        meta = flow_result.per_flow_metadata[flow.flow_id]
        _validate_generated_flow_metadata(flow, meta, network, flow_config, layer_map, region_map)
        category_counts[meta["traffic_category"]] = category_counts.get(meta["traffic_category"], 0) + 1
        stage_counts[meta["processing_stage_count"]] = stage_counts.get(meta["processing_stage_count"], 0) + 1
        slack_counts[meta["slack"]] = slack_counts.get(meta["slack"], 0) + 1
        total_base += meta["base_demand"]
        total_actual += meta["actual_demand"]

    if category_counts != aggregate["traffic"]["realized_category_counts"]:
        raise ValueError("Realized traffic category counts do not match flow metadata.")
    if stage_counts != aggregate["sfc"]["realized_stage_count_counts"]:
        raise ValueError("Realized SFC stage counts do not match flow metadata.")
    if slack_counts != aggregate["deadline"]["realized_slack_counts"]:
        raise ValueError("Realized deadline slack counts do not match flow metadata.")
    if abs(total_base - aggregate["demand"]["total_base_demand"]) > 1e-8:
        raise ValueError("Total base demand metadata does not match generated flows.")
    if abs(total_actual - aggregate["demand"]["total_actual_demand"]) > 1e-8:
        raise ValueError("Total actual demand metadata does not match generated flows.")
    for section, requested_key, realized_key in (
        ("traffic", "requested_category_counts", "realized_category_counts"),
        ("sfc", "requested_stage_count_counts", "realized_stage_count_counts"),
        ("deadline", "requested_slack_counts", "realized_slack_counts"),
    ):
        if sum(aggregate[section][requested_key].values()) != len(flows):
            raise ValueError(f"Requested quota counts in {section} do not sum to flow count.")
        if sum(aggregate[section][realized_key].values()) != len(flows):
            raise ValueError(f"Realized quota counts in {section} do not sum to flow count.")


def validate_instance(instance, instance_config=None) -> None:
    validate_network(instance.network)
    validate_flows(instance.flows)
    topology_metadata = instance.topology_metadata
    flow_metadata = instance.flow_metadata
    flow_config = instance_config.flow_generation_config if instance_config is not None else None

    flow_result_view = SimpleNamespace(
        flows=instance.flows,
        per_flow_metadata=flow_metadata["per_flow"],
        aggregate_metadata=flow_metadata["aggregate"],
    )
    validate_flow_generation_result(flow_result_view, instance.network, flow_config, topology_metadata)
    metadata = instance.instance_metadata
    if metadata["flow_count"] != len(instance.flows):
        raise ValueError("Instance flow_count metadata does not match flows.")
    if metadata["time_slots"] != instance.network.time_slots_num:
        raise ValueError("Instance time_slots metadata does not match network.")
    node_count = len(instance.network.nodes)
    if metadata["node_counts"]["total"] != node_count:
        raise ValueError("Instance node count metadata does not match network.")
    expected_ratio = len(instance.flows) / node_count if node_count else 0.0
    if abs(metadata["flow_per_node_ratio"] - expected_ratio) > 1e-12:
        raise ValueError("Instance flow_per_node_ratio metadata mismatch.")
    processing_proxy = sum(
        flow.flow_demand * sum(stage.processing_coefficient for stage in flow.processing_stages)
        for flow in instance.flows
    )
    if abs(
        metadata["offered_load_proxies"]["total_processing_demand_proxy"] - processing_proxy
    ) > 1e-8:
        raise ValueError("Instance processing demand proxy metadata mismatch.")


def _validate_generated_flow_metadata(
    flow: Flow,
    meta: Mapping,
    network: Network,
    flow_config=None,
    layer_map: Optional[Mapping] = None,
    region_map: Optional[Mapping] = None,
) -> None:
    src_node, arrival = flow.flow_src_time_node
    deadline = flow.flow_dst_max_time
    dst_node = flow.flow_dst_node
    if src_node == dst_node:
        raise ValueError(f"Flow {flow.flow_id} has source equal to destination node {src_node}.")
    if src_node not in network.nodes or dst_node not in network.nodes:
        raise ValueError(f"Flow {flow.flow_id} references a missing source or destination node.")
    if flow.flow_src_time_node not in network.time_nodes:
        raise ValueError(f"Flow {flow.flow_id} source time-node {flow.flow_src_time_node} is missing.")
    if not arrival <= deadline < network.time_slots_num:
        raise ValueError(f"Flow {flow.flow_id} arrival/deadline {arrival}/{deadline} are outside the horizon.")
    if meta["arrival_time"] != arrival or meta["deadline_time"] != deadline:
        raise ValueError(f"Flow {flow.flow_id} arrival/deadline metadata mismatch.")
    if deadline - arrival != meta["slack"]:
        raise ValueError(f"Flow {flow.flow_id} realized slack does not match metadata.")
    expected_actual = meta["base_demand"] * meta["demand_multiplier"]
    if abs(flow.flow_demand - expected_actual) > 1e-8 or abs(meta["actual_demand"] - expected_actual) > 1e-8:
        raise ValueError(f"Flow {flow.flow_id} actual demand does not match base demand times multiplier.")
    if meta["processing_stage_count"] != len(flow.processing_stages):
        raise ValueError(f"Flow {flow.flow_id} processing-stage metadata mismatch.")
    if meta["total_chain_length"] != len(flow.processing_stages) + 2:
        raise ValueError(f"Flow {flow.flow_id} total chain length metadata mismatch.")
    if meta["vnf_label_sequence"] != [stage.vnf_type for stage in flow.processing_stages]:
        raise ValueError(f"Flow {flow.flow_id} VNF label metadata mismatch.")
    coefficients = [stage.processing_coefficient for stage in flow.processing_stages]
    if list(meta["processing_coefficient_sequence"]) != coefficients:
        raise ValueError(f"Flow {flow.flow_id} processing coefficient metadata mismatch.")
    if flow_config is not None:
        low = flow_config.sfc_profile.processing_coefficient_low
        high = flow_config.sfc_profile.processing_coefficient_high
        for stage in flow.processing_stages:
            if not low <= stage.processing_coefficient <= high:
                raise ValueError(
                    f"Flow {flow.flow_id} stage index {stage.stage_index} coefficient "
                    f"{stage.processing_coefficient} outside configured range [{low}, {high}]."
                )
    if layer_map is not None and region_map is not None:
        src_layer = layer_map[src_node]
        dst_layer = layer_map[dst_node]
        if meta["source_layer"] != src_layer or meta["destination_layer"] != dst_layer:
            raise ValueError(f"Flow {flow.flow_id} layer metadata does not match endpoints.")
        category = meta["traffic_category"]
        if category == "cross_region_ground":
            if src_layer != "Ground" or dst_layer != "Ground":
                raise ValueError(f"Flow {flow.flow_id} cross-region flow must be Ground-to-Ground.")
            if region_map[src_node] == region_map[dst_node]:
                raise ValueError(f"Flow {flow.flow_id} cross-region flow uses the same Ground region.")
        elif category == "same_region_ground":
            if src_layer != "Ground" or dst_layer != "Ground":
                raise ValueError(f"Flow {flow.flow_id} same-region flow must be Ground-to-Ground.")
            if region_map[src_node] != region_map[dst_node]:
                raise ValueError(f"Flow {flow.flow_id} same-region flow uses different Ground regions.")
        elif category == "ground_nonground":
            if (src_layer == "Ground") == (dst_layer == "Ground"):
                raise ValueError(f"Flow {flow.flow_id} Ground/non-Ground flow must have exactly one Ground endpoint.")
        else:
            raise ValueError(f"Flow {flow.flow_id} has unknown traffic category {category!r}.")
