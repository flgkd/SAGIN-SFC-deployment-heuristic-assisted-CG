"""Command-line entry point for calibration runs."""

import argparse
import json
from pathlib import Path
import sys

from calibration_runner import calibration_matrix, run_calibration_plan
from calibration_schema import plan_from_dict


def load_plan(path: Path):
    return plan_from_dict(json.loads(path.read_text(encoding="utf-8")))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run calibration framework.")
    parser.add_argument("--plan", required=True, help="Path to calibration plan JSON.")
    parser.add_argument("--mode", choices=("smoke", "full"), required=True)
    parser.add_argument("--output", required=True, help="Output run directory.")
    parser.add_argument("--algorithms", nargs="*", choices=("direct_ilp", "saginfe"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Confirm full calibration mode noninteractively.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    plan = load_plan(Path(args.plan))
    matrix = calibration_matrix(plan, args.mode, args.algorithms)
    print(f"Planned solver runs: {len(matrix)}")
    print(f"Calibration seeds: {list(plan.scenario.calibration_seeds)}")
    print(f"Reserved formal seeds: {list(plan.scenario.reserved_formal_seeds)}")
    if args.mode == "smoke":
        print("WARNING: smoke mode verifies integration only and cannot calibrate parameters.")
    else:
        print("Full calibration mode will run licensed solvers according to the plan.")
        if not args.yes:
            response = input("Proceed with full calibration? Type YES to continue: ")
            if response != "YES":
                print("Aborted.")
                return 2
    result = run_calibration_plan(
        plan=plan,
        output_dir=Path(args.output),
        mode=args.mode,
        algorithms=args.algorithms,
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

