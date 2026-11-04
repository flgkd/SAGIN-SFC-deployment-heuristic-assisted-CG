"""Stable deterministic seed derivation and backend normalization."""

import hashlib
from typing import Any, Dict


PYTHON_RANDOM_SEED_MAX = 2**32 - 1
NUMPY_SEED_MAX = 2**32 - 1
GUROBI_SEED_MAX = 2_000_000_000
C_LONG_SEED_MAX = 2**31 - 1


def stable_seed_digest(*parts: Any) -> str:
    payload = "|".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_seed_digest(digest: str, max_value: int) -> int:
    if max_value < 0:
        raise ValueError("max_value must be nonnegative.")
    return int(digest, 16) % (max_value + 1)


def python_random_seed_from_digest(digest: str) -> int:
    return normalize_seed_digest(digest, PYTHON_RANDOM_SEED_MAX)


def numpy_seed_from_digest(digest: str) -> int:
    return normalize_seed_digest(digest, NUMPY_SEED_MAX)


def gurobi_seed_from_digest(digest: str) -> int:
    return normalize_seed_digest(digest, GUROBI_SEED_MAX)


def normalize_gurobi_seed(value: Any) -> int:
    return int(value) % (GUROBI_SEED_MAX + 1)


def c_long_seed_from_digest(digest: str) -> int:
    return normalize_seed_digest(digest, C_LONG_SEED_MAX)


def seed_bundle(*parts: Any) -> Dict[str, Any]:
    digest = stable_seed_digest(*parts)
    return {
        "seed_digest": digest,
        "python_random_seed": python_random_seed_from_digest(digest),
        "numpy_seed": numpy_seed_from_digest(digest),
        "gurobi_seed": gurobi_seed_from_digest(digest),
        "c_long_seed": c_long_seed_from_digest(digest),
    }
