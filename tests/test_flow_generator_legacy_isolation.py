from pathlib import Path
import unittest


class FlowGeneratorLegacyIsolationTest(unittest.TestCase):
    def test_active_flow_modules_do_not_import_legacy_reference(self):
        for filename in ("flow_generator.py", "instance_builder.py"):
            text = Path(filename).read_text()
            self.assertNotIn("_legacy_reference", text)


if __name__ == "__main__":
    unittest.main()
