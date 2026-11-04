from pathlib import Path
import unittest


class CalibrationLegacyIsolationTest(unittest.TestCase):
    def test_calibration_modules_do_not_import_legacy_reference(self):
        for filename in ("calibration_schema.py", "calibration_runner.py", "calibration_analysis.py", "run_calibration.py"):
            self.assertNotIn("_legacy_reference", Path(filename).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
