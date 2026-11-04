"""Create the 120-record V3 S40 deadline-sensitivity comparison after audit."""

from __future__ import annotations

import argparse
import csv
import json
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

from audit_formal_deadline_sensitivity_s40_v3 import (
    ALGORITHMS,
    PROFILES,
    audit,
    meta,
    normalized_record,
    read_jsonl,
)


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


def write_json(path: Path, payload: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: List[Mapping[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


def _runtime(row: Mapping[str, Any]) -> float | None:
    row_meta = meta(row)
    value = row.get("total_end_to_end_runtime_seconds")
    if value is None:
        value = row_meta.get("total_end_to_end_runtime_seconds")
    if value is None:
        value = row_meta.get("algorithm_core_runtime_seconds")
    return float(value) if value is not None else None


def _accepted(row: Mapping[str, Any]) -> int:
    for field in ("accepted_flow_count", "accepted_count", "accepted_flows"):
        value = row.get(field)
        if isinstance(value, (int, float)):
            return int(value)
    return int(row.get("accepted_flow_ids_count", 0))


def _rate(row: Mapping[str, Any]) -> float:
    accepted = _accepted(row)
    total = int(row.get("total_flow_count") or 0)
    return accepted / total if total else 0.0


def _num_summary(values: List[float]) -> Dict[str, float | None]:
    return {
        "mean": mean(values) if values else None,
        "median": median(values) if values else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def _seed_level(rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    output = []
    for row in rows:
        key = row["key"]
        row_meta = meta(row)
        output.append({
            "deadline_profile": row["deadline_profile"],
            "paper_label": {"RealTime": "Tight", "Balanced": "Balanced", "Flexible": "Loose"}[row["deadline_profile"]],
            "scale": key["scale"],
            "load": key["load_label"],
            "seed": key["formal_seed"],
            "algorithm": key["algorithm"],
            "display_name": DISPLAY_NAMES[key["algorithm"]],
            "accepted_flow_count": _accepted(row),
            "acceptance_rate": _rate(row),
            "total_flow_count": row.get("total_flow_count"),
            "total_end_to_end_runtime_seconds": _runtime(row),
            "termination_status": row.get("termination_status"),
            "hard_wall_clock_limit_reached": row_meta.get("hard_wall_clock_limit_reached", row.get("hard_wall_clock_limit_reached")),
            "deterministic_work_budget_exhausted": row.get("termination_status") == "DETERMINISTIC_WORK_BUDGET_EXHAUSTED",
            "path_validity": row.get("path_validity"),
            "capacity_validity": row.get("capacity_validity"),
            "runtime_accounting_valid": row_meta.get("runtime_accounting_valid"),
            "topology_fingerprint": row.get("topology_fingerprint"),
            "deadline_neutral_flow_fingerprint": row.get("deadline_invariant_flow_fingerprint"),
            "full_instance_fingerprint": row.get("full_instance_fingerprint"),
            "deadline_clipping_count": row.get("deadline_clipping_count"),
            "deadline_horizon_last_slot_count": row.get("deadline_horizon_last_slot_count"),
        })
    return output


def _summary(seed_rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], List[Mapping[str, Any]]] = {}
    for row in seed_rows:
        grouped.setdefault((row["deadline_profile"], row["algorithm"]), []).append(row)
    output = []
    for (profile, algorithm), group in sorted(grouped.items()):
        accepted = [float(row["accepted_flow_count"]) for row in group]
        rates = [float(row["acceptance_rate"]) for row in group]
        runtimes = [float(row["total_end_to_end_runtime_seconds"]) for row in group if row["total_end_to_end_runtime_seconds"] not in (None, "")]
        output.append({
            "deadline_profile": profile,
            "paper_label": {"RealTime": "Tight", "Balanced": "Balanced", "Flexible": "Loose"}[profile],
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
    by_scenario = {(row["deadline_profile"], int(row["seed"]), row["algorithm"]): row for row in seed_rows}
    output = []
    for profile in PROFILES:
        for alg_a, alg_b in PAIRS:
            diffs = []
            ratios = []
            runtime_valid = True
            for seed in range(10):
                a = by_scenario[(profile, seed, alg_a)]
                b = by_scenario[(profile, seed, alg_b)]
                if runtime:
                    ra = a["total_end_to_end_runtime_seconds"]
                    rb = b["total_end_to_end_runtime_seconds"]
                    if ra in (None, "") or rb in (None, "") or float(rb) == 0.0:
                        runtime_valid = False
                        continue
                    diffs.append(float(rb) - float(ra))
                    ratios.append(float(ra) / float(rb))
                else:
                    diffs.append(int(a["accepted_flow_count"]) - int(b["accepted_flow_count"]))
            wins = sum(diff > 0 for diff in diffs)
            ties = sum(diff == 0 for diff in diffs)
            losses = sum(diff < 0 for diff in diffs)
            row = {
                "deadline_profile": profile,
                "paper_label": {"RealTime": "Tight", "Balanced": "Balanced", "Flexible": "Loose"}[profile],
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
                    "mean_runtime_ratio_a_over_b": mean(ratios) if ratios else None,
                    "direct_runtime_interpretation_valid": runtime_valid,
                })
            else:
                row.update({
                    "sign_convention": "positive means algorithm_a accepts more flows",
                    "net_accepted_flow_difference": sum(diffs),
                    "mean_paired_accepted_flow_difference": mean(diffs),
                    "median_paired_accepted_flow_difference": median(diffs),
                    "min_paired_accepted_flow_difference": min(diffs),
                    "max_paired_accepted_flow_difference": max(diffs),
                })
            output.append(row)
    return output


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create audited V3 S40 deadline-sensitivity comparison.")
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
    with (args.output_dir / "deadline_sensitivity_s40_runs.jsonl").open("w", encoding="utf-8") as handle:
        for row in sorted(rows, key=lambda r: (r["deadline_profile"], r["key"]["formal_seed"], r["key"]["algorithm"])):
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    seed_rows = _seed_level(rows)
    summary_rows = _summary(seed_rows)
    pairwise_acceptance = _pairwise(seed_rows, runtime=False)
    pairwise_runtime = _pairwise(seed_rows, runtime=True)
    write_csv(args.output_dir / "deadline_sensitivity_seed_level.csv", seed_rows, list(seed_rows[0]))
    write_csv(args.output_dir / "deadline_sensitivity_summary.csv", summary_rows, list(summary_rows[0]))
    write_csv(args.output_dir / "deadline_sensitivity_pairwise_acceptance.csv", pairwise_acceptance, list(pairwise_acceptance[0]))
    write_csv(args.output_dir / "deadline_sensitivity_pairwise_runtime.csv", pairwise_runtime, list(pairwise_runtime[0]))
    write_json(args.output_dir / "deadline_sensitivity_seed_level.json", {"records": seed_rows})
    write_json(args.output_dir / "deadline_sensitivity_summary.json", {"records": summary_rows})
    write_json(args.output_dir / "deadline_sensitivity_pairwise_acceptance.json", {"records": pairwise_acceptance})
    write_json(args.output_dir / "deadline_sensitivity_pairwise_runtime.json", {"records": pairwise_runtime})
    write_json(args.output_dir / "deadline_sensitivity_audit_summary.json", summary)
    manifest = {
        "pass": True,
        "errors": [],
        "record_count": 120,
        "scenario_count": 30,
        "source_formal_dir": str(args.formal_dir),
        "config": str(args.config),
        "expected_git_commit": args.expected_git_commit,
        "expected_plan_fingerprint": args.expected_plan_fingerprint,
        "generated_files": [
            "deadline_sensitivity_s40_runs.jsonl",
            "deadline_sensitivity_seed_level.csv",
            "deadline_sensitivity_seed_level.json",
            "deadline_sensitivity_summary.csv",
            "deadline_sensitivity_summary.json",
            "deadline_sensitivity_pairwise_acceptance.csv",
            "deadline_sensitivity_pairwise_acceptance.json",
            "deadline_sensitivity_pairwise_runtime.csv",
            "deadline_sensitivity_pairwise_runtime.json",
            "deadline_sensitivity_audit_summary.json",
            "deadline_sensitivity_s40_manifest.json",
        ],
    }
    write_json(args.output_dir / "deadline_sensitivity_s40_manifest.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
