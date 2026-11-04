import unittest

from ILP_solver import solve_ilp
from objective import ObjectiveSpec
from tests_gurobi.test_utils import gurobi_test, tiny_flow, tiny_network, tiny_path


@gurobi_test
class DirectIlpWarmStartTest(unittest.TestCase):
    def test_canonical_warm_start_provides_incumbent_without_changing_result(self):
        network = tiny_network()
        flow = tiny_flow()
        objective_spec = ObjectiveSpec(network.resource_weight)
        warm_path = tiny_path(network, flow)

        cold = solve_ilp(network, [flow], objective_spec, threads=1)
        warm = solve_ilp(network, [flow], objective_spec, threads=1, warm_start_paths={flow.flow_id: warm_path})

        self.assertTrue(warm.has_incumbent)
        self.assertGreater(warm.accepted_count, 0)
        self.assertEqual(cold.accepted_count, warm.accepted_count)
        self.assertAlmostEqual(cold.scalarized_objective, warm.scalarized_objective)
        self.assertTrue(warm.result.metadata["warm_start"]["supplied"])
        self.assertEqual(warm.result.metadata["warm_start"]["accepted_count"], 1)

    def test_all_flow_warm_start_can_certify_acceptance_without_full_objective(self):
        network = tiny_network()
        flow = tiny_flow()
        objective_spec = ObjectiveSpec(network.resource_weight)
        warm_path = tiny_path(network, flow)

        warm = solve_ilp(
            network,
            [flow],
            objective_spec,
            threads=1,
            warm_start_paths={flow.flow_id: warm_path},
            stop_when_acceptance_certified=True,
        )

        self.assertEqual(warm.accepted_count, 1)
        self.assertFalse(warm.global_original_integer_optimal)
        self.assertEqual(warm.status_name, "ACCEPTANCE_CERTIFIED")
        self.assertFalse(warm.result.metadata["acceptance_early_stop"]["solver_invoked"])

    def test_default_cold_solve_does_not_use_acceptance_early_stop(self):
        network = tiny_network()
        flow = tiny_flow()
        result = solve_ilp(network, [flow], ObjectiveSpec(network.resource_weight), threads=1)
        self.assertFalse(result.result.metadata["acceptance_early_stop"]["enabled"])


if __name__ == "__main__":
    unittest.main()
