import unittest

from config_schema import (
    CapacityRange,
    DynamicsProfile,
    NetworkScaleConfig,
    TopologyConfig,
    get_candidate_scale,
)


class TopologyConfigTest(unittest.TestCase):
    def test_candidate_scales(self):
        expected = {
            "S20": (8, 4, 8),
            "S30": (12, 6, 12),
            "S40": (16, 8, 16),
            "S50": (20, 10, 20),
        }
        for name, counts in expected.items():
            scale = get_candidate_scale(name)
            self.assertEqual((scale.space_nodes, scale.air_nodes, scale.ground_nodes), counts)
            self.assertEqual(scale.ground_regions, 4)

    def test_unknown_scale_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown candidate scale"):
            get_candidate_scale("S99")

    def test_invalid_config_rejected(self):
        with self.assertRaisesRegex(ValueError, "time_slots"):
            TopologyConfig(get_candidate_scale("S20"), time_slots=1, seed=1).validate()
        with self.assertRaisesRegex(ValueError, "divisible"):
            NetworkScaleConfig("bad", 8, 4, 10, 4).validate()
        with self.assertRaisesRegex(ValueError, "high"):
            CapacityRange(5, 4).validate("bad")
        with self.assertRaisesRegex(ValueError, "variation"):
            DynamicsProfile({"SS": 1, "SA": 1, "AA": 1, "AG": 1, "GG": 1}, 1.0, 0.1, 1).validate()


if __name__ == "__main__":
    unittest.main()
