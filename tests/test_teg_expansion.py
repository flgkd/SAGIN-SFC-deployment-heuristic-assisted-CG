import math
import unittest

from config_schema import TopologyConfig, get_candidate_scale
from sagin_generator import generate_sagin_topology


class TegExpansionTest(unittest.TestCase):
    def test_time_nodes_edges_and_storage_semantics(self):
        config = TopologyConfig(get_candidate_scale("S20"), 3, 18)
        result = generate_sagin_topology(config)
        network = result.network
        for node in network.nodes:
            for time in network.time_slots:
                self.assertIn((node, time), network.time_nodes)

        comm_edges = [edge for edge in network.time_edges if edge[0][0] != edge[1][0]]
        self.assertTrue(all(edge[0][1] == edge[1][1] for edge in comm_edges))
        self.assertFalse(any(edge[1][1] < edge[0][1] for edge in network.time_edges))
        for node in network.nodes:
            for time in range(config.time_slots - 1):
                self.assertEqual(network.time_edge_capacity[((node, time), (node, time + 1))], math.inf)

    def test_active_and_inactive_communication_edges(self):
        result = generate_sagin_topology(TopologyConfig(get_candidate_scale("S20"), 3, 19, dynamic_topology=True))
        base_edges = {link.edge for link in result.physical_links}
        network = result.network
        for snapshot in result.metadata["snapshots"]:
            time = snapshot["time_slot"]
            active = {tuple(edge) for edge in snapshot["active_edges"]}
            for u, v in active:
                self.assertIn(((u, time), (v, time)), network.time_edges)
                self.assertIn(((v, time), (u, time)), network.time_edges)
            for u, v in base_edges - active:
                self.assertNotIn(((u, time), (v, time)), network.time_edges)
                self.assertNotIn(((v, time), (u, time)), network.time_edges)


if __name__ == "__main__":
    unittest.main()
