"""Create the audited V3 temporal scalability comparison after formal audit."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Mapping, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from audit_formal_temporal_scalability_v3 import (
    ALGORITHMS,
    TIME_SLOTS,
    audit,
    meta,
    normalized_record,
    read_jsonl,
)
from calibration_runner import sha256_fingerprint


DISPLAY_NAMES = {
    "saginfe": "SAGINFE",
    "dwcg_pure": "DWCG-Pure",
    "dwcg_saginfe": "DWCG-SAGINFE",
    "dwcg_saginfe_pro": "DWCG-SAGINFE-PRO",
}
PAIRS = (
    ("dwcg_saginfe_pro", "saginfe"),
    ("dwcg_saginfe_pro", "dwcg_pure"),
    ("dwcg_saginfe_pro", "dwcg_saginfe"),
    ("dwcg_saginfe", "dwcg_pure"),
    ("saginfe", "dwcg_pure"),
)


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: List[Mapping[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


def _num_summary(values: List[float]) -> Dict[str, float | None]:
    return {
        "mean": mean(values) if values else None,
        "median": median(values) if values else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def _accepted(row: Mapping[str, Any]) -> int:
    return int(row.get("accepted_flow_count") or 0)


def _runtime(row: Mapping[str, Any]) -> float | None:
    value = row.get("total_end_to_end_runtime_seconds")
    if value is None:
        value = meta(row).get("total_end_to_end_runtime_seconds")
    if value is None:
        value = meta(row).get("algorithm_core_runtime_seconds")
    return float(value) if value is not None else None


def _seed_level(rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    output = []
    for row in rows:
        key = row["key"]
        row_meta = meta(row)
        runtime = _runtime(row)
        output.append({
            "time_slots": int(key["time_slots"]),
            "scale": key["scale"],
            "load": key["load_label"],
            "seed": int(key["formal_seed"]),
            "algorithm": key["algorithm"],
            "display_name": DISPLAY_NAMES[key["algorithm"]],
            "accepted_flow_count": _accepted(row),
            "acceptance_rate": float(row.get("acceptance_rate") or 0.0),
            "total_flow_count": int(row.get("total_flow_count") or 0),
            "total_end_to_end_runtime_seconds": runtime,
            "algorithm_core_runtime_seconds": row_meta.get("algorithm_core_runtime_seconds"),
            "pricing_runtime_seconds": row_meta.get("combined_pricing_runtime_seconds", row_meta.get("primary_pricing_runtime_seconds")),
            "final_rmp_runtime_seconds": row_meta.get("final_rmp_runtime_seconds"),
            "completed_cg_iterations": row_meta.get("completed_cg_iterations"),
            "final_column_pool_size": row_meta.get("final_column_pool_size"),
            "termination_status": row.get("termination_status"),
            "hard_wall_clock_limit_reached": row_meta.get("hard_wall_clock_limit_reached", row.get("hard_wall_clock_limit_reached")),
            "deterministic_work_budget_exhausted": bool(
                row_meta.get("deterministic_work_budget_exhausted")
                or row_meta.get("max_cg_iterations_reached")
                or row.get("termination_status") == "DETERMINISTIC_WORK_BUDGET_EXHAUSTED"
            ),
            "path_validity": row.get("path_validity"),
            "capacity_validity": row.get("capacity_validity"),
            "runtime_accounting_valid": row_meta.get("runtime_accounting_valid"),
            "teg_node_count": row.get("temporal_teg_node_count"),
            "storage_edge_count": row.get("temporal_storage_edge_count"),
            "communication_edge_count": row.get("temporal_communication_edge_count"),
            "clipping_count": row.get("temporal_clipping_count"),
            "horizon_last_slot_count": row.get("temporal_horizon_last_slot_count"),
            "physical_base_fingerprint": row.get("physical_base_fingerprint"),
            "temporal_prefix_fingerprint": row.get("temporal_prefix_fingerprint"),
            "non_temporal_flow_fingerprint": row.get("non_temporal_flow_fingerprint"),
            "paired_timing_latent_fingerprint": row.get("paired_timing_latent_fingerprint"),
            "full_temporal_flow_fingerprint": row.get("full_temporal_flow_fingerprint"),
            "full_instance_fingerprint": row.get("full_instance_fingerprint"),
        })
    return output


def _summary(seed_rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[int, str], List[Mapping[str, Any]]] = {}
    for row in seed_rows:
        grouped.setdefault((int(row["time_slots"]), row["algorithm"]), []).append(row)
    output = []
    for (time_slots, algorithm), group in sorted(grouped.items()):
        accepted = [float(row["accepted_flow_count"]) for row in group]
        rates = [float(row["acceptance_rate"]) for row in group]
        runtimes = [float(row["total_end_to_end_runtime_seconds"]) for row in group if row["total_end_to_end_runtime_seconds"] not in (None, "")]
        output.append({
            "time_slots": time_slots,
            "algorithm": algorithm,
            "display_name": DISPLAY_NAMES[algorithm],
            "record_count": len(group),
            "mean_accepted_flow_count": _num_summary(accepted)["mean"],
            "median_accepted_flow_count": _num_summary(accepted)["median"],
            "min_accepted_flow_count": _num_summary(accepted)["min"],
            "max_accepted_flow_count": _num_summary(accepted)["max"],
            "mean_acceptance_rate": _num_summary(rates)["mean"],
            "median_acceptance_rate": _num_summary(rates)["median"],
            "runtime_eligible_count": len(runtimes),
            "mean_total_end_to_end_runtime_seconds": _num_summary(runtimes)["mean"],
            "median_total_end_to_end_runtime_seconds": _num_summary(runtimes)["median"],
            "hard_wall_count": sum(str(row.get("hard_wall_clock_limit_reached")).lower() == "true" for row in group),
            "deterministic_work_budget_exhausted_count": sum(bool(row.get("deterministic_work_budget_exhausted")) for row in group),
            "path_valid_count": sum(str(row.get("path_validity")).lower() == "true" for row in group),
            "capacity_valid_count": sum(str(row.get("capacity_validity")).lower() == "true" for row in group),
            "runtime_accounting_valid_count": sum(str(row.get("runtime_accounting_valid")).lower() == "true" for row in group),
        })
    return output


def _pairwise(seed_rows: List[Mapping[str, Any]], runtime: bool = False) -> List[Dict[str, Any]]:
    by_scenario = {(int(row["time_slots"]), int(row["seed"]), row["algorithm"]): row for row in seed_rows}
    output = []
    for time_slots in TIME_SLOTS:
        for alg_a, alg_b in PAIRS:
            diffs: List[float] = []
            ratios: List[float] = []
            runtime_valid = True
            for seed in range(10):
                a = by_scenario[(time_slots, seed, alg_a)]
                b = by_scenario[(time_slots, seed, alg_b)]
                if runtime:
                    ra = a["total_end_to_end_runtime_seconds"]
                    rb = b["total_end_to_end_runtime_seconds"]
                    if ra in (None, "") or rb in (None, "") or float(rb) == 0.0:
                        runtime_valid = False
                        continue
                    diffs.append(float(rb) - float(ra))
                    ratios.append(float(ra) / float(rb))
                else:
                    diffs.append(float(a["accepted_flow_count"]) - float(b["accepted_flow_count"]))
            wins = sum(diff > 0 for diff in diffs)
            ties = sum(diff == 0 for diff in diffs)
            losses = sum(diff < 0 for diff in diffs)
            row = {
                "time_slots": time_slots,
                "pair": f"{DISPLAY_NAMES[alg_a]} vs {DISPLAY_NAMES[alg_b]}",
                "algorithm_a": alg_a,
                "algorithm_b": alg_b,
                "wins": wins,
                "ties": ties,
                "losses": losses,
                "number_of_seeds": 10,
            }
            if runtime:
                row.update({
                    "runtime_lower_is_better": True,
                    "median_runtime_ratio_a_over_b": median(ratios) if ratios else None,
                    "geometric_mean_runtime_ratio_a_over_b": math.exp(mean([math.log(value) for value in ratios])) if ratios else None,
                    "direct_runtime_interpretation_valid": runtime_valid,
                })
            else:
                row.update({
                    "sign_convention": "positive means algorithm_a accepts more flows",
                    "net_accepted_flow_difference": sum(diffs),
                    "mean_paired_accepted_flow_difference": mean(diffs) if diffs else None,
                    "median_paired_accepted_flow_difference": median(diffs) if diffs else None,
                    "min_paired_accepted_flow_difference": min(diffs) if diffs else None,
                    "max_paired_accepted_flow_difference": max(diffs) if diffs else None,
                })
            output.append(row)
    return output


def _complexity(seed_rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[int, str], List[Mapping[str, Any]]] = {}
    for row in seed_rows:
        grouped.setdefault((int(row["time_slots"]), row["algorithm"]), []).append(row)
    rows = []
    for (time_slots, algorithm), group in sorted(grouped.items()):
        for field in ("teg_node_count", "communication_edge_count", "storage_edge_count", "completed_cg_iterations", "pricing_runtime_seconds", "final_rmp_runtime_seconds", "final_column_pool_size", "total_end_to_end_runtime_seconds"):
            values = [float(row[field]) for row in group if row.get(field) not in (None, "")]
            summary = _num_summary(values)
            rows.append({
                "time_slots": time_slots,
                "algorithm": algorithm,
                "metric": field,
                "record_count": len(group),
                "available_count": len(values),
                "mean": summary["mean"],
                "median": summary["median"],
                "min": summary["min"],
                "max": summary["max"],
            })
    return rows


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create audited V3 temporal scalability comparison.")
    parser.add_argument("--formal-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-git-commit", required=True)
    parser.add_argument("--expected-plan-fingerprint", required=True)
    args = parser.parse_args(argv)
    summary = audit(args.formal_dir, args.config, args.expected_git_commit, args.expected_plan_fingerprint)
    if not summary["pass"]:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 1

    raw_rows = read_jsonl(args.formal_dir / "runs.jsonl")
    rows = [normalized_record(row) for row in raw_rows]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "temporal_scalability_runs.jsonl").open("w", encoding="utf-8") as handle:
        for row in sorted(rows, key=lambda r: (int(r["key"]["time_slots"]), int(r["key"]["formal_seed"]), r["key"]["algorithm"])):
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    seed_rows = _seed_level(rows)
    summary_rows = _summary(seed_rows)
    pairwise_acceptance = _pairwise(seed_rows, runtime=False)
    pairwise_runtime = _pairwise(seed_rows, runtime=True)
    complexity_rows = _complexity(seed_rows)
    write_csv(args.output_dir / "temporal_scalability_seed_level.csv", seed_rows, list(seed_rows[0]))
    write_csv(args.output_dir / "temporal_scalability_summary.csv", summary_rows, list(summary_rows[0]))
    write_csv(args.output_dir / "temporal_scalability_pairwise_acceptance.csv", pairwise_acceptance, list(pairwise_acceptance[0]))
    write_csv(args.output_dir / "temporal_scalability_pairwise_runtime.csv", pairwise_runtime, list(pairwise_runtime[0]))
    write_csv(args.output_dir / "temporal_scalability_complexity_summary.csv", complexity_rows, list(complexity_rows[0]))
    write_json(args.output_dir / "temporal_scalability_seed_level.json", {"records": seed_rows})
    write_json(args.output_dir / "temporal_scalability_summary.json", {"records": summary_rows})
    write_json(args.output_dir / "temporal_scalability_pairwise_acceptance.json", {"records": pairwise_acceptance})
    write_json(args.output_dir / "temporal_scalability_pairwise_runtime.json", {"records": pairwise_runtime})
    write_json(args.output_dir / "temporal_scalability_complexity_summary.json", {"records": complexity_rows})
    write_json(args.output_dir / "temporal_scalability_audit_summary.json", summary)
    manifest = {
        "pass": True,
        "errors": [],
        "record_count": 160,
        "scenario_count": 40,
        "source_formal_dir": str(args.formal_dir),
        "config": str(args.config),
        "expected_git_commit": args.expected_git_commit,
        "expected_plan_fingerprint": args.expected_plan_fingerprint,
        "runs_jsonl_sha256": sha256_fingerprint(rows),
        "fixed_workload_temporal_scalability": True,
        "v2_experimental_data_used": 0,
        "plot_generated": False,
        "generated_files": [
            "temporal_scalability_runs.jsonl",
            "temporal_scalability_seed_level.csv",
            "temporal_scalability_seed_level.json",
            "temporal_scalability_summary.csv",
            "temporal_scalability_summary.json",
            "temporal_scalability_pairwise_acceptance.csv",
            "temporal_scalability_pairwise_acceptance.json",
            "temporal_scalability_pairwise_runtime.csv",
            "temporal_scalability_pairwise_runtime.json",
            "temporal_scalability_complexity_summary.csv",
            "temporal_scalability_complexity_summary.json",
            "temporal_scalability_audit_summary.json",
            "temporal_scalability_manifest.json"
        ],
    }
    write_json(args.output_dir / "temporal_scalability_manifest.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
