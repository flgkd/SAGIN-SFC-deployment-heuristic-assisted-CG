import unittest

from data_model import Network
from objective import compute_capacity_denominators


class CapacityDenominatorsTest(unittest.TestCase):
    def test_zero_processing_capacity_raises(self):
        network = Network()
        network.set_nodes(2)
        network.set_time_slots(1)
        network.add_time_node((0, 0), 0.0)
        network.add_time_node((1, 0), 0.0)
        network.add_comm_time_edge((0, 0), (1, 0), 1.0)
        with self.assertRaises(ValueError):
            compute_capacity_denominators(network)

    def test_zero_communication_capacity_raises(self):
        network = Network()
        network.set_nodes(1)
        network.set_time_slots(1)
        network.add_time_node((0, 0), 1.0)
        with self.assertRaises(ValueError):
            compute_capacity_denominators(network)


if __name__ == "__main__":
    unittest.main()

