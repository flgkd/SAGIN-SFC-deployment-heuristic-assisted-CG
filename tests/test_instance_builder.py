import unittest

from config_schema import FlowGenerationConfig, InstanceConfig, TopologyConfig, get_candidate_scale
from instance_builder import build_instance
from validation import validate_flows, validate_instance, validate_network


class InstanceBuilderTest(unittest.TestCase):
    def test_build_instance_validates_network_flows_and_metadata(self):
        config = InstanceConfig(
            topology_config=TopologyConfig(get_candidate_scale("S20"), 6, 0),
            flow_generation_config=FlowGenerationConfig(seed=0),
            scenario_seed=90,
        )
        instance = build_instance(config)
        validate_network(instance.network)
        validate_flows(instance.flows)
        validate_instance(instance, config)
        self.assertEqual(instance.instance_metadata["flow_count"], 80)
        self.assertEqual(instance.instance_metadata["flow_per_node_ratio"], 4.0)


if __name__ == "__main__":
    unittest.main()
