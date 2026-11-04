import pytest

import unittest

from objective import compute_reduced_cost_from_usage, objective_spec_from_network
from result_schema import PricingIterationStats, evaluate_exact_pricing_certification
from tests.test_validation_fixtures import repeated_flow, tiny_network, valid_repeated_path


class PricingCertificationLogicTest(unittest.TestCase):
    def _stats(self, **overrides):
        data = dict(
            iteration=1,
            expected_exact_calls=2,
            launched_exact_calls=2,
            completed_exact_calls=2,
            optimal_exact_calls=2,
            positive_reduced_cost_calls=0,
            time_limit_calls=0,
            failed_calls=0,
            columns_added=0,
        )
        data.update(overrides)
        return PricingIterationStats(**data)

    def test_one_exact_timeout_prevents_certification(self):
        result = evaluate_exact_pricing_certification(self._stats(time_limit_calls=1), 2)
        self.assertFalse(result.certification_possible)

    def test_one_worker_failure_prevents_certification(self):
        result = evaluate_exact_pricing_certification(self._stats(failed_calls=1), 2)
        self.assertFalse(result.certification_possible)

    def test_one_missing_exact_call_prevents_certification(self):
        result = evaluate_exact_pricing_certification(self._stats(launched_exact_calls=1), 2)
        self.assertFalse(result.certification_possible)

    def test_all_exact_calls_optimal_with_no_positive_column_certifies(self):
        result = evaluate_exact_pricing_certification(self._stats(), 2)
        self.assertTrue(result.certification_possible)

    def test_historical_positive_column_does_not_block_later_certification(self):
        pytest.importorskip("gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt")
        from main_solver import latest_exact_iteration_certification

        first = evaluate_exact_pricing_certification(
            self._stats(positive_reduced_cost_calls=1, columns_added=1),
            2,
        )
        second = evaluate_exact_pricing_certification(self._stats(iteration=2), 2)
        certified, reason = latest_exact_iteration_certification({
            "pricing_iteration_stats": [first, second],
        })
        self.assertTrue(certified)
        self.assertEqual(reason, "certified")

    def test_positive_result_rejected_by_extraction_or_validation_blocks_certification(self):
        pytest.importorskip("gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt")
        from main_solver import exact_iteration_had_rejected_positive_column, latest_rejected_positive_reason

        result = evaluate_exact_pricing_certification(
            self._stats(positive_reduced_cost_calls=1),
            2,
            positive_column_rejected=True,
            rejection_reasons=["POSITIVE_COLUMN_VALIDATION_FAILED"],
        )
        self.assertFalse(result.certification_possible)
        self.assertIn("POSITIVE_COLUMN_VALIDATION_FAILED", result.certification_reason)
        stats = {
            "pricing_iteration_stats": [result],
        }
        self.assertTrue(exact_iteration_had_rejected_positive_column(stats))
        self.assertEqual(latest_rejected_positive_reason(stats), "POSITIVE_COLUMN_VALIDATION_FAILED")

    def test_duplicate_non_positive_result_is_not_rejected_positive(self):
        pytest.importorskip("gurobipy", reason="Requires optional gurobipy package; install requirements-solver.txt")
        from main_solver import exact_iteration_had_rejected_positive_column

        result = evaluate_exact_pricing_certification(self._stats(columns_added=0), 2)
        self.assertTrue(result.certification_possible)
        self.assertFalse(exact_iteration_had_rejected_positive_column({
            "pricing_iteration_stats": [result],
            "duplicate_rejection_count": 1,
        }))

    def test_missing_flow_dual_raises_in_reduced_cost_helper(self):
        network = tiny_network()
        flow = repeated_flow()
        path = valid_repeated_path(network, flow)
        with self.assertRaisesRegex(ValueError, "Missing required flow convexity dual"):
            compute_reduced_cost_from_usage(
                flow,
                path.path_cost,
                path.path_node_usage,
                path.path_edge_usage,
                {"flow": {}, "node": {}, "edge": {}},
                objective_spec_from_network(network),
            )


if __name__ == "__main__":
    unittest.main()
