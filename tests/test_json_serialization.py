import json
import math
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
import unittest

from calibration_runner import sha256_fingerprint, to_jsonable
from formal_runner import write_json_atomic
from formal_schema import formal_plan_from_dict


ROOT = Path(__file__).resolve().parents[1]
SMOKE_EASY_CONFIG = ROOT / "configs" / "pilot" / "direct_ilp_acceptance_cold_smoke_easy_v3.json"


class Color(Enum):
    RED = "red"


@dataclass(frozen=True)
class NestedPayload:
    path: Path
    count: int


class JsonSerializationTest(unittest.TestCase):
    def assert_json_dumpable(self, payload):
        converted = to_jsonable(payload)
        json.dumps(converted, sort_keys=True)
        return converted

    def test_paths_are_serialized_recursively(self):
        absolute_path = Path("C:/example/workspace") if os.name == "nt" else Path("/tmp/example")
        payload = {
            Path("relative/key"): {
                "relative": Path("relative/path"),
                "absolute": absolute_path,
                "pure_windows": PureWindowsPath("C:/example/workspace"),
                "pure_posix": PurePosixPath("/tmp/example"),
                "nested": [Path("nested/path"), {"again": Path("another/path")}],
            }
        }
        converted = self.assert_json_dumpable(payload)
        key = str(Path("relative/key"))
        self.assertIn(key, converted)
        self.assertEqual(converted[key]["relative"], str(Path("relative/path")))
        self.assertEqual(converted[key]["absolute"], str(absolute_path))
        self.assertIn("workspace", converted[key]["pure_windows"])
        self.assertEqual(converted[key]["pure_posix"], "/tmp/example")

    def test_existing_supported_types_still_serialize(self):
        payload = {
            "dataclass": NestedPayload(Path("inside/dataclass"), 3),
            "tuple": (1, Path("inside/tuple")),
            "set": {Path("b"), Path("a")},
            "finite_float": 1.23456789012345,
            "nan": math.nan,
            "enum": Color.RED,
        }
        converted = self.assert_json_dumpable(payload)
        self.assertEqual(converted["dataclass"]["path"], str(Path("inside/dataclass")))
        self.assertEqual(converted["tuple"][1], str(Path("inside/tuple")))
        self.assertEqual(converted["set"], [str(Path("a")), str(Path("b"))])
        self.assertIsNone(converted["nan"])
        self.assertEqual(converted["enum"], "red")

    def test_numpy_types_serialize_when_numpy_is_available(self):
        try:
            import numpy as np
        except Exception:
            self.skipTest("numpy is not installed")
        payload = {"scalar": np.int64(7), "array": np.array([Path("x"), Path("y")], dtype=object)}
        converted = self.assert_json_dumpable(payload)
        self.assertEqual(converted["scalar"], 7)
        self.assertEqual(converted["array"], [str(Path("x")), str(Path("y"))])

    def test_direct_ilp_smoke_manifest_payload_writes_with_path_cli_arguments(self):
        plan = formal_plan_from_dict(json.loads(SMOKE_EASY_CONFIG.read_text(encoding="utf-8")))
        plan_fingerprint = sha256_fingerprint(plan)
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "pilot_outputs" / "direct_ilp_cold_smoke_easy_test"
            manifest = {
                "formal_framework_version": "v3_formal_framework",
                "run_category": "integration_pilot",
                "integration_only": True,
                "git_commit": "synthetic",
                "git_dirty": False,
                "cli_arguments": {
                    "config": SMOKE_EASY_CONFIG,
                    "output_dir": output_dir,
                    "scales": None,
                    "loads": None,
                    "seeds": None,
                    "resume": False,
                    "overwrite": False,
                },
                "run_filters": {
                    "algorithms": ["direct_ilp_acceptance_cold"],
                    "scales": None,
                    "loads": None,
                    "seeds": None,
                    "variants": None,
                },
                "plan_fingerprint": plan_fingerprint,
                "allow_dirty": False,
            }
            path = output_dir / "manifest.json"
            write_json_atomic(path, manifest)
            written = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(written["cli_arguments"]["config"], str(SMOKE_EASY_CONFIG))
            self.assertEqual(written["cli_arguments"]["output_dir"], str(output_dir))
            self.assertEqual(written["plan_fingerprint"], plan_fingerprint)


if __name__ == "__main__":
    unittest.main()
