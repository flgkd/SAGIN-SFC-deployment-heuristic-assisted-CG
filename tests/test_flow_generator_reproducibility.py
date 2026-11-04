import unittest

from config_schema import FlowGenerationConfig, TopologyConfig, get_candidate_scale
from flow_generator import generate_flows
from sagin_generator import generate_sagin_topology


class FlowGeneratorReproducibilityTest(unittest.TestCase):
    def test_same_seed_reproduces_flows_and_metadata(self):
        topology = generate_sagin_topology(TopologyConfig(get_candidate_scale("S20"), 6, 70))
        first = generate_flows(FlowGenerationConfig(seed=71), topology)
        second = generate_flows(FlowGenerationConfig(seed=71), topology)
        self.assertEqual(first.per_flow_metadata, second.per_flow_metadata)
        self.assertEqual(first.aggregate_metadata, second.aggregate_metadata)

    def test_different_seed_changes_stochastic_structure(self):
        topology = generate_sagin_topology(TopologyConfig(get_candidate_scale("S20"), 6, 72))
        first = generate_flows(FlowGenerationConfig(seed=73), topology)
        second = generate_flows(FlowGenerationConfig(seed=74), topology)
        self.assertNotEqual(first.per_flow_metadata, second.per_flow_metadata)


if __name__ == "__main__":
    unittest.main()
