import unittest

from tests.test_validation_fixtures import repeated_flow, tiny_network, valid_repeated_path
from validation import validate_solution_capacity


class SolutionCapacityValidationTest(unittest.TestCase):
    def test_aggregate_node_capacity_violation_fails(self):
        network = tiny_network()
        network.time_node_capacity[(1, 0)] = 1.0
        flow1 = repeated_flow(1, demand=1.0)
        flow2 = repeated_flow(2, demand=1.0)
        selected = {
            1: valid_repeated_path(network, flow1, 1),
            2: valid_repeated_path(network, flow2, 2),
        }
        with self.assertRaisesRegex(ValueError, "Node .* capacity"):
            validate_solution_capacity(network, [flow1, flow2], selected)

    def test_aggregate_edge_capacity_violation_fails(self):
        network = tiny_network()
        network.time_edge_capacity[((0, 0), (1, 0))] = 1.0
        flow1 = repeated_flow(1, demand=1.0)
        flow2 = repeated_flow(2, demand=1.0)
        selected = {
            1: valid_repeated_path(network, flow1, 1),
            2: valid_repeated_path(network, flow2, 2),
        }
        with self.assertRaisesRegex(ValueError, "Edge .* capacity"):
            validate_solution_capacity(network, [flow1, flow2], selected)


if __name__ == "__main__":
    unittest.main()
