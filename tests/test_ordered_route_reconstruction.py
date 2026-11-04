import unittest

from data_model import reconstruct_ordered_route


class OrderedRouteReconstructionTest(unittest.TestCase):
    def test_ordered_chain_succeeds(self):
        edges = [((0, 0), (1, 0)), ((1, 0), (2, 0))]
        route = reconstruct_ordered_route(edges, (0, 0), (2, 0))
        self.assertEqual(route, edges)

    def test_disconnected_selection_fails(self):
        edges = [((0, 0), (1, 0)), ((3, 0), (4, 0))]
        with self.assertRaises(ValueError):
            reconstruct_ordered_route(edges, (0, 0), (1, 0))

    def test_branched_selection_fails(self):
        edges = [((0, 0), (1, 0)), ((0, 0), (2, 0))]
        with self.assertRaises(ValueError):
            reconstruct_ordered_route(edges, (0, 0), (2, 0))

    def test_cyclic_selection_fails(self):
        edges = [((0, 0), (1, 0)), ((1, 0), (0, 0))]
        with self.assertRaises(ValueError):
            reconstruct_ordered_route(edges, (0, 0), (2, 0))


if __name__ == "__main__":
    unittest.main()

