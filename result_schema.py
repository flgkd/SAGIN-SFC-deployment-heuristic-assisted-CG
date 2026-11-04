"""Small result and certification structures for v2 solvers."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class RuntimeBreakdown:
    total_runtime: float = 0.0
    initialization_runtime: float = 0.0
    rmp_lp_runtime: float = 0.0
    heuristic_pricing_runtime: float = 0.0
    exact_pricing_runtime: float = 0.0
    final_rmp_runtime: float = 0.0
    other_runtime: float = 0.0


@dataclass(frozen=True)
class OptimalityClaims:
    global_original_integer_optimal: bool = False
    lp_column_generation_converged: bool = False
    lp_column_generation_certified: bool = False
    restricted_pool_integer_optimal: bool = False
    heuristic_feasible_only: bool = False


@dataclass(frozen=True)
class PricingIterationStats:
    iteration: int
    expected_exact_calls: int = 0
    launched_exact_calls: int = 0
    completed_exact_calls: int = 0
    optimal_exact_calls: int = 0
    positive_reduced_cost_calls: int = 0
    time_limit_calls: int = 0
    failed_calls: int = 0
    columns_added: int = 0
    certification_possible: bool = False
    certification_reason: str = ""


@dataclass(frozen=True)
class SolverResult:
    accepted_count: int = 0
    rejected_count: int = 0
    normalized_physical_cost: Optional[float] = None
    scalarized_objective: Optional[float] = None
    has_incumbent: bool = False
    hit_time_limit: bool = False
    solver_status: Any = None
    solver_status_name: Optional[str] = None
    termination_reason: str = ""
    runtime: RuntimeBreakdown = field(default_factory=RuntimeBreakdown)
    optimality: OptimalityClaims = field(default_factory=OptimalityClaims)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ILPResult(SolverResult):
    pass


@dataclass(frozen=True)
class RMPResult(SolverResult):
    lp_restricted_master_optimal: bool = False
    current_restricted_master_optimal: bool = False
    duals: Optional[Dict[str, Dict[Any, float]]] = None
    model: Any = None
    variables: Optional[Dict[Any, Any]] = None

    def __iter__(self):
        """Compatibility with the historical ``model, w, duals`` tuple."""
        yield self.model
        yield self.variables
        yield self.duals


@dataclass(frozen=True)
class ColumnGenerationResult(SolverResult):
    pricing_iterations: List[PricingIterationStats] = field(default_factory=list)


@dataclass(frozen=True)
class SAGINFEResult(SolverResult):
    pass


def evaluate_exact_pricing_certification(
    stats: PricingIterationStats,
    num_flows: int,
    positive_column_rejected: bool = False,
    rejection_reasons=None,
) -> PricingIterationStats:
    """Return ``stats`` with the Phase 1B exact-pricing certificate evaluated."""
    reasons = []
    if stats.expected_exact_calls != num_flows:
        reasons.append("expected exact calls did not match number of flows")
    if stats.launched_exact_calls != stats.expected_exact_calls:
        reasons.append("not all expected exact calls were launched")
    if stats.completed_exact_calls != stats.expected_exact_calls:
        reasons.append("not all exact calls completed")
    if stats.optimal_exact_calls != stats.expected_exact_calls:
        reasons.append("not all exact calls solved to proven optimality")
    if stats.positive_reduced_cost_calls != 0:
        reasons.append("positive reduced-cost column exists")
    if stats.time_limit_calls != 0:
        reasons.append("at least one exact call hit a time limit")
    if stats.failed_calls != 0:
        reasons.append("at least one exact call failed")
    if positive_column_rejected:
        if rejection_reasons:
            reasons.extend(str(reason) for reason in rejection_reasons)
        else:
            reasons.append("a positive column was rejected")

    possible = not reasons
    return PricingIterationStats(
        iteration=stats.iteration,
        expected_exact_calls=stats.expected_exact_calls,
        launched_exact_calls=stats.launched_exact_calls,
        completed_exact_calls=stats.completed_exact_calls,
        optimal_exact_calls=stats.optimal_exact_calls,
        positive_reduced_cost_calls=stats.positive_reduced_cost_calls,
        time_limit_calls=stats.time_limit_calls,
        failed_calls=stats.failed_calls,
        columns_added=stats.columns_added,
        certification_possible=possible,
        certification_reason="certified" if possible else "; ".join(reasons),
    )
