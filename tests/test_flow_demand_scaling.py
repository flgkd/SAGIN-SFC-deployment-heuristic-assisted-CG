import unittest

from config_schema import FlowGenerationConfig, TopologyConfig, get_candidate_load_profile, get_candidate_scale
from flow_generator import generate_flows
from sagin_generator import generate_sagin_topology


def structural_tuple(result):
    return [
        (
            meta["source_node"],
            meta["destination_node"],
            meta["arrival_time"],
            meta["deadline_time"],
            meta["processing_stage_count"],
            tuple(meta["vnf_label_sequence"]),
            tuple(meta["processing_coefficient_sequence"]),
            meta["base_demand"],
        )
        for meta in result.per_flow_metadata.values()
    ]


class FlowDemandScalingTest(unittest.TestCase):
    def test_load_multiplier_changes_only_actual_demand(self):
        topology = generate_sagin_topology(TopologyConfig(get_candidate_scale("S20"), 6, 60))
        low = generate_flows(FlowGenerationConfig(seed=61, demand_multiplier=get_candidate_load_profile("Low")), topology)
        medium = generate_flows(FlowGenerationConfig(seed=61, demand_multiplier=get_candidate_load_profile("Medium")), topology)
        high = generate_flows(FlowGenerationConfig(seed=61, demand_multiplier=get_candidate_load_profile("High")), topology)
        self.assertEqual(structural_tuple(low), structural_tuple(medium))
        self.assertEqual(structural_tuple(medium), structural_tuple(high))
        for flow_id in medium.per_flow_metadata:
            self.assertAlmostEqual(low.per_flow_metadata[flow_id]["actual_demand"] / medium.per_flow_metadata[flow_id]["actual_demand"], 0.8)
            self.assertAlmostEqual(high.per_flow_metadata[flow_id]["actual_demand"] / medium.per_flow_metadata[flow_id]["actual_demand"], 1.2)


if __name__ == "__main__":
    unittest.main()
