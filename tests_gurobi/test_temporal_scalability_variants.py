import unittest

from data_model import Flow, Network
from formal_runner import run_dwcg_formal_adapter, run_saginfe_formal_adapter
from formal_schema import FormalAlgorithmConfig, FormalRunKey
from instance_builder import SaginInstance
from tests_gurobi.test_utils import gurobi_test


def _temporal_tiny_network(time_slots):
    network = Network()
    network.set_nodes(2)
    network.set_time_slots(time_slots)
    for node in (0, 1):
        for time in range(time_slots):
            network.add_time_node((node, time), 20.0)
    for time in range(time_slots):
        network.add_comm_time_edge((0, time), (1, time), 20.0)
    for node in (0, 1):
        for time in range(time_slots - 1):
            network.add_storage_time_edge((node, time))
    return network


def _temporal_tiny_instance(time_slots):
    network = _temporal_tiny_network(time_slots)
    flow = Flow(1, 1, 0, 0, 1, 1, ["A"], {"A": 1.0}, 1.0)
    return SaginInstance(
        network=network,
        flows=(flow,),
        topology_metadata={
            "physical_topology": {
                "base_physical_links": [(0, 1)],
            },
            "snapshots": [
                {"time_slot": time, "active_edges": [(0, 1)]}
                for time in range(time_slots)
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


def _config(algorithm):
    if algorithm == "saginfe":
        return FormalAlgorithmConfig(algorithm="saginfe", enabled=True, threads=1)
    return FormalAlgorithmConfig(
        algorithm=algorithm,
        enabled=True,
        time_limit=30.0,
        threads=1,
        mip_gap=0.0,
        use_saginfe_initialization=algorithm == "dwcg_saginfe",
        deterministic_mode=True,
        pricing_worker_count=1,
        pricing_strategy="heuristic_then_exact" if algorithm == "dwcg_saginfe" else "hybrid",
        max_cg_iterations=4,
        max_exact_pricing_calls=40,
        hard_time_limit_seconds=30.0,
    )


def _run(instance, time_slots, algorithm):
    config = _config(algorithm)
    key = FormalRunKey(f"tiny_T{time_slots}", "TINY", time_slots, 0, "Tiny", 1.0, algorithm)
    context = {
        "key": key,
        "git_commit": "",
        "configuration_snapshot": {"algorithm": algorithm},
        "saginfe_cache": {},
    }
    if algorithm == "saginfe":
        return run_saginfe_formal_adapter(instance, config, context)
    return run_dwcg_formal_adapter(instance, config, context)


@gurobi_test
class TemporalScalabilityGurobiTest(unittest.TestCase):
    def test_t5_t8_t12_tiny_records_are_valid_for_all_algorithms(self):
        for time_slots in (5, 8, 12):
            instance = _temporal_tiny_instance(time_slots)
            for algorithm in ("saginfe", "dwcg_pure", "dwcg_saginfe"):
                with self.subTest(time_slots=time_slots, algorithm=algorithm):
                    record = _run(instance, time_slots, algorithm)
                    self.assertIsNone(record.error_type, record.error_message)
                    self.assertTrue(record.path_validity)
                    self.assertTrue(record.capacity_validity)
                    self.assertEqual(record.key.time_slots, time_slots)
                    if algorithm in {"dwcg_pure", "dwcg_saginfe"}:
                        self.assertEqual(record.dwcg_metadata["pricing_worker_count"], 1)
                        self.assertFalse(record.dwcg_metadata["parallel_pricing_enabled"])
                        self.assertEqual(record.dwcg_metadata["solver_threads_per_worker"], 1)

    def test_temporal_tiny_deterministic_repeats_are_stable(self):
        instance = _temporal_tiny_instance(5)
        first = _run(instance, 5, "dwcg_pure")
        second = _run(instance, 5, "dwcg_pure")
        self.assertIsNone(first.error_type, first.error_message)
        self.assertIsNone(second.error_type, second.error_message)
        self.assertEqual(first.accepted_flow_count, second.accepted_flow_count)
        self.assertEqual(
            first.dwcg_metadata["final_ordered_column_pool_fingerprint"],
            second.dwcg_metadata["final_ordered_column_pool_fingerprint"],
        )


if __name__ == "__main__":
    unittest.main()
