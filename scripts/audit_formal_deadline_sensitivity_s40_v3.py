"""Read-only audit for V3 S40 deadline-sensitivity formal records."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from record_compatibility import normalize_record, resolve_input_path

from calibration_runner import sha256_fingerprint
from config_schema import get_candidate_deadline_profile


ALGORITHMS = ("saginfe", "dwcg_pure", "dwcg_saginfe", "dwcg_saginfe_pro")
PROFILES = ("RealTime", "Balanced", "Flexible")
EXPECTED_DISTRIBUTIONS = {
    "RealTime": {"1": 0.5, "2": 0.5},
    "Balanced": {"2": 0.3, "3": 0.4, "4": 0.3},
    "Flexible": {"3": 0.3, "4": 0.4, "5": 0.3},
}
MISSING = object()
METADATA_FIELDS = (
    "deadline_profile",
    "deadline_clipping_count",
    "deadline_horizon_last_slot_count",
    "deadline_saturation_count",
    "deadline_invariant_flow_fingerprint",
    "deadline_neutral_flow_fingerprint",
    "full_flow_fingerprint",
    "deadline_uniform_fingerprint",
    "deadline_slack_histogram",
    "deadline_arrival_time_histogram",
    "deadline_time_histogram",
    "deadline_minimum_slack",
    "deadline_maximum_slack",
    "deadline_mean_slack",
    "latest_source_time",
)


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    path = resolve_input_path(path)
    if not path.exists():
        return []
    return [normalize_record(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _present(value: Any) -> bool:
    return value is not MISSING and value is not None and value != {} and value != []


def meta(row: Mapping[str, Any]) -> Mapping[str, Any]:
    if row["key"]["algorithm"] == "saginfe":
        return row.get("heuristic_metadata") or {}
    return row.get("dwcg_metadata") or {}


def semantic_value(row: Mapping[str, Any], field: str, errors: List[str] | None = None) -> Any:
    """Return a deadline semantic field from supported raw record layouts.

    New SAGINFE records may expose deadline fields at top level. DWCG-family
    records from commit 36b9632 store the authoritative values inside
    ``dwcg_metadata`` while the top-level dataclass defaults remain null/empty.
    A real conflicting duplicate representation is reported instead of being
    silently resolved.
    """

    aliases = {
        "deadline_neutral_flow_fingerprint": ("deadline_neutral_flow_fingerprint", "deadline_invariant_flow_fingerprint"),
        "deadline_invariant_flow_fingerprint": ("deadline_invariant_flow_fingerprint", "deadline_neutral_flow_fingerprint"),
    }.get(field, (field,))
    containers = [row, meta(row)]
    found: List[Tuple[str, Any]] = []
    for container_name, container in (("top_level", containers[0]), ("metadata", containers[1])):
        for alias in aliases:
            if alias in container:
                value = container.get(alias)
                if _present(value):
                    found.append((f"{container_name}.{alias}", value))
                    break
    if not found:
        return MISSING
    first_name, first_value = found[0]
    for name, value in found[1:]:
        if value != first_value and errors is not None:
            errors.append(f"{_display_key(row)} conflicting {field}: {first_name}={first_value!r}, {name}={value!r}")
    return first_value


def normalized_record(row: Mapping[str, Any], errors: List[str] | None = None) -> Dict[str, Any]:
    normalized = dict(row)
    for field in METADATA_FIELDS:
        value = semantic_value(row, field, errors)
        if value is not MISSING:
            normalized[field] = value
    if "deadline_neutral_flow_fingerprint" not in normalized and "deadline_invariant_flow_fingerprint" in normalized:
        normalized["deadline_neutral_flow_fingerprint"] = normalized["deadline_invariant_flow_fingerprint"]
    return normalized


def _display_key(row: Mapping[str, Any]) -> Tuple[Any, ...]:
    key = row.get("key") or {}
    return (
        key.get("scale"),
        row.get("deadline_profile") or meta(row).get("deadline_profile"),
        key.get("load_label"),
        key.get("formal_seed"),
        key.get("algorithm"),
    )


def record_key(row: Mapping[str, Any]) -> Tuple[str, str, int, str]:
    key = row["key"]
    profile = semantic_value(row, "deadline_profile")
    return str(profile if profile is not MISSING else None), str(key["load_label"]), int(key["formal_seed"]), str(key["algorithm"])


def scenario_key(row: Mapping[str, Any]) -> Tuple[str, str, int]:
    profile, load, seed, _algorithm = record_key(row)
    return profile, load, seed


def require(condition: bool, message: str, errors: List[str]) -> None:
    if not condition:
        errors.append(message)


def expected_plan_fingerprint(config: Mapping[str, Any]) -> str:
    return sha256_fingerprint(config)


def audit(output_dir: Path, config_path: Path, expected_commit: str | None = None, expected_fingerprint: str | None = None) -> Dict[str, Any]:
    errors: List[str] = []
    config = read_json(config_path)
    expected_fp = expected_fingerprint or expected_plan_fingerprint(config)
    rows = read_jsonl(output_dir / "runs.jsonl")
    manifest = read_json(output_dir / "manifest.json") if (output_dir / "manifest.json").exists() else {}
    normalized_rows = [normalized_record(row, errors) for row in rows]
    keys = [record_key(row) for row in normalized_rows]
    expected_keys = {(profile, "Medium", seed, algorithm) for profile in PROFILES for seed in range(10) for algorithm in ALGORITHMS}
    actual_keys = set(keys)
    require(len(rows) == 120, f"expected 120 records, found {len(rows)}", errors)
    require(len({key[:3] for key in keys}) == 30, "expected 30 profile/seed scenarios", errors)
    duplicates = [key for key, count in Counter(keys).items() if count != 1]
    require(not duplicates, f"duplicate run keys: {duplicates[:10]}", errors)
    require(not sorted(expected_keys - actual_keys), f"missing run keys: {sorted(expected_keys - actual_keys)[:10]}", errors)
    require(not sorted(actual_keys - expected_keys), f"extra run keys: {sorted(actual_keys - expected_keys)[:10]}", errors)
    require(Counter(key[0] for key in keys) == Counter({profile: 40 for profile in PROFILES}), "profile counts mismatch", errors)
    require(Counter(key[3] for key in keys) == Counter({algorithm: 30 for algorithm in ALGORITHMS}), "algorithm counts mismatch", errors)
    require(manifest.get("plan_fingerprint") == expected_fp, "manifest plan fingerprint mismatch", errors)
    require(manifest.get("run_category") == "formal_paper_or_diagnostic", "manifest is not formal", errors)
    require(manifest.get("integration_only") is False, "manifest integration_only is not false", errors)
    require(config.get("version") == "v3", "config version is not v3", errors)
    require(config.get("plan_kind") == "formal_deadline_sensitivity", "config plan_kind mismatch", errors)
    require(config.get("integration_only") is False, "config integration_only is not false", errors)
    require(config.get("smoke") is False, "config smoke is not false", errors)

    for profile, distribution in EXPECTED_DISTRIBUTIONS.items():
        actual = {str(k): float(v) for k, v in get_candidate_deadline_profile(profile).slack_distribution.as_dict().items()}
        require(actual == distribution, f"{profile} distribution changed: {actual}", errors)
    require(config["scenario"]["deadline_profiles"] == list(PROFILES), "config deadline_profiles mismatch", errors)
    require(config["scenario"]["loads"] == [{"label": "Medium", "demand_multiplier": 0.6}], "config Medium load mismatch", errors)
    require(config["scenario"]["scales"] == ["S40"], "config scale mismatch", errors)
    require(config["scenario"]["time_slots"] == 8, "config T mismatch", errors)
    require(config["scenario"]["scale_flow_counts"] == {"S40": 160}, "config F mismatch", errors)

    grouped: Dict[Tuple[str, str, int], List[Mapping[str, Any]]] = defaultdict(list)
    by_seed: Dict[int, List[Mapping[str, Any]]] = defaultdict(list)
    for row in normalized_rows:
        key = record_key(row)
        profile, load, seed, algorithm = key
        grouped[(profile, load, seed)].append(row)
        by_seed[seed].append(row)
        row_meta = meta(row)
        require(row["key"]["scale"] == "S40", f"{key} scale mismatch", errors)
        require(row["key"]["time_slots"] == 8, f"{key} T mismatch", errors)
        require(row["total_flow_count"] == 160, f"{key} F mismatch", errors)
        require(row["key"]["demand_multiplier"] == 0.6, f"{key} demand multiplier mismatch", errors)
        require(row.get("integration_only") is False and row.get("run_category") == "formal_paper_or_diagnostic", f"{key} pilot record admitted", errors)
        snapshot_plan = (row.get("configuration_snapshot") or {}).get("plan") or {}
        require(snapshot_plan.get("version") == "v3", f"{key} non-v3 plan snapshot admitted", errors)
        require(snapshot_plan.get("plan_kind") == "formal_deadline_sensitivity", f"{key} plan kind mismatch", errors)
        require(snapshot_plan.get("integration_only") is False, f"{key} integration snapshot admitted", errors)
        require(snapshot_plan.get("smoke") is False, f"{key} smoke snapshot admitted", errors)
        require(row.get("path_validity") is True, f"{key} path invalid", errors)
        require(row.get("capacity_validity") is True, f"{key} capacity invalid", errors)
        require(row_meta.get("runtime_accounting_valid") is True, f"{key} runtime accounting invalid", errors)
        clipping = semantic_value(row, "deadline_clipping_count")
        require(clipping is not MISSING, f"{key} missing deadline clipping count", errors)
        require(clipping == 0, f"{key} deadline clipping nonzero", errors)
        require(row.get("deadline_profile") == profile, f"{key} deadline profile field mismatch", errors)
        require(row.get("deadline_invariant_flow_fingerprint"), f"{key} missing deadline-neutral fingerprint", errors)
        require(row.get("full_instance_fingerprint"), f"{key} missing full-instance fingerprint", errors)
        require(sha256_fingerprint(snapshot_plan) == expected_fp, f"{key} plan snapshot fingerprint mismatch", errors)
        if expected_commit:
            require(row.get("git_commit") == expected_commit, f"{key} git commit mismatch", errors)
    for scenario, group in grouped.items():
        require({row["key"]["algorithm"] for row in group} == set(ALGORITHMS), f"{scenario} missing algorithm", errors)
        for field in ("topology_fingerprint", "deadline_invariant_flow_fingerprint", "full_instance_fingerprint"):
            require(len({row.get(field) for row in group}) == 1, f"{scenario} {field} mismatch across algorithms", errors)
    for seed, group in by_seed.items():
        require(len({row.get("topology_fingerprint") for row in group}) == 1, f"seed {seed} topology mismatch across profiles", errors)
        require(len({row.get("deadline_invariant_flow_fingerprint") for row in group}) == 1, f"seed {seed} deadline-neutral flow mismatch across profiles", errors)
        full_by_profile = {
            profile: {row.get("full_instance_fingerprint") for row in group if row.get("deadline_profile") == profile}
            for profile in PROFILES
        }
        require(all(len(values) == 1 for values in full_by_profile.values()), f"seed {seed} full fingerprint missing by profile", errors)
        if all(len(values) == 1 for values in full_by_profile.values()):
            require(len({next(iter(values)) for values in full_by_profile.values()}) == 3, f"seed {seed} profile full fingerprints not distinct", errors)
    return {
        "pass": not errors,
        "errors": errors,
        "record_count": len(rows),
        "scenario_count": len({key[:3] for key in keys}),
        "counts_by_profile": dict(Counter(key[0] for key in keys)),
        "counts_by_algorithm": dict(Counter(key[3] for key in keys)),
        "plan_fingerprint": expected_fp,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit V3 S40 deadline sensitivity without Gurobi.")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--expected-git-commit")
    parser.add_argument("--expected-plan-fingerprint")
    parser.add_argument("--summary-json", type=Path)
    args = parser.parse_args(argv)
    summary = audit(args.output_dir, args.config, args.expected_git_commit, args.expected_plan_fingerprint)
    text = json.dumps(summary, indent=2, sort_keys=True)
    print(text)
    if args.summary_json:
        args.summary_json.write_text(text + "\n", encoding="utf-8")
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
