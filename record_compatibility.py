"""Input normalization and stable seed namespaces for persisted scientific records."""

import json
from pathlib import Path

_ALIASES = {
    "bwcg_pure": "dwcg_pure", "bwcg_warm": "dwcg_warm",
    "bwcg_hp": "dwcg_pure_hp", "bwcg_saginfe": "dwcg_saginfe",
    "bwcg_saginfe_pro": "dwcg_saginfe_pro",
    "bwcg_saginfe_pro_no_pfe": "dwcg_saginfe_pro_no_pfe",
}
_VARIANT_ALIASES = {"HYB": "HYB_S5"}
_DISPLAY_ALIASES = {
    "BWCG-Pure": "DWCG-Pure", "BWCG-Warm": "Warm", "BWCG-HP": "DWCG-Pure-HP",
    "BWCG-SAGINFE": "DWCG-SAGINFE", "BWCG-SAGINFE-PRO": "DWCG-SAGINFE-PRO",
    "BWCG-SAGINFE-PRO w/o PFE": "PRO-no-PFE",
    "BWCG-SAGINFE-PRO without PFE": "PRO-no-PFE",
}
_SEED_NAMES = {value: key for key, value in _ALIASES.items()}


def canonical_algorithm(value):
    return _ALIASES.get(value, _VARIANT_ALIASES.get(value, _DISPLAY_ALIASES.get(value, value)))


def algorithm_seed_namespace(value):
    return _SEED_NAMES.get(canonical_algorithm(value), value)


def normalize_record(value):
    """Return a new canonical view; preserve numbers and execution snapshots."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            new_key = canonical_algorithm(key)
            if isinstance(new_key, str):
                new_key = new_key.replace("bwcg_", "dwcg_")
            new_item = item if key == "configuration_snapshot" else normalize_record(item)
            if new_key in result and result[new_key] != new_item:
                raise ValueError("Conflicting algorithm fields in record")
            result[new_key] = new_item
        return result
    if isinstance(value, list):
        return [normalize_record(item) for item in value]
    if isinstance(value, tuple):
        return tuple(normalize_record(item) for item in value)
    if isinstance(value, str):
        return canonical_algorithm(value)
    return value


def resolve_input_path(path):
    path = Path(path)
    if path.exists():
        return path
    candidate = Path(str(path).replace("dwcg_pure_hp", "bwcg_hp")
                     .replace("dwcg", "bwcg")
                     .replace("same_flow_temporal_control_hfp_extension", "same_flow_temporal_control_legacy_extension"))
    return candidate if candidate.exists() else path


def decode_records(text):
    return normalize_record(json.loads(text))


def normalize_dataframe(frame):
    frame = frame.copy()
    for column in frame.columns:
        if frame[column].dtype == object:
            frame[column] = frame[column].map(lambda x: canonical_algorithm(x) if isinstance(x, str) else x)
    return frame
