"""Read-only audit helpers for the V3 S40 component-ablation campaign."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from record_compatibility import normalize_record, resolve_input_path

from scripts.experiment_record_validation import recorded_runtime_limit_errors
from calibration_runner import sha256_fingerprint
from formal_schema import expected_dwcg_components, formal_plan_from_dict


REUSE_ALGORITHMS = ("dwcg_pure", "dwcg_saginfe", "dwcg_saginfe_pro")
NEW_ALGORITHMS = ("dwcg_warm", "dwcg_pure_hp", "dwcg_saginfe_pro_no_pfe")
SIX_ALGORITHMS = (
    "dwcg_pure",
    "dwcg_warm",
    "dwcg_pure_hp",
    "dwcg_saginfe",
    "dwcg_saginfe_pro_no_pfe",
    "dwcg_saginfe_pro",
)
EXPECTED_LOADS = ("Low", "Medium", "High")
EXPECTED_SEEDS = tuple(range(10))
EXPECTED_EXACT_CAPS = {"S20": 1040, "S30": 1560, "S40": 2080, "S50": 2600}
FINGERPRINT_FIELDS = ("topology_fingerprint", "base_flow_fingerprint", "full_instance_fingerprint")


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    path = resolve_input_path(path)
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(normalize_record(json.loads(line)))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL: {exc}") from exc
    return rows


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: List[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def key(row: Mapping[str, Any]) -> Tuple[str, str, int, str]:
    run_key = row["key"]
    return (
        str(run_key["scale"]),
        str(run_key["load_label"]),
        int(run_key["formal_seed"]),
        str(run_key["algorithm"]),
    )


def scenario_key(row: Mapping[str, Any]) -> Tuple[str, str, int]:
    scale, load, seed, _algorithm = key(row)
    return scale, load, seed


def dwcg_meta(row: Mapping[str, Any]) -> Mapping[str, Any]:
    return row.get("dwcg_metadata") or {}


def require(condition: bool, message: str, errors: List[str]) -> None:
    if not condition:
        errors.append(message)


def plan_fingerprint(config_path: Path) -> str:
    return sha256_fingerprint(formal_plan_from_dict(read_json(config_path)))


def _record_reuse_status(row: Mapping[str, Any]) -> Tuple[str, str]:
    run_key = key(row)
    scale, _load, _seed, algorithm = run_key
    meta = dwcg_meta(row)
    if scale != "S40":
        return "NOT_REUSABLE_OTHER", "not S40"
    if algorithm not in REUSE_ALGORITHMS:
        return "NOT_REUSABLE_OTHER", "not a frozen reusable component-ablation algorithm"
    expected = expected_dwcg_components(algorithm)
    checks = [
        (row.get("path_validity") is True, "path validity is not true"),
        (row.get("capacity_validity") is True, "capacity validity is not true"),
        (row.get("total_flow_count") == 160, "total_flow_count is not 160"),
        (not recorded_runtime_limit_errors(row, meta), "runtime setting differs from saved configuration"),
        (meta.get("configured_max_cg_iterations") == 12, "CG iteration cap mismatch"),
        (meta.get("configured_max_exact_pricing_calls") == 2080, "S40 exact-pricing cap mismatch"),
        (meta.get("configured_final_rmp_reserve_seconds") == 300.0, "final RMP reserve mismatch"),
        (meta.get("configured_final_rmp_time_cap_seconds") == 300.0, "final RMP cap mismatch"),
        (meta.get("pricing_worker_count") == 1, "pricing worker count mismatch"),
        (meta.get("parallel_pricing_enabled") is False, "parallel pricing is enabled"),
        (meta.get("solver_threads_per_worker") == 1, "solver thread count mismatch"),
        (meta.get("initialization_source") == expected["initialization_source"], "initialization source mismatch"),
        (meta.get("saginfe_initialization_used") is expected["use_saginfe_initialization"], "SAGINFE initialization switch mismatch"),
        (meta.get("configured_pricing_strategy") == expected["pricing_strategy"], "pricing strategy mismatch"),
        (meta.get("pricing_strategy_invariant_valid") is True, "pricing strategy invariant invalid"),
    ]
    if algorithm == "dwcg_saginfe_pro":
        checks.extend([
            (meta.get("selective_pfe_enabled") is True, "PRO selective PFE is not enabled"),
            (meta.get("pro_family_algorithm") is True, "PRO family flag missing"),
        ])
    failed = [reason for ok, reason in checks if not ok]
    if failed:
        return "NOT_REUSABLE_SEMANTIC_MISMATCH", "; ".join(failed)
    missing = [field for field in FINGERPRINT_FIELDS if not row.get(field)]
    if missing:
        return "NOT_REUSABLE_MISSING_FIELD", f"missing fingerprints: {missing}"
    return "REUSABLE_EXACT_MATCH", ""


def _row_plan_fingerprint(row: Mapping[str, Any]) -> str | None:
    snapshot = (row.get("configuration_snapshot") or {}).get("plan")
    if not snapshot:
        return None
    return sha256_fingerprint(formal_plan_from_dict(snapshot))


def audit_reuse(merged_dir: Path) -> Dict[str, Any]:
    rows = read_jsonl(merged_dir / "merged_runs.jsonl")
    manifest = read_json(merged_dir / "merge_manifest.json")
    errors: List[str] = []
    require(manifest.get("pass") is True, "merge manifest pass is not true", errors)
    require(manifest.get("errors") == [], "merge manifest errors are not empty", errors)
    require(len(rows) == 480, f"merged record count {len(rows)} != 480", errors)

    reusable = [
        row for row in rows
        if key(row)[0] == "S40" and key(row)[3] in REUSE_ALGORITHMS
    ]
    require(len(reusable) == 90, f"S40 reusable candidate count {len(reusable)} != 90", errors)
    keys = [key(row) for row in reusable]
    require(not [item for item, count in Counter(keys).items() if count != 1], "duplicate reusable keys", errors)
    require(Counter(k[3] for k in keys) == Counter({algorithm: 30 for algorithm in REUSE_ALGORITHMS}), "reusable algorithm counts mismatch", errors)
    require(Counter(k[1] for k in keys) == Counter({load: 30 for load in EXPECTED_LOADS}), "reusable load counts mismatch", errors)

    by_scenario: Dict[Tuple[str, str, int], List[Dict[str, Any]]] = defaultdict(list)
    by_scale_seed: Dict[Tuple[str, int], Dict[str, Tuple[str, str]]] = defaultdict(dict)
    manifest_rows: List[Dict[str, Any]] = []
    for row in sorted(reusable, key=key):
        status, reason = _record_reuse_status(row)
        run_key = key(row)
        meta = dwcg_meta(row)
        by_scenario[scenario_key(row)].append(row)
        by_scale_seed[(run_key[0], run_key[2])][run_key[1]] = (
            row.get("topology_fingerprint"),
            row.get("base_flow_fingerprint"),
        )
        manifest_rows.append({
            "scale": run_key[0],
            "load_label": run_key[1],
            "formal_seed": run_key[2],
            "algorithm": run_key[3],
            "reuse_class": status,
            "reason": reason,
            "accepted_flow_count": row.get("accepted_flow_count"),
            "acceptance_rate": row.get("acceptance_rate"),
            "total_flow_count": row.get("total_flow_count"),
            "termination_status": row.get("termination_status"),
            "runtime_seconds": row.get("total_end_to_end_runtime_seconds"),
            "path_validity": row.get("path_validity"),
            "capacity_validity": row.get("capacity_validity"),
            "plan_fingerprint": _row_plan_fingerprint(row),
            "configured_hard_time_limit_seconds": meta.get("configured_hard_time_limit_seconds"),
            "configured_max_cg_iterations": meta.get("configured_max_cg_iterations"),
            "configured_max_exact_pricing_calls": meta.get("configured_max_exact_pricing_calls"),
            "configured_final_rmp_reserve_seconds": meta.get("configured_final_rmp_reserve_seconds"),
            "configured_final_rmp_time_cap_seconds": meta.get("configured_final_rmp_time_cap_seconds"),
            "threads": meta.get("solver_threads_per_worker"),
            "pricing_worker_count": meta.get("pricing_worker_count"),
            "parallel_pricing_enabled": meta.get("parallel_pricing_enabled"),
            "initialization_source": meta.get("initialization_source"),
            "saginfe_initialization_used": meta.get("saginfe_initialization_used"),
            "pricing_strategy": meta.get("configured_pricing_strategy"),
            "selective_pfe_enabled": meta.get("selective_pfe_enabled"),
            "topology_fingerprint": row.get("topology_fingerprint"),
            "base_flow_fingerprint": row.get("base_flow_fingerprint"),
            "full_instance_fingerprint": row.get("full_instance_fingerprint"),
        })

    for scenario, group in by_scenario.items():
        require(len(group) == 3, f"{scenario} does not have three reusable algorithms", errors)
        for field in FINGERPRINT_FIELDS:
            require(len({row.get(field) for row in group}) == 1, f"{scenario} {field} mismatch across reusable algorithms", errors)
    for scale_seed, loads in by_scale_seed.items():
        require(set(loads) == set(EXPECTED_LOADS), f"{scale_seed} does not have all loads for cross-load pairing", errors)
        require(len({value for value in loads.values()}) == 1, f"{scale_seed} topology/base-flow mismatch across loads", errors)

    non_reusable = [row for row in manifest_rows if row["reuse_class"] != "REUSABLE_EXACT_MATCH"]
    if non_reusable:
        errors.append(f"{len(non_reusable)} proposed reusable records are not exact matches")
    return {
        "pass": not errors,
        "errors": errors,
        "record_count": len(rows),
        "reusable_record_count": len(reusable),
        "reusable_by_algorithm": dict(Counter(row["algorithm"] for row in manifest_rows)),
        "reusable_by_load": dict(Counter(row["load_label"] for row in manifest_rows)),
        "manifest_rows": manifest_rows,
    }


def _audit_new_record(row: Mapping[str, Any], expected_git_commit: str | None, expected_plan: str | None, errors: List[str]) -> None:
    run_key = key(row)
    scale, _load, _seed, algorithm = run_key
    meta = dwcg_meta(row)
    expected = expected_dwcg_components(algorithm)
    require(scale == "S40", f"{run_key} is not S40", errors)
    require(algorithm in NEW_ALGORITHMS, f"{run_key} unexpected algorithm", errors)
    require(row.get("path_validity") is True, f"{run_key} path invalid", errors)
    require(row.get("capacity_validity") is True, f"{run_key} capacity invalid", errors)
    require(row.get("error_type") in (None, ""), f"{run_key} error_type present", errors)
    require(row.get("integration_only") is False, f"{run_key} is an integration/pilot record", errors)
    require(row.get("run_category") == "formal_paper_or_diagnostic", f"{run_key} run category is not formal", errors)
    require(row.get("total_flow_count") == 160, f"{run_key} total_flow_count mismatch", errors)
    require(meta.get("runtime_accounting_valid") is True, f"{run_key} runtime accounting invalid", errors)
    errors.extend(f"{run_key} {message}" for message in recorded_runtime_limit_errors(row, meta))
    require(meta.get("configured_max_cg_iterations") == 12, f"{run_key} CG cap mismatch", errors)
    require(meta.get("configured_max_exact_pricing_calls") == 2080, f"{run_key} exact-pricing cap mismatch", errors)
    require(meta.get("configured_final_rmp_reserve_seconds") == 300.0, f"{run_key} final-RMP reserve mismatch", errors)
    require(meta.get("configured_final_rmp_time_cap_seconds") == 300.0, f"{run_key} final-RMP cap mismatch", errors)
    require(meta.get("pricing_worker_count") == 1, f"{run_key} pricing worker mismatch", errors)
    require(meta.get("parallel_pricing_enabled") is False, f"{run_key} parallel pricing enabled", errors)
    require(meta.get("solver_threads_per_worker") == 1, f"{run_key} thread mismatch", errors)
    require(meta.get("initialization_source") == expected["initialization_source"], f"{run_key} initialization source mismatch", errors)
    require(meta.get("saginfe_initialization_used") is expected["use_saginfe_initialization"], f"{run_key} SAGINFE init switch mismatch", errors)
    require(meta.get("configured_pricing_strategy") == expected["pricing_strategy"], f"{run_key} pricing strategy mismatch", errors)
    require(meta.get("pricing_strategy_invariant_valid") is True, f"{run_key} pricing invariant invalid", errors)
    if algorithm == "dwcg_saginfe_pro_no_pfe":
        require(meta.get("selective_pfe_enabled") is False, f"{run_key} selective PFE enabled", errors)
        for field in (
            "selective_pfe_call_count",
            "selective_pfe_round_count",
            "selective_pfe_candidate_count",
            "selective_pfe_unique_candidate_count",
            "selective_pfe_positive_column_count",
            "selective_pfe_duplicate_count",
            "selective_pfe_invalid_candidate_count",
        ):
            require(int(meta.get(field) or 0) == 0, f"{run_key} {field} is nonzero", errors)
        require(float(meta.get("selective_pfe_runtime_seconds") or 0.0) == 0.0, f"{run_key} PFE runtime is nonzero", errors)
    if expected_git_commit:
        require(row.get("git_commit") == expected_git_commit, f"{run_key} git commit mismatch", errors)
    if expected_plan:
        snapshot = row.get("configuration_snapshot", {}).get("plan", {})
        if snapshot:
            require(sha256_fingerprint(formal_plan_from_dict(snapshot)) == expected_plan, f"{run_key} plan snapshot fingerprint mismatch", errors)


def audit_new_records(
    new_dir: Path,
    reuse_manifest_json: Path,
    expected_plan_fingerprint: str | None = None,
    expected_git_commit: str | None = None,
) -> Dict[str, Any]:
    errors: List[str] = []
    rows = read_jsonl(new_dir / "runs.jsonl")
    manifest = read_json(new_dir / "manifest.json") if (new_dir / "manifest.json").exists() else {}
    reuse = read_json(reuse_manifest_json)
    reuse_rows = reuse.get("manifest_rows", [])
    reusable_by_scenario: Dict[Tuple[str, str, int], List[Mapping[str, Any]]] = defaultdict(list)
    for row in reuse_rows:
        reusable_by_scenario[(row["scale"], row["load_label"], int(row["formal_seed"]))].append(row)

    keys = [key(row) for row in rows]
    require(len(rows) == 90, f"expected 90 new records, found {len(rows)}", errors)
    duplicate_keys = [item for item, count in Counter(keys).items() if count != 1]
    require(not duplicate_keys, f"duplicate new run keys: {duplicate_keys[:10]}", errors)
    expected_keys = {
        ("S40", load, seed, algorithm)
        for load in EXPECTED_LOADS
        for seed in EXPECTED_SEEDS
        for algorithm in NEW_ALGORITHMS
    }
    actual_keys = set(keys)
    missing_keys = sorted(expected_keys - actual_keys)
    extra_keys = sorted(actual_keys - expected_keys)
    require(not missing_keys, f"missing new run keys: {missing_keys[:10]}", errors)
    require(not extra_keys, f"extra new run keys: {extra_keys[:10]}", errors)
    require(Counter(k[3] for k in keys) == Counter({algorithm: 30 for algorithm in NEW_ALGORITHMS}), "new algorithm counts mismatch", errors)
    require(Counter(k[1] for k in keys) == Counter({load: 30 for load in EXPECTED_LOADS}), "new load counts mismatch", errors)
    if expected_plan_fingerprint:
        require(manifest.get("plan_fingerprint") == expected_plan_fingerprint, "manifest plan fingerprint mismatch", errors)
    if expected_git_commit:
        require(manifest.get("git_commit") == expected_git_commit, "manifest git commit mismatch", errors)

    by_scenario: Dict[Tuple[str, str, int], List[Mapping[str, Any]]] = defaultdict(list)
    by_scale_seed: Dict[Tuple[str, int], Dict[str, Tuple[str, str]]] = defaultdict(dict)
    for row in rows:
        _audit_new_record(row, expected_git_commit, expected_plan_fingerprint, errors)
        scenario = scenario_key(row)
        by_scenario[scenario].append(row)
        by_scale_seed[(scenario[0], scenario[2])][scenario[1]] = (row.get("topology_fingerprint"), row.get("base_flow_fingerprint"))
        for reusable in reusable_by_scenario.get(scenario, []):
            for field in FINGERPRINT_FIELDS:
                require(row.get(field) == reusable.get(field), f"{key(row)} {field} mismatch versus {reusable['algorithm']}", errors)
    for scenario, group in by_scenario.items():
        require({row["key"]["algorithm"] for row in group} == set(NEW_ALGORITHMS), f"{scenario} missing new algorithms", errors)
        for field in FINGERPRINT_FIELDS:
            require(len({row.get(field) for row in group}) == 1, f"{scenario} {field} mismatch across new algorithms", errors)
    for scale_seed, loads in by_scale_seed.items():
        require(set(loads) == set(EXPECTED_LOADS), f"{scale_seed} missing cross-load fingerprints", errors)
        require(len({value for value in loads.values()}) == 1, f"{scale_seed} topology/base-flow mismatch across loads", errors)
    return {
        "pass": not errors,
        "errors": errors,
        "new_record_count": len(rows),
        "new_by_algorithm": dict(Counter(k[3] for k in keys)),
        "new_by_load": dict(Counter(k[1] for k in keys)),
        "plan_fingerprint": manifest.get("plan_fingerprint"),
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit V3 S40 component-ablation records without Gurobi.")
    parser.add_argument("--merged-dir", type=Path, default=Path("outputs/merged_formal_v3_480_dwcg_saginfe_pro_970415f"))
    parser.add_argument("--new-dir", type=Path)
    parser.add_argument("--config", type=Path, default=Path("configs/formal/formal_component_ablation_s40_v3.json"))
    parser.add_argument("--reuse-manifest-json", type=Path, default=Path("results/frozen/component_ablation_s40_v3/existing_v3_reuse_manifest.json"))
    parser.add_argument("--write-reuse-manifest", action="store_true")
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--expected-plan-fingerprint")
    parser.add_argument("--expected-git-commit")
    args = parser.parse_args(argv)

    summary = audit_reuse(args.merged_dir)
    if args.write_reuse_manifest:
        write_csv(Path("results/frozen/component_ablation_s40_v3/existing_v3_reuse_manifest.csv"), summary["manifest_rows"])
        write_json(args.reuse_manifest_json, summary)
    if args.new_dir:
        expected_plan = args.expected_plan_fingerprint or plan_fingerprint(args.config)
        new_summary = audit_new_records(
            args.new_dir,
            args.reuse_manifest_json,
            expected_plan_fingerprint=expected_plan,
            expected_git_commit=args.expected_git_commit,
        )
        summary = {**summary, "new_records_audit": new_summary, "pass": summary["pass"] and new_summary["pass"]}
        summary["errors"] = summary["errors"] + new_summary["errors"]
    text = json.dumps({k: v for k, v in summary.items() if k != "manifest_rows"}, indent=2, sort_keys=True)
    print(text)
    if args.summary_json:
        write_json(args.summary_json, summary)
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
