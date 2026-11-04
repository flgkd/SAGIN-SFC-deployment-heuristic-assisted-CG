import time
import unittest

from data_model import Flow
from formal_runner import direct_ilp_acceptance_configured_e2e_limit
from formal_schema import FormalAlgorithmConfig
from ILP_solver import solve_ilp
from main_solver import GBSolver
from tests_gurobi.test_utils import gurobi_test, tiny_flow, tiny_network, tiny_path


@gurobi_test
class FormalRuntimeExecutionTest(unittest.TestCase):
    def test_cg_has_no_phase_deadline_and_keeps_independent_integer_cap(self):
        network = tiny_network()
        flows = [Flow(i, 1, 0, 0, 1, 0, ["A"], {"A": 1.0}, 6.0) for i in (1, 2)]
        paths = [tiny_path(network, flow, flow.flow_id) for flow in flows]
        solver = GBSolver(network, flows)
        solver.solve(
            use_parallel=False, custom_initial_paths=[{
                "flow_id": flow.flow_id,
                "stage_placement": path.path_stage_placement,
                "segment_routing": path.path_segment_routing,
                "physical_cost": path.path_cost,
            } for flow, path in zip(flows, paths)],
            sp_strategy="exact", max_iterations=12, max_exact_pricing_calls=26,
            final_rmp_reserve_seconds=300.0, final_rmp_time_cap_seconds=300.0,
            solver_threads=1,
        )
        self.assertIsNone(solver.stats["configured_hard_time_limit_seconds"])
        self.assertIsNone(solver.stats["cg_phase_budget"])
        self.assertIsNone(solver.stats["final_rmp_reserved_seconds"])
        self.assertEqual(solver.stats["actual_final_rmp_time_limit_seconds"], 300.0)
        self.assertFalse(solver.stats["internal_phase_limit_reached"])
        self.assertFalse(solver.stats["hard_wall_clock_limit_reached"])
        self.assertTrue(solver.stats["pricing_convergence_certified"])

    def test_cold_ilp_default_reaches_solver_without_time_limit(self):
        config = FormalAlgorithmConfig(algorithm="direct_ilp_acceptance_cold")
        solution = solve_ilp(
            tiny_network(), [tiny_flow()], threads=1, objective_mode="acceptance_only", enforce_simple_routes=True,
            e2e_start_time=time.perf_counter(),
            e2e_time_limit_seconds=direct_ilp_acceptance_configured_e2e_limit(config),
        )
        self.assertEqual(solution.accepted_count, 1)
        self.assertFalse(solution.hit_time_limit)
        self.assertIsNone(solution.result.metadata["effective_solver_time_limit_seconds"])


if __name__ == "__main__":
    unittest.main()
