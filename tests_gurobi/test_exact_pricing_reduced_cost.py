import unittest

from objective import ObjectiveSpec, compute_reduced_cost_from_usage
from sub_problem import extract_path_from_sp, solve_sp_detailed
from tests_gurobi.test_utils import gurobi_test, tiny_flow, tiny_network


@gurobi_test
class ExactPricingReducedCostTest(unittest.TestCase):
    def test_sp_objective_matches_recomputed_reduced_cost(self):
        network = tiny_network()
        flow = tiny_flow()
        spec = ObjectiveSpec(network.resource_weight)
        duals = {"flow": {flow.flow_id: 0.0}, "node": {}, "edge": {}}
        obj, y_res, z_res, metadata = solve_sp_detailed(flow, network, duals, spec, threads=1)
        self.assertTrue(metadata["exact_pricing_optimal"])
        path = extract_path_from_sp(flow, network, y_res, z_res, -1, spec)
        recomputed = compute_reduced_cost_from_usage(
            flow, path.path_cost, path.path_node_usage, path.path_edge_usage, duals, spec
        )
        self.assertAlmostEqual(obj, recomputed)


if __name__ == "__main__":
    unittest.main()
