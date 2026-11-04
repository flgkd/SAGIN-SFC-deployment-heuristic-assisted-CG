"""Schema for v2 calibration plans and records.

Calibration identifies resource-pressure regimes. It must not tune parameters
to make any solver slow or favorable. Runtime is a diagnostic, not a regime
selection criterion.
"""

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, List, Optional, Tuple

from config_schema import (
    get_candidate_deadline_profile,
    get_candidate_scale,
    get_candidate_sfc_profile,
    get_candidate_traffic_mix_profile,
)


FRAMEWORK_VERSION = "v2_phase3a_calibration_framework"


@dataclass(frozen=True)
class CalibrationThresholds:
    underloaded_median_min: float = 0.98
    underloaded_full_acceptance_min: float = 0.60
    boundary_median_min: float = 0.85
    boundary_median_max: float = 0.97
    boundary_full_acceptance_max: float = 0.40
    overloaded_median_min: float = 0.60
    overloaded_median_max: float = 0.85
    severe_median_max: float = 0.60
    min_certified_direct_ilp_seeds: int = 3

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if name == "min_certified_direct_ilp_seeds":
                if value <= 0:
                    raise ValueError("min_certified_direct_ilp_seeds must be positive.")
            elif not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1].")


@dataclass(frozen=True)
class SolverCalibrationConfig:
    algorithm: str
    enabled: bool = True
    time_limit: Optional[float] = None
    threads: int = 1
    mip_gap: Optional[float] = None
    solver_seed: Optional[int] = None
    verbose: bool = False

    def validate(self) -> None:
        if self.algorithm not in ("direct_ilp", "saginfe"):
            raise ValueError(f"Unsupported calibration algorithm {self.algorithm!r}.")
        if self.time_limit is not None and self.time_limit <= 0.0:
            raise ValueError("time_limit must be positive when provided.")
        if self.threads <= 0:
            raise ValueError("threads must be positive.")
        if self.mip_gap is not None and self.mip_gap < 0.0:
            raise ValueError("mip_gap must be nonnegative.")


@dataclass(frozen=True)
class CalibrationScenarioConfig:
    scale_name: str
    time_slots: int
    dynamic_topology: bool
    traffic_profile: str
    sfc_profile: str
    deadline_profile: str
    demand_multipliers: Tuple[float, ...]
    calibration_seeds: Tuple[int, ...]
    reserved_formal_seeds: Tuple[int, ...]
    algorithms: Tuple[SolverCalibrationConfig, ...]
    topology_overrides: Dict[str, Any] = field(default_factory=dict)
    flow_overrides: Dict[str, Any] = field(default_factory=dict)
    output_settings: Dict[str, Any] = field(default_factory=dict)

    def validate(self, require_direct_ilp: bool = True) -> None:
        get_candidate_scale(self.scale_name)
        get_candidate_traffic_mix_profile(self.traffic_profile)
        get_candidate_sfc_profile(self.sfc_profile)
        get_candidate_deadline_profile(self.deadline_profile)
        if self.time_slots < 2:
            raise ValueError("time_slots must be >= 2.")
        if len(set(self.calibration_seeds)) != len(self.calibration_seeds):
            raise ValueError("calibration seeds must be unique.")
        if len(set(self.reserved_formal_seeds)) != len(self.reserved_formal_seeds):
            raise ValueError("reserved formal seeds must be unique.")
        if set(self.calibration_seeds) & set(self.reserved_formal_seeds):
            raise ValueError("calibration and formal seeds must be disjoint.")
        if tuple(sorted(self.demand_multipliers)) != self.demand_multipliers:
            raise ValueError("demand multipliers must be sorted.")
        if len(set(self.demand_multipliers)) != len(self.demand_multipliers):
            raise ValueError("demand multipliers must be unique.")
        for multiplier in self.demand_multipliers:
            if not isinstance(multiplier, (int, float)) or not math.isfinite(multiplier) or multiplier <= 0.0:
                raise ValueError("demand multipliers must be finite and positive.")
        for algorithm in self.algorithms:
            algorithm.validate()
        enabled = [algorithm.algorithm for algorithm in self.algorithms if algorithm.enabled]
        if not enabled:
            raise ValueError("at least one algorithm must be enabled.")
        if require_direct_ilp and "direct_ilp" not in enabled:
            raise ValueError("direct ILP must be enabled for regime classification.")


@dataclass(frozen=True)
class CalibrationPlan:
    name: str
    description: str
    provisional: bool
    scenario: CalibrationScenarioConfig
    thresholds: CalibrationThresholds = field(default_factory=CalibrationThresholds)
    mode_note: str = "Calibration plan only; not a formal experiment."
    plan_type: str = "exact_calibration"

    def validate(self) -> None:
        if not self.name:
            raise ValueError("CalibrationPlan name must be nonempty.")
        if self.plan_type not in ("exact_calibration", "transfer_validation"):
            raise ValueError(f"Unknown calibration plan_type {self.plan_type!r}.")
        self.thresholds.validate()
        self.scenario.validate(require_direct_ilp=self.plan_type == "exact_calibration")


@dataclass(frozen=True)
class CalibrationRunKey:
    scenario_id: str
    calibration_seed: int
    demand_multiplier: float
    algorithm: str

    def as_tuple(self) -> Tuple[str, int, float, str]:
        return (self.scenario_id, self.calibration_seed, self.demand_multiplier, self.algorithm)


@dataclass(frozen=True)
class CalibrationRunRecord:
    key: CalibrationRunKey
    start_timestamp: str
    runtime_seconds: float
    termination_status: str
    success: bool
    feasible_solution: bool
    accepted_flow_count: Optional[int]
    total_flow_count: int
    acceptance_rate: Optional[float]
    certified_acceptance_rate: Optional[float]
    incumbent_acceptance_rate: Optional[float]
    heuristic_acceptance_rate: Optional[float]
    canonical_objective_value: Optional[float]
    normalized_physical_cost: Optional[float]
    objective_globally_optimal: bool
    acceptance_count_certified: bool
    acceptance_certification_method: str
    error_type: Optional[str]
    error_message: Optional[str]
    topology_fingerprint: str
    base_flow_fingerprint: str
    full_instance_fingerprint: str
    instance_metrics: Dict[str, Any] = field(default_factory=dict)
    solver_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CalibrationManifest:
    framework_version: str
    git_commit: str
    git_dirty: bool
    python_version: str
    platform: str
    start_timestamp: str
    finish_timestamp: Optional[str]
    cli_arguments: Dict[str, Any]
    mode: str
    plan_fingerprint: str
    allow_dirty: bool = False


def direct_ilp_default_config() -> SolverCalibrationConfig:
    return SolverCalibrationConfig("direct_ilp", True, time_limit=None, threads=1, mip_gap=0.0, verbose=False)


def saginfe_default_config() -> SolverCalibrationConfig:
    return SolverCalibrationConfig("saginfe", True, time_limit=None, threads=1, mip_gap=None, verbose=False)


def initial_s30_candidate_plan() -> CalibrationPlan:
    return CalibrationPlan(
        name="s30_candidate",
        description="Diagnostic S30 calibration plan; provisional and not a formal experiment.",
        provisional=True,
        scenario=CalibrationScenarioConfig(
            scale_name="S30",
            time_slots=8,
            dynamic_topology=True,
            traffic_profile="Candidate",
            sfc_profile="Candidate",
            deadline_profile="Balanced",
            demand_multipliers=(0.8, 1.0, 1.2, 1.4),
            calibration_seeds=(100, 101, 102, 103, 104),
            reserved_formal_seeds=tuple(range(10)),
            algorithms=(direct_ilp_default_config(), saginfe_default_config()),
        ),
    )


def solver_config_from_dict(data: Dict[str, Any]) -> SolverCalibrationConfig:
    return SolverCalibrationConfig(
        algorithm=data["algorithm"],
        enabled=bool(data.get("enabled", True)),
        time_limit=data.get("time_limit"),
        threads=int(data.get("threads", 1)),
        mip_gap=data.get("mip_gap"),
        solver_seed=data.get("solver_seed"),
        verbose=bool(data.get("verbose", False)),
    )


def plan_from_dict(data: Dict[str, Any]) -> CalibrationPlan:
    scenario_data = data["scenario"]
    thresholds_data = data.get("thresholds", {})
    plan = CalibrationPlan(
        name=data["name"],
        description=data.get("description", ""),
        provisional=bool(data.get("provisional", True)),
        scenario=CalibrationScenarioConfig(
            scale_name=scenario_data["scale_name"],
            time_slots=int(scenario_data["time_slots"]),
            dynamic_topology=bool(scenario_data["dynamic_topology"]),
            traffic_profile=scenario_data["traffic_profile"],
            sfc_profile=scenario_data["sfc_profile"],
            deadline_profile=scenario_data["deadline_profile"],
            demand_multipliers=tuple(float(value) for value in scenario_data["demand_multipliers"]),
            calibration_seeds=tuple(int(value) for value in scenario_data["calibration_seeds"]),
            reserved_formal_seeds=tuple(int(value) for value in scenario_data["reserved_formal_seeds"]),
            algorithms=tuple(solver_config_from_dict(value) for value in scenario_data["algorithms"]),
            topology_overrides=dict(scenario_data.get("topology_overrides", {})),
            flow_overrides=dict(scenario_data.get("flow_overrides", {})),
            output_settings=dict(scenario_data.get("output_settings", {})),
        ),
        thresholds=CalibrationThresholds(**thresholds_data),
        mode_note=data.get("mode_note", "Calibration plan only; not a formal experiment."),
        plan_type=data.get("plan_type", "exact_calibration"),
    )
    plan.validate()
    return plan

