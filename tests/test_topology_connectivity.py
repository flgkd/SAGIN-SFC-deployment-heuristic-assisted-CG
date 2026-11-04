import unittest

from config_schema import TopologyConfig, get_candidate_scale
from sagin_generator import _is_connected, generate_sagin_topology


class TopologyConnectivityTest(unittest.TestCase):
    def test_base_and_snapshots_connected(self):
        result = generate_sagin_topology(TopologyConfig(get_candidate_scale("S40"), time_slots=4, seed=7, dynamic_topology=True))
        nodes = result.metadata["node_structure"]["node_ids"]
        base_edges = [link.edge for link in result.physical_links]
        self.assertTrue(_is_connected(nodes, base_edges))
        for snapshot in result.metadata["snapshots"]:
            self.assertTrue(_is_connected(nodes, [tuple(edge) for edge in snapshot["active_edges"]]))

    def test_no_self_loops_or_duplicates(self):
        result = generate_sagin_topology(TopologyConfig(get_candidate_scale("S20"), time_slots=2, seed=8))
        edges = [link.edge for link in result.physical_links]
        self.assertEqual(len(edges), len(set(edges)))
        self.assertTrue(all(u != v for u, v in edges))


if __name__ == "__main__":
    unittest.main()
