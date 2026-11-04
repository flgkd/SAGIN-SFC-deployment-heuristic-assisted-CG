import unittest
from pprint import pformat

from main_solver import GBSolver
from tests_gurobi.test_utils import gurobi_test, tiny_flow, tiny_network, tiny_path


def _pricing_iteration_records(stats):
    records = []
    for record in stats.get("pricing_iteration_stats", []):
        records.append({
            "iteration": record.iteration,
            "expected_exact_calls": record.expected_exact_calls,
            "launched_exact_calls": record.launched_exact_calls,
            "completed_exact_calls": record.completed_exact_calls,
            "optimal_exact_calls": record.optimal_exact_calls,
            "positive_reduced_cost_calls": record.positive_reduced_cost_calls,
            "columns_added": record.columns_added,
            "rejected_positive_columns": (
                "POSITIVE_COLUMN" in (record.certification_reason or "")
                or "positive column was rejected" in (record.certification_reason or "")
            ),
            "certification_possible": record.certification_possible,
            "certification_reason": record.certification_reason,
        })
    return records


def _diagnostic(solver):
    stats = solver.stats
    return pformat({
        "pricing_convergence_certified": stats.get("pricing_convergence_certified"),
        "pricing_converged": stats.get("converged"),
        "termination_reason": stats.get("termination_reason"),
        "iterations": stats.get("iterations"),
        "final_pool_size": len(solver.pool),
        "pricing_iteration_stats": _pricing_iteration_records(stats),
        "invalid_column_count": stats.get("invalid_column_count"),
        "cost_mismatch_count": stats.get("cost_mismatch_count"),
        "duplicate_rejection_count": stats.get("duplicate_rejection_count"),
        "extraction_failure_count": stats.get("extraction_failure_count"),
        "last_rejected_positive_column": stats.get("last_rejected_positive_column"),
        "final_rmp_status": stats.get("final_rmp_status"),
        "final_rmp_status_name": stats.get("final_rmp_status_name"),
    })


@gurobi_test
class CgTinyConvergenceTest(unittest.TestCase):
    def test_tiny_cg_certifies_when_exact_pricing_has_no_positive_column(self):
        network = tiny_network()
        flow = tiny_flow()
        path = tiny_path(network, flow)
        solver = GBSolver(network, [flow])
        solver.solve(use_parallel=False, custom_initial_paths=[{
            "flow_id": flow.flow_id,
            "stage_placement": path.path_stage_placement,
            "segment_routing": path.path_segment_routing,
            "physical_cost": path.path_cost,
        }], sp_strategy="exact", max_iterations=3, solver_threads=1)
        self.assertTrue(
            solver.stats["pricing_convergence_certified"],
            msg=_diagnostic(solver),
        )


if __name__ == "__main__":
    unittest.main()
