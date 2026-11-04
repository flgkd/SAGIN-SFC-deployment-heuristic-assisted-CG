# data_model.py

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import math

# =========================
# Basic Types
# =========================

Time = int
Node = int
Edge = Tuple[Node, Node]

TimeNode = Tuple[Node, Time]
TimeEdge = Tuple[TimeNode, TimeNode]

NON_PROCESSING_VNFS = {"SRC", "DST"}

Default_Nodes_Num = 10
Default_Time_Slots_Num = 20

# =========================
# Network Model (Time-Expanded Graph)
# =========================


class Network:
    """
    Time-expanded network for space-ground SFC deployment.

    Features:
    - Communication time edges: (u,t) -> (v,t), bidirectional
    - Storage time edges: (m,t) -> (m,t+1), directed
    - Node capacity: processing resource
    - Edge capacity: transmission resource
    """

    def __init__(self):
        # Here can consider replace List to Set
        self.nodes_num: int = Default_Nodes_Num

        # Note node num starts from 0
        self.nodes: List[int] = list(range(0, Default_Nodes_Num))

        self.time_slots_num: int = Default_Time_Slots_Num

        # Note time slot num starts from 0
        self.time_slots: List[int] = list(range(0, Default_Time_Slots_Num))

        self.time_nodes: List[TimeNode] = []

        self.time_edges: List[TimeEdge] = []

        # Node capacity
        self.time_node_capacity: Dict[TimeNode, float] = {}

        # Link capacity
        self.time_edge_capacity: Dict[TimeEdge, float] = {}

        # Adjacency (CRITICAL for SP performance)
        self.out_time_edges: Dict[TimeNode, List[TimeNode]] = {}
        self.in_time_edges: Dict[TimeNode, List[TimeNode]] = {}

        # Resource weight coefficient (0~1)
        self.resource_weight: float = 0.5

        # Total node capacity (computed when needed)
        self.total_time_node_capacity: float = 0.0

        # Total communication edge capacity (computed when needed)
        self.total_comm_edge_capacity: float = 0.0

    def set_nodes(self, new_nodes_num: int):
        if not isinstance(new_nodes_num, int):
            raise TypeError("Nodes num must be an integer.")

        if new_nodes_num < 1:
            raise ValueError("Nodes num must be >= 1.")

        self.nodes_num = new_nodes_num
        self.nodes = list(range(0, new_nodes_num))

    def get_nodes(self) -> List[int]:
        return self.nodes

    def set_time_slots(self, new_time_slots_num: int):
        if not isinstance(new_time_slots_num, int):
            raise TypeError("Time_slots num must be an integer.")

        if new_time_slots_num < 1:
            raise ValueError("Time_slots num must be >= 1.")

        self.time_slots_num = new_time_slots_num
        self.time_slots = list(range(0, new_time_slots_num))

    def get_time_slots(self) -> List[int]:
        return self.time_slots

    def add_time_node(self, new_time_node: TimeNode, capacity: float):
        if new_time_node in self.time_nodes:
            raise ValueError("This time node already exists.")
        node = new_time_node[0]
        time = new_time_node[1]

        if node < 0 or node > self.nodes_num - 1:
            raise ValueError("Invalid node for a time node.")

        if time < 0 or time > self.time_slots_num - 1:
            raise ValueError("Invalid time for a time node.")

        self.time_nodes.append(new_time_node)
        self.time_node_capacity[new_time_node] = capacity
        self.total_time_node_capacity = 0.0

    def set_time_node_capacity(self, time_node: TimeNode, capacity: float):
        if time_node not in self.time_nodes:
            raise ValueError("This time node does not exist.")

        self.time_node_capacity[time_node] = capacity
        self.total_time_node_capacity = 0.0

    def get_time_node_capacity(self, time_node: TimeNode) -> float:
        if time_node not in self.time_nodes:
            raise ValueError("This time node does not exist.")

        return self.time_node_capacity[time_node]

    def _add_out_time_edge_to_adj(self, time_node: TimeNode, next_time_node: TimeNode):
        self.out_time_edges.setdefault(time_node, []).append(next_time_node)

    def _add_in_time_edge_to_adj(self, time_node: TimeNode, pre_time_node: TimeNode):
        self.in_time_edges.setdefault(time_node, []).append(pre_time_node)

    def add_comm_time_edge(self, time_node1: TimeNode, time_node2: TimeNode, capacity: float):
        """
        Add bidirectional communication time edge:
        (u,t) <-> (v,t)
        """
        if (time_node1, time_node2) in self.time_edges or (time_node2, time_node1) in self.time_edges:
            raise ValueError("This communication time edge already exists.")

        if time_node1 == time_node2:
            raise ValueError("Invalid time nodes for a communication time edge.")

        time1 = time_node1[1]
        time2 = time_node2[1]

        if time1 != time2:
            raise ValueError(f"Time slot mismatch for a communication time edge: "
                             f"time_node1 time={time1}, time_node2 time={time2}")

        time_edge1 = (time_node1, time_node2)
        self.time_edges.append(time_edge1)
        self.time_edge_capacity[time_edge1] = capacity
        self.total_comm_edge_capacity = 0.0
        self._add_out_time_edge_to_adj(time_node1, time_node2)
        self._add_in_time_edge_to_adj(time_node2, time_node1)

        time_edge2 = (time_node2, time_node1)
        self.time_edges.append(time_edge2)
        self.time_edge_capacity[time_edge2] = capacity
        self.total_comm_edge_capacity = 0.0
        self._add_out_time_edge_to_adj(time_node2, time_node1)
        self._add_in_time_edge_to_adj(time_node1, time_node2)

    def add_storage_time_edge(self, time_node: TimeNode):
        """
        Add storage time edges:
        (m,t) -> (m,t+1)
        """
        node = time_node[0]
        time = time_node[1]

        if (time_node, (node, time + 1)) in self.time_edges:
            raise ValueError("This storage time edge already exists.")

        if time >= self.time_slots_num - 1:
            raise ValueError("Invalid time slot for a storage time edge.")

        time_edge = (time_node, (node, time + 1))
        self.time_edges.append(time_edge)
        self.time_edge_capacity[time_edge] = math.inf
        self._add_out_time_edge_to_adj(time_node, (node, time + 1))
        self._add_in_time_edge_to_adj((node, time + 1), time_node)

    def set_time_edge_capacity(self, time_edge: TimeEdge, capacity: float):
        if time_edge not in self.time_edges:
            raise ValueError("This time edge does not exist.")

        if self.time_edge_capacity[time_edge] == math.inf:
            raise ValueError("Invalid capacity setting for the storage time edge.")

        self.time_edge_capacity[time_edge] = capacity
        self.total_comm_edge_capacity = 0.0

    def get_time_edge_capacity(self, time_edge: TimeEdge) -> float:
        if time_edge not in self.time_edges:
            raise ValueError("This time edge does not exist.")

        return self.time_edge_capacity[time_edge]

    def set_resource_weight(self, weight: float):
        if not isinstance(weight, (int, float)):
            raise TypeError("Resource weight must be a number.")

        if weight < 0.0 or weight > 1.0:
            raise ValueError("Resource weight must be in [0, 1].")

        self.resource_weight = float(weight)

    def compute_total_time_node_capacity(self):
        total = sum(self.time_node_capacity.values())
        self.total_time_node_capacity = total

    def compute_total_comm_edge_capacity(self):
        total = 0.0

        for edge in self.time_edges:
            # communication edges are characterized by having the same time slot.
            if edge[0][1] == edge[1][1]:
                total += self.time_edge_capacity[edge]

        self.total_comm_edge_capacity = total

@dataclass(frozen=True)
class VNFStage:
    """One processing stage in a flow SFC.

    ``stage_index`` is the canonical placement key. ``vnf_type`` is only a
    label, so repeated types such as A -> B -> A remain unambiguous.
    """

    stage_index: int
    vnf_type: str
    processing_coefficient: float


@dataclass(frozen=True)
class VirtualSegment:
    """One ordered segment between structural endpoints or processing stages."""

    segment_index: int
    from_stage_index: Optional[int]
    to_stage_index: Optional[int]


# =========================
# Flow Model
# =========================


class Flow:
    def __init__(
        self,
        flow_id: int,
        flow_len: int,
        flow_src_node: Node,
        flow_src_time: Time,
        flow_dst_node: Node,
        flow_dst_max_time: Time,
        flow_sfc: List[str],
        flow_vnf_cost: Dict[str, float],
        flow_demand: float,
        stage_processing_coefficients: Optional[Sequence[float]] = None,
    ):
        self.flow_id = flow_id
        self.flow_len = flow_len

        self.flow_src_time_node = (flow_src_node, flow_src_time)
        self.flow_dst_node = flow_dst_node
        self.flow_dst_max_time = flow_dst_max_time

        self.flow_sfc = list(flow_sfc)
        if stage_processing_coefficients is not None:
            if len(stage_processing_coefficients) != len(self.flow_sfc):
                raise ValueError(
                    "stage_processing_coefficients must have one value per processing stage."
                )
            processing_coefficients = [float(value) for value in stage_processing_coefficients]
        else:
            processing_coefficients = [
                float(flow_vnf_cost.get(vnf_type, 0.0)) for vnf_type in self.flow_sfc
            ]
        self.processing_stages: List[VNFStage] = [
            VNFStage(
                stage_index=i,
                vnf_type=vnf_type,
                processing_coefficient=processing_coefficients[i],
            )
            for i, vnf_type in enumerate(self.flow_sfc)
        ]
        self.stage_index_to_stage: Dict[int, VNFStage] = {
            stage.stage_index: stage for stage in self.processing_stages
        }

        # Compatibility labels for solver variables. SRC/DST are structural
        # endpoints; canonical placement is still keyed only by processing
        # stage_index.
        self.flow_extended_sfc = ["SRC"] + self.flow_sfc + ["DST"]

        self.flow_vnf_cost = flow_vnf_cost
        self.flow_extended_vnf_cost = [
            0.0,
            *processing_coefficients,
            0.0,
        ]

        self.flow_virtual_links = [
            (self.flow_extended_sfc[i - 1], self.flow_extended_sfc[i])
            for i in range(1, len(self.flow_extended_sfc))
        ]
        self.virtual_segments: List[VirtualSegment] = []
        for segment_index in range(len(self.processing_stages) + 1):
            from_stage = None if segment_index == 0 else segment_index - 1
            to_stage = (
                None
                if segment_index == len(self.processing_stages)
                else segment_index
            )
            self.virtual_segments.append(
                VirtualSegment(segment_index, from_stage, to_stage)
            )

        self.flow_demand = flow_demand

    def get_processing_coefficient(self, stage_index: int) -> float:
        if stage_index not in self.stage_index_to_stage:
            raise KeyError(f"Unknown processing stage_index {stage_index}.")
        return self.stage_index_to_stage[stage_index].processing_coefficient

    def stage_index_for_extended_index(self, extended_index: int) -> Optional[int]:
        if extended_index == 0 or extended_index == len(self.flow_extended_sfc) - 1:
            return None
        return extended_index - 1

    def __repr__(self):
        return (
            f"Flow(id={self.flow_id}, length={self.flow_len}, src={self.flow_src_time_node}, "
            f"dst=({self.flow_dst_node}, <= {self.flow_dst_max_time}), "
            f"SFC={self.flow_extended_sfc}, VirtualLinks={self.flow_virtual_links}, "
            f"demand={self.flow_demand})"
        )

def canonicalize_stage_placement(placement: Mapping[int, TimeNode]) -> Tuple:
    """Return deterministic processing-stage placement."""
    return tuple(
        sorted(
            (int(stage_index), tuple(time_node))
            for stage_index, time_node in placement.items()
        )
    )


def canonicalize_routing(routing: List[TimeEdge]) -> Tuple:
    """Return ordered routing edges in a stable tuple representation."""
    return tuple(
        (tuple(from_node), tuple(to_node))
        for from_node, to_node in routing
    )


def canonicalize_segment_routing(segment_routing: Mapping[int, Iterable[TimeEdge]]) -> Tuple:
    """Return ordered segment routes in a stable tuple representation."""
    return tuple(
        (int(segment_index), canonicalize_routing(list(edges)))
        for segment_index, edges in sorted(segment_routing.items())
    )


def flatten_segment_routing(segment_routing: Mapping[int, Iterable[TimeEdge]]) -> List[TimeEdge]:
    """Flatten ordered segment routing for legacy display/reporting only."""
    flattened: List[TimeEdge] = []
    for segment_index in sorted(segment_routing):
        flattened.extend(list(segment_routing[segment_index]))
    return flattened


def reconstruct_ordered_route(
    selected_edges: Iterable[TimeEdge],
    start: TimeNode,
    end: TimeNode,
) -> List[TimeEdge]:
    """Reconstruct one simple ordered directed route from selected edges.

    Raises ValueError when the selected edges are disconnected, branched,
    cyclic, ambiguous, or do not form a route from ``start`` to ``end``.
    """
    edges = list(selected_edges)
    if start == end and not edges:
        return []
    if not edges:
        raise ValueError(f"No selected edges connect {start} to {end}.")

    outgoing: Dict[TimeNode, List[TimeEdge]] = {}
    incoming: Dict[TimeNode, List[TimeEdge]] = {}
    for edge in edges:
        outgoing.setdefault(edge[0], []).append(edge)
        incoming.setdefault(edge[1], []).append(edge)

    for node, node_edges in outgoing.items():
        if len(node_edges) > 1:
            raise ValueError(f"Branched route: node {node} has multiple outgoing edges.")
    for node, node_edges in incoming.items():
        if len(node_edges) > 1:
            raise ValueError(f"Branched route: node {node} has multiple incoming edges.")

    ordered: List[TimeEdge] = []
    current = start
    visited_nodes = {start}
    visited_edges = set()
    while current != end:
        next_edges = outgoing.get(current, [])
        if not next_edges:
            raise ValueError(f"Disconnected route: cannot continue from {current} to {end}.")
        edge = next_edges[0]
        if edge in visited_edges or edge[1] in visited_nodes:
            raise ValueError("Cyclic route selection is not a simple path.")
        ordered.append(edge)
        visited_edges.add(edge)
        current = edge[1]
        visited_nodes.add(current)

    if len(ordered) != len(edges):
        raise ValueError("Disconnected or ambiguous route: unused selected edges remain.")
    return ordered


# =========================
# Path (Column)
# =========================


class Path:
    """
    Column (path) representation for Dantzig-Wolfe decomposition.

    """
    def __init__(
        self,
        flow_id: int,
        path_id: int,

        # Indicates if this is an empty path
        path_is_empty: bool,

        # Processing placement mapping: stage_index -> (node, time)
        path_stage_placement: Optional[Dict[int, TimeNode]] = None,

        # Segment routing: segment_index -> ordered Time-Expanded Graph edges
        path_segment_routing: Optional[Dict[int, List[TimeEdge]]] = None,

        # Path cost (used for calculating reduced cost)
        path_cost: float = 0.0,

        # Temporary unambiguous conversion for old distinct-VNF callers.
        flow: Optional[Flow] = None,
        path_flow_vnf_placement: Optional[Dict[str, TimeNode]] = None,
        path_flow_routing: Optional[List[TimeEdge]] = None,
    ):
        self.flow_id = flow_id
        self.path_id = path_id

        self.path_is_empty = path_is_empty

        if path_stage_placement is None:
            path_stage_placement = {}
        if path_flow_vnf_placement:
            if flow is None:
                raise ValueError("Legacy VNF-name placement conversion requires flow.")
            converted: Dict[int, TimeNode] = {}
            used_types: Dict[str, int] = {}
            for stage in flow.processing_stages:
                if stage.vnf_type not in path_flow_vnf_placement:
                    continue
                used_types[stage.vnf_type] = used_types.get(stage.vnf_type, 0) + 1
                if used_types[stage.vnf_type] > 1:
                    raise ValueError(
                        "VNF-name placement is ambiguous for repeated VNF type "
                        f"{stage.vnf_type!r}; use stage-index placement."
                    )
                converted[stage.stage_index] = path_flow_vnf_placement[stage.vnf_type]
            path_stage_placement = converted

        if path_segment_routing is None:
            if path_flow_routing is not None:
                path_segment_routing = {0: path_flow_routing}
            else:
                path_segment_routing = {}

        self.path_stage_placement: Dict[int, TimeNode] = {
            int(stage_index): tuple(time_node)
            for stage_index, time_node in path_stage_placement.items()
        }
        self.path_segment_routing: Dict[int, List[TimeEdge]] = {
            int(segment_index): [
                (tuple(from_node), tuple(to_node))
                for from_node, to_node in edges
            ]
            for segment_index, edges in path_segment_routing.items()
        }
        self.path_flow_routing = flatten_segment_routing(self.path_segment_routing)

        # Initialize signature
        self.signature = None

        # Initialize pre-calculated resource consumption (crucial for performance)
        self.path_node_usage = None
        self.path_edge_usage = None

        self.path_cost = path_cost

    def build_signature(self):
        """
        Build a unique, hashable signature for this path.
        Used to detect duplicate columns in D-W decomposition.
        """
        self.signature = (
            self.flow_id,
            self.path_is_empty,

            # Processing placement: sorted by canonical stage_index.
            canonicalize_stage_placement(self.path_stage_placement),

            # Routing is ordered and segment-indexed.
            canonicalize_segment_routing(self.path_segment_routing)
        )

    def finalize_usage(
        self,
        flow_vnf_cost: Optional[Dict[str, float]] = None,
        flow: Optional[Flow] = None,
    ):
        """
        Precompute resource usage for the Restricted Master Problem (RMP).

        This function aggregates resource consumption from the path structure
        into compact coefficients that can be directly used in linear constraints.

        Node usage:
            node_usage[(n, t)] = sum of VNF costs placed at (n, t)

        Edge usage:
            edge_usage[(u, v, t)] = number of times the edge (u, v, t) is used
                                    by the routing path

        Parameters:
            flow_vnf_cost: Dict[str, float]
                Mapping from VNF name to its resource consumption (cost)
        """

        # Reset usage dictionaries to avoid double counting
        self.path_node_usage = {}
        self.path_edge_usage = {}

        # ---- Node usage: aggregate stage-indexed processing consumption ----
        for stage_index, (n, t) in self.path_stage_placement.items():
            if flow is not None:
                cost = flow.get_processing_coefficient(stage_index)
            elif flow_vnf_cost is not None:
                raise ValueError(
                    "Stage-indexed usage requires flow; VNF-name cost maps are "
                    "ambiguous for repeated VNF types."
                )
            else:
                cost = 0.0

            self.path_node_usage[(n, t)] = (
                    self.path_node_usage.get((n, t), 0.0) + cost
            )

        # ---- Edge usage: count how many times each edge is used ----
        for segment_edges in self.path_segment_routing.values():
            for (from_node, to_node) in segment_edges:
                u, t1 = from_node
                v, t2 = to_node

                # In time-expanded graphs, edges are indexed by departure time.
                t = t1

                edge_key = (u, v, t)

                self.path_edge_usage[edge_key] = (
                        self.path_edge_usage.get(edge_key, 0.0) + 1.0
                )

