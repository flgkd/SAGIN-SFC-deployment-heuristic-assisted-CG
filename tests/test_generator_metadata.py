import unittest

from config_schema import TopologyConfig, get_candidate_scale
from sagin_generator import generate_sagin_topology


class GeneratorMetadataTest(unittest.TestCase):
    def test_metadata_counts_match_generated_graph(self):
        result = generate_sagin_topology(TopologyConfig(get_candidate_scale("S30"), 3, 17, dynamic_topology=True))
        meta = result.metadata
        self.assertEqual(meta["physical_topology"]["physical_link_count"], len(result.physical_links))
        by_type = {key: 0 for key in ("SS", "SA", "AA", "AG", "GG")}
        for link in result.physical_links:
            by_type[link.link_type] += 1
        self.assertEqual(meta["physical_topology"]["physical_link_counts_by_type"], by_type)
        for snapshot in meta["snapshots"]:
            self.assertEqual(snapshot["active_physical_link_count"], len(snapshot["active_edges"]))
            realized = snapshot["realized_survival_rate_by_type"]
            for value in realized.values():
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)
        self.assertIn("connectivity_conditioning", meta["configuration"]["dynamics_profile"])


if __name__ == "__main__":
    unittest.main()
