# saginfe_solver.py

"""
saginfe_solver.py — SAGIN Flow Embedding (SAGINFE) heuristic and
          Per Flow Embedding (PFE) sub-procedure.

Notation mapping (paper → code)
---------------------------------
    ψ  (resource_weight)     network.resource_weight
    f_r (flow demand)        flow.flow_demand
    δ_{v_l} (VNF cost coef) flow.flow_extended_vnf_cost[l]
    c  (node capacity vec)   Dict[TimeNode, float]
    D  (edge capacity mat)   Dict[TimeEdge, float]
    η  (VNF placement)       Dict[str, TimeNode]
    φ  (routing path)        List[TimeEdge]
    ξ  (cumulative cost)     float

Performance design
------------------
(1) Single-source Dijkstra per a' — not per (a, a') pair.
        Reduces Dijkstra calls from O(L·K²) to O(L·K).

(2) Early termination in Dijkstra — stop once all targets in A_l are settled.

(3) Parent-pointer records for φ and η.
        Each record stores only the delta (eta_add, seg) plus a parent pointer.
        Full path is reconstructed once at the end via _traceback().
        Avoids O(K·L·path_length) copying; reduces to O(K·seg_length).

(4) Precomputed candidate sets — computed once before the stage loop,
        not re-evaluated as list comprehensions on every iteration.
        All VNF stages share the same list object and target set object.

(5) Single tracking dict — best_for[a] = (xi, a_star, seg)
        replaces three separate dicts, halving hash lookups per update.
"""

import math
import heapq
from typing import Dict, List, Optional, Set, Tuple

from data_model import Flow, Network, Node, Path, TimeEdge, TimeNode, flatten_segment_routing
from objective import (
    ObjectiveSpec,
    compute_path_physical_cost,
    objective_spec_from_network,
)


# ============================================================
#  Single-source Dijkstra with optional target-set early exit
# ============================================================

def dijkstra_from(
    start: TimeNode,
    edge_costs: Dict[TimeEdge, float],
    out_adj: Dict[TimeNode, List[TimeNode]],
    targets: Optional[Set[TimeNode]] = None,
) -> Tuple[Dict[TimeNode, float], Dict[TimeNode, Tuple[TimeNode, TimeEdge]]]:
    """
    Single-source shortest path from `start`.

    Parameters
    ----------
    targets : set, optional
        If given, terminates as soon as every node in `targets` is settled,
        avoiding exploration of irrelevant parts of the graph.

    Returns
    -------
    dist : Dict[TimeNode, float]
    prev : Dict[TimeNode, Tuple[TimeNode, TimeEdge]]
        prev[v] = (predecessor, edge) for path reconstruction.
    """
    dist: Dict[TimeNode, float] = {start: 0.0}
    prev: Dict[TimeNode, Tuple[TimeNode, TimeEdge]] = {}
    heap: List[Tuple[float, TimeNode]] = [(0.0, start)]
    remaining: Optional[Set[TimeNode]] = set(targets) if targets else None

    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, math.inf):
            continue

        # Early termination once all targets are settled
        if remaining is not None:
            remaining.discard(u)
            if not remaining:
                break

        for v in out_adj.get(u, []):
            edge: TimeEdge = (u, v)
            cost = edge_costs.get(edge, math.inf)
            if cost >= math.inf:
                continue
            nd = d + cost
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = (u, edge)
                heapq.heappush(heap, (nd, v))

    return dist, prev


def _reconstruct_path(
    prev: Dict[TimeNode, Tuple[TimeNode, TimeEdge]],
    start: TimeNode,
    end: TimeNode,
) -> Optional[List[TimeEdge]]:
    """Reconstruct the path from start to end using the prev map."""
    if start == end:
        return []
    if end not in prev:
        return None
    path: List[TimeEdge] = []
    cur = end
    while cur in prev:
        p_node, edge = prev[cur]
        path.append(edge)
        cur = p_node
    path.reverse()
    return path


# ============================================================
#  Traceback: reconstruct full η and φ from parent-pointer chain
# ============================================================

def _traceback(
    records: Dict,
    final_key: Tuple,
) -> Tuple[Dict[int, TimeNode], Dict[int, List[TimeEdge]]]:
    """
    Walk the parent chain from final_key back to stage 0, collecting
    eta_add and seg from each record, then assemble the full η and φ.

    Replaces the previous approach of copying the growing full path into
    every record at every stage (which was O(K · L · path_length)).
    """
    eta: Dict[int, TimeNode] = {}
    seg_chain: List[List[TimeEdge]] = []

    cur = final_key
    while cur is not None:
        rec = records[cur]
        eta.update(rec["eta_add"])
        seg_chain.append(rec["seg"])
        cur = rec["parent"]

    seg_chain.reverse()
    segment_routing = {
        segment_index: list(seg)
        for segment_index, seg in enumerate(seg_chain[1:])
    }
    return eta, segment_routing


# ============================================================
#  Cost helper functions  (equations 15 & 16)
# ============================================================

def _comp_ratio_cost(vnf_demand: float, node_cap: float, psi: float) -> float:
    """
    Equation (15) — computing ratio cost λ_{r,comp}^{l,a',a}.

        = 0                            if vnf_demand == 0
        = ψ · vnf_demand / node_cap   if 0 < vnf_demand ≤ node_cap
        = ∞                            otherwise
    """
    if vnf_demand == 0.0:
        return 0.0
    if node_cap > 0.0 and vnf_demand <= node_cap:
        return psi * vnf_demand / node_cap
    return math.inf


def _build_edge_ratio_costs(
    network: Network,
    D_assumed: Dict[TimeEdge, float],
    flow_demand: float,
    psi: float,
) -> Dict[TimeEdge, float]:
    """
    Equation (16) — build U_r^{l,a'}: link communication ratio costs.

    Called once per a', shared across all a ∈ A_l for the same a'.

        storage edge (cap == inf)  →  0
        comm edge, f_r ≤ d(e)     →  (1-ψ) · f_r / d(e)
        comm edge, f_r > d(e)     →  ∞
    """
    costs: Dict[TimeEdge, float] = {}
    for edge in network.time_edges:
        cap = D_assumed.get(edge, 0.0)
        if cap == math.inf:
            costs[edge] = 0.0
        elif cap > 0.0 and flow_demand <= cap:
            costs[edge] = (1.0 - psi) * flow_demand / cap
        else:
            costs[edge] = math.inf
    return costs


# ============================================================
#  Per Flow Embedding (PFE)  — Algorithm 2
# ============================================================

def pfe(
    network: Network,
    flow: Flow,
    c: Dict[TimeNode, float],
    D: Dict[TimeEdge, float],
    vnf_host_nodes: List[Node],
    dst_candidate_nodes: List[Node],
) -> Tuple[
    Optional[Dict[int, TimeNode]],
    Optional[Dict[int, List[TimeEdge]]],
    Dict[TimeNode, float],
    Dict[TimeEdge, float],
]:
    """
    Per Flow Embedding (PFE) Procedure — Algorithm 2 (standalone).

    Finds the minimum-ratio-cost VNF placement and routing for a single flow
    by constructing |V_r|+2 cost stages on the time-expanded graph.

    Does NOT mutate input c / D. Returns updated copies on success,
    originals unchanged on failure.

    Can be used directly as the pricing sub-problem in Dantzig-Wolfe
    column generation.
    """
    psi      = network.resource_weight
    f_r      = flow.flow_demand
    src_tn   = flow.flow_src_time_node
    t_o      = src_tn[1]
    t_d      = flow.flow_dst_max_time
    dst_node = flow.flow_dst_node

    if flow.flow_demand < 0.0:
        raise ValueError(f"Flow {flow.flow_id} has negative demand.")
    if src_tn not in c:
        return None, None, c, D
    if t_d < t_o:
        return None, None, c, D
    num_vnfs   = len(flow.processing_stages)
    num_stages = num_vnfs + 2

    # ----------------------------------------------------------------
    #  Precompute all candidate sets A_k^r  (equation 14)
    #
    #  Computed once before the stage loop.
    #  All VNF stages (1 … |V_r|) share the same list object and the
    #  same target set object — no duplicate construction.
    #
    #  stage 0         : {src_tn}
    #  stage 1…|V_r|   : vnf_host time-nodes in [t_o, t_d]
    #  stage |V_r|+1   : dst time-nodes in [t_o, t_d]
    # ----------------------------------------------------------------
    vnf_stage_cands: List[TimeNode] = [
        (n, t)
        for n in vnf_host_nodes
        for t in network.time_slots
        if t_o <= t <= t_d and (n, t) in c
    ]
    dst_stage_cands: List[TimeNode] = [
        (n, t)
        for n in dst_candidate_nodes
        if n == dst_node
        for t in network.time_slots
        if t_o <= t <= t_d and (n, t) in c
    ]

    all_candidates: List[List[TimeNode]] = (
        [[src_tn]]
        + [vnf_stage_cands] * num_vnfs   # same object for all VNF stages
        + [dst_stage_cands]
    )

    # Precompute target sets for Dijkstra early termination.
    # VNF stages share one set object; only src and dst get their own.
    vnf_target_set: Set[TimeNode] = set(vnf_stage_cands)
    all_target_sets: List[Set[TimeNode]] = (
        [set([src_tn])]
        + [vnf_target_set] * num_vnfs    # same object for all VNF stages
        + [set(dst_stage_cands)]
    )

    # ----------------------------------------------------------------
    #  Record store:  (stage, TimeNode) → σ_r^{l,a}
    #
    #  Each record stores:
    #      "c"       : full assumed residual node capacity (needed for
    #                  cap_at_a query and _build_edge_ratio_costs at next stage)
    #      "D"       : full assumed residual edge capacity (same reason)
    #      "parent"  : key of parent record, None for stage 0
    #      "eta_add" : VNF placement delta at this stage {vnf_name: TimeNode}
    #      "seg"     : routing edges traversed at this stage [TimeEdge, …]
    #      "xi"      : cumulative ratio cost ξ_r^{l,a}
    #
    #  Full φ and η are NOT stored — reconstructed once at the end via
    #  _traceback(), avoiding O(K · L · path_length) per-stage copying.
    # ----------------------------------------------------------------
    records: Dict[Tuple[int, TimeNode], dict] = {
        (0, src_tn): {
            "c":       dict(c),
            "D":       dict(D),
            "parent":  None,
            "eta_add": {},
            "seg":     [],
            "xi":      0.0,
        }
    }

    # ----------------------------------------------------------------
    #  Main stage loop  l = 1 … |V_r|+1
    # ----------------------------------------------------------------
    for l in range(1, num_stages):
        cands_l    = all_candidates[l]
        cands_prev = all_candidates[l - 1]
        targets_l  = all_target_sets[l]

        if not cands_l:
            return None, None, c, D

        stage_index = l - 1
        vnf_demand = (
            f_r * flow.get_processing_coefficient(stage_index)
            if 0 <= stage_index < num_vnfs
            else 0.0
        )

        # Single tracking dict: best_for[a] = (best_xi, best_a_star, best_seg)
        # Replaces three separate dicts → halves hash lookups per update.
        best_for: Dict[TimeNode, Tuple[float, TimeNode, List[TimeEdge]]] = {}

        # ---- Outer loop: one Dijkstra per a' ----
        for a_prime in cands_prev:
            rec_p = records.get((l - 1, a_prime))
            if rec_p is None or rec_p["xi"] >= math.inf:
                continue

            # Build edge costs once for this a' (equation 16)
            edge_costs = _build_edge_ratio_costs(network, rec_p["D"], f_r, psi)

            # One single-source Dijkstra covers all a ∈ A_l at once
            dist_map, prev_map = dijkstra_from(
                start=a_prime,
                edge_costs=edge_costs,
                out_adj=network.out_time_edges,
                targets=targets_l,      # early termination
            )

            xi_prev = rec_p["xi"]
            c_prev  = rec_p["c"]

            # ---- Inner loop: O(1) dist lookup per a ----
            for a in cands_l:
                lam_comm = dist_map.get(a, math.inf)
                if lam_comm >= math.inf:
                    continue

                lam_comp = _comp_ratio_cost(vnf_demand, c_prev.get(a, 0.0), psi)
                if lam_comp >= math.inf:
                    continue

                total_xi = xi_prev + lam_comp + lam_comm

                current_best = best_for.get(a)
                if current_best is None or total_xi < current_best[0]:
                    seg = _reconstruct_path(prev_map, a_prime, a)
                    if seg is None:
                        continue
                    best_for[a] = (
                        total_xi,
                        a_prime,
                        seg,
                    )

        # ---- Build records for all feasible a in this stage ----
        any_feasible = False

        for a in cands_l:
            entry = best_for.get(a)
            if entry is None:
                continue

            best_xi, a_star, seg = entry
            seg = seg or []
            any_feasible = True
            rec_star = records[(l - 1, a_star)]

            new_c = dict(rec_star["c"])
            new_D = dict(rec_star["D"])

            # Deduct node capacity for VNF compute demand
            if vnf_demand > 0.0 and a in new_c:
                new_c[a] -= vnf_demand

            # Deduct capacity only on the directed edge actually used.
            # The ILP bandwidth constraint (∑ f_r·z_{e^t} ≤ d(e^t)) is
            # per directed edge — using (u,t)→(v,t) does NOT consume
            # capacity of the reverse edge (v,t)→(u,t).
            # Storage edges have infinite capacity: no deduction needed.
            for edge in seg:
                cap = new_D.get(edge, math.inf)
                if cap != math.inf:
                    new_D[edge] = cap - f_r

            # Store only the delta — full φ/η reconstructed at the end
            records[(l, a)] = {
                "c":       new_c,
                "D":       new_D,
                "parent":  (l - 1, a_star),
                "eta_add": {stage_index: a} if 0 <= stage_index < num_vnfs else {},
                "seg":     seg,
                "xi":      best_xi,
            }

        if not any_feasible:
            return None, None, c, D

    # ----------------------------------------------------------------
    #  Select best destination time-node  a**
    # ----------------------------------------------------------------
    final_stage = num_stages - 1
    final_cands = all_candidates[final_stage]

    best_xi_f = math.inf
    best_a_ss: Optional[TimeNode] = None

    for a in final_cands:
        rec = records.get((final_stage, a))
        if rec is not None and rec["xi"] < best_xi_f:
            best_xi_f = rec["xi"]
            best_a_ss = a

    if best_a_ss is None:
        return None, None, c, D

    final_key = (final_stage, best_a_ss)
    final_rec = records[final_key]

    # Reconstruct full η and φ once via parent-pointer traceback
    eta, phi = _traceback(records, final_key)

    return eta, phi, final_rec["c"], final_rec["D"]


# ============================================================
#  SAGIN Flow Embedding (SAGINFE)  — Algorithm 1
# ============================================================

def saginfe(
    network: Network,
    flows: List[Flow],
    vnf_host_nodes: List[Node],
    dst_candidate_nodes: List[Node],
    verbose: bool = True,
) -> Dict[int, Tuple[Optional[Dict[int, TimeNode]], Optional[Dict[int, List[TimeEdge]]]]]:
    """
    SAGIN Flow Embedding (SAGINFE) — Algorithm 1.

    Embeds all flows one by one via PFE, sharing a single residual-capacity
    state updated after each accepted flow.

    Sort key: (flow_len ASC, flow_demand ASC) — shorter/smaller flows first.
    """
    c: Dict[TimeNode, float] = dict(network.time_node_capacity)
    D: Dict[TimeEdge, float] = dict(network.time_edge_capacity)

    sorted_flows = sorted(flows, key=lambda f: (f.flow_len, f.flow_demand))
    results: Dict[int, Tuple] = {}

    for flow in sorted_flows:
        eta, phi, c, D = pfe(
            network=network,
            flow=flow,
            c=c,
            D=D,
            vnf_host_nodes=vnf_host_nodes,
            dst_candidate_nodes=dst_candidate_nodes,
        )
        results[flow.flow_id] = (eta, phi)
        if verbose:
            print(f"  Flow {flow.flow_id}: {'ACCEPTED' if eta is not None else 'REJECTED'}")

    return results


# ============================================================
#  Adapters for Dantzig-Wolfe / Column Generation integration
# ============================================================

def _path_physical_cost(
    network: Network,
    flow: Flow,
    eta: Dict[int, TimeNode],
    phi: Dict[int, List[TimeEdge]],
    objective_spec: Optional[ObjectiveSpec] = None,
) -> float:
    objective_spec = objective_spec or objective_spec_from_network(network)
    path = Path(
        flow_id=flow.flow_id,
        path_id=-1,
        path_is_empty=False,
        path_stage_placement=eta,
        path_segment_routing=phi,
        path_cost=0.0,
    )
    path.finalize_usage(flow=flow)
    return compute_path_physical_cost(network, flow, path, objective_spec)


def build_initial_paths_from_saginfe(
    network: Network,
    flows: List[Flow],
    saginfe_results: Dict[int, Tuple[Optional[Dict[int, TimeNode]], Optional[Dict[int, List[TimeEdge]]]]],
    objective_spec: Optional[ObjectiveSpec] = None,
) -> List[Dict]:
    """
    Convert SAGINFE embeddings to the custom initial path format accepted by GBSolver.

    Rejected flows are skipped; the artificial empty paths in GBSolver still cover
    those flows and keep the RMP initially feasible.
    """
    flow_by_id = {flow.flow_id: flow for flow in flows}
    initial_paths: List[Dict] = []

    for flow_id, (eta, phi) in saginfe_results.items():
        if eta is None or phi is None:
            continue

        flow = flow_by_id[flow_id]
        initial_paths.append(
            embedding_to_initial_path_dict(network, flow, eta, phi, objective_spec)
        )

    return initial_paths


def build_saginfe_initial_paths_from_results(
    network: Network,
    flows: List[Flow],
    saginfe_results: Dict[int, Tuple[Optional[Dict[int, TimeNode]], Optional[Dict[int, List[TimeEdge]]]]],
    objective_spec: Optional[ObjectiveSpec] = None,
) -> List[Dict]:
    """
    Build GBSolver warm-start columns from already computed SAGINFE results.

    This avoids re-running SAGINFE when an experiment has already evaluated it.
    """
    return build_initial_paths_from_saginfe(network, flows, saginfe_results, objective_spec)


def embedding_to_initial_path_dict(
    network: Network,
    flow: Flow,
    eta: Dict[int, TimeNode],
    phi: Dict[int, List[TimeEdge]],
    objective_spec: Optional[ObjectiveSpec] = None,
) -> Dict:
    """
    Convert one accepted SAGINFE/PFE embedding into GBSolver custom_initial_paths format.

    Only actual processing VNFs are retained in the placement dictionary. SRC/DST
    have zero resource cost and are represented implicitly by the flow metadata.
    """
    placement = {
        stage_index: time_node
        for stage_index, time_node in eta.items()
        if stage_index in flow.stage_index_to_stage
    }
    return {
        "flow_id": flow.flow_id,
        "stage_placement": placement,
        "segment_routing": {idx: list(edges) for idx, edges in phi.items()},
        "routing": flatten_segment_routing(phi),
        "physical_cost": _path_physical_cost(network, flow, placement, phi, objective_spec),
    }


def build_saginfe_initial_paths(
    network: Network,
    flows: List[Flow],
    vnf_host_nodes: Optional[List[Node]] = None,
    dst_candidate_nodes: Optional[List[Node]] = None,
    objective_spec: Optional[ObjectiveSpec] = None,
) -> List[Dict]:
    """
    Run SAGINFE and return accepted embeddings as GBSolver warm-start columns.

    Rejected flows are skipped. Empty paths should still be created by GBSolver
    to keep every flow covered in the RMP.
    """
    if vnf_host_nodes is None:
        vnf_host_nodes = network.nodes
    if dst_candidate_nodes is None:
        dst_candidate_nodes = network.nodes

    saginfe_results = saginfe(
        network=network,
        flows=flows,
        vnf_host_nodes=vnf_host_nodes,
        dst_candidate_nodes=dst_candidate_nodes,
        verbose=False,
    )
    return build_saginfe_initial_paths_from_results(network, flows, saginfe_results, objective_spec)


def _embedding_to_sp_results(
    flow: Flow,
    eta: Dict[int, TimeNode],
    phi: Dict[int, List[TimeEdge]],
) -> Optional[Tuple[Dict[Tuple[int, int, int], float], Dict[Tuple[TimeNode, TimeNode, int], float]]]:
    y_results: Dict[Tuple[int, int, int], float] = {}
    z_results: Dict[Tuple[TimeNode, TimeNode, int], float] = {}

    milestones: List[TimeNode] = [flow.flow_src_time_node]
    for stage in flow.processing_stages:
        if stage.stage_index not in eta:
            return None
        milestones.append(eta[stage.stage_index])

    flat_phi = flatten_segment_routing(phi)
    dst_tn = flat_phi[-1][1] if flat_phi else flow.flow_src_time_node
    if dst_tn[0] != flow.flow_dst_node or dst_tn[1] > flow.flow_dst_max_time:
        return None
    milestones.append(dst_tn)

    for k, tn in enumerate(milestones):
        n, t = tn
        y_results[(n, t, k)] = 1.0

    edge_index = 0
    current = milestones[0]
    for l, target in enumerate(milestones[1:]):
        while current != target:
            if edge_index >= len(flat_phi):
                return None
            edge = flat_phi[edge_index]
            if edge[0] != current:
                return None
            z_results[(edge[0], edge[1], l)] = 1.0
            current = edge[1]
            edge_index += 1

    if edge_index != len(flat_phi):
        return None

    return y_results, z_results


def _reduced_cost_from_sp_results(
    flow: Flow,
    network: Network,
    duals: Dict,
    objective_spec: ObjectiveSpec,
    y_results: Dict[Tuple[int, int, int], float],
    z_results: Dict[Tuple[TimeNode, TimeNode, int], float],
) -> float:
    from sub_problem import extract_path_from_sp
    from objective import compute_reduced_cost_from_usage

    path = extract_path_from_sp(
        flow,
        network,
        y_results,
        z_results,
        path_id=-1,
        objective_spec=objective_spec,
    )
    return compute_reduced_cost_from_usage(
        flow,
        path.path_cost,
        path.path_node_usage,
        path.path_edge_usage,
        duals,
        objective_spec,
    )


def solve_sp_with_pfe(
    flow: Flow,
    network: Network,
    duals: Dict,
    objective_spec: ObjectiveSpec,
    stop_early=False,
):
    """
    PFE-based pricing callback for GBSolver.custom_sp_func.

    The PFE deployment is converted to the same sparse y/z dictionaries used by
    the exact Gurobi subproblem. A column is returned only if its reduced cost is
    positive under the current RMP duals.
    """
    eta, phi, _, _ = pfe(
        network=network,
        flow=flow,
        c=dict(network.time_node_capacity),
        D=dict(network.time_edge_capacity),
        vnf_host_nodes=network.nodes,
        dst_candidate_nodes=network.nodes,
    )

    if eta is None or phi is None:
        return None, None, None

    sp_results = _embedding_to_sp_results(flow, eta, phi)
    if sp_results is None:
        return None, None, None

    y_results, z_results = sp_results
    obj_val = _reduced_cost_from_sp_results(
        flow, network, duals, objective_spec, y_results, z_results
    )

    if obj_val > 1e-6:
        return obj_val, y_results, z_results

    return None, None, None

