import unittest

from data_model import Path
from tests.test_validation_fixtures import repeated_flow, tiny_network, valid_repeated_path
from validation import validate_path


class PathValidationTest(unittest.TestCase):
    def test_valid_repeated_vnf_path_passes(self):
        network = tiny_network()
        flow = repeated_flow()
        validate_path(network, flow, valid_repeated_path(network, flow))

    def test_missing_stage_fails(self):
        network = tiny_network()
        flow = repeated_flow()
        path = valid_repeated_path(network, flow)
        del path.path_stage_placement[1]
        with self.assertRaisesRegex(ValueError, "missing=\\[1\\]"):
            validate_path(network, flow, path)

    def test_missing_segment_fails(self):
        network = tiny_network()
        flow = repeated_flow()
        path = valid_repeated_path(network, flow)
        del path.path_segment_routing[2]
        with self.assertRaisesRegex(ValueError, "segment indices"):
            validate_path(network, flow, path)

    def test_disconnected_segment_fails(self):
        network = tiny_network()
        flow = repeated_flow()
        path = valid_repeated_path(network, flow)
        path.path_segment_routing[0] = [((1, 0), (2, 0))]
        path.finalize_usage(flow=flow)
        with self.assertRaisesRegex(ValueError, "continuous|ends"):
            validate_path(network, flow, path)

    def test_backward_time_edge_fails(self):
        network = tiny_network()
        flow = repeated_flow()
        bad_edge = ((1, 1), (1, 0))
        network.time_edges.append(bad_edge)
        network.time_edge_capacity[bad_edge] = float("inf")
        path = valid_repeated_path(network, flow)
        path.path_segment_routing[1] = [bad_edge]
        path.finalize_usage(flow=flow)
        with self.assertRaisesRegex(ValueError, "backward|semantics|continuous"):
            validate_path(network, flow, path)

    def test_unknown_network_edge_fails(self):
        network = tiny_network()
        flow = repeated_flow()
        path = valid_repeated_path(network, flow)
        path.path_segment_routing[0] = [((0, 0), (9, 0))]
        path.finalize_usage(flow=flow)
        with self.assertRaisesRegex(ValueError, "unknown edge"):
            validate_path(network, flow, path)

    def test_incorrect_stored_usage_fails(self):
        network = tiny_network()
        flow = repeated_flow()
        path = valid_repeated_path(network, flow)
        path.path_node_usage[(1, 0)] = 99.0
        with self.assertRaisesRegex(ValueError, "node usage"):
            validate_path(network, flow, path)

    def test_path_cost_mismatch_fails(self):
        network = tiny_network()
        flow = repeated_flow()
        path = valid_repeated_path(network, flow)
        path.path_cost += 1.0
        with self.assertRaisesRegex(ValueError, "cost mismatch"):
            validate_path(network, flow, path)


if __name__ == "__main__":
    unittest.main()
