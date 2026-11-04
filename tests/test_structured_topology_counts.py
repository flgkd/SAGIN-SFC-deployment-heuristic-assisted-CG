import unittest

from config_schema import TopologyConfig, get_candidate_scale
from sagin_generator import generate_sagin_topology


class StructuredTopologyCountsTest(unittest.TestCase):
    def test_scale_counts_and_sparse_growth(self):
        previous_links = 0
        for name in ("S20", "S30", "S40", "S50"):
            config = TopologyConfig(get_candidate_scale(name), time_slots=3, seed=11)
            result = generate_sagin_topology(config)
            meta = result.metadata
            scale = config.scale
            self.assertEqual(len(result.network.nodes), scale.space_nodes + scale.air_nodes + scale.ground_nodes)
            self.assertEqual(meta["node_structure"]["space_nodes"], scale.space_nodes)
            self.assertEqual(meta["node_structure"]["air_nodes"], scale.air_nodes)
            self.assertEqual(meta["node_structure"]["ground_nodes"], scale.ground_nodes)
            counts = meta["physical_topology"]["physical_link_counts_by_type"]
            self.assertEqual(counts["SS"], 2 * scale.space_nodes)
            self.assertEqual(counts["AA"], scale.air_nodes)
            self.assertEqual(counts["SA"], 2 * scale.air_nodes)
            self.assertEqual(counts["AG"], 2 * scale.ground_nodes)
            self.assertGreater(meta["physical_topology"]["physical_link_count"], previous_links)
            previous_links = meta["physical_topology"]["physical_link_count"]
            self.assertLess(meta["physical_topology"]["average_physical_degree"], 7.0)

    def test_expected_links_exist(self):
        result = generate_sagin_topology(TopologyConfig(get_candidate_scale("S20"), time_slots=2, seed=3))
        links = {link.edge: link.link_type for link in result.physical_links}
        space = list(range(8))
        for i, node in enumerate(space):
            self.assertIn(tuple(sorted((node, space[(i + 1) % 8]))), links)
            self.assertIn(tuple(sorted((node, space[(i + 2) % 8]))), links)


if __name__ == "__main__":
    unittest.main()
