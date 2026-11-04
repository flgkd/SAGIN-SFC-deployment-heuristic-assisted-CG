import unittest

from config_schema import FlowGenerationConfig, TopologyConfig, get_candidate_scale
from flow_generator import generate_flows
from sagin_generator import generate_sagin_topology


class FlowOdGenerationTest(unittest.TestCase):
    def test_od_categories_match_endpoint_layers_and_regions(self):
        topology = generate_sagin_topology(TopologyConfig(get_candidate_scale("S20"), 6, 30))
        result = generate_flows(FlowGenerationConfig(seed=31), topology)
        counts = {"cross_region_ground": 0, "same_region_ground": 0, "ground_nonground": 0}
        for meta in result.per_flow_metadata.values():
            counts[meta["traffic_category"]] += 1
            self.assertNotEqual(meta["source_node"], meta["destination_node"])
            if meta["traffic_category"] == "cross_region_ground":
                self.assertEqual(meta["source_layer"], "Ground")
                self.assertEqual(meta["destination_layer"], "Ground")
                self.assertNotEqual(meta["source_ground_region"], meta["destination_ground_region"])
            elif meta["traffic_category"] == "same_region_ground":
                self.assertEqual(meta["source_layer"], "Ground")
                self.assertEqual(meta["destination_layer"], "Ground")
                self.assertEqual(meta["source_ground_region"], meta["destination_ground_region"])
            else:
                self.assertEqual(
                    [meta["source_layer"], meta["destination_layer"]].count("Ground"),
                    1,
                )
        self.assertEqual(counts, result.aggregate_metadata["traffic"]["realized_category_counts"])


if __name__ == "__main__":
    unittest.main()
