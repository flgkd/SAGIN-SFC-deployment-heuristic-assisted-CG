"""Human-invoked runner for the V3 S40 component-ablation plan.

This wrapper intentionally does not run on import. It delegates execution to
formal_runner only when the human calls the CLI.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from formal_runner import load_json, run_formal_plan
from formal_schema import formal_plan_from_dict


ALLOWED_ALGORITHMS = ("dwcg_warm", "dwcg_pure_hp", "dwcg_saginfe_pro_no_pfe")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run V3 S40 component-ablation records.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--algorithms", nargs="*", choices=ALLOWED_ALGORITHMS)
    parser.add_argument("--loads", nargs="*", choices=("Low", "Medium", "High"))
    parser.add_argument("--seeds", nargs="*", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    plan = formal_plan_from_dict(load_json(args.config))
    if plan.scenario.scales != ("S40",):
        raise ValueError("Component ablation runner accepts only S40 plans.")
    plan_algorithms = {algorithm.algorithm for algorithm in plan.scenario.algorithms if algorithm.enabled}
    unexpected = plan_algorithms - set(ALLOWED_ALGORITHMS)
    if unexpected:
        raise ValueError(f"Component ablation new-run plan contains unexpected algorithms: {sorted(unexpected)}")

    result = run_formal_plan(
        plan,
        args.output_dir,
        algorithms=args.algorithms or list(ALLOWED_ALGORITHMS),
        scales=["S40"],
        loads=args.loads,
        seeds=args.seeds,
        resume=args.resume,
        overwrite=args.overwrite,
        allow_dirty=False,
        cli_arguments=vars(args),
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
