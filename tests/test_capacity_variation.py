import math
import unittest

from config_schema import TopologyConfig, candidate_dynamics_profile, get_candidate_scale
from dataclasses import replace
from sagin_generator import generate_sagin_topology


class CapacityVariationTest(unittest.TestCase):
    def test_zero_ratio_keeps_node_capacity_constant_over_time(self):
        dynamics = replace(candidate_dynamics_profile(), node_capacity_variation_ratio=0.0, link_capacity_variation_ratio=0.0)
        result = generate_sagin_topology(TopologyConfig(get_candidate_scale("S20"), 3, 12, dynamics_profile=dynamics))
        network = result.network
        for node in network.nodes:
            values = [network.time_node_capacity[(node, t)] for t in network.time_slots]
            self.assertEqual(len(set(values)), 1)

    def test_variation_bounds_and_opposite_direction_capacity(self):
        config = TopologyConfig(get_candidate_scale("S20"), 3, 13)
        result = generate_sagin_topology(config)
        network = result.network
        base_node = result.metadata["node_structure"]["base_node_capacities"]
        ratio = config.dynamics_profile.node_capacity_variation_ratio
        for node, base in base_node.items():
            for time in network.time_slots:
                value = network.time_node_capacity[(node, time)]
                self.assertGreaterEqual(value, base * (1 - ratio) - 1e-9)
                self.assertLessEqual(value, base * (1 + ratio) + 1e-9)
        for snapshot in result.metadata["snapshots"]:
            t = snapshot["time_slot"]
            for u, v in [tuple(edge) for edge in snapshot["active_edges"]]:
                self.assertEqual(network.time_edge_capacity[((u, t), (v, t))], network.time_edge_capacity[((v, t), (u, t))])
        self.assertTrue(any(cap == math.inf for cap in network.time_edge_capacity.values()))


if __name__ == "__main__":
    unittest.main()
