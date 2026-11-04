import unittest

from column_pool import ColumnPool
from data_model import Flow
from objective import ObjectiveSpec, empty_path_value


class EmptyPathObjectiveTest(unittest.TestCase):
    def test_empty_path_objective_is_zero(self):
        spec = ObjectiveSpec(resource_weight=0.5)
        self.assertEqual(empty_path_value(spec), 0.0)

    def test_column_pool_empty_paths_have_zero_cost(self):
        flow = Flow(30, 1, 0, 0, 1, 1, ["A"], {"A": 1.0}, 1.0)
        pool = ColumnPool()
        pool.initialize_empty_paths([flow])
        [path] = pool.get_all_paths()
        self.assertTrue(path.path_is_empty)
        self.assertEqual(path.path_cost, 0.0)


if __name__ == "__main__":
    unittest.main()

