import unittest

from objective import (
    ObjectiveSpec,
    accepted_path_value,
    compute_path_physical_cost,
    compute_usage_physical_cost,
    CapacityDenominators,
)
from data_model import Flow, Network, Path


class ObjectiveBoundTest(unittest.TestCase):
    def test_full_capacity_usage_cost_is_at_most_one(self):
        spec = ObjectiveSpec(resource_weight=0.4, acceptance_reward=2.0)
        denoms = CapacityDenominators(
            total_processing_capacity=10.0,
            total_communication_capacity=20.0,
        )
        cost = compute_usage_physical_cost(
            node_usage={(0, 0): 10.0},
            edge_usage={(0, 1, 0): 20.0},
            flow_demand=1.0,
            denominators=denoms,
            objective_spec=spec,
        )
        self.assertLessEqual(cost, 1.0)
        self.assertAlmostEqual(cost, 1.0)

    def test_acceptance_reward_two_preserves_acceptance_priority(self):
        spec = ObjectiveSpec(resource_weight=0.5, acceptance_reward=2.0)
        one_more_accepted_worst_cost = accepted_path_value(1.0, spec)
        one_fewer_accepted_best_cost = 0.0
        self.assertGreater(one_more_accepted_worst_cost, one_fewer_accepted_best_cost)

    def test_storage_edges_have_zero_physical_communication_cost(self):
        network = Network()
        network.set_nodes(2)
        network.set_time_slots(2)
        for node in [0, 1]:
            for time in [0, 1]:
                network.add_time_node((node, time), 10.0)
        network.add_comm_time_edge((0, 0), (1, 0), 10.0)
        network.add_storage_time_edge((0, 0))

        flow = Flow(1, 1, 0, 0, 0, 1, ["A"], {"A": 0.0}, 1.0)
        storage_path = Path(
            flow_id=1,
            path_id=1,
            path_is_empty=False,
            path_stage_placement={0: (0, 1)},
            path_segment_routing={0: [((0, 0), (0, 1))], 1: []},
        )
        storage_path.finalize_usage(flow=flow)
        self.assertEqual(
            compute_path_physical_cost(network, flow, storage_path, ObjectiveSpec(0.5)),
            0.0,
        )


if __name__ == "__main__":
    unittest.main()
