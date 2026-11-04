import unittest

from column_pool import ColumnPool
from objective import ObjectiveSpec
from restricted_master_problem import solve_rmp
from tests_gurobi.test_utils import gurobi_test, tiny_flow, tiny_network, tiny_path


@gurobi_test
class FinalRmpScopeTest(unittest.TestCase):
    def test_final_rmp_is_restricted_pool_not_global_original(self):
        network = tiny_network()
        flow = tiny_flow()
        pool = ColumnPool()
        pool.initialize_empty_paths([flow])
        pool.add_path(tiny_path(network, flow, pool.generate_path_id()))
        result = solve_rmp(pool.get_all_paths(), [flow], network, ObjectiveSpec(network.resource_weight), is_integer=True)
        self.assertTrue(result.optimality.restricted_pool_integer_optimal)
        self.assertFalse(result.optimality.global_original_integer_optimal)


if __name__ == "__main__":
    unittest.main()
