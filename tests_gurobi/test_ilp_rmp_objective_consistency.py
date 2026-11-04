import unittest

from ILP_solver import solve_ilp
from column_pool import ColumnPool
from objective import ObjectiveSpec
from restricted_master_problem import solve_rmp
from tests_gurobi.test_utils import gurobi_test, tiny_flow, tiny_network, tiny_path


@gurobi_test
class IlpRmpObjectiveConsistencyTest(unittest.TestCase):
    def test_complete_integer_master_matches_direct_ilp(self):
        network = tiny_network()
        flow = tiny_flow()
        spec = ObjectiveSpec(network.resource_weight)
        ilp = solve_ilp(network, [flow], spec, threads=1)
        pool = ColumnPool()
        pool.initialize_empty_paths([flow])
        pool.add_path(tiny_path(network, flow, pool.generate_path_id()))
        rmp = solve_rmp(pool.get_all_paths(), [flow], network, spec, is_integer=True, threads=1)
        self.assertEqual(rmp.accepted_count, ilp.accepted_count)
        self.assertAlmostEqual(rmp.normalized_physical_cost, ilp.normalized_physical_cost)


if __name__ == "__main__":
    unittest.main()
