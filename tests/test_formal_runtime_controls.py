import copy
import json
from pathlib import Path
import unittest

from formal_runner import dwcg_configured_e2e_limit, direct_ilp_acceptance_configured_e2e_limit
from formal_schema import FormalAlgorithmConfig, formal_plan_from_dict
from scripts.audit_direct_ilp_acceptance_cold_common import runtime_accounting_diagnostics
from scripts.experiment_record_validation import recorded_runtime_limit_errors

ROOT = Path(__file__).resolve().parents[1]


class FormalRuntimeControlsTest(unittest.TestCase):
    def test_absent_and_null_runtime_settings_have_no_implicit_limit(self):
        for settings in ({}, {"hard_time_limit_seconds": None}):
            for algorithm, resolver in (
                ("direct_ilp_acceptance_cold", direct_ilp_acceptance_configured_e2e_limit),
                ("dwcg_pure", dwcg_configured_e2e_limit),
            ):
                with self.subTest(algorithm=algorithm, settings=settings):
                    config = FormalAlgorithmConfig(algorithm=algorithm, **settings)
                    self.assertIsNone(resolver(config))

    def test_explicit_compatibility_limits_and_disable_override(self):
        for algorithm, resolver in (
            ("direct_ilp_acceptance_cold", direct_ilp_acceptance_configured_e2e_limit),
            ("dwcg_saginfe", dwcg_configured_e2e_limit),
        ):
            self.assertEqual(resolver(FormalAlgorithmConfig(
                algorithm=algorithm, hard_time_limit_seconds=45.0)), 45.0)
            self.assertIsNone(resolver(FormalAlgorithmConfig(
                algorithm=algorithm, disable_hard_time_limit=True)))
        self.assertEqual(dwcg_configured_e2e_limit(FormalAlgorithmConfig(
            algorithm="dwcg_pure", time_limit=45.0)), 45.0)
        self.assertIsNone(dwcg_configured_e2e_limit(FormalAlgorithmConfig(
            algorithm="dwcg_saginfe", time_limit=45.0, disable_hard_time_limit=True)))

    def test_all_formal_plans_preserve_intrinsic_limits_without_overall_limit(self):
        paths = list((ROOT / "configs/formal").glob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            plan = formal_plan_from_dict(json.loads(path.read_text(encoding="utf-8")))
            for algorithm in plan.scenario.algorithms:
                with self.subTest(plan=path.name, algorithm=algorithm.algorithm):
                    self.assertIsNone(algorithm.hard_time_limit_seconds)
                    self.assertIsNone(algorithm.time_limit)
                    if algorithm.algorithm.startswith("dwcg"):
                        self.assertIsNone(dwcg_configured_e2e_limit(algorithm))
                        self.assertEqual(algorithm.max_cg_iterations, 12)
                        self.assertEqual(algorithm.final_rmp_time_cap_seconds, 300)
                        for scale in plan.scenario.scales:
                            self.assertEqual(algorithm.max_exact_pricing_calls_by_scale[scale],
                                             13 * plan.scenario.scale_flow_counts[scale])
                    elif algorithm.algorithm == "direct_ilp_acceptance_cold":
                        self.assertIsNone(direct_ilp_acceptance_configured_e2e_limit(algorithm))

    def test_record_validation_uses_saved_settings_and_does_not_rewrite_them(self):
        for limit in (None, 45.0):
            row = {"configured_hard_time_limit_seconds": limit,
                   "configuration_snapshot": {"algorithm": {"hard_time_limit_seconds": limit}}}
            before = copy.deepcopy(row)
            self.assertEqual(recorded_runtime_limit_errors(row), [])
            self.assertEqual(row, before)
            row["configuration_snapshot"]["algorithm"]["hard_time_limit_seconds"] = 60.0
            self.assertTrue(recorded_runtime_limit_errors(row))
        self.assertTrue(recorded_runtime_limit_errors({}))
        for invalid in (-1, True, float("inf"), "none"):
            self.assertTrue(recorded_runtime_limit_errors({"configured_hard_time_limit_seconds": invalid}))

    def test_direct_ilp_accounting_accepts_unlimited_and_explicit_limits(self):
        for limit in (None, 45.0):
            row = {
                "key": {"scale": "S20", "load_label": "Low", "formal_seed": 0,
                        "algorithm": "direct_ilp_acceptance_cold"},
                "termination_status": "OPTIMAL",
                "total_end_to_end_runtime_seconds": 4.0,
                "direct_ilp_metadata": {
                    "total_end_to_end_runtime_seconds": 4.0,
                    "model_build_runtime_seconds": 1.0,
                    "solver_runtime_seconds": 2.0,
                    "solution_extraction_runtime_seconds": 1.0,
                    "effective_solver_time_limit_seconds": None if limit is None else 44.0,
                    "runtime_accounting_residual_seconds": 0.0,
                    "runtime_accounting_valid": True,
                    "solver_status_name": "OPTIMAL",
                    "solver_time_limit_reached": False,
                    "hard_wall_clock_limit_reached": False,
                },
            }
            result = runtime_accounting_diagnostics(row, limit)
            self.assertTrue(result["runtime_accounting_valid_recomputed"], result)
            row["direct_ilp_metadata"]["runtime_accounting_residual_seconds"] = 1.0
            self.assertFalse(runtime_accounting_diagnostics(row, limit)["runtime_accounting_valid_recomputed"])


if __name__ == "__main__":
    unittest.main()
