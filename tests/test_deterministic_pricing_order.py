import pytest

pytest.importorskip(
    "gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt"
)

import unittest

from main_solver import sort_pricing_results_deterministically
from tests.test_validation_fixtures import repeated_flow


class DeterministicPricingOrderTest(unittest.TestCase):
    def test_nondeterministic_input_gets_stable_processing_order(self):
        f2 = repeated_flow(2)
        f1 = repeated_flow(1)
        items = [
            (f2, "Pricing", 1.0, None, None, {"fallback_key": "b"}),
            (f1, "Pricing", 1.0, None, None, {"fallback_key": "a"}),
        ]
        ordered = sort_pricing_results_deterministically(items)
        self.assertEqual([item[0].flow_id for item in ordered], [1, 2])


if __name__ == "__main__":
    unittest.main()
