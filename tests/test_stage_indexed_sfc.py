import unittest

from data_model import Flow, Path


class StageIndexedSfcTest(unittest.TestCase):
    def test_repeated_vnf_types_have_independent_stage_indices(self):
        flow = Flow(
            flow_id=10,
            flow_len=3,
            flow_src_node=0,
            flow_src_time=0,
            flow_dst_node=1,
            flow_dst_max_time=1,
            flow_sfc=["A", "B", "A"],
            flow_vnf_cost={"A": 2.0, "B": 3.0},
            flow_demand=1.0,
        )
        self.assertEqual([s.stage_index for s in flow.processing_stages], [0, 1, 2])
        self.assertEqual([s.vnf_type for s in flow.processing_stages], ["A", "B", "A"])
        self.assertEqual(flow.get_processing_coefficient(0), 2.0)
        self.assertEqual(flow.get_processing_coefficient(2), 2.0)

        path = Path(
            flow_id=10,
            path_id=0,
            path_is_empty=False,
            path_stage_placement={0: (0, 0), 1: (1, 0), 2: (0, 1)},
            path_segment_routing={},
        )
        path.finalize_usage(flow=flow)
        self.assertEqual(path.path_stage_placement[0], (0, 0))
        self.assertEqual(path.path_stage_placement[2], (0, 1))
        self.assertEqual(path.path_node_usage[(0, 0)], 2.0)
        self.assertEqual(path.path_node_usage[(0, 1)], 2.0)


if __name__ == "__main__":
    unittest.main()

