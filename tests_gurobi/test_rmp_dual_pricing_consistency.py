import unittest

from column_pool import ColumnPool
from objective import (
    ObjectiveSpec,
    accepted_path_value,
    compute_reduced_cost_from_usage,
)
from restricted_master_problem import solve_rmp
from sub_problem import solve_sp_detailed
from tests_gurobi.test_utils import gurobi_test, tiny_flow, tiny_network, tiny_path


@gurobi_test
class RmpDualPricingConsistencyTest(unittest.TestCase):
    def test_existing_selected_path_has_zero_reduced_cost_under_rmp_duals(self):
        network = tiny_network()
        flow = tiny_flow()
        spec = ObjectiveSpec(network.resource_weight)
        pool = ColumnPool()
        pool.initialize_empty_paths([flow])
        path = tiny_path(network, flow, pool.generate_path_id())
        pool.add_path(path)

        result = solve_rmp(pool.get_all_paths(), [flow], network, spec, is_integer=False, threads=1)
        model, w_vars, duals = result

        self.assertEqual(model.ModelSense, -1)
        self.assertAlmostEqual(model.ObjVal, accepted_path_value(path.path_cost, spec))
        self.assertAlmostEqual(w_vars[0].X, 0.0)
        self.assertAlmostEqual(w_vars[path.path_id].X, 1.0)
        self.assertIn(flow.flow_id, duals["flow"])
        self.assertAlmostEqual(duals["flow"][flow.flow_id], accepted_path_value(path.path_cost, spec))

        nonzero_capacity_duals = {
            "node": {key: value for key, value in duals["node"].items() if abs(value) > 1e-8},
            "edge": {key: value for key, value in duals["edge"].items() if abs(value) > 1e-8},
        }
        self.assertEqual(nonzero_capacity_duals, {"node": {}, "edge": {}})

        reduced_cost = compute_reduced_cost_from_usage(
            flow,
            path.path_cost,
            path.path_node_usage,
            path.path_edge_usage,
            duals,
            spec,
        )
        self.assertLessEqual(abs(reduced_cost), 1e-6)

        sp_obj, _, _, metadata = solve_sp_detailed(flow, network, duals, spec, threads=1)
        self.assertTrue(metadata["exact_pricing_optimal"])
        self.assertFalse(metadata["positive_reduced_cost_found"])
        self.assertIsNone(sp_obj)


if __name__ == "__main__":
    unittest.main()
