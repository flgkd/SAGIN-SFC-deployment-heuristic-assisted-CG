import unittest

from data_model import Flow, Network
from formal_runner import run_dwcg_formal_adapter, run_saginfe_formal_adapter
from formal_schema import FormalAlgorithmConfig, FormalRunKey
from instance_builder import SaginInstance
from tests_gurobi.test_utils import gurobi_test


def _network():
    network = Network()
    network.set_nodes(2)
    network.set_time_slots(6)
    for node in (0, 1):
        for time in range(6):
            network.add_time_node((node, time), 20.0)
    for time in range(6):
        network.add_comm_time_edge((0, time), (1, time), 20.0)
    for node in (0, 1):
        for time in range(5):
            network.add_storage_time_edge((node, time))
    return network


def _instance(profile, slack):
    flow = Flow(1, 1, 0, 0, 1, slack, ["A"], {"A": 1.0}, 1.0)
    return SaginInstance(
        network=_network(),
        flows=(flow,),
        topology_metadata={
            "physical_topology": {"base_physical_links": [(0, 1)]},
            "snapshots": [{"time_slot": time, "active_edges": [(0, 1)]} for time in range(6)],
        },
        flow_metadata={
            "per_flow": {
                flow.flow_id: {
                    "flow_id": flow.flow_id,
                    "traffic_category": "same_region_ground",
                    "source_node": 0,
                    "destination_node": 1,
                    "source_layer": "Ground",
                    "destination_layer": "Ground",
                    "source_ground_region": 0,
                    "destination_ground_region": 1,
                    "arrival_time": 0,
                    "deadline_time": slack,
                    "slack": slack,
                    "deadline_profile": profile,
                    "base_demand": 1.0,
                    "demand_multiplier": 1.0,
                    "actual_demand": 1.0,
                    "processing_stage_count": 1,
                    "total_chain_length": 3,
                    "vnf_label_sequence": ["A"],
                    "processing_coefficient_sequence": [1.0],
                    "contains_repeated_vnf_type": False,
                },
            },
            "aggregate": {},
        },
        instance_metadata={
            "topology_seed": 0,
            "demand_multiplier": 1.0,
            "paired_deadline_sensitivity": {
                "deadline_profile": profile,
                "deadline_uniform_fingerprint": "tiny",
                "latest_source_time": 0,
            },
        },
    )


def _dwcg_config():
    return FormalAlgorithmConfig(
        algorithm="dwcg_saginfe",
        enabled=True,
        time_limit=30.0,
        threads=1,
        mip_gap=0.0,
        use_saginfe_initialization=True,
        deterministic_mode=True,
        pricing_worker_count=1,
        pricing_strategy="heuristic_then_exact",
        max_cg_iterations=4,
        max_exact_pricing_calls=40,
        hard_time_limit_seconds=30.0,
    )


@gurobi_test
class DeadlineSensitivityVariantsGurobiTest(unittest.TestCase):
    def test_deadline_variants_solve_and_windows_are_monotonic(self):
        records = []
        windows = []
        for profile, slack in (("RealTime", 1), ("Balanced", 3), ("Flexible", 5)):
            instance = _instance(profile, slack)
            windows.append((instance.flows[0].flow_src_time_node[1], instance.flows[0].flow_dst_max_time))
            for algorithm, config in (
                ("saginfe", FormalAlgorithmConfig(algorithm="saginfe", enabled=True, threads=1)),
                ("dwcg_saginfe", _dwcg_config()),
            ):
                key = FormalRunKey(f"tiny_{profile}", "TINY", 6, 0, "Tiny", 1.0, algorithm)
                context = {
                    "key": key,
                    "git_commit": "",
                    "configuration_snapshot": {"algorithm": algorithm},
                    "saginfe_cache": {},
                }
                record = (
                    run_saginfe_formal_adapter(instance, config, context)
                    if algorithm == "saginfe"
                    else run_dwcg_formal_adapter(instance, config, context)
                )
                self.assertIsNone(record.error_type, record.error_message)
                self.assertTrue(record.path_validity)
                self.assertTrue(record.capacity_validity)
                self.assertFalse(record.objective_globally_optimal)
                records.append(record)
        self.assertLessEqual(windows[0][1] - windows[0][0], windows[1][1] - windows[1][0])
        self.assertLessEqual(windows[1][1] - windows[1][0], windows[2][1] - windows[2][0])
        for record in records:
            if record.key.algorithm == "dwcg_saginfe":
                self.assertFalse(record.dwcg_metadata["global_original_integer_optimal"])


if __name__ == "__main__":
    unittest.main()
