import unittest

from formal_runner import run_dwcg_formal_adapter
from formal_schema import FormalAlgorithmConfig, FormalRunKey
from instance_builder import SaginInstance
from tests_gurobi.test_utils import gurobi_test, tiny_flow, tiny_network


def _config(algorithm, use_init, pricing):
    return FormalAlgorithmConfig(
        algorithm=algorithm,
        enabled=True,
        time_limit=20.0,
        threads=1,
        mip_gap=0.0,
        use_saginfe_initialization=use_init,
        deterministic_mode=True,
        pricing_worker_count=1,
        pricing_strategy=pricing,
        max_cg_iterations=2,
        max_exact_pricing_calls=8,
        hard_time_limit_seconds=20.0,
    )


def _tiny_instance():
    network = tiny_network()
    flow = tiny_flow()
    return SaginInstance(
        network=network,
        flows=(flow,),
        topology_metadata={
            "physical_topology": {
                "base_physical_links": [(0, 1)],
            },
            "snapshots": [
                {"time_slot": 0, "active_edges": [((0, 0), (1, 0))]},
                {"time_slot": 1, "active_edges": []},
            ],
        },
        flow_metadata={
            "per_flow": {
                flow.flow_id: {
                    "base_demand": flow.flow_demand,
                },
            },
        },
        instance_metadata={
            "topology_seed": 0,
            "demand_multiplier": 1.0,
        },
    )


@gurobi_test
class DwcgAblationVariantsGurobiTest(unittest.TestCase):
    def test_all_ablation_variants_produce_valid_tiny_records(self):
        instance = _tiny_instance()
        variants = [
            _config("dwcg_pure", False, "hybrid"),
            _config("dwcg_warm", True, "hybrid"),
            _config("dwcg_pure_hp", False, "heuristic_then_exact"),
            _config("dwcg_saginfe", True, "heuristic_then_exact"),
        ]
        for config in variants:
            key = FormalRunKey("tiny", "TINY", 2, 0, "Tiny", 1.0, config.algorithm)
            context = {
                "key": key,
                "git_commit": "",
                "configuration_snapshot": {"algorithm": config.algorithm},
                "saginfe_cache": {},
            }
            with self.subTest(algorithm=config.algorithm):
                record = run_dwcg_formal_adapter(instance, config, context)
                self.assertIsNone(record.error_type, record.error_message)
                self.assertEqual(record.key.algorithm, config.algorithm)
                self.assertTrue(record.path_validity)
                self.assertTrue(record.capacity_validity)
                self.assertEqual(record.dwcg_metadata["variant"], config.algorithm)
                self.assertEqual(record.dwcg_metadata["pricing_strategy"], config.pricing_strategy)
                self.assertEqual(
                    record.dwcg_metadata["saginfe_initialization_used"],
                    config.use_saginfe_initialization,
                )
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


if __name__ == "__main__":
    unittest.main()
