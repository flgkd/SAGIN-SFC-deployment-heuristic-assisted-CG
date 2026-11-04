"""Solver-free helpers for deterministic parallel-pricing scheduling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class PricingTask:
    """A deterministic logical pricing work unit."""

    ordinal: int
    flow_id: int
    flow: object


def ordered_pricing_flows(flows: Sequence[T], deterministic_mode: bool) -> List[T]:
    if deterministic_mode:
        return sorted(flows, key=lambda flow: flow.flow_id)
    return list(flows)


def plan_pricing_tasks(
    flows: Sequence[T],
    deterministic_mode: bool,
    remaining_exact_budget: Optional[int] = None,
) -> List[PricingTask]:
    """Return the logical pricing task set for one pricing phase.

    A task is one per-flow pricing solve against a fixed RMP dual state. When an
    exact-pricing budget is active, the returned list is truncated before any
    execution/submission so worker count cannot change the logical work set.
    """

    ordered = ordered_pricing_flows(flows, deterministic_mode)
    if remaining_exact_budget is not None:
        if remaining_exact_budget <= 0:
            ordered = []
        else:
            ordered = ordered[:remaining_exact_budget]
    return [
        PricingTask(ordinal=index, flow_id=flow.flow_id, flow=flow)
        for index, flow in enumerate(ordered)
    ]


def pricing_result_task_sort_key(item: Tuple[int, object]) -> Tuple[int, int]:
    ordinal, flow = item
    return ordinal, flow.flow_id
