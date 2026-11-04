import unittest

from config_schema import TopologyConfig, get_candidate_scale
from sagin_generator import generate_sagin_topology


class DynamicTopologyTest(unittest.TestCase):
    def test_disabled_dynamics_keeps_all_links_active(self):
        result = generate_sagin_topology(TopologyConfig(get_candidate_scale("S20"), time_slots=3, seed=9, dynamic_topology=False))
        base_count = len(result.physical_links)
        for snapshot in result.metadata["snapshots"]:
            self.assertEqual(snapshot["active_physical_link_count"], base_count)
            self.assertEqual(snapshot["connectivity_resampling_attempts"], 1)

    def test_enabled_dynamics_is_deterministic(self):
        config = TopologyConfig(get_candidate_scale("S20"), time_slots=4, seed=10, dynamic_topology=True)
        first = generate_sagin_topology(config)
        second = generate_sagin_topology(config)
        self.assertEqual(
            [snapshot["active_edges"] for snapshot in first.metadata["snapshots"]],
            [snapshot["active_edges"] for snapshot in second.metadata["snapshots"]],
        )
        self.assertTrue(all("connectivity_resampling_attempts" in s for s in first.metadata["snapshots"]))


if __name__ == "__main__":
    unittest.main()
