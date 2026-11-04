"""Optional solver integration tests; guard imports before collection."""

import pytest

pytest.importorskip(
    "gurobipy", reason="Requires optional Gurobi dependency: pip install -r requirements-solver.txt"
)
