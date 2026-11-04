import unittest

from config_schema import FlowGenerationConfig, TopologyConfig, get_candidate_deadline_profile, get_candidate_scale
from flow_generator import generate_flows
from sagin_generator import generate_sagin_topology


class FlowDeadlineGenerationTest(unittest.TestCase):
    def test_deadline_slack_is_exact_and_not_clipped(self):
        topology = generate_sagin_topology(TopologyConfig(get_candidate_scale("S20"), 6, 40))
        result = generate_flows(FlowGenerationConfig(seed=41, deadline_profile=get_candidate_deadline_profile("Flexible")), topology)
        for flow in result.flows:
            meta = result.per_flow_metadata[flow.flow_id]
            self.assertEqual(flow.flow_src_time_node[1] + meta["slack"], flow.flow_dst_max_time)
            self.assertLess(flow.flow_dst_max_time, topology.network.time_slots_num)
        self.assertEqual(result.aggregate_metadata["deadline"]["clipping_count"], 0)

    def test_impossible_deadline_profile_fails(self):
        topology = generate_sagin_topology(TopologyConfig(get_candidate_scale("S20"), 3, 42))
        with self.assertRaises(ValueError):
            generate_flows(FlowGenerationConfig(seed=43, deadline_profile=get_candidate_deadline_profile("Flexible")), topology)


if __name__ == "__main__":
    unittest.main()
