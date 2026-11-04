import random
import unittest

from flow_generator import allocate_largest_remainder, shuffled_quota_labels


class QuotaAllocationTest(unittest.TestCase):
    def test_largest_remainder_counts_and_tie_break_are_deterministic(self):
        counts = allocate_largest_remainder(7, {"a": 0.5, "b": 0.25, "c": 0.25}, tie_order=("b", "c", "a"))
        self.assertEqual(sum(counts.values()), 7)
        self.assertEqual(counts, {"a": 3, "b": 2, "c": 2})

    def test_shuffled_assignment_reproducible_by_seed(self):
        first, counts = shuffled_quota_labels(10, {"x": 0.6, "y": 0.4}, random.Random(4), tie_order=("x", "y"))
        second, _ = shuffled_quota_labels(10, {"x": 0.6, "y": 0.4}, random.Random(4), tie_order=("x", "y"))
        self.assertEqual(counts, {"x": 6, "y": 4})
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
