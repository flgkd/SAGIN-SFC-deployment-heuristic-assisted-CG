import pytest

pytest.importorskip(
    "gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt"
)

import unittest
from unittest.mock import patch

from gurobipy import GRB

from main_solver import GBSolver
from tests.test_validation_fixtures import repeated_flow, tiny_network, valid_repeated_path


class _Var:
    def __init__(self, value):
        self.X = value


class _Model:
    Status = GRB.OPTIMAL
    SolCount = 1
    Runtime = 0.0
    MIPGap = 0.0
    ObjBound = 0.0


def _fake_solve_rmp(paths, flows, network, objective_spec=None, is_integer=False, **kwargs):
    w_vars = {
        path.path_id: _Var(1.0 if not path.path_is_empty else 0.0)
        for path in paths
    }
    duals = None if is_integer else {
        "flow": {flow.flow_id: 0.0 for flow in flows},
        "node": {},
        "edge": {},
    }
    return _Model(), w_vars, duals


def _positive_metadata(obj=1.0):
    return {
        "exact_pricing_optimal": True,
        "hit_time_limit": False,
        "failed": False,
        "positive_reduced_cost_found": True,
        "objective": obj,
    }


def _no_positive_metadata():
    return {
        "exact_pricing_optimal": True,
        "hit_time_limit": False,
        "failed": False,
        "positive_reduced_cost_found": False,
        "objective": None,
    }


class PricingNoProgressControllerTest(unittest.TestCase):
    def _solver(self):
        network = tiny_network()
        flow = repeated_flow()
        return network, flow, GBSolver(network, [flow])

    def test_positive_column_inserted_then_later_no_positive_certifies(self):
        network, flow, solver = self._solver()
        calls = {"count": 0}

        def pricing(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return 1.0, {"candidate": 1.0}, {"candidate": 1.0}, _positive_metadata()
            return None, None, None, _no_positive_metadata()

        def extract(flow_obj, network_obj, y_res, z_res, path_id, objective_spec=None):
            return valid_repeated_path(network, flow, path_id)

        with patch("main_solver.solve_rmp", _fake_solve_rmp), patch("main_solver.extract_path_from_sp", extract):
            solver.solve(use_parallel=False, sp_strategy="exact", exact_sp_func=pricing, max_iterations=5)

        self.assertGreaterEqual(solver.stats["iterations"], 2)
        self.assertEqual(solver.stats["columns_added_total"], 1)
        self.assertTrue(solver.stats["pricing_convergence_certified"])

    def test_complete_no_positive_iteration_certifies(self):
        _, _, solver = self._solver()

        def pricing(*args, **kwargs):
            return None, None, None, _no_positive_metadata()

        with patch("main_solver.solve_rmp", _fake_solve_rmp):
            solver.solve(use_parallel=False, sp_strategy="exact", exact_sp_func=pricing, max_iterations=5)

        self.assertEqual(solver.stats["iterations"], 1)
        self.assertTrue(solver.stats["pricing_convergence_certified"])

    def test_duplicate_positive_with_unchanged_rmp_terminates_non_certified(self):
        network, flow, solver = self._solver()
        path = valid_repeated_path(network, flow)

        def pricing(*args, **kwargs):
            return 1.0, {"candidate": 1.0}, {"candidate": 1.0}, _positive_metadata()

        def extract(flow_obj, network_obj, y_res, z_res, path_id, objective_spec=None):
            return valid_repeated_path(network, flow, path_id)

        with patch("main_solver.solve_rmp", _fake_solve_rmp), patch("main_solver.extract_path_from_sp", extract):
            solver.solve(
                use_parallel=False,
                sp_strategy="exact",
                exact_sp_func=pricing,
                custom_initial_paths=[{
                    "flow_id": flow.flow_id,
                    "stage_placement": path.path_stage_placement,
                    "segment_routing": path.path_segment_routing,
                    "physical_cost": path.path_cost,
                }],
                max_iterations=5,
            )

        self.assertEqual(solver.stats["iterations"], 1)
        self.assertFalse(solver.stats["pricing_convergence_certified"])
        self.assertEqual(solver.stats["cg_status"], "DUPLICATE_POSITIVE_COLUMN_INCONSISTENCY")
        self.assertEqual(solver.stats["no_progress_termination_count"], 1)

    def test_extraction_failed_positive_with_unchanged_rmp_terminates_non_certified(self):
        _, _, solver = self._solver()

        def pricing(*args, **kwargs):
            return 1.0, {"candidate": 1.0}, {"candidate": 1.0}, _positive_metadata()

        def extract(*args, **kwargs):
            raise ValueError("synthetic extraction failure")

        with patch("main_solver.solve_rmp", _fake_solve_rmp), patch("main_solver.extract_path_from_sp", extract):
            solver.solve(use_parallel=False, sp_strategy="exact", exact_sp_func=pricing, max_iterations=5)

        self.assertEqual(solver.stats["iterations"], 1)
        self.assertFalse(solver.stats["pricing_convergence_certified"])
        self.assertEqual(solver.stats["cg_status"], "POSITIVE_COLUMN_EXTRACTION_FAILED")
        self.assertEqual(solver.stats["no_progress_termination_count"], 1)

    def test_pfe_success_is_heuristic_in_history_counters_and_certification(self):
        from formal_runner import _primary_pricing_phases

        network, flow, solver = self._solver()
        exact_calls = []

        def heuristic(*args, **kwargs):
            # Even optimistic callback metadata must not certify a heuristic phase.
            return 1.0, {"candidate": 1.0}, {"candidate": 1.0}, _positive_metadata()

        def exact(*args, **kwargs):
            exact_calls.append(1)
            return None, None, None, _no_positive_metadata()

        def extract(flow_obj, network_obj, y_res, z_res, path_id, objective_spec=None):
            return valid_repeated_path(network, flow, path_id)

        with patch("main_solver.solve_rmp", _fake_solve_rmp), patch("main_solver.extract_path_from_sp", extract):
            solver.solve(use_parallel=False, sp_strategy="heuristic_then_exact",
                         heuristic_sp_func=heuristic, exact_sp_func=exact, max_iterations=1)

        self.assertEqual(exact_calls, [])
        self.assertEqual(solver.stats["columns_added_by_phase"], {"PFE heuristic": 1})
        history = solver.stats["pricing_phase_history"]
        self.assertEqual([p["phase"] for p in history], ["PFE heuristic"])
        self.assertEqual(history[0]["columns_added"], 1)
        self.assertEqual(_primary_pricing_phases(history), [])
        self.assertEqual(solver.stats["exact_pricing_budget_used"], 0)
        self.assertEqual(solver.stats["heuristic_sp_calls"], 1)
        self.assertEqual(solver.stats["exact_sp_calls"], 0)
        self.assertEqual(solver.stats["exact_pricing_optimal_calls"], 0)
        self.assertEqual(solver.stats["strict_exact_positive_column_count"], 0)
        self.assertEqual(solver.stats["early_stop_positive_column_count"], 0)
        self.assertEqual(solver.stats["pricing_iteration_stats"], [])
        self.assertFalse(solver.stats["pricing_convergence_certified"])

    def test_pfe_miss_falls_back_and_only_exact_phase_can_certify(self):
        from formal_runner import _primary_pricing_phases

        for certified in (False, True):
            with self.subTest(exact_certified=certified):
                _, _, solver = self._solver()
                calls = []

                def heuristic(*args, **kwargs):
                    calls.append("heuristic")
                    return None, None, None

                def exact(*args, **kwargs):
                    calls.append("exact")
                    metadata = _no_positive_metadata()
                    metadata["exact_pricing_optimal"] = certified
                    metadata["hit_time_limit"] = not certified
                    return None, None, None, metadata

                with patch("main_solver.solve_rmp", _fake_solve_rmp):
                    solver.solve(use_parallel=False, sp_strategy="heuristic_then_exact",
                                 heuristic_sp_func=heuristic, exact_sp_func=exact, max_iterations=2)

                rounds = 1 if certified else 2
                self.assertEqual(calls, ["heuristic", "exact"] * rounds)
                history = solver.stats["pricing_phase_history"]
                self.assertEqual([p["phase"] for p in history],
                                 ["PFE heuristic", "Exact Gurobi fallback"] * rounds)
                self.assertEqual(solver.stats["columns_added_by_phase"],
                                 {"PFE heuristic": 0, "Exact Gurobi fallback": 0})
                self.assertEqual(_primary_pricing_phases(history), history[1::2])
                self.assertEqual(solver.stats["exact_pricing_budget_used"], rounds)
                self.assertEqual(solver.stats["heuristic_miss_count"], rounds)
                self.assertEqual(len(solver.stats["pricing_iteration_stats"]), rounds)
                self.assertEqual(solver.stats["pricing_convergence_certified"], certified)
                self.assertEqual(solver.stats["heuristic_sp_calls"], rounds)
                self.assertEqual(solver.stats["exact_sp_calls"], rounds)
                self.assertEqual(solver.stats["exact_pricing_time_limit_calls"], 0 if certified else rounds)


if __name__ == "__main__":
    unittest.main()
