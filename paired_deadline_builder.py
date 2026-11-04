"""Strictly paired deadline-profile instance construction for Phase 5F."""

from dataclasses import dataclass
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
from data_model import Flow
from flow_generator import (
    GENERATOR_VERSION as FLOW_GENERATOR_VERSION,
    allocate_largest_remainder,
    default_num_flows_for_topology,
    shuffled_quota_labels,
)
from instance_builder import SaginInstance, _build_instance_metadata, _resolve_seeds, _with_topology_seed
from sagin_generator import generate_sagin_topology
from validation import validate_flows, validate_instance, validate_network


DEADLINE_SENSITIVITY_PROFILES = ("RealTime", "Balanced", "Flexible")
MAX_DEADLINE_SENSITIVITY_SLACK = 5


@dataclass(frozen=True)
class PairedDeadlineResult:
    instance: SaginInstance
    deadline_invariant_flow_fingerprint: str
    full_flow_fingerprint: str
    deadline_uniform_fingerprint: str
    deadline_pairing_metadata: Dict[str, Any]


def build_paired_deadline_instance(
    config: InstanceConfig,
    deadline_profile_name: str,
) -> PairedDeadlineResult:
    if deadline_profile_name not in DEADLINE_SENSITIVITY_PROFILES:
        raise ValueError(f"Unsupported paired deadline profile {deadline_profile_name!r}.")
    config.validate()
    topology_seed, flow_seed = _resolve_seeds(config)
    topology_config = _with_topology_seed(config.topology_config, topology_seed)
    if topology_config.time_slots < MAX_DEADLINE_SENSITIVITY_SLACK + 1:
        raise ValueError("Phase 5F paired deadline sensitivity requires T >= 6.")
    topology_result = generate_sagin_topology(topology_config)
    flow_result = _generate_paired_deadline_flows(config.flow_generation_config, topology_result, flow_seed, deadline_profile_name)
    validate_network(topology_result.network)
    validate_flows(flow_result["flows"])
    instance_metadata = _build_instance_metadata(config, topology_seed, flow_seed, topology_result, flow_result["flow_generation_result"])
    instance_metadata["paired_deadline_sensitivity"] = {
        "deadline_profile": deadline_profile_name,
        "maximum_profile_slack": MAX_DEADLINE_SENSITIVITY_SLACK,
        "latest_source_time": topology_config.time_slots - 1 - MAX_DEADLINE_SENSITIVITY_SLACK,
        "deadline_uniform_fingerprint": flow_result["deadline_uniform_fingerprint"],
    }
    instance = SaginInstance(
        network=topology_result.network,
        flows=tuple(flow_result["flows"]),
        topology_metadata=topology_result.metadata,
        flow_metadata={
            "per_flow": flow_result["per_flow"],
            "aggregate": flow_result["aggregate"],
        },
        instance_metadata=instance_metadata,
    )
    validate_instance(instance, config)
    invariant_fp = deadline_invariant_flow_fingerprint(instance)
    full_fp = full_flow_fingerprint(instance)
    metadata = {
        "deadline_profile": deadline_profile_name,
        "deadline_invariant_flow_fingerprint": invariant_fp,
        "full_flow_fingerprint": full_fp,
        "deadline_uniform_fingerprint": flow_result["deadline_uniform_fingerprint"],
        "latest_source_time": flow_result["latest_source_time"],
        "source_time_support": [0, flow_result["latest_source_time"]],
        "deadline_clipping_count": 0,
        "per_flow_deadline_uniforms": flow_result["deadline_uniforms"],
    }
    return PairedDeadlineResult(instance, invariant_fp, full_fp, flow_result["deadline_uniform_fingerprint"], metadata)


def _generate_paired_deadline_flows(flow_config: FlowGenerationConfig, topology_result, flow_seed: int, deadline_profile_name: str) -> Dict[str, Any]:
    config = FlowGenerationConfig(
        seed=flow_seed,
        num_flows=flow_config.num_flows,
        base_demand_low=flow_config.base_demand_low,
        base_demand_high=flow_config.base_demand_high,
        demand_multiplier=flow_config.demand_multiplier,
        traffic_mix_profile=flow_config.traffic_mix_profile,
        sfc_profile=flow_config.sfc_profile,
        deadline_profile=get_candidate_deadline_profile(deadline_profile_name),
        flow_id_start=flow_config.flow_id_start,
        load_profile_name=flow_config.load_profile_name,
    )
    config.validate()
    num_flows = config.num_flows if config.num_flows is not None else default_num_flows_for_topology(topology_result)
    latest_source_time = topology_result.network.time_slots_num - 1 - MAX_DEADLINE_SENSITIVITY_SLACK
    if latest_source_time < 0:
        raise ValueError("Phase 5F common source-time support is empty.")
    base_rng = random.Random(_derive_stream_seed(flow_seed, "paired_deadline_base"))
    deadline_rng = random.Random(_derive_stream_seed(flow_seed, "paired_deadline_uniform"))
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
    deadline_uniforms = {
        config.flow_id_start + offset: deadline_rng.random()
        for offset in range(num_flows)
    }
    deadline_profile = get_candidate_deadline_profile(deadline_profile_name)
    slack_counts = allocate_largest_remainder(
        num_flows,
        deadline_profile.slack_distribution.as_dict(),
        tie_order=sorted(deadline_profile.slack_distribution.probabilities),
    )
    nodes_by_layer = _nodes_by_layer(topology_result.metadata)
    ground_by_region = _ground_by_region(topology_result.metadata)
    flows = []
    per_flow = {}
    for offset in range(num_flows):
        flow_id = config.flow_id_start + offset
        category = traffic_labels[offset]
        stage_count = int(stage_labels[offset])
        src, dst, od_meta = _sample_od(category, base_rng, nodes_by_layer, ground_by_region, topology_result.metadata)
        arrival = base_rng.randint(0, latest_source_time)
        slack = _inverse_cdf_slack(deadline_profile.slack_distribution.as_dict(), deadline_uniforms[flow_id])
        deadline = arrival + slack
        if deadline >= topology_result.network.time_slots_num:
            raise ValueError("Phase 5F paired deadline generation clipped or exceeded the horizon.")
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
            "deadline_profile": deadline_profile.name,
            "deadline_uniform": deadline_uniforms[flow_id],
            "base_demand": base_demand,
            "demand_multiplier": config.demand_multiplier,
            "actual_demand": actual_demand,
            "processing_stage_count": stage_count,
            "total_chain_length": stage_count + 2,
            "vnf_label_sequence": list(labels),
            "processing_coefficient_sequence": list(coefficients),
            "contains_repeated_vnf_type": len(set(labels)) < len(labels),
        }
    aggregate = _aggregate_metadata(config, flows, per_flow, traffic_counts, stage_counts, slack_counts)
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
        "deadline_uniforms": deadline_uniforms,
        "deadline_uniform_fingerprint": sha256_fingerprint(sorted(deadline_uniforms.items())),
        "latest_source_time": latest_source_time,
    }


def deadline_invariant_flow_fingerprint(instance: SaginInstance) -> str:
    return sha256_fingerprint([_flow_payload(instance, flow_id, include_deadline=False) for flow_id in sorted(instance.flow_metadata["per_flow"])])


def full_flow_fingerprint(instance: SaginInstance) -> str:
    return sha256_fingerprint([_flow_payload(instance, flow_id, include_deadline=True) for flow_id in sorted(instance.flow_metadata["per_flow"])])


def _flow_payload(instance: SaginInstance, flow_id: int, include_deadline: bool) -> Dict[str, Any]:
    meta = instance.flow_metadata["per_flow"][flow_id]
    payload = {
        "flow_id": flow_id,
        "traffic_category": meta["traffic_category"],
        "source_node": meta["source_node"],
        "destination_node": meta["destination_node"],
        "source_layer": meta["source_layer"],
        "destination_layer": meta["destination_layer"],
        "source_ground_region": meta["source_ground_region"],
        "destination_ground_region": meta["destination_ground_region"],
        "arrival_time": meta["arrival_time"],
        "base_demand": meta["base_demand"],
        "demand_multiplier": meta["demand_multiplier"],
        "actual_demand": meta["actual_demand"],
        "processing_stage_count": meta["processing_stage_count"],
        "total_chain_length": meta["total_chain_length"],
        "vnf_label_sequence": meta["vnf_label_sequence"],
        "processing_coefficient_sequence": meta["processing_coefficient_sequence"],
        "contains_repeated_vnf_type": meta["contains_repeated_vnf_type"],
    }
    if include_deadline:
        payload.update({
            "deadline_time": meta["deadline_time"],
            "slack": meta["slack"],
            "deadline_profile": meta["deadline_profile"],
        })
    return payload


def _inverse_cdf_slack(probabilities: Mapping[int, float], u: float) -> int:
    cumulative = 0.0
    for slack in sorted(probabilities):
        cumulative += float(probabilities[slack])
        if u < cumulative:
            return int(slack)
    return int(max(probabilities))


def _derive_stream_seed(seed: int, namespace: str) -> int:
    return int(sha256_fingerprint({"seed": int(seed), "namespace": namespace})[:16], 16) & 0x7FFFFFFF


def _nodes_by_layer(topology_metadata: Mapping[str, Any]) -> Dict[str, List[int]]:
    layer_map = topology_metadata["node_structure"]["node_layer_map"]
    return {
        layer: sorted(int(node) for node, node_layer in layer_map.items() if node_layer == layer)
        for layer in ("Space", "Air", "Ground")
    }


def _ground_by_region(topology_metadata: Mapping[str, Any]) -> Dict[int, List[int]]:
    regions = {}
    for node, region in topology_metadata["node_structure"]["ground_region_map"].items():
        if region is not None:
            regions.setdefault(int(region), []).append(int(node))
    return {region: sorted(nodes) for region, nodes in regions.items()}


def _sample_od(category: str, rng: random.Random, nodes_by_layer: Mapping[str, List[int]], ground_by_region: Mapping[int, List[int]], topology_metadata: Mapping[str, Any]) -> Tuple[int, int, Dict[str, Any]]:
    if category == "cross_region_ground":
        regions = sorted(ground_by_region)
        src_region, dst_region = rng.sample(regions, 2)
        src = rng.choice(ground_by_region[src_region])
        dst = rng.choice(ground_by_region[dst_region])
    elif category == "same_region_ground":
        regions = [region for region, nodes in sorted(ground_by_region.items()) if len(nodes) >= 2]
        src_region = dst_region = rng.choice(regions)
        src, dst = rng.sample(ground_by_region[src_region], 2)
    elif category == "ground_nonground":
        ground = rng.choice(nodes_by_layer["Ground"])
        non_ground_layer = "Air" if rng.random() < 0.5 else "Space"
        non_ground = rng.choice(nodes_by_layer[non_ground_layer])
        if rng.random() < 0.5:
            src, dst = ground, non_ground
        else:
            src, dst = non_ground, ground
        src_region = topology_metadata["node_structure"]["ground_region_map"][src] if src == ground else None
        dst_region = topology_metadata["node_structure"]["ground_region_map"][dst] if dst == ground else None
    else:
        raise ValueError(f"Unknown traffic category {category!r}.")
    layer_map = topology_metadata["node_structure"]["node_layer_map"]
    return src, dst, {
        "source_node": src,
        "destination_node": dst,
        "source_layer": layer_map[src],
        "destination_layer": layer_map[dst],
        "source_ground_region": src_region,
        "destination_ground_region": dst_region,
    }


def _sample_vnf_labels(config: FlowGenerationConfig, stage_count: int, rng: random.Random) -> List[str]:
    catalog = list(config.sfc_profile.vnf_type_catalog)
    if config.sfc_profile.sample_with_replacement:
        return [rng.choice(catalog) for _ in range(stage_count)]
    return rng.sample(catalog, stage_count)


def _aggregate_metadata(config: FlowGenerationConfig, flows: List[Flow], per_flow: Dict[int, Dict[str, Any]], traffic_counts, stage_counts, slack_counts) -> Dict[str, Any]:
    values = list(per_flow.values())
    return {
        "configuration": {
            "generator_version": f"{FLOW_GENERATOR_VERSION}_paired_deadline_v1",
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
                "name": config.deadline_profile.name,
                "slack_distribution": config.deadline_profile.slack_distribution.as_dict(),
                "definition": "deadline_time = arrival_time + slack",
                "paired_deadline_sensitivity": True,
            },
        },
        "traffic": {
            "requested_category_counts": dict(traffic_counts),
            "realized_category_counts": _count((meta["traffic_category"] for meta in values), keys=TRAFFIC_CATEGORIES),
            "requested_category_ratios": config.traffic_mix_profile.probabilities(),
            "realized_category_ratios": _ratios(_count((meta["traffic_category"] for meta in values), keys=TRAFFIC_CATEGORIES), len(flows)),
            "source_layer_counts": _count(meta["source_layer"] for meta in values),
            "destination_layer_counts": _count(meta["destination_layer"] for meta in values),
            "cross_region_count": _count((meta["traffic_category"] for meta in values), keys=TRAFFIC_CATEGORIES)["cross_region_ground"],
        },
        "sfc": {
            "requested_stage_count_counts": dict(stage_counts),
            "realized_stage_count_counts": _count((meta["processing_stage_count"] for meta in values), keys=sorted(stage_counts)),
            "requested_stage_count_distribution": config.sfc_profile.stage_count_distribution.as_dict(),
            "realized_stage_count_distribution": _ratios(_count((meta["processing_stage_count"] for meta in values), keys=sorted(stage_counts)), len(flows)),
            "average_processing_stage_count": _mean([meta["processing_stage_count"] for meta in values]),
            "average_total_chain_length": _mean([meta["total_chain_length"] for meta in values]),
            "repeated_vnf_chain_count": sum(meta["contains_repeated_vnf_type"] for meta in values),
            "processing_coefficient_summary": _summary([coefficient for meta in values for coefficient in meta["processing_coefficient_sequence"]]),
        },
        "deadline": {
            "requested_slack_counts": dict(slack_counts),
            "realized_slack_counts": _count((meta["slack"] for meta in values), keys=sorted(slack_counts)),
            "requested_slack_distribution": config.deadline_profile.slack_distribution.as_dict(),
            "realized_slack_distribution": _ratios(_count((meta["slack"] for meta in values), keys=sorted(slack_counts)), len(flows)),
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
