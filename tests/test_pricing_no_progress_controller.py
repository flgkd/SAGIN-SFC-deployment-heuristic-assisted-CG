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


if __name__ == "__main__":
    unittest.main()
