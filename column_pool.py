# column_pool.py

from typing import List
from data_model import Path, Flow


class ColumnPool:
    """
    Manages the generated columns (Paths) for the Dantzig-Wolfe decomposition.
    Ensures that no duplicate columns are added to the RMP and manages path IDs.
    """

    def __init__(self):
        self.paths: List[Path] = []
        self._signatures = set()
        self._path_id_counter = 0  # Global unique path ID counter
        self.duplicate_rejections = 0

    def generate_path_id(self) -> int:
        """
        Generates a sequential unique path ID.
        NOT thread/process safe by itself — must only be called
        from the main process (e.g., in the as_completed callback).
        """
        pid = self._path_id_counter
        self._path_id_counter += 1
        return pid

    def initialize_empty_paths(self, flows: List[Flow]):
        """
        Generates an artificial 'empty' path for every flow.
        This is required to ensure the RMP has an initial feasible solution.
        Semantically, the empty path means rejecting the flow and contributes
        zero objective value in the RMP.
        """
        for flow in flows:
            empty_path = Path(
                flow_id=flow.flow_id,
                path_id=self.generate_path_id(),
                path_is_empty=True,
                path_stage_placement={},
                path_segment_routing={},
                path_cost=0.0
            )

            # Finalize usage with an empty dict (since there's no placement, no costs are added)
            empty_path.finalize_usage(flow=flow)

            self.add_path(empty_path)

    def add_path(self, path: Path) -> bool:
        """
        Attempts to add a new path to the pool.
        Returns True if added successfully, False if it was a duplicate.
        """
        # Ensure the signature is built for comparison
        if path.signature is None:
            path.build_signature()

        # Check for duplicates
        if path.signature in self._signatures:
            self.duplicate_rejections += 1
            return False

        # If unique, add to the pool
        self.paths.append(path)
        self._signatures.add(path.signature)
        return True

    def add_paths_batch(self, paths: List[Path]) -> int:
        """
        Adds multiple paths at once. Useful for gathering results from parallel workers.
        Returns the number of paths successfully added (non-duplicates).
        """
        added_count = 0
        for path in paths:
            if self.add_path(path):
                added_count += 1
        return added_count

    def get_all_paths(self) -> List[Path]:
        """
        Returns all paths currently in the pool to be fed into the RMP.
        """
        return self.paths

    def get_paths_by_flow(self, flow_id: int) -> List[Path]:
        """
        Returns all paths belonging to a specific flow.
        """
        return [p for p in self.paths if p.flow_id == flow_id]

    def __len__(self):
        return len(self.paths)

