"""Command-line entry point for V3 formal benchmark runs."""

import argparse
import json
from pathlib import Path
import sys

from formal_runner import formal_matrix, run_formal_plan
from formal_schema import FORMAL_ALGORITHMS, formal_plan_from_dict


def load_formal_plan(path: Path):
    return formal_plan_from_dict(json.loads(path.read_text(encoding="utf-8")))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run V3 formal benchmark framework.")
    parser.add_argument("--plan", required=True, help="Path to formal plan JSON.")
    parser.add_argument("--output", required=True, help="Output run directory.")
    parser.add_argument("--algorithms", nargs="*", choices=FORMAL_ALGORITHMS)
    parser.add_argument("--scales", nargs="*", help="Optional scale filter, e.g. S30 S50.")
    parser.add_argument("--loads", nargs="*", help="Optional load-label filter, e.g. Medium High.")
    parser.add_argument("--seeds", nargs="*", type=int, help="Optional formal-seed filter.")
    parser.add_argument("--variants", nargs="*", help="Optional algorithm variant_id filter.")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Confirm non-smoke formal plans noninteractively.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    plan = load_formal_plan(Path(args.plan))
    matrix = formal_matrix(
        plan,
        args.algorithms,
        scales=args.scales,
        loads=args.loads,
        seeds=args.seeds,
        variants=args.variants,
    )
    print(f"Planned formal runs: {len(matrix)}")
    print(f"Formal seeds: {list(plan.scenario.formal_seeds)}")
    print(f"Loads: {[load.label + '=' + str(load.demand_multiplier) for load in plan.scenario.loads]}")
    if plan.integration_only:
        print("WARNING: this plan is integration-only and must not be used for paper statistics.")
    else:
        print("Formal benchmark plan selected.")
        if not args.yes:
            response = input("Proceed with formal benchmark? Type YES to continue: ")
            if response != "YES":
                print("Aborted.")
                return 2
    result = run_formal_plan(
        plan=plan,
        output_dir=Path(args.output),
        algorithms=args.algorithms,
        scales=args.scales,
        loads=args.loads,
        seeds=args.seeds,
        variants=args.variants,
        resume=args.resume,
        allow_dirty=args.allow_dirty,
        overwrite=args.overwrite,
        cli_arguments=vars(args),
    )
    print(f"Completed this invocation: {result['completed_this_invocation']}")
    print(f"Output directory: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

