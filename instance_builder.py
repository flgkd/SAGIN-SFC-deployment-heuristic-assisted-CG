"""Complete candidate SAGIN instance construction."""

from dataclasses import dataclass
import hashlib
import math
from typing import Dict, Tuple

from config_schema import FlowGenerationConfig, InstanceConfig, TopologyConfig
from flow_generator import FlowGenerationResult, generate_flows
from sagin_generator import NetworkGenerationResult, generate_sagin_topology
from validation import validate_flow_generation_result, validate_flows, validate_instance, validate_network


GENERATOR_VERSION = "v2_phase2b_instance_candidate"


@dataclass(frozen=True)
class SaginInstance:
    network: object
    flows: Tuple
    topology_metadata: Dict
    flow_metadata: Dict
    instance_metadata: Dict


def build_instance(config: InstanceConfig) -> SaginInstance:
    config.validate()
    topology_seed, flow_seed = _resolve_seeds(config)
    topology_config = _with_topology_seed(config.topology_config, topology_seed)
    flow_config = _with_flow_seed(config.flow_generation_config, flow_seed)

    topology_result = generate_sagin_topology(topology_config)
    flow_result = generate_flows(flow_config, topology_result)

    validate_network(topology_result.network)
    validate_flows(flow_result.flows)
    validate_flow_generation_result(flow_result, topology_result.network, flow_config, topology_result.metadata)
    instance_metadata = _build_instance_metadata(config, topology_seed, flow_seed, topology_result, flow_result)
    instance = SaginInstance(
        network=topology_result.network,
        flows=flow_result.flows,
        topology_metadata=topology_result.metadata,
        flow_metadata={
            "per_flow": flow_result.per_flow_metadata,
            "aggregate": flow_result.aggregate_metadata,
        },
        instance_metadata=instance_metadata,
    )
    validate_instance(instance, config)
    return instance


def _resolve_seeds(config: InstanceConfig) -> Tuple[int, int]:
    if config.topology_seed is not None and config.flow_seed is not None:
        return int(config.topology_seed), int(config.flow_seed)
    scenario_seed = int(config.scenario_seed)
    return (
        _derive_seed(scenario_seed, "topology"),
        _derive_seed(scenario_seed, "flow"),
    )


def _derive_seed(scenario_seed: int, namespace: str) -> int:
    payload = f"v2-phase2b:{namespace}:{int(scenario_seed)}".encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFF


def _with_topology_seed(config: TopologyConfig, seed: int) -> TopologyConfig:
    return TopologyConfig(
        scale=config.scale,
        time_slots=config.time_slots,
        seed=seed,
        resource_weight=config.resource_weight,
        dynamic_topology=config.dynamic_topology,
        capacity_profile=config.capacity_profile,
        dynamics_profile=config.dynamics_profile,
    )


def _with_flow_seed(config: FlowGenerationConfig, seed: int) -> FlowGenerationConfig:
    return FlowGenerationConfig(
        seed=seed,
        num_flows=config.num_flows,
        base_demand_low=config.base_demand_low,
        base_demand_high=config.base_demand_high,
        demand_multiplier=config.demand_multiplier,
        traffic_mix_profile=config.traffic_mix_profile,
        sfc_profile=config.sfc_profile,
        deadline_profile=config.deadline_profile,
        flow_id_start=config.flow_id_start,
        load_profile_name=config.load_profile_name,
    )


def _build_instance_metadata(
    config: InstanceConfig,
    topology_seed: int,
    flow_seed: int,
    topology_result: NetworkGenerationResult,
    flow_result: FlowGenerationResult,
) -> Dict:
    network = topology_result.network
    finite_comm_capacity = sum(
        capacity
        for edge, capacity in network.time_edge_capacity.items()
        if capacity != math.inf and edge[0][1] == edge[1][1]
    )
    total_processing_proxy = sum(
        flow.flow_demand * sum(stage.processing_coefficient for stage in flow.processing_stages)
        for flow in flow_result.flows
    )
    total_virtual_link_proxy = sum(
        flow.flow_demand * len(flow.virtual_segments)
        for flow in flow_result.flows
    )
    node_count = len(topology_result.metadata["node_structure"]["node_ids"])
    return {
        "generator_versions": {
            "instance": GENERATOR_VERSION,
            "topology": topology_result.metadata["configuration"]["generator_version"],
            "flow": flow_result.aggregate_metadata["configuration"]["generator_version"],
        },
        "scenario_seed": config.scenario_seed,
        "topology_seed": topology_seed,
        "flow_seed": flow_seed,
        "scale": topology_result.metadata["configuration"]["scale_name"],
        "node_counts": {
            "total": node_count,
            "space": topology_result.metadata["node_structure"]["space_nodes"],
            "air": topology_result.metadata["node_structure"]["air_nodes"],
            "ground": topology_result.metadata["node_structure"]["ground_nodes"],
        },
        "physical_link_counts": topology_result.metadata["physical_topology"]["physical_link_counts_by_type"],
        "physical_link_count": topology_result.metadata["physical_topology"]["physical_link_count"],
        "time_slots": network.time_slots_num,
        "dynamic_topology": topology_result.metadata["configuration"]["dynamic_topology"],
        "flow_count": len(flow_result.flows),
        "flow_per_node_ratio": len(flow_result.flows) / node_count if node_count else 0.0,
        "load_profile": flow_result.aggregate_metadata["configuration"]["load_profile_name"],
        "demand_multiplier": flow_result.aggregate_metadata["configuration"]["demand_multiplier"],
        "traffic_mix": flow_result.aggregate_metadata["configuration"]["traffic_mix_profile"],
        "sfc_profile": flow_result.aggregate_metadata["configuration"]["sfc_profile"],
        "deadline_profile": flow_result.aggregate_metadata["configuration"]["deadline_profile"],
        "offered_load_proxies": {
            "total_processing_demand_proxy": total_processing_proxy,
            "total_virtual_link_demand_proxy": total_virtual_link_proxy,
            "total_time_node_capacity": sum(network.time_node_capacity.values()),
            "total_finite_communication_capacity": finite_comm_capacity,
            "interpretation": (
                "These are offered-load proxies, not exact utilization; exact "
                "utilization depends on placement and routing decisions."
            ),
        },
        "topology_summary": {
            "physical_link_count": topology_result.metadata["physical_topology"]["physical_link_count"],
            "average_active_physical_links": topology_result.metadata["aggregate_dynamics"]["average_active_physical_links"],
        },
        "flow_summary": {
            "traffic": flow_result.aggregate_metadata["traffic"],
            "sfc": flow_result.aggregate_metadata["sfc"],
            "deadline": flow_result.aggregate_metadata["deadline"],
            "demand": flow_result.aggregate_metadata["demand"],
        },
    }
