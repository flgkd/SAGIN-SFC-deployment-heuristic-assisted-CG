import unittest

from formal_runner import run_dwcg_formal_adapter
from formal_schema import FormalAlgorithmConfig, FormalRunKey
from tests_gurobi.test_dwcg_ablation_variants import _tiny_instance
from tests_gurobi.test_utils import gurobi_test


def _config(worker_count):
    return FormalAlgorithmConfig(
        algorithm="dwcg_pure",
        variant_id=f"W{worker_count}",
        enabled=True,
        time_limit=60.0,
        threads=1,
        mip_gap=0.0,
        use_saginfe_initialization=False,
        deterministic_mode=True,
        algorithm_seed=12345,
        pricing_worker_count=worker_count,
        pricing_strategy="hybrid",
        max_cg_iterations=2,
        max_exact_pricing_calls=16,
        hard_time_limit_seconds=60.0,
    )


@gurobi_test
class ParallelPricingVariantsGurobiTest(unittest.TestCase):
    def test_worker_variants_match_on_tiny_instance_when_work_matches(self):
        instance = _tiny_instance()
        records = []
        for worker_count in (1, 2, 4, 8):
            config = _config(worker_count)
            key = FormalRunKey("tiny", "TINY", 2, 0, "Tiny", 1.0, config.algorithm, config.variant_id)
            with self.subTest(worker_count=worker_count):
                record = run_dwcg_formal_adapter(
                    instance,
                    config,
                    {
                        "key": key,
                        "git_commit": "",
                        "configuration_snapshot": {"algorithm": config.algorithm, "variant_id": config.variant_id},
                        "sgfe_cache": {},
                    },
                )
                self.assertIsNone(record.error_type, record.error_message)
                self.assertTrue(record.path_validity)
                self.assertTrue(record.capacity_validity)
                self.assertEqual(record.dwcg_metadata["algorithm_seed"], records[0].dwcg_metadata["algorithm_seed"] if records else record.dwcg_metadata["algorithm_seed"])
                self.assertEqual(record.dwcg_metadata["python_random_seed"], records[0].dwcg_metadata["python_random_seed"] if records else record.dwcg_metadata["python_random_seed"])
                self.assertEqual(record.dwcg_metadata["numpy_seed"], records[0].dwcg_metadata["numpy_seed"] if records else record.dwcg_metadata["numpy_seed"])
                self.assertEqual(record.dwcg_metadata["gurobi_seed"], records[0].dwcg_metadata["gurobi_seed"] if records else record.dwcg_metadata["gurobi_seed"])
                records.append(record)

        accepted = {record.accepted_flow_count for record in records}
        objectives = {round(record.canonical_objective_value, 9) for record in records}
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(objectives), 1)

        completed_work = {
            (
                record.dwcg_metadata["completed_cg_iteration_count"],
                record.dwcg_metadata["exact_pricing_call_count"],
            )
            for record in records
        }
        if len(completed_work) == 1:
            self.assertEqual(
                len({record.dwcg_metadata["final_ordered_column_pool_fingerprint"] for record in records}),
                1,
            )


if __name__ == "__main__":
    unittest.main()
