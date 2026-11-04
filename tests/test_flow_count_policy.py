import unittest

from config_schema import FlowGenerationConfig, TopologyConfig, get_candidate_scale
from flow_generator import default_num_flows_for_scale, generate_flows, resolve_num_flows
from sagin_generator import generate_sagin_topology


class FlowCountPolicyTest(unittest.TestCase):
    def test_standard_weak_scaling_counts(self):
        self.assertEqual(default_num_flows_for_scale(get_candidate_scale("S20")), 80)
        self.assertEqual(default_num_flows_for_scale(get_candidate_scale("S30")), 120)
        self.assertEqual(default_num_flows_for_scale(get_candidate_scale("S40")), 160)
        self.assertEqual(default_num_flows_for_scale(get_candidate_scale("S50")), 200)

    def test_explicit_override_works(self):
        topology = generate_sagin_topology(TopologyConfig(get_candidate_scale("S20"), 6, 20))
        config = FlowGenerationConfig(seed=21, num_flows=9)
        self.assertEqual(resolve_num_flows(config, topology), 9)
        self.assertEqual(len(generate_flows(config, topology).flows), 9)


if __name__ == "__main__":
    unittest.main()
