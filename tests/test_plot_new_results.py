import json
from pathlib import Path
import tempfile
import unittest

from scripts.plot.plot_new_results import load_groups


def row(metadata=None, termination_status="OPTIMAL"):
    return {
        "key": {
            "scenario_id": "s40_medium",
            "scale": "S40",
            "load_label": "Medium",
            "time_slots": 8,
            "algorithm": "dwcg_saginfe_pro",
            "formal_seed": 0,
        },
        "total_flow_count": 160,
        "accepted_flow_count": 150,
        "total_end_to_end_runtime_seconds": 400.0,
        "path_validity": True,
        "capacity_validity": True,
        "termination_status": termination_status,
        "full_instance_fingerprint": "same-instance",
        "dwcg_metadata": metadata or {},
    }


class PlotNewResultsValidityTest(unittest.TestCase):
    def load_one(self, record):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            return load_groups([path])

    def test_independent_final_rmp_time_cap_is_not_an_overall_deadline(self):
        groups = self.load_one(row(
            {"actual_final_rmp_time_limit_seconds": 300.0},
            termination_status="TIME_LIMIT_WITH_INCUMBENT",
        ))
        self.assertIn(("S40", "Medium", 8, "default"), groups)

    def test_interrupted_execution_is_rejected(self):
        for metadata in (
            {"hard_wall_clock_limit_reached": True},
            {"internal_phase_limit_reached": True},
            {"termination_trigger": "GLOBAL_HARD_TIME_LIMIT"},
        ):
            with self.subTest(metadata=metadata):
                with self.assertRaisesRegex(ValueError, "Interrupted execution"):
                    self.load_one(row(metadata))


if __name__ == "__main__":
    unittest.main()
