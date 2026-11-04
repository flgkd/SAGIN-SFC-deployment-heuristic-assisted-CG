import unittest

from config_schema import FlowGenerationConfig, InstanceConfig, TopologyConfig, get_candidate_scale
from instance_builder import build_instance
from validation import validate_instance


class InstanceValidationTest(unittest.TestCase):
    def test_validation_rejects_deadline_clipping_metadata(self):
        config = InstanceConfig(
            TopologyConfig(get_candidate_scale("S20"), 6, 0),
            FlowGenerationConfig(seed=0),
            scenario_seed=110,
        )
        instance = build_instance(config)
        instance.flow_metadata["aggregate"]["deadline"]["clipping_count"] = 1
        with self.assertRaises(ValueError):
            validate_instance(instance, config)


if __name__ == "__main__":
    unittest.main()
