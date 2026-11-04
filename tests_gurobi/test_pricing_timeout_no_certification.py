import unittest

from main_solver import GBSolver
from tests_gurobi.test_utils import gurobi_test, tiny_flow, tiny_network


@gurobi_test
class PricingTimeoutNoCertificationTest(unittest.TestCase):
    def test_forced_incomplete_exact_pricing_cannot_certify(self):
        network = tiny_network()
        flow = tiny_flow()
        solver = GBSolver(network, [flow])
        solver.solve(use_parallel=False, sp_strategy="exact", global_time_limit=0.001, solver_threads=1)
        self.assertFalse(solver.stats["pricing_convergence_certified"])
        self.assertFalse(solver.stats["converged"])
        self.assertNotEqual(solver.stats.get("final_solution_source"), "FINAL_INTEGER_RMP")

    def test_incomplete_exact_pricing_records_no_certification(self):
        network = tiny_network()
        flow = tiny_flow()
        solver = GBSolver(network, [flow])

        def timeout_pricing(flow, network, duals, objective_spec, stop_early, **kwargs):
            return None, None, None, {
                "hit_time_limit": True,
                "exact_pricing_optimal": False,
                "positive_reduced_cost_found": False,
            }

        solver.solve(
            use_parallel=False,
            sp_strategy="exact",
            exact_sp_func=timeout_pricing,
            max_iterations=2,
            solver_threads=1,
        )
        self.assertFalse(solver.stats["pricing_convergence_certified"])
        self.assertFalse(solver.stats["converged"])
        self.assertGreater(solver.stats["exact_pricing_incomplete_calls"], 0)
        self.assertGreater(solver.stats["exact_pricing_time_limit_calls"], 0)
        self.assertNotEqual(solver.stats.get("cg_status"), "CONVERGED")


if __name__ == "__main__":
    unittest.main()
