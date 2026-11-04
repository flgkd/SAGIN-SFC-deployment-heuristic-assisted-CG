import pytest

pytest.importorskip(
    "gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt"
)

import unittest

from main_solver import GBSolver
from tests.test_validation_fixtures import repeated_flow, tiny_network, valid_repeated_path


class InvalidColumnRejectionTest(unittest.TestCase):
    def test_custom_initial_path_cost_mismatch_is_tracked_and_rejected(self):
        network = tiny_network()
        flow = repeated_flow()
        path = valid_repeated_path(network, flow)
        solver = GBSolver(network, [flow])
        added = solver._add_custom_initial_paths([
            {
                "flow_id": flow.flow_id,
                "stage_placement": dict(path.path_stage_placement),
                "segment_routing": {idx: list(edges) for idx, edges in path.path_segment_routing.items()},
                "physical_cost": path.path_cost + 10.0,
            }
        ])
        self.assertEqual(added, 0)
        self.assertEqual(solver.stats["cost_mismatch_count"], 1)
        self.assertEqual(solver.stats["invalid_column_count"], 1)

    def test_legacy_vnf_placement_input_is_rejected(self):
        network = tiny_network()
        flow = repeated_flow()
        solver = GBSolver(network, [flow])
        with self.assertRaisesRegex(ValueError, "stage_placement"):
            solver._add_custom_initial_paths([
                {"flow_id": flow.flow_id, "vnf_placement": {"A": (1, 0)}, "routing": []}
            ])


if __name__ == "__main__":
    unittest.main()
