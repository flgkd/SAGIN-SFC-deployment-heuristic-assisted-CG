import unittest

from config_schema import (
    FlowGenerationConfig,
    InstanceConfig,
    TopologyConfig,
    get_candidate_load_profile,
    get_candidate_scale,
)
from instance_builder import build_instance


def flow_structure(instance):
    return [
        (
            meta["source_node"],
            meta["destination_node"],
            meta["arrival_time"],
            meta["deadline_time"],
            tuple(meta["vnf_label_sequence"]),
            tuple(meta["processing_coefficient_sequence"]),
            meta["base_demand"],
        )
        for meta in instance.flow_metadata["per_flow"].values()
    ]


class InstancePairedLoadsTest(unittest.TestCase):
    def test_paired_loads_keep_topology_and_base_flow_structure(self):
        topology_config = TopologyConfig(get_candidate_scale("S20"), 6, 0)
        low = build_instance(InstanceConfig(
            topology_config,
            FlowGenerationConfig(seed=0, demand_multiplier=get_candidate_load_profile("Low"), load_profile_name="Low"),
            scenario_seed=100,
        ))
        high = build_instance(InstanceConfig(
            topology_config,
            FlowGenerationConfig(seed=0, demand_multiplier=get_candidate_load_profile("High"), load_profile_name="High"),
            scenario_seed=100,
        ))
        self.assertEqual(low.topology_metadata, high.topology_metadata)
        self.assertEqual(flow_structure(low), flow_structure(high))
        first_id = next(iter(low.flow_metadata["per_flow"]))
        self.assertAlmostEqual(
            high.flow_metadata["per_flow"][first_id]["actual_demand"]
            / low.flow_metadata["per_flow"][first_id]["actual_demand"],
            1.5,
        )


if __name__ == "__main__":
    unittest.main()
