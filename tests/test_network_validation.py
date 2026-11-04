import math
import unittest

from tests.test_validation_fixtures import tiny_network
from validation import validate_network


class NetworkValidationTest(unittest.TestCase):
    def test_valid_network_passes(self):
        validate_network(tiny_network())

    def test_bad_resource_weight_fails(self):
        network = tiny_network()
        network.resource_weight = 1.5
        with self.assertRaisesRegex(ValueError, "resource_weight"):
            validate_network(network)

    def test_storage_capacity_must_be_infinite(self):
        network = tiny_network()
        edge = ((0, 0), (0, 1))
        network.time_edge_capacity[edge] = 1.0
        with self.assertRaisesRegex(ValueError, "Storage edge"):
            validate_network(network)

    def test_comm_capacity_must_be_finite(self):
        network = tiny_network()
        edge = ((0, 0), (1, 0))
        network.time_edge_capacity[edge] = math.inf
        with self.assertRaisesRegex(ValueError, "Communication edge"):
            validate_network(network)


if __name__ == "__main__":
    unittest.main()
