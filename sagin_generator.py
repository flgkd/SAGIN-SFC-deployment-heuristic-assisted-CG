"""Structured candidate SAGIN topology generator.

This module builds a sparse undirected physical SAGIN graph and expands each
realized snapshot into the existing ``data_model.Network`` time-expanded graph.
Capacities are normalized synthetic resource units.
"""

from dataclasses import dataclass
import math
import random
from typing import Dict, Iterable, List, Set, Tuple

from config_schema import LINK_TYPES, TopologyConfig
from data_model import Network
from validation import validate_network


PhysicalEdge = Tuple[int, int]


@dataclass(frozen=True)
class PhysicalLink:
    u: int
    v: int
    link_type: str
    base_capacity: float

    def __post_init__(self):
        if self.u == self.v:
            raise ValueError("PhysicalLink cannot be a self-loop.")
        low, high = sorted((self.u, self.v))
        object.__setattr__(self, "u", low)
        object.__setattr__(self, "v", high)

    @property
    def edge(self) -> PhysicalEdge:
        return self.u, self.v


@dataclass(frozen=True)
class NetworkGenerationResult:
    network: Network
    physical_links: Tuple[PhysicalLink, ...]
    metadata: Dict


def generate_sagin_topology(config: TopologyConfig) -> NetworkGenerationResult:
    config.validate()
    return _StructuredSaginGenerator(config).generate()


def validate_generated_topology(result: NetworkGenerationResult) -> None:
    validate_network(result.network)
    metadata = result.metadata
    node_ids = metadata["node_structure"]["node_ids"]
    base_edges = [link.edge for link in result.physical_links]
    if len(base_edges) != len(set(base_edges)):
        raise ValueError("Duplicate physical links found.")
    if any(u == v for u, v in base_edges):
        raise ValueError("Self-loop physical link found.")
    if not _is_connected(node_ids, base_edges):
        raise ValueError("Base physical graph is disconnected.")

    network = result.network
    for snapshot in metadata["snapshots"]:
        time_slot = snapshot["time_slot"]
        active_edges = {tuple(edge) for edge in snapshot["active_edges"]}
        if not _is_connected(node_ids, active_edges):
            raise ValueError(f"Snapshot {time_slot} is disconnected.")
        for u, v in active_edges:
            forward = ((u, time_slot), (v, time_slot))
            reverse = ((v, time_slot), (u, time_slot))
            if forward not in network.time_edge_capacity or reverse not in network.time_edge_capacity:
                raise ValueError(f"Missing directed communication edges for {(u, v)} at t={time_slot}.")
            if network.time_edge_capacity[forward] != network.time_edge_capacity[reverse]:
                raise ValueError(f"Opposite directed capacities differ for {(u, v)} at t={time_slot}.")

    for node in network.nodes:
        for time_slot in range(network.time_slots_num - 1):
            edge = ((node, time_slot), (node, time_slot + 1))
            if edge not in network.time_edge_capacity or network.time_edge_capacity[edge] != math.inf:
                raise ValueError(f"Missing storage edge {edge}.")


class _StructuredSaginGenerator:
    def __init__(self, config: TopologyConfig):
        self.config = config
        self.base_rng = random.Random(config.seed)

    def generate(self) -> NetworkGenerationResult:
        node_metadata = self._build_nodes()
        physical_links = self._build_physical_links(node_metadata)
        snapshots = self._build_snapshots(physical_links, node_metadata)
        network = self._build_network(node_metadata, snapshots)
        metadata = self._build_metadata(node_metadata, physical_links, snapshots)
        result = NetworkGenerationResult(network, tuple(physical_links), metadata)
        validate_generated_topology(result)
        return result

    def _build_nodes(self) -> Dict:
        scale = self.config.scale
        nodes_by_layer = {"Space": [], "Air": [], "Ground": []}
        node_layer = {}
        layer_local_index = {}
        ground_region = {}
        base_node_capacity = {}
        node_id = 0
        for layer, count in (("Space", scale.space_nodes), ("Air", scale.air_nodes), ("Ground", scale.ground_nodes)):
            cap_range = self.config.capacity_profile.node_capacity_by_layer[layer]
            for local_index in range(count):
                node_layer[node_id] = layer
                layer_local_index[node_id] = local_index
                nodes_by_layer[layer].append(node_id)
                base_node_capacity[node_id] = self.base_rng.randint(int(cap_range.low), int(cap_range.high))
                if layer == "Ground":
                    region_size = scale.ground_nodes // scale.ground_regions
                    ground_region[node_id] = local_index // region_size
                else:
                    ground_region[node_id] = None
                node_id += 1
        return {
            "node_ids": list(range(node_id)),
            "nodes_by_layer": nodes_by_layer,
            "node_layer": node_layer,
            "layer_local_index": layer_local_index,
            "ground_region": ground_region,
            "base_node_capacity": base_node_capacity,
        }

    def _build_physical_links(self, node_metadata: Dict) -> List[PhysicalLink]:
        links: Dict[PhysicalEdge, PhysicalLink] = {}
        space = node_metadata["nodes_by_layer"]["Space"]
        air = node_metadata["nodes_by_layer"]["Air"]
        ground = node_metadata["nodes_by_layer"]["Ground"]

        def add(u: int, v: int, link_type: str) -> None:
            link = PhysicalLink(u, v, link_type, self._sample_link_capacity(link_type))
            links.setdefault(link.edge, link)

        for i, node in enumerate(space):
            add(node, space[(i + 1) % len(space)], "SS")
            add(node, space[(i + 2) % len(space)], "SS")

        for i, node in enumerate(air):
            add(node, air[(i + 1) % len(air)], "AA")

        for i, air_node in enumerate(air):
            first_index = (i * len(space)) // len(air)
            second_index = (first_index + max(2, len(space) // 2)) % len(space)
            if second_index == first_index:
                second_index = (first_index + 1) % len(space)
            add(air_node, space[first_index], "SA")
            add(air_node, space[second_index], "SA")

        region_size = len(ground) // self.config.scale.ground_regions
        for region in range(self.config.scale.ground_regions):
            region_nodes = ground[region * region_size:(region + 1) * region_size]
            if len(region_nodes) == 2:
                add(region_nodes[0], region_nodes[1], "GG")
            else:
                for i, node in enumerate(region_nodes):
                    add(node, region_nodes[(i + 1) % len(region_nodes)], "GG")

            primary_air = air[region % len(air)]
            for local_index, ground_node in enumerate(region_nodes):
                secondary_air = air[(region + 1 + local_index) % len(air)]
                if secondary_air == primary_air:
                    secondary_air = air[(air.index(primary_air) + 1) % len(air)]
                add(ground_node, primary_air, "AG")
                add(ground_node, secondary_air, "AG")

        ordered = [links[edge] for edge in sorted(links)]
        if not _is_connected(node_metadata["node_ids"], [link.edge for link in ordered]):
            raise RuntimeError("Structured base physical graph is disconnected.")
        return ordered

    def _build_snapshots(self, physical_links: List[PhysicalLink], node_metadata: Dict) -> List[Dict]:
        snapshots = []
        base_edges = [link.edge for link in physical_links]
        link_by_edge = {link.edge: link for link in physical_links}
        for time_slot in range(self.config.time_slots):
            attempts = 1
            if self.config.dynamic_topology:
                active_edges: Set[PhysicalEdge] = set()
                for attempts in range(1, self.config.dynamics_profile.max_connectivity_resamples + 1):
                    rng = random.Random(_derive_seed(self.config.seed, "snapshot", time_slot, attempts))
                    active_edges = {
                        link.edge for link in physical_links
                        if rng.random() <= self.config.dynamics_profile.survival_probability_by_type[link.link_type]
                    }
                    if _is_connected(node_metadata["node_ids"], active_edges):
                        break
                else:
                    raise RuntimeError(
                        f"Could not sample a connected snapshot at time {time_slot} "
                        f"after {self.config.dynamics_profile.max_connectivity_resamples} attempts."
                    )
            else:
                active_edges = set(base_edges)

            node_capacity = {}
            for node_id, base_capacity in node_metadata["base_node_capacity"].items():
                rng = random.Random(_derive_seed(self.config.seed, "node_capacity", time_slot, node_id))
                node_capacity[node_id] = _vary(base_capacity, self.config.dynamics_profile.node_capacity_variation_ratio, rng)

            link_capacity = {}
            for edge in sorted(active_edges):
                link = link_by_edge[edge]
                rng = random.Random(_derive_seed(self.config.seed, "link_capacity", time_slot, edge[0], edge[1]))
                link_capacity[edge] = _vary(link.base_capacity, self.config.dynamics_profile.link_capacity_variation_ratio, rng)

            snapshots.append({
                "time_slot": time_slot,
                "active_edges": sorted(active_edges),
                "connectivity_resampling_attempts": attempts,
                "varied_node_capacity": node_capacity,
                "varied_link_capacity": link_capacity,
            })
        return snapshots

    def _build_network(self, node_metadata: Dict, snapshots: List[Dict]) -> Network:
        network = Network()
        network.set_nodes(len(node_metadata["node_ids"]))
        network.set_time_slots(self.config.time_slots)
        network.set_resource_weight(self.config.resource_weight)
        for snapshot in snapshots:
            time_slot = snapshot["time_slot"]
            for node_id in node_metadata["node_ids"]:
                network.add_time_node((node_id, time_slot), snapshot["varied_node_capacity"][node_id])
        for snapshot in snapshots:
            time_slot = snapshot["time_slot"]
            for u, v in snapshot["active_edges"]:
                network.add_comm_time_edge((u, time_slot), (v, time_slot), snapshot["varied_link_capacity"][(u, v)])
        for node_id in node_metadata["node_ids"]:
            for time_slot in range(self.config.time_slots - 1):
                network.add_storage_time_edge((node_id, time_slot))
        network.compute_total_time_node_capacity()
        network.compute_total_comm_edge_capacity()
        return network

    def _build_metadata(self, node_metadata: Dict, physical_links: List[PhysicalLink], snapshots: List[Dict]) -> Dict:
        link_type_by_edge = {link.edge: link.link_type for link in physical_links}
        base_counts = _counts_by_type(link.link_type for link in physical_links)
        degrees = _degrees(node_metadata["node_ids"], link_type_by_edge)
        snapshot_meta = []
        active_counts = []
        survival_values = {link_type: [] for link_type in LINK_TYPES}
        for snapshot in snapshots:
            active_edges = {tuple(edge) for edge in snapshot["active_edges"]}
            active_counts.append(len(active_edges))
            active_by_type = _counts_by_type(link_type_by_edge[edge] for edge in active_edges)
            realized = {
                link_type: (active_by_type[link_type] / base_counts[link_type] if base_counts[link_type] else 0.0)
                for link_type in LINK_TYPES
            }
            for link_type, value in realized.items():
                survival_values[link_type].append(value)
            snapshot_meta.append({
                "time_slot": snapshot["time_slot"],
                "active_edges": [list(edge) for edge in sorted(active_edges)],
                "active_physical_link_count": len(active_edges),
                "active_link_counts_by_type": active_by_type,
                "connectivity_resampling_attempts": snapshot["connectivity_resampling_attempts"],
                "realized_survival_rate_by_type": realized,
                "varied_node_capacity_summary": _summary(list(snapshot["varied_node_capacity"].values())),
                "varied_link_capacity_summary": _summary(list(snapshot["varied_link_capacity"].values())),
            })

        churn_rates = []
        for previous, current in zip(snapshots, snapshots[1:]):
            prev_edges = {tuple(edge) for edge in previous["active_edges"]}
            curr_edges = {tuple(edge) for edge in current["active_edges"]}
            churn_rates.append(len(prev_edges.symmetric_difference(curr_edges)) / len(physical_links))

        return {
            "configuration": {
                "generator_version": "v2_phase2a_structured_candidate",
                "seed": self.config.seed,
                "scale_name": self.config.scale.name,
                "time_slots": self.config.time_slots,
                "resource_weight": self.config.resource_weight,
                "dynamic_topology": self.config.dynamic_topology,
                "capacity_profile": _capacity_profile_metadata(self.config),
                "dynamics_profile": _dynamics_profile_metadata(self.config),
            },
            "node_structure": {
                "space_nodes": self.config.scale.space_nodes,
                "air_nodes": self.config.scale.air_nodes,
                "ground_nodes": self.config.scale.ground_nodes,
                "node_ids": list(node_metadata["node_ids"]),
                "node_layer_map": dict(node_metadata["node_layer"]),
                "layer_local_index_map": dict(node_metadata["layer_local_index"]),
                "ground_region_map": dict(node_metadata["ground_region"]),
                "base_node_capacities": dict(node_metadata["base_node_capacity"]),
            },
            "physical_topology": {
                "physical_link_count": len(physical_links),
                "physical_link_counts_by_type": base_counts,
                "average_physical_degree": sum(degrees.values()) / len(degrees),
                "minimum_physical_degree": min(degrees.values()),
                "maximum_physical_degree": max(degrees.values()),
                "base_connectivity_status": _is_connected(node_metadata["node_ids"], link_type_by_edge),
                "base_physical_links": [
                    {"u": link.u, "v": link.v, "link_type": link.link_type, "base_capacity": link.base_capacity}
                    for link in physical_links
                ],
            },
            "snapshots": snapshot_meta,
            "aggregate_dynamics": {
                "average_active_physical_links": sum(active_counts) / len(active_counts),
                "snapshot_to_snapshot_link_churn_rate": sum(churn_rates) / len(churn_rates) if churn_rates else 0.0,
                "mean_realized_survival_by_type": {
                    link_type: sum(values) / len(values) for link_type, values in survival_values.items()
                },
            },
        }

    def _sample_link_capacity(self, link_type: str) -> int:
        cap_range = self.config.capacity_profile.link_capacity_by_type[link_type]
        return self.base_rng.randint(int(cap_range.low), int(cap_range.high))


def _derive_seed(base_seed: int, label: str, *parts: int) -> int:
    value = (int(base_seed) + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    for char in label:
        value = ((value * 131) + ord(char)) & 0xFFFFFFFFFFFFFFFF
    for part in parts:
        value = ((value * 1000003) ^ int(part)) & 0xFFFFFFFFFFFFFFFF
    return value


def _vary(base_capacity: float, ratio: float, rng: random.Random) -> float:
    if ratio == 0.0:
        return float(base_capacity)
    return float(base_capacity) * rng.uniform(1.0 - ratio, 1.0 + ratio)


def _is_connected(nodes: Iterable[int], edges: Iterable[PhysicalEdge]) -> bool:
    nodes = list(nodes)
    if not nodes:
        return False
    adjacency = {node: set() for node in nodes}
    for u, v in edges:
        adjacency[u].add(v)
        adjacency[v].add(u)
    seen = {nodes[0]}
    stack = [nodes[0]]
    while stack:
        current = stack.pop()
        for neighbor in adjacency[current]:
            if neighbor not in seen:
                seen.add(neighbor)
                stack.append(neighbor)
    return len(seen) == len(nodes)


def _counts_by_type(link_types: Iterable[str]) -> Dict[str, int]:
    counts = {link_type: 0 for link_type in LINK_TYPES}
    for link_type in link_types:
        counts[link_type] += 1
    return counts


def _degrees(nodes: Iterable[int], edges: Iterable[PhysicalEdge]) -> Dict[int, int]:
    degrees = {node: 0 for node in nodes}
    for u, v in edges:
        degrees[u] += 1
        degrees[v] += 1
    return degrees


def _summary(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"min": 0.0, "max": 0.0, "mean": 0.0}
    return {"min": min(values), "max": max(values), "mean": sum(values) / len(values)}


def _capacity_profile_metadata(config: TopologyConfig) -> Dict:
    return {
        "candidate_profile": True,
        "node_capacity_by_layer": {
            layer: {"low": cap.low, "high": cap.high}
            for layer, cap in config.capacity_profile.node_capacity_by_layer.items()
        },
        "link_capacity_by_type": {
            link_type: {"low": cap.low, "high": cap.high}
            for link_type, cap in config.capacity_profile.link_capacity_by_type.items()
        },
    }


def _dynamics_profile_metadata(config: TopologyConfig) -> Dict:
    profile = config.dynamics_profile
    return {
        "candidate_profile": True,
        "survival_probability_by_type": dict(profile.survival_probability_by_type),
        "node_capacity_variation_ratio": profile.node_capacity_variation_ratio,
        "link_capacity_variation_ratio": profile.link_capacity_variation_ratio,
        "max_connectivity_resamples": profile.max_connectivity_resamples,
        "connectivity_conditioning": (
            "Dynamic snapshots are resampled until connected, so realized "
            "survival rates may differ from raw input probabilities."
        ),
    }
