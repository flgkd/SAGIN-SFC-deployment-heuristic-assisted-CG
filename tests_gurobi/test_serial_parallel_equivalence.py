import unittest

from main_solver import GBSolver
from tests_gurobi.test_utils import gurobi_test, tiny_flow, tiny_network


@gurobi_test
class SerialParallelEquivalenceTest(unittest.TestCase):
    def test_serial_parallel_column_signatures_agree(self):
        network = tiny_network()
        flow = tiny_flow()
        serial = GBSolver(network, [flow])
        parallel = GBSolver(network, [flow])
        serial.solve(use_parallel=False, sp_strategy="exact", max_iterations=2, solver_threads=1)
        parallel.solve(use_parallel=True, parallel_workers=1, sp_strategy="exact", max_iterations=2, solver_threads=1)
        self.assertEqual(
            [p.signature for p in serial.pool.get_all_paths()],
            [p.signature for p in parallel.pool.get_all_paths()],
        )


if __name__ == "__main__":
    unittest.main()
