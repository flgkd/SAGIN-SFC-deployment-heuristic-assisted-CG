import unittest

from formal_runner import run_dwcg_formal_adapter
from formal_schema import FormalAlgorithmConfig, FormalRunKey
from tests_gurobi.test_dwcg_ablation_variants import _tiny_instance
from tests_gurobi.test_utils import gurobi_test


def _config(variant_id, strategy):
    return FormalAlgorithmConfig(
        algorithm="dwcg_pure",
        variant_id=variant_id,
        enabled=True,
        time_limit=60.0,
        threads=1,
        mip_gap=0.0,
        use_saginfe_initialization=False,
        deterministic_mode=True,
        algorithm_seed=12345,
        pricing_worker_count=1,
        pricing_strategy=strategy,
        hybrid_switch_iteration=5 if strategy == "hybrid" else None,
        max_cg_iterations=6,
        max_exact_pricing_calls=60,
        hard_time_limit_seconds=60.0,
    )


@gurobi_test
class PricingStrategyVariantsGurobiTest(unittest.TestCase):
    def test_pure_pricing_strategy_variants_on_tiny_instance(self):
        instance = _tiny_instance()
        configs = [
            _config("EX", "exact"),
            _config("ESV", "early_stop"),
            _config("HYB_S5", "hybrid"),
        ]
        records = []
        for config in configs:
            key = FormalRunKey("tiny", "TINY", 2, 0, "Tiny", 1.0, config.algorithm, config.variant_id)
            record = run_dwcg_formal_adapter(
                instance,
                config,
                {
                    "key": key,
                    "git_commit": "",
                    "configuration_snapshot": {"algorithm": config.algorithm, "variant_id": config.variant_id},
                    "saginfe_cache": {},
                },
            )
            self.assertIsNone(record.error_type, record.error_message)
            self.assertTrue(record.path_validity)
            self.assertTrue(record.capacity_validity)
            self.assertEqual(record.dwcg_metadata["solver_pricing_strategy"], config.pricing_strategy)
            self.assertFalse(record.dwcg_metadata["parallel_pricing_enabled"])
            self.assertEqual(record.dwcg_metadata["solver_threads_per_worker"], 1)
            self.assertTrue(record.dwcg_metadata["pricing_strategy_invariant_valid"])
            self.assertEqual(
                record.dwcg_metadata["total_positive_column_count"],
                record.dwcg_metadata["strict_exact_positive_column_count"]
                + record.dwcg_metadata["early_stop_positive_column_count"],
            )
            self.assertEqual(
                record.dwcg_metadata["strict_exact_pricing_call_count"],
                record.dwcg_metadata["exact_pricing_call_count"]
                - record.dwcg_metadata["early_stop_pricing_call_count"],
            )
            if config.variant_id == "EX":
                self.assertEqual(record.dwcg_metadata["early_stop_pricing_call_count"], 0)
                self.assertEqual(record.dwcg_metadata["early_stop_positive_column_count"], 0)
            if config.variant_id == "ESV":
                self.assertFalse(
                    record.dwcg_metadata["convergence_certification"]
                    and record.dwcg_metadata["early_stop_pricing_call_count"] == record.dwcg_metadata["exact_pricing_call_count"]
                )
                self.assertGreaterEqual(record.dwcg_metadata["early_stop_positive_column_count"], 0)
            if config.variant_id == "HYB_S5":
                self.assertEqual(record.dwcg_metadata["hybrid_switch_iteration"], 5)
            records.append(record)

        repeat = run_dwcg_formal_adapter(
            instance,
            configs[0],
            {
                "key": FormalRunKey("tiny", "TINY", 2, 0, "Tiny", 1.0, "dwcg_pure", "EX"),
                "git_commit": "",
                "configuration_snapshot": {"algorithm": "dwcg_pure", "variant_id": "EX"},
                "saginfe_cache": {},
            },
        )
        self.assertEqual(
            repeat.dwcg_metadata["final_ordered_column_pool_fingerprint"],
            records[0].dwcg_metadata["final_ordered_column_pool_fingerprint"],
        )

        completed_work = {
            (
                record.dwcg_metadata["completed_cg_iteration_count"],
                record.dwcg_metadata["exact_pricing_call_count"],
            )
            for record in records
        }
        if len(completed_work) == 1:
            self.assertEqual(len({record.accepted_flow_count for record in records}), 1)
            self.assertEqual(len({round(record.canonical_objective_value, 9) for record in records}), 1)


if __name__ == "__main__":
    unittest.main()
