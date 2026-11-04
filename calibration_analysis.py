"""Analysis helpers for calibration records."""

import json
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Mapping, Optional

from calibration_schema import CalibrationThresholds


REGIME_LABELS = {
    "UNDERLOADED",
    "CAPACITY_BOUNDARY",
    "OVERLOADED",
    "SEVERELY_OVERLOADED",
    "INSUFFICIENT_CERTIFICATION",
    "INVALID_DATA",
}


def analyze_calibration_records(
    records: Iterable[Mapping[str, Any]],
    thresholds: Optional[CalibrationThresholds] = None,
    smoke_mode: bool = False,
    transfer_validation_only: bool = False,
) -> Dict[str, Any]:
    thresholds = thresholds or CalibrationThresholds()
    thresholds.validate()
    records = list(records)
    grouped: Dict[tuple, List[Mapping[str, Any]]] = {}
    for record in records:
        key = record["key"]
        grouped.setdefault((float(key["demand_multiplier"]), key["algorithm"]), []).append(record)

    summary_rows = []
    regime_by_multiplier = {}
    direct_by_multiplier: Dict[float, List[Mapping[str, Any]]] = {}
    saginfe_by_multiplier: Dict[float, List[Mapping[str, Any]]] = {}
    for (multiplier, algorithm), group in sorted(grouped.items()):
        certified = [r["certified_acceptance_rate"] for r in group if r.get("certified_acceptance_rate") is not None]
        acceptance = [r["acceptance_rate"] for r in group if r.get("acceptance_rate") is not None]
        runtimes = [r["runtime_seconds"] for r in group if r.get("runtime_seconds") is not None]
        node_counts = [
            r.get("solver_metadata", {}).get("node_count")
            for r in group
            if r.get("solver_metadata", {}).get("node_count") is not None
        ]
        timeouts = [r for r in group if _is_timeout(r)]
        errors = [r for r in group if r.get("error_type")]
        successes = [r for r in group if r.get("success")]
        label = ""
        sufficient = ""
        if algorithm == "direct_ilp":
            label = classify_regime(certified, group, thresholds, smoke_mode)
            sufficient = label not in ("INSUFFICIENT_CERTIFICATION", "INVALID_DATA")
            regime_by_multiplier[multiplier] = label
            direct_by_multiplier[multiplier] = group
        elif algorithm == "saginfe":
            saginfe_by_multiplier[multiplier] = group
        summary_rows.append({
            "demand_multiplier": multiplier,
            "algorithm": algorithm,
            "planned_seeds": len(group),
            "completed_seeds": len(group),
            "certified_seeds": len(certified),
            "success_count": len(successes),
            "error_count": len(errors),
            "timeout_count": len(timeouts),
            "median_acceptance": _median_or_none(acceptance),
            "median_certified_acceptance": _median_or_none(certified),
            "full_acceptance_fraction": _full_acceptance_fraction(certified),
            "mean_runtime": mean(runtimes) if runtimes else None,
            "median_runtime": _median_or_none(runtimes),
            "median_ilp_node_count": _median_or_none(node_counts) if algorithm == "direct_ilp" else None,
            "root_solve_fraction": _root_solve_fraction(group) if algorithm == "direct_ilp" else None,
            "regime_label": label,
            "classification_sufficient": sufficient,
        })

    gaps = _paired_gaps(direct_by_multiplier, saginfe_by_multiplier)
    transfer_validation = (
        analyze_transfer_validation(records)
        if transfer_validation_only
        else None
    )
    recommendation = build_recommendation(regime_by_multiplier, smoke_mode)
    return {
        "smoke_mode": smoke_mode,
        "transfer_validation_only": transfer_validation_only,
        "summary_rows": summary_rows,
        "regime_by_multiplier": {} if transfer_validation_only else {str(key): value for key, value in regime_by_multiplier.items()},
        "saginfe_diagnostics": _heuristic_stats(saginfe_by_multiplier),
        "transfer_validation": transfer_validation,
        "ilp_minus_saginfe_acceptance_gaps": gaps,
        "recommendation": (
            {"final_regimes_declared": False, "message": "TRANSFER_VALIDATION_ONLY: heuristic output cannot certify regimes."}
            if transfer_validation_only else recommendation
        ),
        "principles": {
            "primary_evidence": "certified direct-ILP acceptance and offered-load proxies",
            "runtime_role": "diagnostic only",
            "heuristic_role": "diagnostic only; never certifies regimes",
        },
    }


def analyze_transfer_validation(records: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    groups: Dict[float, List[Mapping[str, Any]]] = {}
    for record in records:
        key = record["key"]
        if key["algorithm"] != "saginfe":
            continue
        groups.setdefault(float(key["demand_multiplier"]), []).append(record)

    by_multiplier = {}
    med_acceptances = []
    proc_ratios = []
    comm_ratios = []
    topology_by_seed: Dict[int, set] = {}
    base_by_seed: Dict[int, set] = {}
    for multiplier, group in sorted(groups.items()):
        acceptance = [
            record.get("heuristic_acceptance_rate")
            for record in group
            if record.get("heuristic_acceptance_rate") is not None
        ]
        proc = [
            record.get("instance_metrics", {}).get("processing_offered_load_ratio_proxy")
            for record in group
            if record.get("instance_metrics", {}).get("processing_offered_load_ratio_proxy") is not None
        ]
        comm = [
            record.get("instance_metrics", {}).get("communication_offered_load_ratio_proxy")
            for record in group
            if record.get("instance_metrics", {}).get("communication_offered_load_ratio_proxy") is not None
        ]
        for record in group:
            seed = record["key"]["calibration_seed"]
            topology_by_seed.setdefault(seed, set()).add(record.get("topology_fingerprint"))
            base_by_seed.setdefault(seed, set()).add(record.get("base_flow_fingerprint"))
        median_acceptance = _median_or_none(acceptance)
        median_proc = _median_or_none(proc)
        median_comm = _median_or_none(comm)
        med_acceptances.append(median_acceptance)
        proc_ratios.append(median_proc)
        comm_ratios.append(median_comm)
        by_multiplier[str(multiplier)] = {
            "median_saginfe_acceptance": median_acceptance,
            "minimum_saginfe_acceptance": min(acceptance) if acceptance else None,
            "maximum_saginfe_acceptance": max(acceptance) if acceptance else None,
            "median_processing_offered_load_ratio_proxy": median_proc,
            "median_communication_offered_load_ratio_proxy": median_comm,
        }
    return {
        "label": "TRANSFER_VALIDATION_ONLY",
        "by_multiplier": by_multiplier,
        "processing_load_proxy_monotone_nondecreasing": _monotone_nondecreasing(proc_ratios),
        "communication_load_proxy_monotone_nondecreasing": _monotone_nondecreasing(comm_ratios),
        "median_saginfe_acceptance_monotone_nonincreasing": _monotone_nonincreasing(med_acceptances),
        "paired_topology_fingerprint_consistency": all(len(values) == 1 for values in topology_by_seed.values()),
        "paired_base_flow_fingerprint_consistency": all(len(values) == 1 for values in base_by_seed.values()),
        "certified_regime_labels_allowed": False,
    }


def classify_regime(
    certified_acceptance_rates: List[float],
    records: List[Mapping[str, Any]],
    thresholds: CalibrationThresholds,
    smoke_mode: bool = False,
) -> str:
    if smoke_mode:
        return "INSUFFICIENT_CERTIFICATION"
    if any(record.get("error_type") and record.get("accepted_flow_count") is None for record in records):
        if not certified_acceptance_rates:
            return "INVALID_DATA"
    if len(certified_acceptance_rates) < thresholds.min_certified_direct_ilp_seeds:
        return "INSUFFICIENT_CERTIFICATION"
    med = median(certified_acceptance_rates)
    full_fraction = _full_acceptance_fraction(certified_acceptance_rates)
    if med >= thresholds.underloaded_median_min and full_fraction >= thresholds.underloaded_full_acceptance_min:
        return "UNDERLOADED"
    if thresholds.boundary_median_min <= med <= thresholds.boundary_median_max and full_fraction <= thresholds.boundary_full_acceptance_max:
        return "CAPACITY_BOUNDARY"
    if thresholds.overloaded_median_min <= med <= thresholds.overloaded_median_max:
        return "OVERLOADED"
    if med < thresholds.severe_median_max:
        return "SEVERELY_OVERLOADED"
    return "INVALID_DATA"


def build_recommendation(regime_by_multiplier: Mapping[float, str], smoke_mode: bool) -> Dict[str, Any]:
    if smoke_mode:
        return {
            "final_regimes_declared": False,
            "message": "Smoke mode verifies integration only and cannot calibrate parameters.",
        }
    labels = list(regime_by_multiplier.values())
    if not labels:
        return {"final_regimes_declared": False, "message": "No direct-ILP records are available."}
    if all(label == "UNDERLOADED" for label in labels):
        return {"final_regimes_declared": False, "extended_sweep_needed": True, "message": "All multipliers are underloaded; extend only the demand multiplier sweep first."}
    if all(label == "SEVERELY_OVERLOADED" for label in labels):
        return {"final_regimes_declared": False, "extended_sweep_needed": True, "message": "All multipliers are severely overloaded; reduce only the demand multiplier sweep first."}
    inverse = {label: [mult for mult, regime in regime_by_multiplier.items() if regime == label] for label in REGIME_LABELS}
    return {
        "final_regimes_declared": False,
        "candidate_low_multiplier": min(inverse.get("UNDERLOADED", []) or [None]),
        "candidate_medium_boundary_multiplier": min(inverse.get("CAPACITY_BOUNDARY", []) or [None]),
        "candidate_high_multiplier": min(inverse.get("OVERLOADED", []) or [None]),
        "extended_sweep_needed": any(label in ("INSUFFICIENT_CERTIFICATION", "INVALID_DATA") for label in labels),
        "message": "Recommendations are provisional; candidate files are not modified automatically.",
    }


def write_reports(output_dir: Path, analysis: Mapping[str, Any]) -> None:
    report_json = output_dir / "calibration_report.json"
    report_md = output_dir / "calibration_report.md"
    report_json.write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# Calibration Report", ""]
    lines.append(f"Smoke mode: {analysis.get('smoke_mode')}")
    if analysis.get("transfer_validation_only"):
        lines.append("Mode: TRANSFER_VALIDATION_ONLY")
    lines.append("")
    lines.append("## Regime Labels")
    for multiplier, label in analysis.get("regime_by_multiplier", {}).items():
        lines.append(f"- multiplier {multiplier}: {label}")
    lines.append("")
    lines.append("## Recommendation")
    lines.append(str(analysis.get("recommendation", {}).get("message", "")))
    lines.append("")
    lines.append("Runtime and heuristic results are diagnostics only; certified direct-ILP acceptance drives regime labels.")
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _median_or_none(values: List[float]) -> Optional[float]:
    return median(values) if values else None


def _full_acceptance_fraction(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(1 for value in values if value >= 1.0 - 1e-9) / len(values)


def _is_timeout(record: Mapping[str, Any]) -> bool:
    if "TIME_LIMIT" in str(record.get("termination_status", "")):
        return True
    return bool(record.get("solver_metadata", {}).get("timeout"))


def _root_solve_fraction(records: List[Mapping[str, Any]]) -> Optional[float]:
    counts = [
        record.get("solver_metadata", {}).get("node_count")
        for record in records
        if record.get("solver_metadata", {}).get("node_count") is not None
    ]
    if not counts:
        return None
    return sum(1 for count in counts if count <= 1) / len(counts)


def _heuristic_stats(groups: Mapping[float, List[Mapping[str, Any]]]) -> Dict[str, Any]:
    result = {}
    for multiplier, records in groups.items():
        values = [record.get("heuristic_acceptance_rate") for record in records if record.get("heuristic_acceptance_rate") is not None]
        result[str(multiplier)] = {
            "count": len(values),
            "median_heuristic_acceptance": _median_or_none(values),
            "mean_heuristic_acceptance": mean(values) if values else None,
        }
    return result


def _paired_gaps(
    direct: Mapping[float, List[Mapping[str, Any]]],
    saginfe: Mapping[float, List[Mapping[str, Any]]],
) -> Dict[str, Any]:
    gaps = {}
    for multiplier, direct_records in direct.items():
        heuristic_by_seed = {
            record["key"]["calibration_seed"]: record
            for record in saginfe.get(multiplier, [])
            if record.get("heuristic_acceptance_rate") is not None
        }
        values = []
        for record in direct_records:
            seed = record["key"]["calibration_seed"]
            if record.get("certified_acceptance_rate") is not None and seed in heuristic_by_seed:
                values.append(record["certified_acceptance_rate"] - heuristic_by_seed[seed]["heuristic_acceptance_rate"])
        gaps[str(multiplier)] = {
            "count": len(values),
            "median_ilp_minus_saginfe_acceptance": _median_or_none(values),
            "mean_ilp_minus_saginfe_acceptance": mean(values) if values else None,
        }
    return gaps


def _monotone_nondecreasing(values: List[Optional[float]]) -> Optional[bool]:
    clean = [value for value in values if value is not None]
    if len(clean) < 2:
        return None
    return all(a <= b + 1e-12 for a, b in zip(clean, clean[1:]))


def _monotone_nonincreasing(values: List[Optional[float]]) -> Optional[bool]:
    clean = [value for value in values if value is not None]
    if len(clean) < 2:
        return None
    return all(a + 1e-12 >= b for a, b in zip(clean, clean[1:]))
