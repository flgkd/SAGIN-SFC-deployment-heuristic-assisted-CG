import unittest

from config_schema import FlowGenerationConfig, TopologyConfig, get_candidate_scale
from flow_generator import generate_flows
from sagin_generator import generate_sagin_topology


class FlowGeneratorMetadataTest(unittest.TestCase):
    def test_metadata_totals_match_generated_flows(self):
        topology = generate_sagin_topology(TopologyConfig(get_candidate_scale("S20"), 6, 80))
        result = generate_flows(FlowGenerationConfig(seed=81), topology)
        aggregate = result.aggregate_metadata
        self.assertEqual(aggregate["configuration"]["actual_flow_count"], len(result.flows))
        self.assertEqual(sum(aggregate["traffic"]["realized_category_counts"].values()), len(result.flows))
        self.assertEqual(sum(aggregate["sfc"]["realized_stage_count_counts"].values()), len(result.flows))
        self.assertEqual(sum(aggregate["deadline"]["realized_slack_counts"].values()), len(result.flows))
        self.assertAlmostEqual(
            aggregate["demand"]["total_actual_demand"],
            sum(flow.flow_demand for flow in result.flows),
        )


if __name__ == "__main__":
    unittest.main()
