"""Create the post-run six-variant V3 S40 component-ablation comparison."""

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

from audit_formal_component_ablation_s40_v3 import (
    EXPECTED_LOADS,
    EXPECTED_SEEDS,
    FINGERPRINT_FIELDS,
    NEW_ALGORITHMS,
    REUSE_ALGORITHMS,
    SIX_ALGORITHMS,
    key,
    read_json,
    read_jsonl,
    scenario_key,
)


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


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


def summarize(rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        run_key = row["key"]
        grouped[(run_key["load_label"], run_key["algorithm"])].append(row)
    summary: List[Dict[str, Any]] = []
    for (load, algorithm), group in sorted(grouped.items(), key=lambda item: (EXPECTED_LOADS.index(item[0][0]), SIX_ALGORITHMS.index(item[0][1]))):
        accepted = [float(row["accepted_flow_count"]) for row in group if row.get("accepted_flow_count") is not None]
        runtimes = [float(row["total_end_to_end_runtime_seconds"]) for row in group if row.get("total_end_to_end_runtime_seconds") is not None]
        summary.append({
            "scale": "S40",
            "load_label": load,
            "algorithm": algorithm,
            "record_count": len(group),
            "mean_accepted_flow_count": sum(accepted) / len(accepted) if accepted else None,
            "median_runtime_seconds": sorted(runtimes)[len(runtimes) // 2] if runtimes else None,
            "timeout_or_hard_wall_count": sum(
                1 for row in group
                if row.get("termination_status") in {"TIME_LIMIT", "TIME_LIMIT_WITH_INCUMBENT", "E2E_HARD_WALL_WITH_INCUMBENT"}
                or (row.get("dwcg_metadata") or {}).get("hard_wall_clock_limit_reached") is True
            ),
        })
    return summary


def create_comparison(merged_dir: Path, new_dir: Path, output_dir: Path, expected_new_git_commit: str | None = None) -> Dict[str, Any]:
    errors: List[str] = []
    merged_rows = read_jsonl(merged_dir / "merged_runs.jsonl")
    new_rows = read_jsonl(new_dir / "runs.jsonl")
    reuse_rows = [row for row in merged_rows if key(row)[0] == "S40" and key(row)[3] in REUSE_ALGORITHMS]
    combined = sorted(reuse_rows + new_rows, key=lambda row: (key(row)[1], key(row)[2], SIX_ALGORITHMS.index(key(row)[3])))

    if len(reuse_rows) != 90:
        errors.append(f"expected 90 reusable records, found {len(reuse_rows)}")
    if len(new_rows) != 90:
        errors.append(f"expected 90 new records, found {len(new_rows)}")
    if len(combined) != 180:
        errors.append(f"expected 180 combined records, found {len(combined)}")
    keys = [key(row) for row in combined]
    duplicates = [item for item, count in Counter(keys).items() if count != 1]
    if duplicates:
        errors.append(f"duplicate combined keys: {duplicates[:10]}")
    scenarios: Dict[Tuple[str, str, int], List[Mapping[str, Any]]] = defaultdict(list)
    for row in combined:
        scenarios[scenario_key(row)].append(row)
        if key(row)[3] in NEW_ALGORITHMS and expected_new_git_commit and row.get("git_commit") != expected_new_git_commit:
            errors.append(f"{key(row)} git commit mismatch")
        if key(row)[3] in NEW_ALGORITHMS and row.get("integration_only") is not False:
            errors.append(f"{key(row)} is an integration/pilot record")
    expected_scenarios = {("S40", load, seed) for load in EXPECTED_LOADS for seed in EXPECTED_SEEDS}
    if set(scenarios) != expected_scenarios:
        errors.append("combined scenario set mismatch")
    for scenario, group in scenarios.items():
        algorithms = {key(row)[3] for row in group}
        if algorithms != set(SIX_ALGORITHMS):
            errors.append(f"{scenario} algorithms {sorted(algorithms)} != expected six variants")
        for field in FINGERPRINT_FIELDS:
            if len({row.get(field) for row in group}) != 1:
                errors.append(f"{scenario} {field} mismatch")
    manifest = {
        "pass": not errors,
        "errors": errors,
        "record_count": len(combined),
        "scenario_count": len(scenarios),
        "algorithms": list(SIX_ALGORITHMS),
        "source_merged_dir": str(merged_dir),
        "source_new_dir": str(new_dir),
        "expected_new_git_commit": expected_new_git_commit,
    }
    if errors:
        return manifest
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "component_ablation_s40_six_variant_runs.jsonl", combined)
    write_json(output_dir / "component_ablation_s40_six_variant_manifest.json", manifest)
    write_csv(output_dir / "component_ablation_s40_summary.csv", summarize(combined))
    return manifest


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create six-variant S40 component-ablation comparison after all new records pass audit.")
    parser.add_argument("--merged-dir", required=True, type=Path)
    parser.add_argument("--new-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-new-git-commit")
    args = parser.parse_args(argv)
    manifest = create_comparison(args.merged_dir, args.new_dir, args.output_dir, args.expected_new_git_commit)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
