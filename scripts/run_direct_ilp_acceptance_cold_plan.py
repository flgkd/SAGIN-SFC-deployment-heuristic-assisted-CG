"""Human-invoked runner for the cold Direct ILP acceptance-only plans."""

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


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a cold Direct ILP acceptance-only formal/pilot plan.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--scales", nargs="*")
    parser.add_argument("--loads", nargs="*")
    parser.add_argument("--seeds", nargs="*", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    plan = formal_plan_from_dict(load_json(args.config))
    result = run_formal_plan(
        plan,
        args.output_dir,
        algorithms=["direct_ilp_acceptance_cold"],
        scales=args.scales,
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
