import json
from pathlib import Path
import unittest

from calibration_runner import base_flow_fingerprint, topology_fingerprint
from config_schema import get_candidate_scale
from formal_runner import build_formal_instance, formal_matrix
from formal_schema import formal_plan_from_dict


ROOT = Path(__file__).resolve().parents[1]
FORMAL_CONFIG = ROOT / "configs" / "formal" / "formal_scale_load_master_v3.json"
PILOT_CONFIG = ROOT / "configs" / "pilot" / "scaling_runtime_pilot_v3.json"
CG_CALIBRATION_CONFIG = ROOT / "configs" / "pilot" / "scaling_cg_budget_calibration_v3.json"
CANDIDATE_BUDGET_CONFIG = ROOT / "configs" / "pilot" / "scaling_candidate_budget_validation_v3.json"
FINAL_RMP_300_CONFIG = ROOT / "configs" / "pilot" / "scaling_final_rmp_300_validation_v3.json"
PREFLIGHT_A_CONFIG = ROOT / "configs" / "preflight" / "scale_load_master_preflight_s20_low_v3.json"
PREFLIGHT_B_CONFIG = ROOT / "configs" / "preflight" / "scale_load_master_preflight_s50_high_v3.json"


def load_plan(path):
    return formal_plan_from_dict(json.loads(path.read_text(encoding="utf-8")))


class ScalingV3ConfigTest(unittest.TestCase):
    def test_formal_scaling_matrix(self):
        plan = load_plan(FORMAL_CONFIG)
        self.assertEqual(plan.name, "formal_scale_load_master_v3")
        self.assertEqual(plan.scenario.scales, ("S20", "S30", "S40", "S50"))
        self.assertEqual(plan.scenario.time_slots, 8)
        self.assertEqual(plan.scenario.formal_seeds, tuple(range(10)))
        self.assertEqual(
            [(load.label, load.demand_multiplier) for load in plan.scenario.loads],
            [("Low", 0.4), ("Medium", 0.6), ("High", 0.8)],
        )
        self.assertEqual(
            [algorithm.algorithm for algorithm in plan.scenario.algorithms],
            ["saginfe", "dwcg_pure", "dwcg_saginfe"],
        )
        self.assertEqual(len(formal_matrix(plan)), 360)

    def test_scale_counts_and_explicit_flow_counts(self):
        plan = load_plan(FORMAL_CONFIG)
        expected = {
            "S20": (8, 4, 8, 80),
            "S30": (12, 6, 12, 120),
            "S40": (16, 8, 16, 160),
            "S50": (20, 10, 20, 200),
        }
        self.assertEqual(plan.scenario.scale_flow_counts, {key: value[3] for key, value in expected.items()})
        for scale_name, (space, air, ground, flow_count) in expected.items():
            scale = get_candidate_scale(scale_name)
            total = space + air + ground
            self.assertEqual((scale.space_nodes, scale.air_nodes, scale.ground_nodes), (space, air, ground))
            self.assertEqual((space / total, air / total, ground / total), (0.4, 0.2, 0.4))
            self.assertEqual(flow_count, 4 * total)

    def test_dwcg_runtime_controls(self):
        plan = load_plan(FORMAL_CONFIG)
        expected_caps = {"S20": 1040, "S30": 1560, "S40": 2080, "S50": 2600}
        for algorithm in plan.scenario.algorithms:
            self.assertEqual(algorithm.threads, 1)
            self.assertIsNone(algorithm.hard_time_limit_seconds)
            if algorithm.algorithm.startswith("dwcg"):
                self.assertEqual(algorithm.max_cg_iterations, 12)
                self.assertEqual(algorithm.pricing_worker_count, 1)
                self.assertEqual(algorithm.mip_gap, 0.0)
                self.assertIsNone(algorithm.final_rmp_reserve_ratio)
                self.assertEqual(algorithm.final_rmp_reserve_seconds, 300.0)
                self.assertEqual(algorithm.final_rmp_time_cap_seconds, 300.0)
                self.assertEqual(algorithm.max_exact_pricing_calls_by_scale, expected_caps)
        self.assertFalse(plan.scenario.output_settings["parallel_pricing_enabled"])

    def test_pilot_matrix_is_non_formal_18_runs(self):
        plan = load_plan(PILOT_CONFIG)
        self.assertEqual(plan.name, "scaling_runtime_pilot_v3")
        self.assertTrue(plan.integration_only)
        self.assertFalse(plan.include_in_core_ranking)
        self.assertEqual(plan.plan_kind, "scaling_runtime_pilot")
        self.assertEqual(plan.scenario.output_settings["recommended_output_dir"], "outputs/scaling_runtime_pilot_v3")
        self.assertEqual(
            plan.scenario.scenario_pairs,
            (("S30", "Medium"), ("S50", "Medium"), ("S50", "High")),
        )
        self.assertEqual(plan.scenario.formal_seeds, (0, 1, 2))
        self.assertEqual([algorithm.algorithm for algorithm in plan.scenario.algorithms], ["dwcg_pure", "dwcg_saginfe"])
        self.assertEqual(len(formal_matrix(plan)), 18)
        self.assertEqual(len(formal_matrix(plan, scales=["S50"], loads=["High"], seeds=[0], algorithms=["dwcg_pure"])), 1)

    def test_cross_load_pairing_preserves_base_instance_except_demand(self):
        plan = load_plan(FORMAL_CONFIG)
        low = next(load for load in plan.scenario.loads if load.label == "Low")
        medium = next(load for load in plan.scenario.loads if load.label == "Medium")
        high = next(load for load in plan.scenario.loads if load.label == "High")
        instances = [
            build_formal_instance(plan, "S30", 0, load)
            for load in (low, medium, high)
        ]
        self.assertEqual({topology_fingerprint(instance) for instance in instances}, {topology_fingerprint(instances[0])})
        self.assertEqual({base_flow_fingerprint(instance) for instance in instances}, {base_flow_fingerprint(instances[0])})
        demand_vectors = [
            [flow.flow_demand for flow in instance.flows]
            for instance in instances
        ]
        self.assertNotEqual(demand_vectors[0], demand_vectors[1])
        self.assertNotEqual(demand_vectors[1], demand_vectors[2])

    def test_algorithm_variants_share_scenario_instance_keys(self):
        plan = load_plan(FORMAL_CONFIG)
        rows = formal_matrix(plan, scales=["S20"], loads=["Medium"], seeds=[0])
        self.assertEqual([row[3].algorithm for row in rows], ["saginfe", "dwcg_pure", "dwcg_saginfe"])
        self.assertEqual({(scale, seed, load.label) for scale, seed, load, _ in rows}, {("S20", 0, "Medium")})

    def test_cg_budget_calibration_matrix_and_filters(self):
        plan = load_plan(CG_CALIBRATION_CONFIG)
        self.assertEqual(plan.name, "scaling_cg_budget_calibration_v3")
        self.assertTrue(plan.integration_only)
        self.assertEqual(plan.scenario.scales, ("S30",))
        self.assertEqual(plan.scenario.formal_seeds, (0,))
        self.assertEqual([(load.label, load.demand_multiplier) for load in plan.scenario.loads], [("Medium", 0.6)])
        rows = formal_matrix(plan)
        self.assertEqual(len(rows), 3)
        self.assertEqual([row[3].variant_id for row in rows], ["CG8", "CG12", "CG16"])
        self.assertEqual([row[3].max_cg_iterations for row in rows], [8, 12, 16])
        self.assertEqual([row[3].max_exact_pricing_calls_by_scale["S30"] for row in rows], [1080, 1560, 2040])
        self.assertEqual(len(formal_matrix(plan, variants=["CG12"])), 1)
        self.assertEqual(formal_matrix(plan, variants=["CG12"])[0][3].max_cg_iterations, 12)
        self.assertEqual({(scale, seed, load.label) for scale, seed, load, _ in rows}, {("S30", 0, "Medium")})

    def test_formal_and_pilot_runtime_controls(self):
        for path in (FORMAL_CONFIG, PILOT_CONFIG, CANDIDATE_BUDGET_CONFIG, FINAL_RMP_300_CONFIG):
            plan = load_plan(path)
            dwcg = [algorithm for algorithm in plan.scenario.algorithms if algorithm.algorithm.startswith("dwcg")]
            self.assertTrue(dwcg)
            self.assertEqual({algorithm.hard_time_limit_seconds for algorithm in dwcg}, {None})
            self.assertEqual({algorithm.threads for algorithm in dwcg}, {1})
            self.assertEqual({algorithm.pricing_worker_count for algorithm in dwcg}, {1})
            if path == FORMAL_CONFIG:
                self.assertEqual({algorithm.final_rmp_reserve_seconds for algorithm in dwcg}, {300.0})
            else:
                self.assertEqual({algorithm.final_rmp_reserve_ratio for algorithm in dwcg}, {0.1})

    def test_end_to_end_budget_and_runtime_metadata_are_plumbed_statically(self):
        runner_source = (ROOT / "formal_runner.py").read_text(encoding="utf-8-sig")
        solver_source = (ROOT / "main_solver.py").read_text(encoding="utf-8-sig")
        self.assertIn('hard_time_limit_scope: str = "algorithm_core_runtime_seconds"', solver_source)
        self.assertIn('overall_deadline = start + configured_hard_limit', runner_source)
        self.assertIn("overall_start_time=start", runner_source)
        self.assertIn('hard_time_limit_scope="end_to_end_algorithm_runtime_seconds"', runner_source)
        for field in (
            "pre_solver_setup_runtime_seconds",
            "cg_termination_trigger",
            "run_hard_wall_clock_limit_reached",
            "final_integer_rmp_status",
            "restricted_pool_integer_optimal",
            "convergence_certification",
            "configured_final_rmp_time_cap_seconds",
            "configured_final_rmp_reserve_seconds",
            "actual_final_rmp_time_limit_seconds",
            "rmp_lp_runtime_seconds",
            "final_rmp_runtime_seconds",
            "final_rmp_remaining_seconds_at_start",
            "final_rmp_first_incumbent_runtime_seconds",
            "final_rmp_best_incumbent_runtime_seconds",
            "final_rmp_final_incumbent_runtime_seconds",
            "final_rmp_incumbent_events",
            "final_rmp_mip_gap",
            "final_rmp_best_bound",
        ):
            self.assertIn(field, runner_source)
        self.assertIn("final_rmp_time_cap_seconds=algorithm_config.final_rmp_time_cap_seconds", runner_source)
        self.assertIn("final_rmp_reserve_seconds=algorithm_config.final_rmp_reserve_seconds", runner_source)
        self.assertIn("actual_final_time_limit = min(final_remaining, final_rmp_time_cap_seconds)", solver_source)
        self.assertIn("time_limit=actual_final_time_limit", solver_source)
        self.assertIn("final_reserved = final_rmp_reserve_seconds", solver_source)
        self.assertIn("Cannot resume output directory with a different formal plan fingerprint.", runner_source)
        self.assertIn("runtime_accounting_valid", runner_source)

    def test_formal_saginfe_solution_cache_is_disabled_for_timed_algorithms(self):
        runner_source = (ROOT / "formal_runner.py").read_text(encoding="utf-8-sig")
        self.assertIn("allow_solution_cache: bool = False", runner_source)
        self.assertIn("cache_reused\": False", runner_source)
        self.assertEqual(runner_source.count("allow_solution_cache=False"), 3)
        self.assertIn("instance_cache: Dict[Tuple[str, int, str], SaginInstance] = {}", runner_source)
        self.assertIn("instance_cache[instance_key] = build_formal_instance", runner_source)

    def test_dwcg_saginfe_initialization_is_inside_own_deadline_statically(self):
        runner_source = (ROOT / "formal_runner.py").read_text(encoding="utf-8-sig")
        self.assertLess(
            runner_source.index("start = time.perf_counter()"),
            runner_source.index("initialization = _get_or_run_saginfe_initialization"),
        )
        self.assertLess(
            runner_source.index("initialization = _get_or_run_saginfe_initialization"),
            runner_source.index("overall_deadline = start + configured_hard_limit"),
        )
        self.assertIn("initialization_generation_runtime_seconds", runner_source)
        self.assertIn("pre_solver_setup_runtime_seconds", runner_source)
        self.assertIn("runtime_accounting_valid", runner_source)

    def test_candidate_budget_validation_matrix_is_exactly_four_non_formal_runs(self):
        plan = load_plan(CANDIDATE_BUDGET_CONFIG)
        self.assertEqual(plan.name, "scaling_candidate_budget_validation_v3")
        self.assertEqual(plan.plan_kind, "scaling_runtime_pilot")
        self.assertTrue(plan.integration_only)
        self.assertFalse(plan.include_in_core_ranking)
        self.assertEqual(plan.scenario.output_settings["recommended_output_dir"], "outputs/scaling_candidate_budget_validation_v3")
        self.assertTrue(plan.scenario.output_settings["candidate_budget_validation_not_formal_results"])
        rows = formal_matrix(plan)
        self.assertEqual(len(rows), 4)
        self.assertEqual(
            [(scale, load.label, seed, algorithm.algorithm) for scale, seed, load, algorithm in rows],
            [
                ("S30", "Medium", 1, "dwcg_saginfe"),
                ("S30", "Medium", 2, "dwcg_saginfe"),
                ("S50", "High", 0, "dwcg_saginfe"),
                ("S50", "High", 0, "dwcg_pure"),
            ],
        )
        self.assertEqual(len(formal_matrix(plan, scales=["S30"], loads=["Medium"], seeds=[1], algorithms=["dwcg_saginfe"])), 1)
        self.assertEqual(len(formal_matrix(plan, scales=["S50"], loads=["High"], seeds=[0], algorithms=["dwcg_pure"])), 1)

    def test_candidate_budget_validation_controls(self):
        plan = load_plan(CANDIDATE_BUDGET_CONFIG)
        expected_caps = {"S30": 1560, "S50": 2600}
        self.assertEqual(plan.scenario.scale_flow_counts, {"S30": 120, "S50": 200})
        self.assertEqual(plan.scenario.time_slots, 8)
        for algorithm in plan.scenario.algorithms:
            self.assertEqual(algorithm.max_cg_iterations, 12)
            self.assertEqual(algorithm.max_exact_pricing_calls_by_scale, expected_caps)
            self.assertEqual(algorithm.final_rmp_reserve_ratio, 0.1)
            self.assertEqual(algorithm.final_rmp_time_cap_seconds, 180.0)
            self.assertIsNone(algorithm.hard_time_limit_seconds)
            self.assertEqual(algorithm.threads, 1)
            self.assertEqual(algorithm.pricing_worker_count, 1)
            self.assertEqual(algorithm.mip_gap, 0.0)
        self.assertEqual(
            {algorithm.use_saginfe_initialization for algorithm in plan.scenario.algorithms},
            {False, True},
        )

    def test_final_rmp_300_validation_is_exactly_one_non_formal_run(self):
        plan = load_plan(FINAL_RMP_300_CONFIG)
        self.assertEqual(plan.name, "scaling_final_rmp_300_validation_v3")
        self.assertEqual(plan.plan_kind, "scaling_runtime_pilot")
        self.assertTrue(plan.integration_only)
        self.assertFalse(plan.include_in_core_ranking)
        self.assertEqual(plan.scenario.output_settings["recommended_output_dir"], "outputs/scaling_final_rmp_300_validation_v3")
        self.assertTrue(plan.scenario.output_settings["final_rmp_300_validation_not_formal_results"])
        rows = formal_matrix(plan)
        self.assertEqual(len(rows), 1)
        scale, seed, load, algorithm = rows[0]
        self.assertEqual((scale, load.label, seed, algorithm.algorithm), ("S50", "High", 0, "dwcg_pure"))
        self.assertEqual(plan.scenario.scale_flow_counts, {"S50": 200})
        self.assertEqual(plan.scenario.time_slots, 8)
        self.assertEqual(load.demand_multiplier, 0.8)
        self.assertEqual(algorithm.max_cg_iterations, 12)
        self.assertEqual(algorithm.max_exact_pricing_calls_by_scale, {"S50": 2600})
        self.assertIsNone(algorithm.hard_time_limit_seconds)
        self.assertEqual(algorithm.final_rmp_reserve_ratio, 0.1)
        self.assertEqual(algorithm.final_rmp_time_cap_seconds, 300.0)
        self.assertEqual(algorithm.threads, 1)
        self.assertEqual(algorithm.pricing_worker_count, 1)
        self.assertEqual(algorithm.mip_gap, 0.0)
        self.assertFalse(algorithm.use_saginfe_initialization)
        self.assertEqual(algorithm.resolved_pricing_strategy(), "hybrid")

    def test_final_rmp_300_matches_final_rmp_180_scientific_identity(self):
        previous = load_plan(CANDIDATE_BUDGET_CONFIG)
        current = load_plan(FINAL_RMP_300_CONFIG)
        previous_row = formal_matrix(
            previous,
            scales=["S50"],
            loads=["High"],
            seeds=[0],
            algorithms=["dwcg_pure"],
        )[0]
        current_row = formal_matrix(current)[0]
        self.assertEqual(
            (previous_row[0], previous_row[1], previous_row[2].label, previous_row[3].algorithm),
            (current_row[0], current_row[1], current_row[2].label, current_row[3].algorithm),
        )
        self.assertEqual(previous_row[2].demand_multiplier, current_row[2].demand_multiplier)
        previous_algorithm = previous_row[3]
        current_algorithm = current_row[3]
        for field in (
            "threads",
            "mip_gap",
            "use_saginfe_initialization",
            "deterministic_mode",
            "pricing_worker_count",
            "max_cg_iterations",
            "hard_time_limit_seconds",
            "final_rmp_reserve_ratio",
        ):
            self.assertEqual(getattr(previous_algorithm, field), getattr(current_algorithm, field))
        self.assertEqual(previous_algorithm.resolved_pricing_strategy(), current_algorithm.resolved_pricing_strategy())
        self.assertEqual(previous_algorithm.max_exact_pricing_calls_by_scale["S50"], current_algorithm.max_exact_pricing_calls_by_scale["S50"])
        self.assertEqual(previous_algorithm.final_rmp_time_cap_seconds, 180.0)
        self.assertEqual(current_algorithm.final_rmp_time_cap_seconds, 300.0)

        previous_instance = build_formal_instance(previous, previous_row[0], previous_row[1], previous_row[2])
        current_instance = build_formal_instance(current, current_row[0], current_row[1], current_row[2])
        self.assertEqual(topology_fingerprint(previous_instance), topology_fingerprint(current_instance))
        self.assertEqual(base_flow_fingerprint(previous_instance), base_flow_fingerprint(current_instance))

    def test_preflight_a_is_exact_master_subset(self):
        master = load_plan(FORMAL_CONFIG)
        preflight = load_plan(PREFLIGHT_A_CONFIG)
        self.assertEqual(preflight.name, "scale_load_master_preflight_s20_low_v3")
        self.assertTrue(preflight.integration_only)
        self.assertFalse(preflight.include_in_core_ranking)
        self.assertEqual(preflight.scenario.output_settings["recommended_output_dir"], "outputs/scale_load_master_preflight_s20_low_v3")
        rows = formal_matrix(preflight)
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            [(scale, load.label, seed, algorithm.algorithm) for scale, seed, load, algorithm in rows],
            [
                ("S20", "Low", 0, "saginfe"),
                ("S20", "Low", 0, "dwcg_pure"),
                ("S20", "Low", 0, "dwcg_saginfe"),
            ],
        )
        self.assertEqual({(scale, seed, load.label) for scale, seed, load, _ in rows}, {("S20", 0, "Low")})
        master_rows = formal_matrix(master, scales=["S20"], loads=["Low"], seeds=[0])
        self.assertEqual(
            [(scale, load.label, seed, algorithm.algorithm) for scale, seed, load, algorithm in master_rows],
            [(scale, load.label, seed, algorithm.algorithm) for scale, seed, load, algorithm in rows],
        )
        for master_row, preflight_row in zip(master_rows, rows):
            self.assert_master_policy_equal(master_row[3], preflight_row[3])

    def test_preflight_b_is_exact_master_subset(self):
        master = load_plan(FORMAL_CONFIG)
        preflight = load_plan(PREFLIGHT_B_CONFIG)
        self.assertEqual(preflight.name, "scale_load_master_preflight_s50_high_v3")
        self.assertTrue(preflight.integration_only)
        self.assertFalse(preflight.include_in_core_ranking)
        self.assertEqual(preflight.scenario.output_settings["recommended_output_dir"], "outputs/scale_load_master_preflight_s50_high_v3")
        rows = formal_matrix(preflight)
        self.assertEqual(len(rows), 1)
        scale, seed, load, algorithm = rows[0]
        self.assertEqual((scale, load.label, seed, algorithm.algorithm), ("S50", "High", 0, "dwcg_saginfe"))
        master_row = formal_matrix(master, scales=["S50"], loads=["High"], seeds=[0], algorithms=["dwcg_saginfe"])[0]
        self.assertEqual((master_row[0], master_row[1], master_row[2].label, master_row[3].algorithm), ("S50", 0, "High", "dwcg_saginfe"))
        self.assert_master_policy_equal(master_row[3], algorithm)

    def test_preflight_pairing_and_master_cross_load_pairing(self):
        master = load_plan(FORMAL_CONFIG)
        preflight_a = load_plan(PREFLIGHT_A_CONFIG)
        preflight_rows = formal_matrix(preflight_a)
        instances = [
            build_formal_instance(preflight_a, scale, seed, load)
            for scale, seed, load, _ in preflight_rows
        ]
        self.assertEqual({topology_fingerprint(instance) for instance in instances}, {topology_fingerprint(instances[0])})
        self.assertEqual({base_flow_fingerprint(instance) for instance in instances}, {base_flow_fingerprint(instances[0])})
        low = next(load for load in master.scenario.loads if load.label == "Low")
        medium = next(load for load in master.scenario.loads if load.label == "Medium")
        high = next(load for load in master.scenario.loads if load.label == "High")
        load_instances = [build_formal_instance(master, "S50", 0, load) for load in (low, medium, high)]
        self.assertEqual({topology_fingerprint(instance) for instance in load_instances}, {topology_fingerprint(load_instances[0])})
        self.assertEqual({base_flow_fingerprint(instance) for instance in load_instances}, {base_flow_fingerprint(load_instances[0])})

    def assert_master_policy_equal(self, expected, actual):
        for field in (
            "algorithm",
            "threads",
            "mip_gap",
            "use_saginfe_initialization",
            "deterministic_mode",
            "pricing_worker_count",
            "pricing_strategy",
            "max_cg_iterations",
            "hard_time_limit_seconds",
            "final_rmp_reserve_seconds",
            "final_rmp_time_cap_seconds",
        ):
            self.assertEqual(getattr(expected, field), getattr(actual, field))
        self.assertEqual(expected.max_exact_pricing_calls_by_scale, actual.max_exact_pricing_calls_by_scale)
        self.assertEqual(expected.resolved_pricing_strategy(), actual.resolved_pricing_strategy())


if __name__ == "__main__":
    unittest.main()
