import unittest

from config_schema import TopologyConfig, get_candidate_scale
from sagin_generator import generate_sagin_topology


class TopologyLayersAndRegionsTest(unittest.TestCase):
    def test_node_id_policy_and_balanced_regions(self):
        config = TopologyConfig(get_candidate_scale("S30"), time_slots=2, seed=5)
        result = generate_sagin_topology(config)
        ns = result.metadata["node_structure"]
        layer = ns["node_layer_map"]
        self.assertTrue(all(layer[i] == "Space" for i in range(12)))
        self.assertTrue(all(layer[i] == "Air" for i in range(12, 18)))
        self.assertTrue(all(layer[i] == "Ground" for i in range(18, 30)))
        regions = ns["ground_region_map"]
        region_counts = {region: 0 for region in range(4)}
        for node, region in regions.items():
            if layer[node] == "Ground":
                region_counts[region] += 1
        self.assertEqual(set(region_counts.values()), {3})

    def test_layer_specific_attachment_rules(self):
        result = generate_sagin_topology(TopologyConfig(get_candidate_scale("S20"), time_slots=2, seed=6))
        layer = result.metadata["node_structure"]["node_layer_map"]
        ground_region = result.metadata["node_structure"]["ground_region_map"]
        sa_degree = {node: 0 for node, node_layer in layer.items() if node_layer == "Air"}
        ag_degree = {node: 0 for node, node_layer in layer.items() if node_layer == "Ground"}
        for link in result.physical_links:
            endpoint_layers = {layer[link.u], layer[link.v]}
            if link.link_type == "SA":
                air_node = link.u if layer[link.u] == "Air" else link.v
                sa_degree[air_node] += 1
            if link.link_type == "AG":
                ground_node = link.u if layer[link.u] == "Ground" else link.v
                ag_degree[ground_node] += 1
            if link.link_type == "GG":
                self.assertEqual(ground_region[link.u], ground_region[link.v])
            self.assertIn(link.link_type, {"SS", "SA", "AA", "AG", "GG"})
            self.assertNotEqual(link.u, link.v)
            self.assertNotEqual(endpoint_layers, {"Space", "Ground"})
        self.assertTrue(all(value == 2 for value in sa_degree.values()))
        self.assertTrue(all(value == 2 for value in ag_degree.values()))


if __name__ == "__main__":
    unittest.main()
