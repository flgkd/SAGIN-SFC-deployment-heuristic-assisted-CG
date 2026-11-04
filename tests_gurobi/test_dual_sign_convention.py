import unittest

from column_pool import ColumnPool
from objective import ObjectiveSpec
from restricted_master_problem import solve_rmp
from tests_gurobi.test_utils import gurobi_test, tiny_flow, tiny_network, tiny_path


@gurobi_test
class DualSignConventionTest(unittest.TestCase):
    def test_positive_reduced_cost_column_improves_rmp_objective(self):
        network = tiny_network()
        flow = tiny_flow()
        spec = ObjectiveSpec(network.resource_weight)
        pool = ColumnPool()
        pool.initialize_empty_paths([flow])
        before = solve_rmp(pool.get_all_paths(), [flow], network, spec, is_integer=False, threads=1)
        pool.add_path(tiny_path(network, flow, pool.generate_path_id()))
        after = solve_rmp(pool.get_all_paths(), [flow], network, spec, is_integer=False, threads=1)
        self.assertGreater(after.scalarized_objective, before.scalarized_objective)


if __name__ == "__main__":
    unittest.main()
