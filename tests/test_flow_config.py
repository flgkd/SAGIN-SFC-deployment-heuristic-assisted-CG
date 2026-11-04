import unittest

from config_schema import (
    FlowGenerationConfig,
    WeightedIntegerDistribution,
    get_candidate_deadline_profile,
    get_candidate_load_profile,
    get_candidate_sfc_profile,
    get_candidate_traffic_mix_profile,
)


class FlowConfigTest(unittest.TestCase):
    def test_candidate_profiles_are_exact(self):
        self.assertEqual(get_candidate_load_profile("Low"), 0.8)
        self.assertEqual(get_candidate_load_profile("Medium"), 1.0)
        self.assertEqual(get_candidate_load_profile("High"), 1.2)
        self.assertEqual(get_candidate_deadline_profile("RealTime").slack_distribution.as_dict(), {1: 0.5, 2: 0.5})
        self.assertEqual(get_candidate_deadline_profile("Balanced").slack_distribution.as_dict(), {2: 0.3, 3: 0.4, 4: 0.3})
        self.assertEqual(get_candidate_deadline_profile("Flexible").slack_distribution.as_dict(), {3: 0.3, 4: 0.4, 5: 0.3})
        self.assertEqual(get_candidate_traffic_mix_profile("Candidate").probabilities(), {
            "cross_region_ground": 0.7,
            "same_region_ground": 0.2,
            "ground_nonground": 0.1,
        })
        self.assertEqual(get_candidate_sfc_profile("Candidate").stage_count_distribution.as_dict(), {2: 0.2, 3: 0.5, 4: 0.3})

    def test_invalid_probability_profiles_fail(self):
        with self.assertRaises(ValueError):
            WeightedIntegerDistribution({1: 0.4, 2: 0.4}).validate()
        with self.assertRaises(ValueError):
            WeightedIntegerDistribution({1: -0.1, 2: 1.1}).validate()
        with self.assertRaises(ValueError):
            FlowGenerationConfig(seed=1, base_demand_low=25, base_demand_high=24).validate()


if __name__ == "__main__":
    unittest.main()
