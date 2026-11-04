import unittest

from config_schema import FlowGenerationConfig, SFCProfile, TopologyConfig, WeightedIntegerDistribution, get_candidate_scale
from flow_generator import generate_flows
from sagin_generator import generate_sagin_topology


class FlowSfcGenerationTest(unittest.TestCase):
    def test_stage_indices_coefficients_and_chain_lengths(self):
        topology = generate_sagin_topology(TopologyConfig(get_candidate_scale("S20"), 6, 50))
        result = generate_flows(FlowGenerationConfig(seed=51), topology)
        for flow in result.flows:
            indices = [stage.stage_index for stage in flow.processing_stages]
            self.assertEqual(indices, list(range(len(flow.processing_stages))))
            self.assertEqual(flow.flow_len, len(flow.processing_stages) + 2)
            for stage in flow.processing_stages:
                self.assertGreaterEqual(stage.processing_coefficient, 2.5)
                self.assertLessEqual(stage.processing_coefficient, 5.0)
        self.assertEqual(
            result.aggregate_metadata["sfc"]["realized_stage_count_counts"],
            result.aggregate_metadata["sfc"]["requested_stage_count_counts"],
        )

    def test_repeated_vnf_types_are_supported_with_stage_coefficients(self):
        topology = generate_sagin_topology(TopologyConfig(get_candidate_scale("S20"), 6, 52))
        profile = SFCProfile(
            name="RepeatOnly",
            stage_count_distribution=WeightedIntegerDistribution({4: 1.0}),
            vnf_type_catalog=("FW",),
            processing_coefficient_low=2.5,
            processing_coefficient_high=5.0,
            sample_with_replacement=True,
        )
        result = generate_flows(FlowGenerationConfig(seed=53, num_flows=3, sfc_profile=profile), topology)
        self.assertEqual(result.aggregate_metadata["sfc"]["repeated_vnf_chain_count"], 3)
        self.assertTrue(all(len(flow.processing_stages) == 4 for flow in result.flows))


if __name__ == "__main__":
    unittest.main()
