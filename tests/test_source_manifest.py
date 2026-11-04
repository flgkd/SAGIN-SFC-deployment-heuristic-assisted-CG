"""Solver-independent checks for the current-file integrity inventory."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import unittest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = "docs/source-manifest.json"


class SourceManifestTest(unittest.TestCase):
    def test_manifest_schema_and_paths(self):
        manifest = json.loads((ROOT / MANIFEST_PATH).read_bytes())
        self.assertEqual(set(manifest), {"description", "files"})
        self.assertIn("current repository", manifest["description"])
        entries = manifest["files"]
        self.assertTrue(entries)
        paths = [entry["path"] for entry in entries]
        self.assertEqual(len(paths), len(set(paths)))
        self.assertNotIn(MANIFEST_PATH, paths)
        for entry in entries:
            with self.subTest(path=entry["path"]):
                self.assertEqual(set(entry), {"path", "sha256"})
                path = PurePosixPath(entry["path"])
                self.assertFalse(path.is_absolute())
                self.assertNotIn("..", path.parts)
                self.assertEqual(path.as_posix(), entry["path"])
                self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue((ROOT / path).is_file())

    def test_all_current_file_hashes_match(self):
        manifest = json.loads((ROOT / MANIFEST_PATH).read_bytes())
        for entry in manifest["files"]:
            with self.subTest(path=entry["path"]):
                actual = hashlib.sha256((ROOT / entry["path"]).read_bytes()).hexdigest()
                self.assertEqual(actual, entry["sha256"],
                                 "Refresh all manifest hashes after final edits to listed files.")
