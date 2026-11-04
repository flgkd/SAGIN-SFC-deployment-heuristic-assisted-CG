"""Canonical names must preserve record identity, numerical inputs and RNG streams."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from algorithm_names import ALGORITHM_NAMES, SHORT_NAMES, algorithm_label
from deterministic_seeds import seed_bundle
from formal_runner import derive_formal_algorithm_seed_bundle, read_jsonl
from formal_schema import FormalAlgorithmConfig, formal_algorithm_from_dict
from record_compatibility import _ALIASES, normalize_record, normalize_dataframe, resolve_input_path


@pytest.mark.parametrize("saved,canonical", list(_ALIASES.items()))
def test_saved_records_keep_observations_and_execution_snapshot(tmp_path, saved, canonical):
    original = {"key": {"algorithm": saved}, "accepted_flow_count": 19,
                "total_end_to_end_runtime_seconds": 31.125,
                "configuration_snapshot": {"algorithm": saved, "settings": {"threads": 1}},
                "metadata": {saved: {"calls": 7}}}
    untouched = copy.deepcopy(original)
    path = tmp_path / "runs.jsonl"
    path.write_text(json.dumps(original) + "\n", encoding="utf-8")
    row = read_jsonl(path)[0]
    assert row["key"]["algorithm"] == canonical
    assert row["metadata"] == {canonical: {"calls": 7}}
    assert row["accepted_flow_count"] == 19
    assert row["total_end_to_end_runtime_seconds"] == 31.125
    assert row["configuration_snapshot"] == original["configuration_snapshot"]
    assert original == untouched
    assert json.loads(path.read_text(encoding="utf-8")) == original
    assert normalize_record(row) == row


@pytest.mark.parametrize("saved,canonical", list(_ALIASES.items()))
def test_algorithm_renaming_preserves_seed_streams(saved, canonical):
    key = SimpleNamespace(formal_seed=8, load_label="Medium")
    config = FormalAlgorithmConfig(algorithm=canonical)
    with patch("formal_runner.full_instance_fingerprint", return_value="fixed-instance"):
        actual = derive_formal_algorithm_seed_bundle(object(), key, config)
    assert actual == seed_bundle("fixed-instance", saved, 8, "Medium")
    config = FormalAlgorithmConfig(algorithm=canonical, algorithm_seed=123)
    assert derive_formal_algorithm_seed_bundle(object(), key, config) == seed_bundle("configured", 123)


def test_input_aliases_cannot_merge_distinct_records():
    saved, canonical = next(iter(_ALIASES.items()))
    with pytest.raises(ValueError, match="Conflicting"):
        normalize_record({saved: 1, canonical: 2})


def test_dataframe_normalization_does_not_change_values_or_source():
    saved = list(_ALIASES)
    source = pd.DataFrame({"algorithm": saved, "runtime": [1.25 + i for i in range(len(saved))], "seed": range(len(saved))})
    before = source.copy(deep=True)
    result = normalize_dataframe(source)
    assert list(result.algorithm) == list(_ALIASES.values())
    pd.testing.assert_frame_equal(source, before)
    pd.testing.assert_frame_equal(result[["runtime", "seed"]], source[["runtime", "seed"]])


def test_input_path_resolution_prefers_explicit_file(tmp_path):
    saved, canonical = next(iter(_ALIASES.items()))
    old = tmp_path / (saved + ".jsonl")
    current = tmp_path / (canonical + ".jsonl")
    old.write_text("old", encoding="utf-8")
    assert resolve_input_path(current) == old
    current.write_text("current", encoding="utf-8")
    assert resolve_input_path(current) == current


def test_config_and_display_names_are_unambiguous():
    for saved, canonical in _ALIASES.items():
        config = formal_algorithm_from_dict({"algorithm": saved})
        assert config.algorithm == canonical
        assert config.display_name == ALGORITHM_NAMES[canonical]
        assert algorithm_label(saved) == ALGORITHM_NAMES[canonical]
    assert SHORT_NAMES["dwcg_pure_hp"] == "HP"
    assert SHORT_NAMES["dwcg_saginfe"] == "HFP"
    assert SHORT_NAMES["dwcg_saginfe_pro_no_pfe"] == "PRO-no-PFE"
    assert FormalAlgorithmConfig(algorithm="direct_ilp").display_name == "ILP-FullObjective"
    assert FormalAlgorithmConfig(algorithm="direct_ilp_acceptance_cold").display_name == "ILP-AdmissionOnly"


def test_saved_schedule_and_run_matrix_use_canonical_identifiers():
    from pathlib import Path
    from formal_schema import formal_plan_from_dict
    from formal_runner import formal_matrix
    from record_compatibility import _VARIANT_ALIASES
    saved_schedule = next(iter(_VARIANT_ALIASES))
    config = formal_algorithm_from_dict({"algorithm": "dwcg_pure", "variant_id": saved_schedule})
    assert config.variant_id == "HYB_S5"
    assert config.display_name == "DWCG-Pure-HYB-S5"
    assert algorithm_label("dwcg_pure:HYB_S5", short=True) == "Pure / HYB-S5"
    data = json.loads(Path("configs/formal/formal_scale_load_master_v3.json").read_text(encoding="utf-8"))
    saved = next(key for key, value in _ALIASES.items() if value == "dwcg_pure")
    data["scenario"]["run_matrix"] = [{"scale": "S20", "formal_seed": 0, "load_label": "Low", "algorithm": saved}]
    plan = formal_plan_from_dict(data)
    assert formal_matrix(plan)[0][3].algorithm == "dwcg_pure"
