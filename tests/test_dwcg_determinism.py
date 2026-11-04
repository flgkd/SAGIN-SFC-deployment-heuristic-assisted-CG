import json
import inspect
import random
from pathlib import Path
import unittest

import pytest

from formal_runner import formal_matrix, derive_formal_algorithm_seed, derive_formal_algorithm_seed_bundle
from formal_schema import formal_plan_from_dict
from deterministic_seeds import (
    GUROBI_SEED_MAX,
    NUMPY_SEED_MAX,
    PYTHON_RANDOM_SEED_MAX,
    gurobi_seed_from_digest,
    normalize_gurobi_seed,
    numpy_seed_from_digest,
    python_random_seed_from_digest,
    seed_bundle,
)


FORMAL_DIR = Path("configs/templates/formal")


class _Flow:
    def __init__(self, flow_id):
        self.flow_id = flow_id


class _Path:
    def __init__(self, signature):
        self.signature = signature

    def build_signature(self):
        return self.signature


class DeterministicDWCGTest(unittest.TestCase):
    def test_stable_seed_derivation_does_not_use_python_hash(self):
        pytest.importorskip("gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt")
        from main_solver import stable_int_seed

        first = stable_int_seed("fingerprint", "dwcg_pure", 999, "High", 3, 42)
        second = stable_int_seed("fingerprint", "dwcg_pure", 999, "High", 3, 42)
        self.assertEqual(first, second)
        self.assertIsInstance(first, int)
        self.assertNotEqual(first, hash(("fingerprint", "dwcg_pure", 999, "High", 3, 42)))

    def test_max_sha_digest_normalizes_to_backend_ranges(self):
        digest = "f" * 64
        self.assertGreater(int(digest, 16), GUROBI_SEED_MAX)
        self.assertGreaterEqual(python_random_seed_from_digest(digest), 0)
        self.assertLessEqual(python_random_seed_from_digest(digest), PYTHON_RANDOM_SEED_MAX)
        self.assertGreaterEqual(numpy_seed_from_digest(digest), 0)
        self.assertLessEqual(numpy_seed_from_digest(digest), NUMPY_SEED_MAX)
        self.assertGreaterEqual(gurobi_seed_from_digest(digest), 0)
        self.assertLessEqual(gurobi_seed_from_digest(digest), GUROBI_SEED_MAX)

    def test_seed_normalization_is_deterministic_and_distinguishes_inputs(self):
        first = seed_bundle("instance", "dwcg_pure", 999, "High")
        repeat = seed_bundle("instance", "dwcg_pure", 999, "High")
        second = seed_bundle("instance", "dwcg_saginfe", 999, "High")
        self.assertEqual(first, repeat)
        self.assertNotEqual(first["seed_digest"], second["seed_digest"])
        self.assertNotEqual(first["gurobi_seed"], second["gurobi_seed"])
        self.assertLessEqual(first["gurobi_seed"], GUROBI_SEED_MAX)

    def test_explicit_oversized_gurobi_seed_is_normalized(self):
        huge = int("f" * 64, 16)
        normalized = normalize_gurobi_seed(huge)
        self.assertGreater(huge, GUROBI_SEED_MAX)
        self.assertLessEqual(normalized, GUROBI_SEED_MAX)

    def test_solve_sp_seed_parameters_are_backward_compatible(self):
        pytest.importorskip("gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt")
        from sub_problem import solve_sp, solve_sp_detailed

        detailed = inspect.signature(solve_sp_detailed)
        wrapper = inspect.signature(solve_sp)
        self.assertIn("gurobi_seed", detailed.parameters)
        self.assertIsNone(detailed.parameters["gurobi_seed"].default)
        self.assertIn("gurobi_seed", wrapper.parameters)
        self.assertIsNone(wrapper.parameters["gurobi_seed"].default)

    def test_supported_kwargs_propagates_explicit_gurobi_seed(self):
        pytest.importorskip("gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt")
        from main_solver import GBSolver

        def fake_sp(flow, network, duals, objective_spec, stop_early, threads=None, gurobi_seed=None):
            return gurobi_seed, None, None, {"failed": False}

        kwargs = GBSolver._supported_kwargs(fake_sp, {"threads": 1, "gurobi_seed": 12345, "ignored": True})
        self.assertEqual(kwargs, {"threads": 1, "gurobi_seed": 12345})

    def test_serial_and_worker_seed_policy_use_same_resolved_seed(self):
        pytest.importorskip("gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt")
        from main_solver import GBSolver, stable_int_seed

        seed = stable_int_seed(7, 3, "Pricing", 42)
        captured = []

        def fake_sp(flow, network, duals, objective_spec, stop_early, gurobi_seed=None):
            captured.append(gurobi_seed)
            return None, None, None, {"failed": False}

        flow = _Flow(42)
        solver = GBSolver(network=object(), flows=[flow], objective_spec=object())
        solver._call_sp(
            fake_sp,
            flow,
            duals={},
            stop_early=False,
            kwargs={"gurobi_seed": seed},
        )
        result = GBSolver._parallel_call_sp(
            fake_sp,
            flow,
            network=object(),
            duals={},
            objective_spec=None,
            stop_early=False,
            kwargs={"gurobi_seed": seed},
        )
        self.assertEqual(captured, [seed, seed])
        self.assertEqual(result[3]["failed"], False)

    def test_formal_seed_bundle_metadata_uses_safe_backend_fields(self):
        class _Instance:
            pass

        instance = _Instance()
        instance.network = None
        instance.flows = []
        # Use a fake by monkeying through configured seed path to avoid building an instance.
        class _Key:
            formal_seed = 999
            load_label = "High"

        class _Cfg:
            algorithm_seed = int("f" * 64, 16)
            algorithm = "dwcg_pure"

        bundle = derive_formal_algorithm_seed_bundle(instance, _Key(), _Cfg())
        self.assertIn("seed_digest", bundle)
        self.assertLessEqual(bundle["python_random_seed"], PYTHON_RANDOM_SEED_MAX)
        self.assertLessEqual(bundle["numpy_seed"], NUMPY_SEED_MAX)
        self.assertLessEqual(bundle["gurobi_seed"], GUROBI_SEED_MAX)
        self.assertLessEqual(derive_formal_algorithm_seed(instance, _Key(), _Cfg()), 2**31 - 1)

    def test_shuffled_pricing_results_sort_to_same_insertion_order(self):
        pytest.importorskip("gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt")
        from main_solver import pricing_result_sort_key, sort_pricing_results_deterministically

        items = [
            (_Flow(2), "Pricing", 0.5, None, None, {"path_signature": ("b",)}),
            (_Flow(1), "Pricing", 0.2, None, None, {"path_signature": ("c",)}),
            (_Flow(1), "Pricing", 0.7, None, None, {"path_signature": ("a",)}),
        ]
        expected = [pricing_result_sort_key(item) for item in sort_pricing_results_deterministically(items)]
        shuffled = list(items)
        random.Random(123).shuffle(shuffled)
        observed = [pricing_result_sort_key(item) for item in sort_pricing_results_deterministically(shuffled)]
        self.assertEqual(observed, expected)

    def test_shuffled_candidate_columns_have_same_pool_fingerprint(self):
        pytest.importorskip("gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt")
        from main_solver import column_pool_fingerprint

        columns = [_Path(("flow", 2, "b")), _Path(("flow", 1, "a")), _Path(("flow", 1, "dup"))]
        shuffled = list(columns)
        random.Random(5).shuffle(shuffled)
        self.assertEqual(column_pool_fingerprint(columns), column_pool_fingerprint(shuffled))

    def test_duplicate_column_fingerprint_is_deterministic(self):
        pytest.importorskip("gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt")
        from main_solver import column_pool_fingerprint

        columns_a = [_Path(("x", 1)), _Path(("x", 1)), _Path(("y", 2))]
        columns_b = [_Path(("y", 2)), _Path(("x", 1)), _Path(("x", 1))]
        self.assertEqual(column_pool_fingerprint(columns_a), column_pool_fingerprint(columns_b))

    def test_paper_facing_formal_plans_enable_deterministic_dwcg(self):
        for name in ("formal_s20_v1.json", "formal_scaling_v1.json", "formal_smoke_v1.json"):
            plan = formal_plan_from_dict(json.loads((FORMAL_DIR / name).read_text(encoding="utf-8")))
            for algorithm in plan.scenario.algorithms:
                if algorithm.algorithm.startswith("dwcg"):
                    self.assertTrue(algorithm.deterministic_mode)
                    self.assertEqual(algorithm.pricing_worker_count, 1)
                    self.assertIsNotNone(algorithm.max_cg_iterations)
                    self.assertIsNotNone(algorithm.max_exact_pricing_calls)

    def test_reproducibility_smoke_matrix_has_four_runs(self):
        plan = formal_plan_from_dict(json.loads((FORMAL_DIR / "formal_dwcg_repro_smoke_v1.json").read_text(encoding="utf-8")))
        self.assertTrue(plan.smoke)
        self.assertEqual(plan.scenario.formal_seeds, (999,))
        self.assertEqual([load.label for load in plan.scenario.loads], ["Medium", "High"])
        self.assertEqual({cfg.algorithm for cfg in plan.scenario.algorithms}, {"dwcg_pure", "dwcg_saginfe"})
        self.assertEqual(len(formal_matrix(plan)), 4)

    def test_checkpoint_metadata_for_interrupted_iteration(self):
        pytest.importorskip("gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt")
        from main_solver import iteration_metadata_invariant_holds

        metadata = {
            "iterations": 4,
            "completed_cg_iteration_count": 3,
            "interrupted_iteration": True,
            "deterministic_work_budget_exhausted": False,
            "hard_wall_clock_limit_reached": True,
            "last_completed_checkpoint_column_pool_size": 120,
        }
        self.assertTrue(metadata["interrupted_iteration"])
        self.assertEqual(metadata["completed_cg_iteration_count"], 3)
        self.assertEqual(metadata["last_completed_checkpoint_column_pool_size"], 120)
        self.assertTrue(iteration_metadata_invariant_holds(metadata))

    def test_completed_iteration_metadata_invariant_without_interrupt(self):
        pytest.importorskip("gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt")
        from main_solver import iteration_metadata_invariant_holds

        metadata = {
            "iterations": 8,
            "completed_cg_iteration_count": 8,
            "interrupted_iteration": False,
        }
        self.assertTrue(iteration_metadata_invariant_holds(metadata))
        metadata["completed_cg_iteration_count"] = 7
        self.assertFalse(iteration_metadata_invariant_holds(metadata))

    def test_work_budget_flags_use_inclusive_boundaries(self):
        pytest.importorskip("gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt")
        from main_solver import GBSolver

        stats = GBSolver._new_stats()
        iteration = 8
        exact_calls = 1600
        max_iterations = 8
        max_exact_calls = 1600
        stats["max_cg_iterations_reached"] = iteration >= max_iterations
        stats["max_exact_pricing_calls_reached"] = exact_calls >= max_exact_calls
        stats["deterministic_work_budget_exhausted"] = (
            stats["max_cg_iterations_reached"] or stats["max_exact_pricing_calls_reached"]
        )
        self.assertTrue(stats["max_cg_iterations_reached"])
        self.assertTrue(stats["max_exact_pricing_calls_reached"])
        self.assertTrue(stats["deterministic_work_budget_exhausted"])

    def test_internal_phase_cutoff_does_not_set_global_hard_limit_flag(self):
        pytest.importorskip("gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt")
        from main_solver import GBSolver, apply_hard_limit_metadata

        stats = GBSolver._new_stats()
        stats["internal_phase_limit_reached"] = True
        stats["internal_phase_name"] = "column_generation"
        stats["internal_phase_limit_seconds"] = 480.0
        apply_hard_limit_metadata(stats, elapsed_seconds=481.0, configured_hard_time_limit_seconds=600.0)
        self.assertTrue(stats["internal_phase_limit_reached"])
        self.assertFalse(stats["hard_wall_clock_limit_reached"])
        self.assertEqual(stats["hard_limit_overrun_seconds"], 0.0)

    def test_crossing_global_hard_limit_records_overrun(self):
        pytest.importorskip("gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt")
        from main_solver import GBSolver, apply_hard_limit_metadata

        stats = GBSolver._new_stats()
        apply_hard_limit_metadata(stats, elapsed_seconds=601.25, configured_hard_time_limit_seconds=600.0)
        self.assertTrue(stats["hard_wall_clock_limit_reached"])
        self.assertAlmostEqual(stats["hard_limit_overrun_seconds"], 1.25)
        self.assertEqual(stats["termination_trigger"], "GLOBAL_HARD_TIME_LIMIT")

    def test_finalization_after_threshold_is_explicit_in_hard_limit_scope(self):
        pytest.importorskip("gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt")
        from main_solver import GBSolver, apply_hard_limit_metadata

        stats = GBSolver._new_stats()
        stats["termination_trigger"] = "MAX_CG_ITERATIONS"
        apply_hard_limit_metadata(stats, elapsed_seconds=601.0, configured_hard_time_limit_seconds=600.0)
        self.assertTrue(stats["hard_wall_clock_limit_reached"])
        self.assertEqual(stats["termination_trigger"], "MAX_CG_ITERATIONS")
        self.assertEqual(stats["elapsed_seconds_in_hard_limit_scope"], 601.0)


if __name__ == "__main__":
    unittest.main()

