import unittest

from formal_runner import run_dwcg_formal_adapter
from formal_schema import FormalAlgorithmConfig, FormalRunKey
from tests_gurobi.test_dwcg_ablation_variants import _tiny_instance
from tests_gurobi.test_utils import gurobi_test


def _config(variant_id, switch_iteration):
    return FormalAlgorithmConfig(
        algorithm="dwcg_pure",
        variant_id=variant_id,
        display_name_override=f"DWCG-Pure-HYB-S{switch_iteration}" if variant_id != "HYB_S5" else None,
        enabled=True,
        time_limit=60.0,
        threads=1,
        mip_gap=0.0,
        use_saginfe_initialization=False,
        deterministic_mode=True,
        algorithm_seed=12345,
        pricing_worker_count=1,
        pricing_strategy="hybrid",
        hybrid_switch_iteration=switch_iteration,
        max_cg_iterations=8,
        max_exact_pricing_calls=80,
        hard_time_limit_seconds=60.0,
    )


@gurobi_test
class HybridSwitchVariantsGurobiTest(unittest.TestCase):
    def test_hybrid_switch_s2_s7_tiny_records_are_valid_and_stable(self):
        instance = _tiny_instance()
        records = []
        for variant_id, switch_iteration in (("HYB_S2", 2), ("HYB_S7", 7)):
            config = _config(variant_id, switch_iteration)
            key = FormalRunKey("tiny", "TINY", 2, 0, "Tiny", 1.0, "dwcg_pure", variant_id)
            record = run_dwcg_formal_adapter(
                instance,
                config,
                {
                    "key": key,
                    "git_commit": "",
                    "configuration_snapshot": {"algorithm": "dwcg_pure", "variant_id": variant_id},
                    "sgfe_cache": {},
                },
            )
            self.assertIsNone(record.error_type, record.error_message)
            self.assertTrue(record.path_validity)
            self.assertTrue(record.capacity_validity)
            self.assertEqual(record.dwcg_metadata["hybrid_switch_iteration"], switch_iteration)
            self.assertEqual(record.dwcg_metadata["pricing_strategy"], "hybrid")
            self.assertTrue(record.dwcg_metadata["pricing_strategy_invariant_valid"])
            self.assertEqual(record.dwcg_metadata["pricing_worker_count"], 1)
            self.assertFalse(record.dwcg_metadata["parallel_pricing_enabled"])
            self.assertEqual(
                record.dwcg_metadata["total_positive_column_count"],
                record.dwcg_metadata["strict_exact_positive_column_count"]
                + record.dwcg_metadata["early_stop_positive_column_count"],
            )
            records.append(record)

        repeat = run_dwcg_formal_adapter(
            instance,
            _config("HYB_S2", 2),
            {
                "key": FormalRunKey("tiny", "TINY", 2, 0, "Tiny", 1.0, "dwcg_pure", "HYB_S2"),
                "git_commit": "",
                "configuration_snapshot": {"algorithm": "dwcg_pure", "variant_id": "HYB_S2"},
                "sgfe_cache": {},
            },
        )
        self.assertEqual(
            repeat.dwcg_metadata["final_ordered_column_pool_fingerprint"],
            records[0].dwcg_metadata["final_ordered_column_pool_fingerprint"],
        )

    def test_default_hyb_s5_behavior_remains_unchanged(self):
        instance = _tiny_instance()
        config = _config("HYB_S5", 5)
        key = FormalRunKey("tiny", "TINY", 2, 0, "Tiny", 1.0, "dwcg_pure", "HYB_S5")
        record = run_dwcg_formal_adapter(
            instance,
            config,
            {
                "key": key,
                "git_commit": "",
                "configuration_snapshot": {"algorithm": "dwcg_pure", "variant_id": "HYB_S5"},
                "sgfe_cache": {},
            },
        )
        self.assertIsNone(record.error_type, record.error_message)
        self.assertEqual(record.dwcg_metadata["hybrid_switch_iteration"], 5)
        self.assertEqual(record.dwcg_metadata["display_name"], "DWCG-Pure-HYB-S5")
        self.assertTrue(record.dwcg_metadata["pricing_strategy_invariant_valid"])


if __name__ == "__main__":
    unittest.main()
