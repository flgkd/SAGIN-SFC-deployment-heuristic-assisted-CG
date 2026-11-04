import unittest

from ILP_solver import solve_ilp
from objective import ObjectiveSpec
from tests_gurobi.test_utils import gurobi_test, tiny_flow, tiny_network


@gurobi_test
class DirectIlpTinyTest(unittest.TestCase):
    def test_one_feasible_flow_is_accepted_and_objective_components_match(self):
        network = tiny_network()
        flow = tiny_flow()
        result = solve_ilp(network, [flow], ObjectiveSpec(network.resource_weight), threads=1)
        self.assertEqual(result.accepted_count, 1)
        self.assertAlmostEqual(
            result.scalarized_objective,
            2.0 * result.accepted_count - result.normalized_physical_cost,
        )
        self.assertTrue(result.result.optimality.global_original_integer_optimal)


if __name__ == "__main__":
    unittest.main()
