"""Strict same-flow temporal-horizon control construction for Phase 5G."""

from dataclasses import dataclass
import copy
import random
from typing import Any, Dict, List, Mapping, Tuple

from calibration_runner import sha256_fingerprint
from config_schema import (
    FlowGenerationConfig,
    InstanceConfig,
    TopologyConfig,
    TRAFFIC_CATEGORIES,
    get_candidate_deadline_profile,
)
from data_model import Flow, Network
from flow_generator import (
    GENERATOR_VERSION as FLOW_GENERATOR_VERSION,
    allocate_largest_remainder,
    default_num_flows_for_topology,
    shuffled_quota_labels,
)
from instance_builder import SaginInstance, _build_instance_metadata, _resolve_seeds
from paired_deadline_builder import _ground_by_region, _nodes_by_layer, _sample_od, _sample_vnf_labels
from sagin_generator import NetworkGenerationResult, generate_sagin_topology
from validation import validate_flows, validate_instance, validate_network


CONTROL_TIME_SLOTS = (5, 8, 12)
BALANCED_CONTROL_TIME_SLOTS = (6, 8, 10, 12)
CANONICAL_TIME_SLOTS = 12
FLOW_ANCHOR_TIME_SLOTS = 6
SOURCE_TIME_SUPPORT = (0, 1, 2)
SOURCE_TIME_PROBABILITIES = {0: 1.0 / 3.0, 1: 1.0 / 3.0, 2: 1.0 / 3.0}


@dataclass(frozen=True)
class SameFlowTemporalResult:
    instance: SaginInstance
    strict_same_flow_fingerprint: str
    canonical_t12_topology_fingerprint: str
    temporal_prefix_fingerprint: str
    common_prefix_fingerprint: str


def build_same_flow_temporal_instance(config: InstanceConfig) -> SameFlowTemporalResult:
    config.validate()
    if config.flow_generation_config.deadline_profile.name == "Balanced":
        return _build_balanced_same_flow_temporal_instance(config)
    return _build_realtime_same_flow_temporal_instance(config)


def _build_realtime_same_flow_temporal_instance(config: InstanceConfig) -> SameFlowTemporalResult:
    requested_t = int(config.topology_config.time_slots)
    if requested_t not in CONTROL_TIME_SLOTS:
        raise ValueError(f"Phase 5G supports T in {CONTROL_TIME_SLOTS}, got {requested_t}.")
    if config.flow_generation_config.deadline_profile.name != "RealTime":
        raise ValueError("Phase 5G same-flow temporal control requires RealTime deadlines.")
    topology_seed, flow_seed = _resolve_seeds(config)
    canonical_topology_config = TopologyConfig(
        scale=config.topology_config.scale,
        time_slots=CANONICAL_TIME_SLOTS,
        seed=topology_seed,
        resource_weight=config.topology_config.resource_weight,
        dynamic_topology=config.topology_config.dynamic_topology,
        capacity_profile=config.topology_config.capacity_profile,
        dynamics_profile=config.topology_config.dynamics_profile,
    )
    canonical_topology = generate_sagin_topology(canonical_topology_config)
    prefix_topology = _prefix_topology(canonical_topology, requested_t)
    flow_result = _generate_canonical_flows(config.flow_generation_config, canonical_topology, flow_seed)
    validate_network(prefix_topology.network)
    validate_flows(flow_result["flows"])
    instance_metadata = _build_instance_metadata(config, topology_seed, flow_seed, prefix_topology, flow_result["flow_generation_result"])
    canonical_topology_fp = canonical_t12_topology_fingerprint(canonical_topology)
    instance_metadata["same_flow_temporal_control"] = {
        "requested_time_slots": requested_t,
        "canonical_time_slots": CANONICAL_TIME_SLOTS,
        "deadline_profile": "RealTime",
        "source_time_support": list(SOURCE_TIME_SUPPORT),
        "source_time_counts": flow_result["source_time_counts"],
        "slack_counts": flow_result["slack_counts"],
        "deadline_clipping_count": 0,
        "canonical_t12_topology_fingerprint": canonical_topology_fp,
    }
    instance = SaginInstance(
        network=prefix_topology.network,
        flows=tuple(flow_result["flows"]),
        topology_metadata=prefix_topology.metadata,
        flow_metadata={
            "per_flow": flow_result["per_flow"],
            "aggregate": flow_result["aggregate"],
        },
        instance_metadata=instance_metadata,
    )
    validate_instance(instance, config)
    strict_fp = strict_same_flow_fingerprint(instance)
    prefix_fp = temporal_prefix_fingerprint(instance)
    common_fp = common_prefix_fingerprint(instance, requested_t)
    instance.instance_metadata["same_flow_temporal_control"].update({
        "strict_same_flow_fingerprint": strict_fp,
        "temporal_prefix_fingerprint": prefix_fp,
        "common_prefix_fingerprint": common_fp,
    })
    return SameFlowTemporalResult(instance, strict_fp, canonical_topology_fp, prefix_fp, common_fp)


def _build_balanced_same_flow_temporal_instance(config: InstanceConfig) -> SameFlowTemporalResult:
    requested_t = int(config.topology_config.time_slots)
    if requested_t not in BALANCED_CONTROL_TIME_SLOTS:
        raise ValueError(f"V3 same-flow temporal control supports T in {BALANCED_CONTROL_TIME_SLOTS}, got {requested_t}.")
    topology_seed, flow_seed = _resolve_seeds(config)
    canonical_topology_config = TopologyConfig(
        scale=config.topology_config.scale,
        time_slots=CANONICAL_TIME_SLOTS,
        seed=topology_seed,
        resource_weight=config.topology_config.resource_weight,
        dynamic_topology=config.topology_config.dynamic_topology,
        capacity_profile=config.topology_config.capacity_profile,
        dynamics_profile=config.topology_config.dynamics_profile,
    )
    canonical_topology = generate_sagin_topology(canonical_topology_config)
    prefix_topology = _prefix_topology(canonical_topology, requested_t)
    from paired_temporal_builder import (
        _generate_paired_temporal_flows,
        full_temporal_flow_fingerprint as paired_full_temporal_flow_fingerprint,
        non_temporal_flow_fingerprint,
        paired_timing_latent_fingerprint,
        physical_base_fingerprint,
    )

    flow_result = _generate_paired_temporal_flows(
        config.flow_generation_config,
        canonical_topology,
        flow_seed,
        FLOW_ANCHOR_TIME_SLOTS,
    )
    validate_network(prefix_topology.network)
    validate_flows(flow_result["flows"])
    instance_metadata = _build_instance_metadata(config, topology_seed, flow_seed, prefix_topology, flow_result["flow_generation_result"])
    canonical_topology_fp = canonical_t12_topology_fingerprint(canonical_topology)
    instance_metadata["same_flow_temporal_control"] = {
        "requested_time_slots": requested_t,
        "canonical_time_slots": CANONICAL_TIME_SLOTS,
        "flow_anchor_time_slots": FLOW_ANCHOR_TIME_SLOTS,
        "supported_time_slots": list(BALANCED_CONTROL_TIME_SLOTS),
        "deadline_profile": "Balanced",
        "source_time_support": sorted(set(meta["arrival_time"] for meta in flow_result["per_flow"].values())),
        "source_time_counts": _count(meta["arrival_time"] for meta in flow_result["per_flow"].values()),
        "slack_counts": flow_result["aggregate"]["deadline"]["realized_slack_counts"],
        "deadline_clipping_count": 0,
        "canonical_t12_topology_fingerprint": canonical_topology_fp,
        "topology_anchor": "canonical_T12_strict_prefixes",
        "flow_anchor": "canonical_T6_complete_flow_bundle",
        "arrival_mapping": "T6 anchor only; arrival/deadline copied unchanged for larger T",
    }
    instance = SaginInstance(
        network=prefix_topology.network,
        flows=tuple(flow_result["flows"]),
        topology_metadata=prefix_topology.metadata,
        flow_metadata={
            "per_flow": flow_result["per_flow"],
            "aggregate": flow_result["aggregate"],
        },
        instance_metadata=instance_metadata,
    )
    validate_instance(instance, config)
    strict_fp = strict_same_flow_fingerprint(instance)
    prefix_fp = temporal_prefix_fingerprint(instance)
    common_fp = common_prefix_fingerprint(instance, requested_t)
    physical_fp = physical_base_fingerprint(instance)
    latent_fp = paired_timing_latent_fingerprint(instance)
    non_temporal_fp = non_temporal_flow_fingerprint(instance)
    full_flow_fp = paired_full_temporal_flow_fingerprint(instance)
    instance.instance_metadata["same_flow_temporal_control"].update({
        "strict_same_flow_fingerprint": strict_fp,
        "physical_base_fingerprint": physical_fp,
        "non_temporal_flow_fingerprint": non_temporal_fp,
        "paired_timing_latent_fingerprint": latent_fp,
        "full_temporal_flow_fingerprint": full_flow_fp,
        "temporal_prefix_fingerprint": prefix_fp,
        "common_prefix_fingerprint": common_fp,
    })
    return SameFlowTemporalResult(instance, strict_fp, canonical_topology_fp, prefix_fp, common_fp)


def strict_same_flow_fingerprint(instance: SaginInstance) -> str:
    return sha256_fingerprint([_flow_payload(instance, flow_id) for flow_id in sorted(instance.flow_metadata["per_flow"])])


def canonical_t12_topology_fingerprint(topology: NetworkGenerationResult) -> str:
    return sha256_fingerprint(_topology_payload(topology.network, topology.metadata, CANONICAL_TIME_SLOTS))


def temporal_prefix_fingerprint(instance: SaginInstance) -> str:
    return sha256_fingerprint(_topology_payload(instance.network, instance.topology_metadata, instance.network.time_slots_num))


def common_prefix_fingerprint(instance: SaginInstance, prefix_slots: int) -> str:
    return sha256_fingerprint(_topology_payload(instance.network, instance.topology_metadata, prefix_slots))


def full_temporal_flow_fingerprint(instance: SaginInstance) -> str:
    if instance.instance_metadata.get("same_flow_temporal_control", {}).get("deadline_profile") == "Balanced":
        from paired_temporal_builder import full_temporal_flow_fingerprint as paired_full_temporal_flow_fingerprint

        return paired_full_temporal_flow_fingerprint(instance)
    return strict_same_flow_fingerprint(instance)


def _prefix_topology(canonical: NetworkGenerationResult, time_slots: int) -> NetworkGenerationResult:
    network = Network()
    network.set_nodes(canonical.network.nodes_num)
    network.set_time_slots(time_slots)
    network.set_resource_weight(canonical.network.resource_weight)
    for (node, time), capacity in sorted(canonical.network.time_node_capacity.items()):
        if time < time_slots:
            network.add_time_node((node, time), capacity)
    for edge, capacity in sorted(canonical.network.time_edge_capacity.items()):
        (u, t1), (v, t2) = edge
        if u != v and u < v and t1 == t2 and t1 < time_slots:
            network.add_comm_time_edge((u, t1), (v, t2), capacity)
    for node in network.nodes:
        for time in range(time_slots - 1):
            network.add_storage_time_edge((node, time))
    network.compute_total_time_node_capacity()
    network.compute_total_comm_edge_capacity()
    metadata = copy.deepcopy(canonical.metadata)
    metadata["configuration"]["time_slots"] = time_slots
    metadata["configuration"]["canonical_time_slots"] = CANONICAL_TIME_SLOTS
    metadata["snapshots"] = [
        copy.deepcopy(snapshot)
        for snapshot in canonical.metadata["snapshots"]
        if int(snapshot["time_slot"]) < time_slots
    ]
    metadata["same_flow_temporal_control"] = {
        "canonical_time_slots": CANONICAL_TIME_SLOTS,
        "requested_time_slots": time_slots,
    }
    return NetworkGenerationResult(network, canonical.physical_links, metadata)


def _generate_canonical_flows(flow_config: FlowGenerationConfig, canonical_topology: NetworkGenerationResult, flow_seed: int) -> Dict[str, Any]:
    config = FlowGenerationConfig(
        seed=flow_seed,
        num_flows=flow_config.num_flows,
        base_demand_low=flow_config.base_demand_low,
        base_demand_high=flow_config.base_demand_high,
        demand_multiplier=flow_config.demand_multiplier,
        traffic_mix_profile=flow_config.traffic_mix_profile,
        sfc_profile=flow_config.sfc_profile,
        deadline_profile=get_candidate_deadline_profile("RealTime"),
        flow_id_start=flow_config.flow_id_start,
        load_profile_name=flow_config.load_profile_name,
    )
    config.validate()
    num_flows = config.num_flows if config.num_flows is not None else default_num_flows_for_topology(canonical_topology)
    base_rng = random.Random(_derive_stream_seed(flow_seed, "same_flow_base"))
    arrival_rng = random.Random(_derive_stream_seed(flow_seed, "same_flow_arrival"))
    slack_rng = random.Random(_derive_stream_seed(flow_seed, "same_flow_slack"))
    traffic_labels, traffic_counts = shuffled_quota_labels(
        num_flows,
        config.traffic_mix_profile.probabilities(),
        base_rng,
        tie_order=TRAFFIC_CATEGORIES,
    )
    stage_labels, stage_counts = shuffled_quota_labels(
        num_flows,
        config.sfc_profile.stage_count_distribution.as_dict(),
        base_rng,
        tie_order=sorted(config.sfc_profile.stage_count_distribution.probabilities),
    )
    arrival_labels, source_time_counts = shuffled_quota_labels(
        num_flows,
        SOURCE_TIME_PROBABILITIES,
        arrival_rng,
        tie_order=list(SOURCE_TIME_SUPPORT),
    )
    slack_distribution = get_candidate_deadline_profile("RealTime").slack_distribution.as_dict()
    slack_labels, slack_counts = shuffled_quota_labels(
        num_flows,
        slack_distribution,
        slack_rng,
        tie_order=sorted(slack_distribution),
    )
    nodes_by_layer = _nodes_by_layer(canonical_topology.metadata)
    ground_by_region = _ground_by_region(canonical_topology.metadata)
    flows = []
    per_flow = {}
    for offset in range(num_flows):
        flow_id = config.flow_id_start + offset
        category = traffic_labels[offset]
        stage_count = int(stage_labels[offset])
        src, dst, od_meta = _sample_od(category, base_rng, nodes_by_layer, ground_by_region, canonical_topology.metadata)
        arrival = int(arrival_labels[offset])
        slack = int(slack_labels[offset])
        deadline = arrival + slack
        if deadline > 4:
            raise ValueError("Phase 5G generated a deadline outside the T=5 common horizon.")
        base_demand = base_rng.randint(config.base_demand_low, config.base_demand_high)
        actual_demand = float(base_demand) * float(config.demand_multiplier)
        labels = _sample_vnf_labels(config, stage_count, base_rng)
        coefficients = [
            base_rng.uniform(config.sfc_profile.processing_coefficient_low, config.sfc_profile.processing_coefficient_high)
            for _ in range(stage_count)
        ]
        first_cost_by_label = {}
        for label, coefficient in zip(labels, coefficients):
            first_cost_by_label.setdefault(label, coefficient)
        flow = Flow(
            flow_id=flow_id,
            flow_len=stage_count + 2,
            flow_src_node=src,
            flow_src_time=arrival,
            flow_dst_node=dst,
            flow_dst_max_time=deadline,
            flow_sfc=labels,
            flow_vnf_cost=first_cost_by_label,
            flow_demand=actual_demand,
            stage_processing_coefficients=coefficients,
        )
        flows.append(flow)
        per_flow[flow_id] = {
            "flow_id": flow_id,
            "traffic_category": category,
            **od_meta,
            "arrival_time": arrival,
            "deadline_time": deadline,
            "slack": slack,
            "deadline_profile": "RealTime",
            "base_demand": base_demand,
            "demand_multiplier": config.demand_multiplier,
            "actual_demand": actual_demand,
            "processing_stage_count": stage_count,
            "total_chain_length": stage_count + 2,
            "vnf_label_sequence": list(labels),
            "processing_coefficient_sequence": list(coefficients),
            "contains_repeated_vnf_type": len(set(labels)) < len(labels),
        }
    aggregate = _aggregate_metadata(config, flows, per_flow, traffic_counts, stage_counts, slack_counts, source_time_counts)
    flow_generation_result = type("FlowGenerationResult", (), {
        "flows": tuple(flows),
        "per_flow_metadata": per_flow,
        "aggregate_metadata": aggregate,
    })()
    return {
        "flows": flows,
        "per_flow": per_flow,
        "aggregate": aggregate,
        "flow_generation_result": flow_generation_result,
        "source_time_counts": dict(source_time_counts),
        "slack_counts": dict(slack_counts),
    }


def _aggregate_metadata(config: FlowGenerationConfig, flows: List[Flow], per_flow: Dict[int, Dict[str, Any]], traffic_counts, stage_counts, slack_counts, source_time_counts) -> Dict[str, Any]:
    values = list(per_flow.values())
    traffic_realized = _count((meta["traffic_category"] for meta in values), keys=TRAFFIC_CATEGORIES)
    stage_realized = _count((meta["processing_stage_count"] for meta in values), keys=sorted(stage_counts))
    slack_realized = _count((meta["slack"] for meta in values), keys=sorted(slack_counts))
    arrival_realized = _count((meta["arrival_time"] for meta in values), keys=list(SOURCE_TIME_SUPPORT))
    return {
        "configuration": {
            "generator_version": f"{FLOW_GENERATOR_VERSION}_same_flow_temporal_control_v1",
            "seed": config.seed,
            "requested_flow_count": len(flows),
            "actual_flow_count": len(flows),
            "base_demand_range": {"low": config.base_demand_low, "high": config.base_demand_high},
            "demand_multiplier": config.demand_multiplier,
            "load_profile_name": config.load_profile_name,
            "traffic_mix_profile": {"name": config.traffic_mix_profile.name, "probabilities": config.traffic_mix_profile.probabilities()},
            "sfc_profile": {
                "name": config.sfc_profile.name,
                "stage_count_distribution": config.sfc_profile.stage_count_distribution.as_dict(),
                "vnf_type_catalog": list(config.sfc_profile.vnf_type_catalog),
                "processing_coefficient_range": {
                    "low": config.sfc_profile.processing_coefficient_low,
                    "high": config.sfc_profile.processing_coefficient_high,
                },
                "sample_with_replacement": config.sfc_profile.sample_with_replacement,
            },
            "deadline_profile": {
                "name": "RealTime",
                "slack_distribution": config.deadline_profile.slack_distribution.as_dict(),
                "definition": "deadline_time = arrival_time + slack",
                "same_flow_temporal_control": True,
            },
            "source_time_distribution": dict(SOURCE_TIME_PROBABILITIES),
        },
        "traffic": {
            "requested_category_counts": dict(traffic_counts),
            "realized_category_counts": traffic_realized,
            "requested_category_ratios": config.traffic_mix_profile.probabilities(),
            "realized_category_ratios": _ratios(traffic_realized, len(flows)),
            "source_layer_counts": _count(meta["source_layer"] for meta in values),
            "destination_layer_counts": _count(meta["destination_layer"] for meta in values),
            "cross_region_count": traffic_realized["cross_region_ground"],
        },
        "sfc": {
            "requested_stage_count_counts": dict(stage_counts),
            "realized_stage_count_counts": stage_realized,
            "requested_stage_count_distribution": config.sfc_profile.stage_count_distribution.as_dict(),
            "realized_stage_count_distribution": _ratios(stage_realized, len(flows)),
            "average_processing_stage_count": _mean([meta["processing_stage_count"] for meta in values]),
            "average_total_chain_length": _mean([meta["total_chain_length"] for meta in values]),
            "repeated_vnf_chain_count": sum(meta["contains_repeated_vnf_type"] for meta in values),
            "processing_coefficient_summary": _summary([coefficient for meta in values for coefficient in meta["processing_coefficient_sequence"]]),
        },
        "deadline": {
            "requested_slack_counts": dict(slack_counts),
            "realized_slack_counts": slack_realized,
            "requested_slack_distribution": config.deadline_profile.slack_distribution.as_dict(),
            "realized_slack_distribution": _ratios(slack_realized, len(flows)),
            "requested_source_time_counts": dict(source_time_counts),
            "realized_source_time_counts": arrival_realized,
            "arrival_time_summary": _summary([meta["arrival_time"] for meta in values]),
            "deadline_time_summary": _summary([meta["deadline_time"] for meta in values]),
            "minimum_slack": min(meta["slack"] for meta in values) if values else 0,
            "mean_slack": _mean([meta["slack"] for meta in values]),
            "maximum_slack": max(meta["slack"] for meta in values) if values else 0,
            "clipping_count": 0,
        },
        "demand": {
            "base_demand_summary": _summary([meta["base_demand"] for meta in values]),
            "actual_demand_summary": _summary([meta["actual_demand"] for meta in values]),
            "total_base_demand": sum(meta["base_demand"] for meta in values),
            "total_actual_demand": sum(meta["actual_demand"] for meta in values),
        },
    }


def _flow_payload(instance: SaginInstance, flow_id: int) -> Dict[str, Any]:
    meta = instance.flow_metadata["per_flow"][flow_id]
    return {
        "flow_id": flow_id,
        "traffic_category": meta["traffic_category"],
        "source_node": meta["source_node"],
        "destination_node": meta["destination_node"],
        "source_layer": meta["source_layer"],
        "destination_layer": meta["destination_layer"],
        "source_ground_region": meta["source_ground_region"],
        "destination_ground_region": meta["destination_ground_region"],
        "arrival_time": meta["arrival_time"],
        "deadline_time": meta["deadline_time"],
        "slack": meta["slack"],
        "deadline_profile": meta["deadline_profile"],
        "base_demand": meta["base_demand"],
        "demand_multiplier": meta["demand_multiplier"],
        "actual_demand": meta["actual_demand"],
        "processing_stage_count": meta["processing_stage_count"],
        "total_chain_length": meta["total_chain_length"],
        "vnf_label_sequence": meta["vnf_label_sequence"],
        "processing_coefficient_sequence": meta["processing_coefficient_sequence"],
        "contains_repeated_vnf_type": meta["contains_repeated_vnf_type"],
    }


def _topology_payload(network: Network, metadata: Mapping[str, Any], prefix_slots: int) -> Dict[str, Any]:
    return {
        "nodes": list(network.nodes),
        "physical_links": metadata["physical_topology"]["base_physical_links"],
        "time_node_capacity": sorted(
            (node, time, capacity)
            for (node, time), capacity in network.time_node_capacity.items()
            if time < prefix_slots
        ),
        "communication_capacity": sorted(
            (list(edge[0]), list(edge[1]), capacity)
            for edge, capacity in network.time_edge_capacity.items()
            if edge[0][0] != edge[1][0] and edge[0][1] == edge[1][1] and edge[0][1] < prefix_slots
        ),
        "storage_edges": sorted(
            (list(edge[0]), list(edge[1]), capacity)
            for edge, capacity in network.time_edge_capacity.items()
            if edge[0][0] == edge[1][0] and edge[0][1] + 1 == edge[1][1] and edge[1][1] < prefix_slots
        ),
        "snapshots": [
            snapshot
            for snapshot in metadata["snapshots"]
            if int(snapshot["time_slot"]) < prefix_slots
        ],
    }


def _derive_stream_seed(seed: int, namespace: str) -> int:
    return int(sha256_fingerprint({"seed": int(seed), "namespace": namespace})[:16], 16) & 0x7FFFFFFF


def _count(values, keys=None) -> Dict[Any, int]:
    counts = {key: 0 for key in keys} if keys is not None else {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _ratios(counts: Mapping[Any, int], total: int) -> Dict[Any, float]:
    return {key: (value / total if total else 0.0) for key, value in counts.items()}


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _summary(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"min": 0.0, "max": 0.0, "mean": 0.0}
    return {"min": min(values), "max": max(values), "mean": sum(values) / len(values)}
