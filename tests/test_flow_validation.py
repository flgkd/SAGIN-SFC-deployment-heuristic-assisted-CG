import unittest

from data_model import Flow
from tests.test_validation_fixtures import repeated_flow, tiny_network
from validation import validate_flow, validate_flows, validate_unique_flow_ids


class FlowValidationTest(unittest.TestCase):
    def test_repeated_vnf_flow_passes(self):
        validate_flow(repeated_flow(), tiny_network())

    def test_duplicate_flow_id_fails(self):
        flows = [repeated_flow(7), repeated_flow(7)]
        with self.assertRaisesRegex(ValueError, "Duplicate flow ID 7"):
            validate_unique_flow_ids(flows)

    def test_source_after_deadline_fails(self):
        flow = Flow(1, 1, 0, 2, 2, 1, ["A"], {"A": 1.0}, 1.0)
        with self.assertRaisesRegex(ValueError, "deadline"):
            validate_flow(flow)

    def test_validate_flows_checks_all(self):
        validate_flows([repeated_flow(1), repeated_flow(2)])


if __name__ == "__main__":
    unittest.main()
