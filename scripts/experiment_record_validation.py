"""Validate optional runtime settings against a record's own configuration."""

import math
from typing import Any, List, Mapping, Optional


def recorded_runtime_limit_errors(
    row: Mapping[str, Any], metadata: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    metadata = row if metadata is None else metadata
    field = "configured_hard_time_limit_seconds"
    if field not in metadata:
        return ["missing recorded runtime setting"]
    recorded = metadata[field]
    if recorded is not None and (
        isinstance(recorded, bool) or not isinstance(recorded, (int, float))
        or not math.isfinite(recorded) or recorded <= 0
    ):
        return ["invalid recorded runtime setting"]
    algorithm = (row.get("configuration_snapshot") or {}).get("algorithm")
    if algorithm is not None:
        expected = None if algorithm.get("disable_hard_time_limit") else (
            algorithm.get("hard_time_limit_seconds") or algorithm.get("time_limit")
        )
        if recorded != expected:
            return ["runtime setting differs from saved configuration"]
    return []
