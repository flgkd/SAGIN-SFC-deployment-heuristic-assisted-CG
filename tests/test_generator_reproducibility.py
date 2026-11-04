import unittest

from config_schema import TopologyConfig, get_candidate_scale
from sagin_generator import generate_sagin_topology


class GeneratorReproducibilityTest(unittest.TestCase):
    def test_same_seed_reproduces_identical_instance(self):
        config = TopologyConfig(get_candidate_scale("S20"), 3, 14, dynamic_topology=True)
        first = generate_sagin_topology(config)
        second = generate_sagin_topology(config)
        self.assertEqual(first.physical_links, second.physical_links)
        self.assertEqual(first.network.time_node_capacity, second.network.time_node_capacity)
        self.assertEqual(first.network.time_edge_capacity, second.network.time_edge_capacity)
        self.assertEqual(first.metadata, second.metadata)

    def test_different_seed_changes_stochastic_component(self):
        first = generate_sagin_topology(TopologyConfig(get_candidate_scale("S20"), 3, 15, dynamic_topology=True))
        second = generate_sagin_topology(TopologyConfig(get_candidate_scale("S20"), 3, 16, dynamic_topology=True))
        self.assertNotEqual(first.metadata["node_structure"]["base_node_capacities"], second.metadata["node_structure"]["base_node_capacities"])


if __name__ == "__main__":
    unittest.main()
