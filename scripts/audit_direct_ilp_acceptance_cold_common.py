"""Shared non-Gurobi audit helpers for cold Direct ILP outputs."""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from record_compatibility import normalize_record, resolve_input_path

import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple


DIRECT_ALGORITHM = "direct_ilp_acceptance_cold"
BASELINE_DIR = Path("outputs/merged_formal_v3_480_dwcg_saginfe_pro_970415f")
FINGERPRINT_FIELDS = ("topology_fingerprint", "base_flow_fingerprint", "full_instance_fingerprint")
ALLOWED_TERMINATIONS = {
    "OPTIMAL",
    "BOUND_CERTIFIED",
    "TIME_LIMIT_WITH_INCUMBENT",
    "TIME_LIMIT_NO_INCUMBENT",
    "E2E_HARD_WALL_WITH_INCUMBENT",
    "E2E_HARD_WALL_NO_INCUMBENT",
    "INFEASIBLE",
}
RUNTIME_ACCOUNTING_CLOCK_TOLERANCE_SECONDS = 0.05
RUNTIME_ACCOUNTING_E2E_OVERRUN_TOLERANCE_SECONDS = 5.0
RUNTIME_ACCOUNTING_HARD_WALL_FLAG_TOLERANCE_SECONDS = 1.0


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    path = resolve_input_path(path)
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    rows.append(normalize_record(json.loads(line)))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid JSONL: {exc}") from exc
    return rows


def run_key(row: Mapping[str, Any]) -> Tuple[str, str, int, str]:
    key = row["key"]
    return str(key["scale"]), str(key["load_label"]), int(key["formal_seed"]), str(key["algorithm"])


def scenario_key(row: Mapping[str, Any]) -> Tuple[str, str, int]:
    scale, load, seed, _algorithm = run_key(row)
    return scale, load, seed


def require(condition: bool, message: str, errors: List[str]) -> None:
    if not condition:
        errors.append(message)


def direct_meta(row: Mapping[str, Any]) -> Dict[str, Any]:
    return dict(row.get("direct_ilp_metadata") or {})


def safe_integer_upper(raw_bound: Any, total_flows: int, tolerance: float = 1e-7) -> int | None:
    if raw_bound is None:
        return None
    value = float(raw_bound)
    if not math.isfinite(value):
        return None
    return max(0, min(total_flows, int(math.ceil(value - tolerance))))


def _finite_nonnegative(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and float(value) >= 0.0


def runtime_accounting_diagnostics(row: Mapping[str, Any], configured_limit: float | None) -> Dict[str, Any]:
    key = run_key(row)
    meta = direct_meta(row)
    errors: List[str] = []
    total = row.get("total_end_to_end_runtime_seconds")
    meta_total = meta.get("total_end_to_end_runtime_seconds")
    model_build = meta.get("model_build_runtime_seconds")
    solver_runtime = meta.get("solver_runtime_seconds")
    extraction = meta.get("solution_extraction_runtime_seconds")
    effective_limit = meta.get("effective_solver_time_limit_seconds")
    persisted_residual = meta.get("runtime_accounting_residual_seconds")
    for field_name, value in (
        ("total_end_to_end_runtime_seconds", total),
        ("direct_ilp_metadata.total_end_to_end_runtime_seconds", meta_total),
        ("model_build_runtime_seconds", model_build),
        ("solver_runtime_seconds", solver_runtime),
        ("solution_extraction_runtime_seconds", extraction),
        ("effective_solver_time_limit_seconds", effective_limit),
        ("runtime_accounting_residual_seconds", persisted_residual),
    ):
        if field_name == "effective_solver_time_limit_seconds" and configured_limit is None and value is None:
            continue
        if not _finite_nonnegative(value) and not (field_name == "runtime_accounting_residual_seconds" and isinstance(value, (int, float)) and math.isfinite(float(value))):
            errors.append(f"{key} {field_name} missing, negative, or non-finite")
    if errors:
        return {
            "runtime_accounting_valid_raw": meta.get("runtime_accounting_valid"),
            "runtime_accounting_valid_recomputed": False,
            "runtime_accounting_residual_seconds": persisted_residual,
            "runtime_accounting_recomputed_residual_seconds": None,
            "runtime_accounting_tolerance_seconds": RUNTIME_ACCOUNTING_CLOCK_TOLERANCE_SECONDS,
            "runtime_accounting_errors": errors,
        }

    total = float(total)
    meta_total = float(meta_total)
    model_build = float(model_build)
    solver_runtime = float(solver_runtime)
    extraction = float(extraction)
    effective_limit = float(effective_limit) if effective_limit is not None else None
    persisted_residual = float(persisted_residual)
    component_sum = model_build + solver_runtime + extraction
    residual = total - component_sum
    tolerance = RUNTIME_ACCOUNTING_CLOCK_TOLERANCE_SECONDS
    if abs(meta_total - total) > tolerance:
        errors.append(f"{key} metadata total {meta_total} differs from record total {total}")
    if abs(persisted_residual - residual) > tolerance:
        errors.append(f"{key} persisted runtime residual {persisted_residual} differs from recomputed {residual}")
    if residual < -tolerance:
        errors.append(f"{key} component runtimes exceed total by {-residual:.9f}s > {tolerance}s tolerance")
    if configured_limit is None:
        if effective_limit is not None:
            errors.append(f"{key} unexpected solver limit without a configured runtime limit")
    else:
        if total > configured_limit + RUNTIME_ACCOUNTING_E2E_OVERRUN_TOLERANCE_SECONDS:
            errors.append(f"{key} total runtime {total} exceeds configured limit {configured_limit} plus tolerance")
        if effective_limit is not None and model_build + effective_limit > configured_limit + tolerance:
            errors.append(f"{key} solver budget plus model build exceeds configured limit {configured_limit}")

    solver_status_name = meta.get("solver_status_name")
    termination = row.get("termination_status")
    solver_time_limit_reached = meta.get("solver_time_limit_reached")
    hard_wall_reached = meta.get("hard_wall_clock_limit_reached")
    if solver_status_name == "TIME_LIMIT" and solver_time_limit_reached is not True:
        errors.append(f"{key} solver TIME_LIMIT status without solver_time_limit_reached=true")
    if solver_time_limit_reached is True and solver_status_name != "TIME_LIMIT":
        errors.append(f"{key} solver_time_limit_reached=true but solver status is {solver_status_name!r}")
    if solver_status_name == "TIME_LIMIT" and termination not in ("TIME_LIMIT_WITH_INCUMBENT", "TIME_LIMIT_NO_INCUMBENT"):
        errors.append(f"{key} solver TIME_LIMIT has incompatible termination {termination!r}")
    if configured_limit is None:
        if hard_wall_reached:
            errors.append(f"{key} interruption flag without a configured runtime limit")
    else:
        exceeded = total > configured_limit + RUNTIME_ACCOUNTING_HARD_WALL_FLAG_TOLERANCE_SECONDS
        if hard_wall_reached is not exceeded:
            errors.append(f"{key} interruption flag differs from recorded runtime accounting")
    return {
        "runtime_accounting_valid_raw": meta.get("runtime_accounting_valid"),
        "runtime_accounting_valid_recomputed": not errors,
        "runtime_accounting_residual_seconds": persisted_residual,
        "runtime_accounting_recomputed_residual_seconds": residual,
        "runtime_accounting_tolerance_seconds": tolerance,
        "runtime_accounting_errors": errors,
    }


def validate_runtime_accounting(row: Mapping[str, Any], configured_limit: float | None) -> List[str]:
    errors = runtime_accounting_diagnostics(row, configured_limit)["runtime_accounting_errors"]
    return errors


def baseline_by_scenario(baseline_dir: Path) -> Dict[Tuple[str, str, int], List[Dict[str, Any]]]:
    rows = read_jsonl(baseline_dir / "merged_runs.jsonl")
    grouped: Dict[Tuple[str, str, int], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[scenario_key(row)].append(row)
    return grouped


def audit_direct_records(
    rows: List[Dict[str, Any]],
    output_dir: Path,
    baseline_dir: Path,
    expected_scenarios: Iterable[Tuple[str, str, int]],
    expected_plan_fingerprint: str | None,
    expected_git_commit: str | None,
    expected_limits: Mapping[Tuple[str, str, int], float | None] | float | None,
    require_integration_only: bool,
) -> Dict[str, Any]:
    errors: List[str] = []
    expected = set(expected_scenarios)
    manifest = read_json(output_dir / "manifest.json") if (output_dir / "manifest.json").exists() else {}
    keys = [run_key(row) for row in rows]
    duplicate_keys = [item for item, count in Counter(keys).items() if count != 1]
    require(not duplicate_keys, f"duplicate run keys: {duplicate_keys[:10]}", errors)
    require({key[3] for key in keys} <= {DIRECT_ALGORITHM}, f"unexpected algorithms: {sorted({key[3] for key in keys})}", errors)
    require({key[:3] for key in keys} == expected, f"scenario set mismatch: {sorted({key[:3] for key in keys})}", errors)
    require(len(rows) == len(expected), f"expected {len(expected)} records, found {len(rows)}", errors)
    require(bool(manifest.get("integration_only")) is require_integration_only, "manifest integration_only mismatch", errors)
    if require_integration_only:
        require(str(output_dir).replace("\\", "/").startswith("pilot_outputs/"), "smoke output is not isolated under pilot_outputs", errors)
        require(manifest.get("run_category") == "integration_pilot", "smoke run_category mismatch", errors)
    else:
        require(str(output_dir).replace("\\", "/").startswith("formal_outputs/"), "formal output is not isolated under formal_outputs", errors)
        require(manifest.get("run_category") == "formal_paper_or_diagnostic", "formal run_category mismatch", errors)
    if expected_plan_fingerprint:
        require(manifest.get("plan_fingerprint") == expected_plan_fingerprint, "plan fingerprint mismatch", errors)
    if expected_git_commit:
        require(manifest.get("git_commit") == expected_git_commit, "manifest git_commit mismatch", errors)

    baseline = baseline_by_scenario(baseline_dir)
    for row in rows:
        key = run_key(row)
        scenario = key[:3]
        meta = direct_meta(row)
        total = int(row.get("total_flow_count") or 0)
        incumbent = row.get("accepted_flow_count")
        raw_bound = meta.get("raw_objective_bound", row.get("best_bound"))
        upper = meta.get("safe_integer_acceptance_upper_bound")
        lower = meta.get("safe_integer_acceptance_lower_bound")
        configured_limit = expected_limits[scenario] if isinstance(expected_limits, dict) else expected_limits

        require(row.get("error_type") in (None, ""), f"{key} unexpected error_type {row.get('error_type')}", errors)
        require(row.get("termination_status") in ALLOWED_TERMINATIONS, f"{key} unexpected termination {row.get('termination_status')}", errors)
        require(meta.get("cold_start") is True, f"{key} cold_start mismatch", errors)
        require(meta.get("mip_start_mode") == "none", f"{key} mip_start_mode mismatch", errors)
        require(meta.get("external_mip_start_used") is False, f"{key} external MIP start used", errors)
        require(meta.get("initialization_cache_reused") is False, f"{key} reused initialization cache", errors)
        require(meta.get("objective_mode") == "admission_only", f"{key} objective_mode mismatch", errors)
        require(meta.get("physical_cost_in_objective") is False, f"{key} physical cost appears in objective", errors)
        require(meta.get("threads", meta.get("configured_threads")) == 1, f"{key} Threads mismatch", errors)
        require(meta.get("solver_seed", meta.get("configured_gurobi_seed")) == 0, f"{key} solver Seed mismatch", errors)
        require(meta.get("configured_mip_gap") == 0.0, f"{key} MIPGap mismatch", errors)
        require(meta.get("configured_mip_gap_abs") == 0.0, f"{key} MIPGapAbs mismatch", errors)
        errors.extend(validate_runtime_accounting(row, configured_limit))
        require(meta.get("configured_end_to_end_limit_seconds") == configured_limit, f"{key} E2E limit mismatch", errors)
        effective_limit = meta.get("effective_solver_time_limit_seconds")
        if effective_limit is not None:
            require(configured_limit is not None and 0.0 <= float(effective_limit) <= configured_limit, f"{key} effective solver limit invalid", errors)
        if incumbent is not None:
            require(row.get("path_validity") is True, f"{key} path invalid with incumbent", errors)
            require(row.get("capacity_validity") is True, f"{key} capacity invalid with incumbent", errors)
            require(lower == int(incumbent), f"{key} integer LB mismatch", errors)
        require(upper == safe_integer_upper(raw_bound, total), f"{key} safe integer UB mismatch", errors)
        certified = bool(meta.get("acceptance_optimality_certified") or row.get("acceptance_count_certified"))
        if row.get("termination_status") in ("OPTIMAL", "BOUND_CERTIFIED"):
            require(certified, f"{key} certified termination lacks certification flag", errors)
            require(incumbent is not None and upper == int(incumbent), f"{key} certified record lacks matching LB/UB", errors)
        if meta.get("solver_status_name") == "TIME_LIMIT":
            require(row.get("termination_status") != "OPTIMAL", f"{key} TIME_LIMIT mislabeled OPTIMAL", errors)
        if expected_git_commit:
            require(row.get("git_commit") == expected_git_commit, f"{key} git_commit mismatch", errors)

        matches = baseline.get(scenario, [])
        require(len(matches) == 4, f"{key} baseline scenario does not have four algorithms", errors)
        for baseline_row in matches:
            for field in FINGERPRINT_FIELDS:
                require(row.get(field) == baseline_row.get(field), f"{key} {field} mismatches {baseline_row['key']['algorithm']}", errors)

    return {
        "output_dir": str(output_dir),
        "record_count": len(rows),
        "per_scale_counts": dict(Counter(key[0] for key in keys)),
        "per_scale_load_counts": {f"{scale}|{load}": count for (scale, load), count in Counter((key[0], key[1]) for key in keys).items()},
        "duplicate_run_keys": duplicate_keys,
        "errors": errors,
        "pass": not errors,
    }
