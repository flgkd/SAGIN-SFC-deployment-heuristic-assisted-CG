"""Pure policy helpers for the DWCG-SAGINFE-PRO candidate family.

This module intentionally imports no solver-backed code.  It centralizes the
deterministic orchestration rules and metadata defaults used by runner plumbing
and non-Gurobi tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


PRO_NO_PFE_ID = "dwcg_saginfe_pro_no_pfe"
PRO_ID = "dwcg_saginfe_pro"
PRO_ALGORITHMS = (PRO_NO_PFE_ID, PRO_ID)


@dataclass(frozen=True)
class SelectivePFEConfig:
    per_round_fraction: float = 0.05
    per_round_cap: int = 10
    global_fraction: float = 0.20
    global_cap: int = 40
    reserve_safety_margin_seconds: float = 5.0
    deadline_safety_margin_seconds: float = 5.0


@dataclass(frozen=True)
class FlowRepairFeatures:
    flow_id: int
    lp_rejection_weight: float = 0.0
    represented_by_rejection: bool = False
    primary_pricing_generated: bool = False
    non_rejection_column_count: int = 0
    lp_fractionality: float = 0.0
    dual_bottleneck_exposure: float = 0.0


@dataclass
class SelectivePFECounters:
    runtime_seconds: float = 0.0
    call_count: int = 0
    round_count: int = 0
    selected_flow_count: int = 0
    candidate_count: int = 0
    unique_candidate_count: int = 0
    positive_column_count: int = 0
    duplicate_count: int = 0
    invalid_candidate_count: int = 0
    non_improving_candidate_count: int = 0
    global_cap_reached: bool = False
    per_round_cap_reached: bool = False
    skipped_full_acceptance: bool = False
    skipped_no_eligible_flow: bool = False
    skipped_reserve_protection: bool = False
    skipped_deadline_protection: bool = False

    def as_metadata(self) -> Dict[str, Any]:
        return {
            "selective_pfe_runtime_seconds": self.runtime_seconds,
            "selective_pfe_call_count": self.call_count,
            "selective_pfe_round_count": self.round_count,
            "selective_pfe_selected_flow_count": self.selected_flow_count,
            "selective_pfe_candidate_count": self.candidate_count,
            "selective_pfe_unique_candidate_count": self.unique_candidate_count,
            "selective_pfe_positive_column_count": self.positive_column_count,
            "selective_pfe_duplicate_count": self.duplicate_count,
            "selective_pfe_invalid_candidate_count": self.invalid_candidate_count,
            "selective_pfe_non_improving_candidate_count": self.non_improving_candidate_count,
            "selective_pfe_seconds_per_positive_column": seconds_per_positive_column(
                self.runtime_seconds,
                self.positive_column_count,
            ),
            "selective_pfe_global_cap_reached": self.global_cap_reached,
            "selective_pfe_per_round_cap_reached": self.per_round_cap_reached,
            "selective_pfe_skipped_full_acceptance": self.skipped_full_acceptance,
            "selective_pfe_skipped_no_eligible_flow": self.skipped_no_eligible_flow,
            "selective_pfe_skipped_reserve_protection": self.skipped_reserve_protection,
            "selective_pfe_skipped_deadline_protection": self.skipped_deadline_protection,
        }


@dataclass(frozen=True)
class IncumbentSummary:
    source: str
    accepted_flow_count: int
    normalized_physical_cost: float
    objective_value: Optional[float] = None
    fingerprint: Optional[str] = None
    valid: bool = True


def is_pro_algorithm(algorithm: str) -> bool:
    return algorithm in PRO_ALGORITHMS


def selective_pfe_enabled_for_algorithm(algorithm: str) -> bool:
    return algorithm == PRO_ID


def selective_pfe_per_round_cap(flow_count: int, config: SelectivePFEConfig = SelectivePFEConfig()) -> int:
    if flow_count <= 0:
        return 0
    return min(math.ceil(config.per_round_fraction * flow_count), config.per_round_cap)


def selective_pfe_global_cap(flow_count: int, config: SelectivePFEConfig = SelectivePFEConfig()) -> int:
    if flow_count <= 0:
        return 0
    return min(math.ceil(config.global_fraction * flow_count), config.global_cap)


def is_selective_pfe_eligible(features: FlowRepairFeatures) -> bool:
    return (
        features.lp_rejection_weight > 0.0
        or features.represented_by_rejection
        or not features.primary_pricing_generated
        or features.non_rejection_column_count <= 1
        or features.lp_fractionality > 1e-9
        or features.dual_bottleneck_exposure > 0.0
    )


def selective_pfe_priority_key(features: FlowRepairFeatures) -> Tuple[Any, ...]:
    return (
        -features.lp_rejection_weight,
        0 if not features.primary_pricing_generated else 1,
        features.non_rejection_column_count,
        -features.lp_fractionality,
        -features.dual_bottleneck_exposure,
        features.flow_id,
    )


def select_critical_flows(
    features: Iterable[FlowRepairFeatures],
    flow_count: int,
    already_used_global_calls: int = 0,
    config: SelectivePFEConfig = SelectivePFEConfig(),
    initial_acceptance_is_full: bool = False,
    reserve_protected: bool = False,
    deadline_protected: bool = False,
) -> Tuple[List[FlowRepairFeatures], SelectivePFECounters]:
    counters = SelectivePFECounters()
    if initial_acceptance_is_full:
        counters.skipped_full_acceptance = True
        return [], counters
    if reserve_protected:
        counters.skipped_reserve_protection = True
        return [], counters
    if deadline_protected:
        counters.skipped_deadline_protection = True
        return [], counters

    global_cap = selective_pfe_global_cap(flow_count, config)
    remaining_global = max(0, global_cap - already_used_global_calls)
    if remaining_global <= 0:
        counters.global_cap_reached = True
        return [], counters

    eligible = sorted(
        [item for item in features if is_selective_pfe_eligible(item) and not item.primary_pricing_generated],
        key=selective_pfe_priority_key,
    )
    if not eligible:
        counters.skipped_no_eligible_flow = True
        return [], counters

    round_cap = selective_pfe_per_round_cap(flow_count, config)
    limit = min(round_cap, remaining_global)
    selected = eligible[:limit]
    counters.round_count = 1
    counters.selected_flow_count = len(selected)
    counters.call_count = len(selected)
    counters.per_round_cap_reached = len(eligible) > limit and limit == round_cap
    counters.global_cap_reached = len(eligible) > limit and limit == remaining_global
    return selected, counters


def record_pfe_candidate(counters: SelectivePFECounters, *, valid: bool, unique: bool, improving: bool) -> None:
    counters.candidate_count += 1
    if not valid:
        counters.invalid_candidate_count += 1
        return
    if not unique:
        counters.duplicate_count += 1
        return
    counters.unique_candidate_count += 1
    if not improving:
        counters.non_improving_candidate_count += 1
        return
    counters.positive_column_count += 1


def seconds_per_positive_column(runtime_seconds: float, positive_column_count: int) -> Optional[float]:
    if positive_column_count <= 0:
        return None
    return runtime_seconds / positive_column_count


def mip_start_from_incumbent(
    final_pool_by_flow: Mapping[int, Sequence[str]],
    incumbent_signature_by_flow: Mapping[int, str],
    all_flow_ids: Sequence[int],
) -> Tuple[Optional[Dict[Tuple[int, str], float]], bool, Optional[str]]:
    values: Dict[Tuple[int, str], float] = {}
    for flow_id in all_flow_ids:
        signatures = list(final_pool_by_flow.get(flow_id, ()))
        if not signatures:
            return None, False, f"flow {flow_id} has no final-RMP column"
        selected = incumbent_signature_by_flow.get(flow_id)
        if selected is None:
            return None, False, f"flow {flow_id} missing incumbent signature"
        if selected not in signatures:
            return None, False, f"flow {flow_id} incumbent column absent from final pool"
        for signature in signatures:
            values[(flow_id, signature)] = 1.0 if signature == selected else 0.0
    return values, True, None


def better_incumbent(left: IncumbentSummary, right: IncumbentSummary) -> IncumbentSummary:
    if left.valid and not right.valid:
        return left
    if right.valid and not left.valid:
        return right
    if left.accepted_flow_count != right.accepted_flow_count:
        return left if left.accepted_flow_count > right.accepted_flow_count else right
    if left.normalized_physical_cost != right.normalized_physical_cost:
        return left if left.normalized_physical_cost < right.normalized_physical_cost else right
    return left if left.source <= right.source else right


def incumbent_safeguard_metadata(
    initial: IncumbentSummary,
    solver: Optional[IncumbentSummary],
    returned: Optional[IncumbentSummary] = None,
) -> Dict[str, Any]:
    chosen = returned or (better_incumbent(initial, solver) if solver is not None else initial)
    solver_valid = solver is not None and solver.valid
    solver_accepted = solver.accepted_flow_count if solver is not None else None
    solver_cost = solver.normalized_physical_cost if solver is not None else None
    return {
        "incumbent_safeguard_used": True,
        "initial_incumbent_accepted_flow_count": initial.accepted_flow_count,
        "initial_incumbent_normalized_physical_cost": initial.normalized_physical_cost,
        "final_solver_incumbent_accepted_flow_count": solver_accepted,
        "final_solver_incumbent_normalized_physical_cost": solver_cost,
        "returned_incumbent_accepted_flow_count": chosen.accepted_flow_count,
        "returned_incumbent_normalized_physical_cost": chosen.normalized_physical_cost,
        "final_acceptance_gain_over_saginfe": chosen.accepted_flow_count - initial.accepted_flow_count,
        "returned_initial_incumbent_due_to_dominance": chosen.source == initial.source and (not solver_valid or chosen != solver),
        "returned_incumbent_source": chosen.source,
    }


def pro_metadata_defaults(algorithm: str, flow_count: int) -> Dict[str, Any]:
    pfe = SelectivePFECounters()
    enabled = selective_pfe_enabled_for_algorithm(algorithm)
    return {
        "pro_family_algorithm": is_pro_algorithm(algorithm),
        "pro_candidate_role": "full_selective_pfe" if enabled else "no_selective_pfe",
        "selective_pfe_enabled": enabled,
        "selective_pfe_per_round_cap": selective_pfe_per_round_cap(flow_count),
        "selective_pfe_global_cap": selective_pfe_global_cap(flow_count),
        "primary_pricing_policy_source": "dwcg_pure_historical_hybrid",
        "standalone_saginfe_solution_cache_reuse_allowed": False,
        "saginfe_final_rmp_mip_start_policy": "use_when_complete_valid_incumbent_columns_remain",
        **pfe.as_metadata(),
    }
