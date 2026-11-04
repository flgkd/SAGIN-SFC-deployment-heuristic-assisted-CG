"""Create solver-free derived outputs for the V3 pricing-schedule study."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calibration_runner import sha256_fingerprint, utc_now, write_json_atomic
from scripts.audit_formal_pricing_schedule_v3 import (
    LOADS,
    SCHEDULES,
    audit,
    evidence_key,
    normalized_new_row,
    normalized_reuse_row,
    read_json,
    read_jsonl,
    scenario_key,
)


STRATEGY_PAIRS = (("EX", "ESV"), ("EX", "HYB-S5"), ("ESV", "HYB-S5"))
SWITCH_PAIRS = (("HYB-S2", "HYB-S5"), ("HYB-S2", "HYB-S7"), ("HYB-S5", "HYB-S7"))


def ensure_output_dir(path: Path, overwrite: bool) -> None:
    if path.exists() and any(path.iterdir()) and not overwrite:
        raise ValueError("Output directory is nonempty; choose a new directory or pass --overwrite.")
    path.mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, rows: List[Mapping[str, Any]], fieldnames: List[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: List[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def median(values: List[float]) -> float | None:
    return None if not values else float(statistics.median(values))


def mean(values: List[float]) -> float | None:
    return None if not values else float(statistics.mean(values))


def ratio(numerator: Any, denominator: Any) -> float | None:
    if numerator in (None, 0) or denominator in (None, 0):
        return None
    return float(numerator) / float(denominator)


def public_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "scale": row["scale"],
        "time_slots": row["time_slots"],
        "load_label": row["load_label"],
        "formal_seed": row["formal_seed"],
        "algorithm": row["algorithm"],
        "pricing_schedule": row["pricing_schedule"],
        "source_kind": row["source_kind"],
        "reuse_classification": row["reuse_classification"],
        "source_commit": row.get("source_commit") or row.get("git_commit"),
        "source_tag": row.get("source_tag"),
        "topology_fingerprint": row["topology_fingerprint"],
        "base_flow_fingerprint": row["base_flow_fingerprint"],
        "full_instance_fingerprint": row["full_instance_fingerprint"],
        "pricing_strategy": row["pricing_strategy"],
        "hybrid_switch_iteration": row["hybrid_switch_iteration"],
        "accepted_flow_count": row["accepted_flow_count"],
        "acceptance_rate": row["acceptance_rate"],
        "total_end_to_end_runtime_seconds": row["total_end_to_end_runtime_seconds"],
        "total_pricing_runtime": row["total_pricing_runtime"],
        "exact_pricing_call_count": row["exact_pricing_call_count"],
        "early_stop_pricing_call_count": row["early_stop_pricing_call_count"],
        "strict_exact_pricing_call_count": row["strict_exact_pricing_call_count"],
        "termination_status": row["termination_status"],
        "path_validity": row["path_validity"],
        "capacity_validity": row["capacity_validity"],
        "runtime_accounting_valid": row["runtime_accounting_valid"],
    }


def summarize(rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["load_label"], row["pricing_schedule"])].append(row)
    result = []
    for (load, schedule), group in sorted(groups.items()):
        accepted = [float(row["accepted_flow_count"]) for row in group if row["accepted_flow_count"] is not None]
        acceptance_rate = [float(row["acceptance_rate"]) for row in group if row["acceptance_rate"] is not None]
        e2e = [float(row["total_end_to_end_runtime_seconds"]) for row in group if row["total_end_to_end_runtime_seconds"] is not None]
        pricing = [float(row["total_pricing_runtime"]) for row in group if row["total_pricing_runtime"] is not None]
        result.append({
            "load_label": load,
            "pricing_schedule": schedule,
            "record_count": len(group),
            "mean_accepted_flow_count": mean(accepted),
            "median_accepted_flow_count": median(accepted),
            "mean_acceptance_rate": mean(acceptance_rate),
            "median_acceptance_rate": median(acceptance_rate),
            "median_e2e_runtime_seconds": median(e2e),
            "mean_e2e_runtime_seconds": mean(e2e),
            "median_pricing_runtime_seconds": median(pricing),
            "deterministic_work_budget_exhausted_count": sum(1 for row in group if row["termination_status"] == "DETERMINISTIC_WORK_BUDGET_EXHAUSTED"),
            "hard_wall_count": sum(1 for row in group if row["hard_wall_clock_limit_reached"] is True),
        })
    return result


def paired(rows: List[Mapping[str, Any]], pairs: Tuple[Tuple[str, str], ...]) -> List[Dict[str, Any]]:
    by_scenario: Dict[Tuple[str, int, str, int], Dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        by_scenario[scenario_key(row)][row["pricing_schedule"]] = row
    result = []
    for load in LOADS:
        for left, right in pairs:
            diffs: List[float] = []
            runtime_ratios: List[float] = []
            pricing_ratios: List[float] = []
            seeds = []
            for scenario, group in sorted(by_scenario.items()):
                if scenario[2] != load or left not in group or right not in group:
                    continue
                left_row, right_row = group[left], group[right]
                diff = float(left_row["accepted_flow_count"]) - float(right_row["accepted_flow_count"])
                diffs.append(diff)
                seeds.append(scenario[3])
                runtime_value = ratio(left_row["total_end_to_end_runtime_seconds"], right_row["total_end_to_end_runtime_seconds"])
                pricing_value = ratio(left_row["total_pricing_runtime"], right_row["total_pricing_runtime"])
                if runtime_value is not None:
                    runtime_ratios.append(runtime_value)
                if pricing_value is not None:
                    pricing_ratios.append(pricing_value)
            result.append({
                "load_label": load,
                "schedule_a": left,
                "schedule_b": right,
                "seed_count": len(seeds),
                "wins": sum(1 for value in diffs if value > 0),
                "ties": sum(1 for value in diffs if value == 0),
                "losses": sum(1 for value in diffs if value < 0),
                "net_accepted_flow_difference": sum(diffs),
                "mean_paired_accepted_flow_difference": mean(diffs),
                "median_paired_accepted_flow_difference": median(diffs),
                "median_e2e_runtime_ratio_a_over_b": median(runtime_ratios),
                "median_pricing_runtime_ratio_a_over_b": median(pricing_ratios),
                "seeds": ",".join(str(seed) for seed in seeds),
            })
    return result


def complexity(rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["load_label"], row["pricing_schedule"])].append(row)
    result = []
    for (load, schedule), group in sorted(groups.items()):
        def values(name: str) -> List[float]:
            return [float(row[name]) for row in group if row.get(name) is not None]
        result.append({
            "load_label": load,
            "pricing_schedule": schedule,
            "record_count": len(group),
            "median_exact_pricing_call_count": median(values("exact_pricing_call_count")),
            "median_early_stop_pricing_call_count": median(values("early_stop_pricing_call_count")),
            "median_strict_exact_pricing_call_count": median(values("strict_exact_pricing_call_count")),
            "median_total_pricing_runtime": median(values("total_pricing_runtime")),
            "median_total_end_to_end_runtime_seconds": median(values("total_end_to_end_runtime_seconds")),
        })
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create V3 pricing-schedule comparison outputs.")
    parser.add_argument("--new-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--reuse-manifest-json", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-git-commit")
    parser.add_argument("--expected-plan-fingerprint")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    audit_result = audit(args.new_dir, args.config, args.reuse_manifest_json, args.expected_git_commit, args.expected_plan_fingerprint)
    if not audit_result["pass"]:
        raise RuntimeError("Pricing-schedule audit failed; refusing to create comparison outputs.")
    ensure_output_dir(args.output_dir, args.overwrite)
    new_rows = [normalized_new_row(row) for row in read_jsonl(args.new_dir / "runs.jsonl")]
    reuse_rows = [normalized_reuse_row(row) for row in read_json(args.reuse_manifest_json).get("rows", [])]
    rows = sorted(new_rows + reuse_rows, key=evidence_key)
    public_rows = [public_row(row) for row in rows]
    summary_rows = summarize(rows)
    strategy_rows = paired(rows, STRATEGY_PAIRS)
    switch_rows = paired(rows, SWITCH_PAIRS)
    complexity_rows = complexity(rows)
    write_jsonl(args.output_dir / "pricing_schedule_runs.jsonl", public_rows)
    seed_fields = list(public_rows[0].keys()) if public_rows else []
    write_csv(args.output_dir / "pricing_schedule_seed_level.csv", public_rows, seed_fields)
    write_json_atomic(args.output_dir / "pricing_schedule_seed_level.json", public_rows)
    summary_fields = list(summary_rows[0].keys()) if summary_rows else []
    write_csv(args.output_dir / "pricing_schedule_summary.csv", summary_rows, summary_fields)
    write_json_atomic(args.output_dir / "pricing_schedule_summary.json", summary_rows)
    pair_fields = list(strategy_rows[0].keys()) if strategy_rows else []
    write_csv(args.output_dir / "pricing_strategy_pairwise_acceptance.csv", strategy_rows, pair_fields)
    write_json_atomic(args.output_dir / "pricing_strategy_pairwise_acceptance.json", strategy_rows)
    write_csv(args.output_dir / "pricing_strategy_pairwise_runtime.csv", strategy_rows, pair_fields)
    write_json_atomic(args.output_dir / "pricing_strategy_pairwise_runtime.json", strategy_rows)
    switch_fields = list(switch_rows[0].keys()) if switch_rows else []
    write_csv(args.output_dir / "hybrid_switch_pairwise_acceptance.csv", switch_rows, switch_fields)
    write_json_atomic(args.output_dir / "hybrid_switch_pairwise_acceptance.json", switch_rows)
    write_csv(args.output_dir / "hybrid_switch_pairwise_runtime.csv", switch_rows, switch_fields)
    write_json_atomic(args.output_dir / "hybrid_switch_pairwise_runtime.json", switch_rows)
    complexity_fields = list(complexity_rows[0].keys()) if complexity_rows else []
    write_csv(args.output_dir / "pricing_schedule_complexity_summary.csv", complexity_rows, complexity_fields)
    write_json_atomic(args.output_dir / "pricing_schedule_complexity_summary.json", complexity_rows)
    write_json_atomic(args.output_dir / "pricing_schedule_audit_summary.json", audit_result)
    manifest = {
        "manifest_type": "pricing_schedule_v3_comparison",
        "created_at": utc_now(),
        "new_source_dir": str(args.new_dir),
        "reuse_manifest_json": str(args.reuse_manifest_json),
        "record_count": len(rows),
        "new_count": len(new_rows),
        "reused_count": len(reuse_rows),
        "scenario_count": len({scenario_key(row) for row in rows}),
        "loads": list(LOADS),
        "pricing_schedules": list(SCHEDULES),
        "plan_fingerprint": audit_result["plan_fingerprint"],
        "pricing_schedule_runs_sha256": sha256_fingerprint(public_rows),
        "audit_pass": audit_result["pass"],
    }
    write_json_atomic(args.output_dir / "pricing_schedule_manifest.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
