import unittest

from column_pool import ColumnPool
from data_model import Flow, Path


class NonContiguousFlowIdsTest(unittest.TestCase):
    def test_non_contiguous_flow_ids_work_in_pool_and_signatures(self):
        flows = [
            Flow(10, 1, 0, 0, 1, 1, ["A"], {"A": 1.0}, 1.0),
            Flow(30, 1, 0, 0, 1, 1, ["A"], {"A": 1.0}, 1.0),
        ]
        pool = ColumnPool()
        pool.initialize_empty_paths(flows)
        path = Path(
            flow_id=30,
            path_id=pool.generate_path_id(),
            path_is_empty=False,
            path_stage_placement={0: (1, 0)},
            path_segment_routing={0: [((0, 0), (1, 0))]},
            path_cost=0.25,
        )
        path.finalize_usage(flow=flows[1])
        self.assertTrue(pool.add_path(path))
        self.assertEqual(len(pool.get_paths_by_flow(10)), 1)
        self.assertEqual(len(pool.get_paths_by_flow(30)), 2)
        self.assertEqual(path.signature[0], 30)


if __name__ == "__main__":
    unittest.main()

