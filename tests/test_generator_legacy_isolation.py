import unittest


class GeneratorLegacyIsolationTest(unittest.TestCase):
    def test_active_generator_does_not_import_legacy_reference(self):
        with open("sagin_generator.py", "r", encoding="utf-8") as handle:
            content = handle.read()
        self.assertNotIn("_legacy_reference", content)


if __name__ == "__main__":
    unittest.main()
