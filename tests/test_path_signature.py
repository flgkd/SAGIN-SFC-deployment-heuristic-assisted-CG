import unittest

from data_model import Path


class PathSignatureTest(unittest.TestCase):
    def test_same_edge_set_different_order_has_different_signature(self):
        edges_a = [((0, 0), (1, 0)), ((1, 0), (2, 0))]
        edges_b = [((1, 0), (2, 0)), ((0, 0), (1, 0))]
        p1 = Path(
            flow_id=10,
            path_id=1,
            path_is_empty=False,
            path_stage_placement={0: (1, 0)},
            path_segment_routing={0: edges_a},
        )
        p2 = Path(
            flow_id=10,
            path_id=2,
            path_is_empty=False,
            path_stage_placement={0: (1, 0)},
            path_segment_routing={0: edges_b},
        )
        p1.build_signature()
        p2.build_signature()
        self.assertNotEqual(p1.signature, p2.signature)


if __name__ == "__main__":
    unittest.main()

