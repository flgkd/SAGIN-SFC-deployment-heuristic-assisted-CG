"""Controlled candidate SFC flow generation.

The profiles in this module are synthetic candidate profiles. Demands and
processing coefficients are normalized resource units, not operator trace data.
"""

from dataclasses import dataclass
import random
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from config_schema import (
    FlowGenerationConfig,
    NetworkScaleConfig,
    TopologyConfig,
    TRAFFIC_CATEGORIES,
)
from data_model import Flow
from sagin_generator import NetworkGenerationResult


GENERATOR_VERSION = "v2_phase2b_flow_candidate"


@dataclass(frozen=True)
class FlowGenerationResult:
    flows: Tuple[Flow, ...]
    per_flow_metadata: Dict[int, Dict]
    aggregate_metadata: Dict


def default_num_flows_for_scale(scale: NetworkScaleConfig) -> int:
    """Candidate weak-scaling rule: F = 4 * physical_node_count."""

    scale.validate()
    return 4 * (scale.space_nodes + scale.air_nodes + scale.ground_nodes)


def default_num_flows_for_topology(topology: NetworkGenerationResult) -> int:
    return 4 * len(topology.metadata["node_structure"]["node_ids"])


def resolve_num_flows(config: FlowGenerationConfig, topology: NetworkGenerationResult) -> int:
    config.validate()
    if config.num_flows is not None:
        return config.num_flows
    return default_num_flows_for_topology(topology)


def allocate_largest_remainder(total: int, probabilities: Mapping, tie_order: Optional[Sequence] = None) -> Dict:
    if total < 0:
        raise ValueError("total must be nonnegative.")
    if not probabilities:
        raise ValueError("probabilities must not be empty.")
    probability_sum = sum(float(value) for value in probabilities.values())
    if abs(probability_sum - 1.0) > 1e-9:
        raise ValueError(f"probabilities must sum to 1, got {probability_sum}.")
    order = list(tie_order) if tie_order is not None else sorted(probabilities)
    missing = set(probabilities) - set(order)
    if missing:
        order.extend(sorted(missing))
    raw = {key: total * float(probabilities[key]) for key in probabilities}
    counts = {key: int(raw[key] // 1) for key in probabilities}
    remaining = total - sum(counts.values())
    ranked = sorted(
        probabilities,
        key=lambda key: (-(raw[key] - counts[key]), order.index(key) if key in order else len(order), str(key)),
    )
    for key in ranked[:remaining]:
        counts[key] += 1
    return counts


def shuffled_quota_labels(
    total: int,
    probabilities: Mapping,
    rng: random.Random,
    tie_order: Optional[Sequence] = None,
) -> Tuple[List, Dict]:
    counts = allocate_largest_remainder(total, probabilities, tie_order=tie_order)
    labels = []
    for key in (tie_order or sorted(counts)):
        labels.extend([key] * counts.get(key, 0))
    rng.shuffle(labels)
    return labels, counts


def generate_flows(
    config: FlowGenerationConfig,
    topology: NetworkGenerationResult,
) -> FlowGenerationResult:
    config.validate()
    generator = _FlowGenerator(config, topology)
    return generator.generate()


class _FlowGenerator:
    def __init__(self, config: FlowGenerationConfig, topology: NetworkGenerationResult):
        self.config = config
        self.topology = topology
        self.rng = random.Random(config.seed)
        self.node_meta = topology.metadata["node_structure"]
        self.time_slots = topology.network.time_slots_num
        self.num_flows = resolve_num_flows(config, topology)
        self._validate_horizon()
        self.nodes_by_layer = self._nodes_by_layer()
        self.ground_by_region = self._ground_by_region()

    def generate(self) -> FlowGenerationResult:
        traffic_labels, traffic_counts = shuffled_quota_labels(
            self.num_flows,
            self.config.traffic_mix_profile.probabilities(),
            self.rng,
            tie_order=TRAFFIC_CATEGORIES,
        )
        stage_labels, stage_counts = shuffled_quota_labels(
            self.num_flows,
            self.config.sfc_profile.stage_count_distribution.as_dict(),
            self.rng,
            tie_order=sorted(self.config.sfc_profile.stage_count_distribution.probabilities),
        )
        slack_labels, slack_counts = shuffled_quota_labels(
            self.num_flows,
            self.config.deadline_profile.slack_distribution.as_dict(),
            self.rng,
            tie_order=sorted(self.config.deadline_profile.slack_distribution.probabilities),
        )

        flows: List[Flow] = []
        per_flow: Dict[int, Dict] = {}
        for offset in range(self.num_flows):
            flow_id = self.config.flow_id_start + offset
            category = traffic_labels[offset]
            stage_count = int(stage_labels[offset])
            slack = int(slack_labels[offset])
            src, dst, od_meta = self._sample_od(category)
            arrival = self.rng.randint(0, self.time_slots - 1 - slack)
            deadline = arrival + slack
            base_demand = self.rng.randint(self.config.base_demand_low, self.config.base_demand_high)
            actual_demand = float(base_demand) * float(self.config.demand_multiplier)
            labels = self._sample_vnf_labels(stage_count)
            coefficients = [
                self.rng.uniform(
                    self.config.sfc_profile.processing_coefficient_low,
                    self.config.sfc_profile.processing_coefficient_high,
                )
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
                "deadline_profile": self.config.deadline_profile.name,
                "base_demand": base_demand,
                "demand_multiplier": self.config.demand_multiplier,
                "actual_demand": actual_demand,
                "processing_stage_count": stage_count,
                "total_chain_length": stage_count + 2,
                "vnf_label_sequence": list(labels),
                "processing_coefficient_sequence": list(coefficients),
                "contains_repeated_vnf_type": len(set(labels)) < len(labels),
            }

        aggregate = self._aggregate_metadata(
            flows, per_flow, traffic_counts, stage_counts, slack_counts
        )
        return FlowGenerationResult(tuple(flows), per_flow, aggregate)

    def _validate_horizon(self) -> None:
        max_slack = max(self.config.deadline_profile.slack_distribution.probabilities)
        if self.time_slots < max_slack + 1:
            raise ValueError(
                f"Deadline profile {self.config.deadline_profile.name} requires at least "
                f"{max_slack + 1} time slots, got {self.time_slots}."
            )

    def _nodes_by_layer(self) -> Dict[str, List[int]]:
        layer_map = self.node_meta["node_layer_map"]
        return {
            layer: sorted(int(node) for node, node_layer in layer_map.items() if node_layer == layer)
            for layer in ("Space", "Air", "Ground")
        }

    def _ground_by_region(self) -> Dict[int, List[int]]:
        regions: Dict[int, List[int]] = {}
        for node, region in self.node_meta["ground_region_map"].items():
            if region is not None:
                regions.setdefault(int(region), []).append(int(node))
        return {region: sorted(nodes) for region, nodes in regions.items()}

    def _sample_od(self, category: str) -> Tuple[int, int, Dict]:
        if category == "cross_region_ground":
            regions = sorted(self.ground_by_region)
            src_region, dst_region = self.rng.sample(regions, 2)
            src = self.rng.choice(self.ground_by_region[src_region])
            dst = self.rng.choice(self.ground_by_region[dst_region])
        elif category == "same_region_ground":
            regions = [region for region, nodes in sorted(self.ground_by_region.items()) if len(nodes) >= 2]
            if not regions:
                raise ValueError("Same-region Ground traffic requires a region with at least two Ground nodes.")
            src_region = dst_region = self.rng.choice(regions)
            src, dst = self.rng.sample(self.ground_by_region[src_region], 2)
        elif category == "ground_nonground":
            ground = self.rng.choice(self.nodes_by_layer["Ground"])
            non_ground_layer = "Air" if self.rng.random() < 0.5 else "Space"
            non_ground = self.rng.choice(self.nodes_by_layer[non_ground_layer])
            if self.rng.random() < 0.5:
                src, dst = ground, non_ground
            else:
                src, dst = non_ground, ground
            src_region = self.node_meta["ground_region_map"][src] if src == ground else None
            dst_region = self.node_meta["ground_region_map"][dst] if dst == ground else None
        else:
            raise ValueError(f"Unknown traffic category {category!r}.")
        if src == dst:
            raise RuntimeError(f"Generated invalid OD with source == destination for category {category}.")
        layer_map = self.node_meta["node_layer_map"]
        return src, dst, {
            "source_node": src,
            "destination_node": dst,
            "source_layer": layer_map[src],
            "destination_layer": layer_map[dst],
            "source_ground_region": src_region,
            "destination_ground_region": dst_region,
        }

    def _sample_vnf_labels(self, stage_count: int) -> List[str]:
        catalog = list(self.config.sfc_profile.vnf_type_catalog)
        if self.config.sfc_profile.sample_with_replacement:
            return [self.rng.choice(catalog) for _ in range(stage_count)]
        return self.rng.sample(catalog, stage_count)

    def _aggregate_metadata(
        self,
        flows: List[Flow],
        per_flow: Dict[int, Dict],
        traffic_counts: Dict,
        stage_counts: Dict,
        slack_counts: Dict,
    ) -> Dict:
        demand_values = [meta["actual_demand"] for meta in per_flow.values()]
        base_demands = [meta["base_demand"] for meta in per_flow.values()]
        coefficients = [
            coefficient
            for meta in per_flow.values()
            for coefficient in meta["processing_coefficient_sequence"]
        ]
        arrivals = [meta["arrival_time"] for meta in per_flow.values()]
        deadlines = [meta["deadline_time"] for meta in per_flow.values()]
        slacks = [meta["slack"] for meta in per_flow.values()]
        stage_count_values = [meta["processing_stage_count"] for meta in per_flow.values()]
        chain_lengths = [meta["total_chain_length"] for meta in per_flow.values()]
        source_layer_counts = _count(meta["source_layer"] for meta in per_flow.values())
        destination_layer_counts = _count(meta["destination_layer"] for meta in per_flow.values())
        realized_categories = _count((meta["traffic_category"] for meta in per_flow.values()), keys=TRAFFIC_CATEGORIES)
        realized_stage_counts = _count(stage_count_values, keys=sorted(stage_counts))
        realized_slack_counts = _count(slacks, keys=sorted(slack_counts))
        return {
            "configuration": {
                "generator_version": GENERATOR_VERSION,
                "seed": self.config.seed,
                "requested_flow_count": self.num_flows,
                "actual_flow_count": len(flows),
                "base_demand_range": {"low": self.config.base_demand_low, "high": self.config.base_demand_high},
                "demand_multiplier": self.config.demand_multiplier,
                "load_profile_name": self.config.load_profile_name,
                "traffic_mix_profile": {
                    "name": self.config.traffic_mix_profile.name,
                    "probabilities": self.config.traffic_mix_profile.probabilities(),
                },
                "sfc_profile": {
                    "name": self.config.sfc_profile.name,
                    "stage_count_distribution": self.config.sfc_profile.stage_count_distribution.as_dict(),
                    "vnf_type_catalog": list(self.config.sfc_profile.vnf_type_catalog),
                    "processing_coefficient_range": {
                        "low": self.config.sfc_profile.processing_coefficient_low,
                        "high": self.config.sfc_profile.processing_coefficient_high,
                    },
                    "sample_with_replacement": self.config.sfc_profile.sample_with_replacement,
                },
                "deadline_profile": {
                    "name": self.config.deadline_profile.name,
                    "slack_distribution": self.config.deadline_profile.slack_distribution.as_dict(),
                    "definition": "deadline_time = arrival_time + slack",
                },
            },
            "traffic": {
                "requested_category_counts": dict(traffic_counts),
                "realized_category_counts": realized_categories,
                "requested_category_ratios": self.config.traffic_mix_profile.probabilities(),
                "realized_category_ratios": _ratios(realized_categories, len(flows)),
                "source_layer_counts": source_layer_counts,
                "destination_layer_counts": destination_layer_counts,
                "cross_region_count": realized_categories["cross_region_ground"],
            },
            "sfc": {
                "requested_stage_count_counts": dict(stage_counts),
                "realized_stage_count_counts": realized_stage_counts,
                "requested_stage_count_distribution": self.config.sfc_profile.stage_count_distribution.as_dict(),
                "realized_stage_count_distribution": _ratios(realized_stage_counts, len(flows)),
                "average_processing_stage_count": _mean(stage_count_values),
                "average_total_chain_length": _mean(chain_lengths),
                "repeated_vnf_chain_count": sum(meta["contains_repeated_vnf_type"] for meta in per_flow.values()),
                "processing_coefficient_summary": _summary(coefficients),
            },
            "deadline": {
                "requested_slack_counts": dict(slack_counts),
                "realized_slack_counts": realized_slack_counts,
                "requested_slack_distribution": self.config.deadline_profile.slack_distribution.as_dict(),
                "realized_slack_distribution": _ratios(realized_slack_counts, len(flows)),
                "arrival_time_summary": _summary(arrivals),
                "deadline_time_summary": _summary(deadlines),
                "minimum_slack": min(slacks) if slacks else 0,
                "mean_slack": _mean(slacks),
                "maximum_slack": max(slacks) if slacks else 0,
                "clipping_count": 0,
            },
            "demand": {
                "base_demand_summary": _summary(base_demands),
                "actual_demand_summary": _summary(demand_values),
                "total_base_demand": sum(base_demands),
                "total_actual_demand": sum(demand_values),
            },
        }


def _count(values: Iterable, keys: Optional[Iterable] = None) -> Dict:
    counts = {key: 0 for key in keys} if keys is not None else {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _ratios(counts: Mapping, total: int) -> Dict:
    return {key: (value / total if total else 0.0) for key, value in counts.items()}


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _summary(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"min": 0.0, "max": 0.0, "mean": 0.0}
    return {"min": min(values), "max": max(values), "mean": sum(values) / len(values)}
