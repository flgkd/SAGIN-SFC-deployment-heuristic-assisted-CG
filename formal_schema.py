"""Schema for frozen load regimes and formal benchmark plans."""

from record_compatibility import canonical_algorithm
from algorithm_names import ALGORITHM_NAMES
from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

from config_schema import get_candidate_deadline_profile, get_candidate_scale, get_candidate_sfc_profile, get_candidate_traffic_mix_profile


FORMAL_FRAMEWORK_VERSION = "v3_formal_framework"
FORMAL_ALGORITHMS = (
    "direct_ilp",
    "direct_ilp_acceptance_cold",
    "saginfe",
    "dwcg_pure",
    "dwcg_warm",
    "dwcg_pure_hp",
    "dwcg_saginfe",
    "dwcg_saginfe_pro_no_pfe",
    "dwcg_saginfe_pro",
)
FORMAL_DWCG_ALGORITHMS = (
    "dwcg_pure",
    "dwcg_warm",
    "dwcg_pure_hp",
    "dwcg_saginfe",
    "dwcg_saginfe_pro_no_pfe",
    "dwcg_saginfe_pro",
)
FORMAL_DWCG_DISPLAY_NAMES = {key: value for key, value in ALGORITHM_NAMES.items() if key.startswith("dwcg_")}
FORMAL_PRICING_STRATEGIES = ("exact", "early_stop", "hybrid", "heuristic_then_exact")
CALIBRATION_SEEDS_V1 = tuple(range(100, 110))
FORMAL_SEEDS_V1 = tuple(range(10))
INTEGRATION_SEED_V1 = 999
PILOT_SEED_V1 = 900


@dataclass(frozen=True)
class LoadRegime:
    label: str
    demand_multiplier: float


@dataclass(frozen=True)
class FormalAlgorithmConfig:
    algorithm: str
    variant_id: Optional[str] = None
    display_name_override: Optional[str] = None
    enabled: bool = True
    time_limit: Optional[float] = None
    threads: int = 1
    mip_gap: Optional[float] = None
    mip_gap_abs: Optional[float] = None
    gurobi_seed: Optional[int] = None
    use_saginfe_initialization: bool = False
    direct_ilp_warm_start: bool = False
    deterministic_mode: bool = False
    algorithm_seed: Optional[int] = None
    pricing_worker_count: int = 1
    pricing_strategy: Optional[str] = None
    max_cg_iterations: Optional[int] = None
    max_exact_pricing_calls: Optional[int] = None
    max_exact_pricing_calls_by_scale: Dict[str, int] = field(default_factory=dict)
    hard_time_limit_seconds: Optional[float] = None
    disable_hard_time_limit: bool = False
    final_rmp_reserve_ratio: Optional[float] = None
    final_rmp_reserve_seconds: Optional[float] = None
    final_rmp_time_cap_seconds: Optional[float] = None
    hybrid_switch_iteration: Optional[int] = None

    def validate(self) -> None:
        if self.algorithm not in FORMAL_ALGORITHMS:
            raise ValueError(f"Unsupported formal algorithm {self.algorithm!r}.")
        if self.variant_id is not None and not self.variant_id:
            raise ValueError("variant_id must be nonempty when provided.")
        if self.time_limit is not None and self.time_limit <= 0.0:
            raise ValueError("time_limit must be positive when provided.")
        if self.threads <= 0:
            raise ValueError("threads must be positive.")
        if self.mip_gap is not None and self.mip_gap < 0.0:
            raise ValueError("mip_gap must be nonnegative.")
        if self.mip_gap_abs is not None and self.mip_gap_abs < 0.0:
            raise ValueError("mip_gap_abs must be nonnegative.")
        if self.gurobi_seed is not None and self.gurobi_seed < 0:
            raise ValueError("gurobi_seed must be nonnegative when provided.")
        if self.pricing_strategy is not None and self.pricing_strategy not in FORMAL_PRICING_STRATEGIES:
            raise ValueError(f"Unsupported pricing_strategy {self.pricing_strategy!r}.")
        if self.algorithm in FORMAL_DWCG_ALGORITHMS:
            expected = expected_formal_algorithm_components(self)
            if self.use_saginfe_initialization != expected["use_saginfe_initialization"]:
                state = "must" if expected["use_saginfe_initialization"] else "must not"
                raise ValueError(f"{self.algorithm} {state} use SAGINFE initialization.")
            if self.resolved_pricing_strategy() != expected["pricing_strategy"]:
                raise ValueError(
                    f"{self.algorithm} must use pricing_strategy {expected['pricing_strategy']!r}."
                )
        elif self.pricing_strategy is not None:
            raise ValueError("pricing_strategy is only supported for DWCG algorithms.")
        if self.pricing_worker_count <= 0:
            raise ValueError("pricing_worker_count must be positive.")
        if self.max_cg_iterations is not None and self.max_cg_iterations <= 0:
            raise ValueError("max_cg_iterations must be positive.")
        if self.max_exact_pricing_calls is not None and self.max_exact_pricing_calls <= 0:
            raise ValueError("max_exact_pricing_calls must be positive.")
        for scale, call_count in self.max_exact_pricing_calls_by_scale.items():
            get_candidate_scale(scale)
            if call_count <= 0:
                raise ValueError(f"max_exact_pricing_calls_by_scale for {scale!r} must be positive.")
        if self.hard_time_limit_seconds is not None and self.hard_time_limit_seconds <= 0.0:
            raise ValueError("hard_time_limit_seconds must be positive.")
        if self.disable_hard_time_limit:
            if self.algorithm not in ("direct_ilp_acceptance_cold", "dwcg_saginfe"):
                raise ValueError("disable_hard_time_limit is only supported for direct_ilp_acceptance_cold and dwcg_saginfe.")
            if self.hard_time_limit_seconds is not None:
                raise ValueError("disable_hard_time_limit cannot be combined with hard_time_limit_seconds.")
        if self.final_rmp_reserve_ratio is not None and not 0.0 <= self.final_rmp_reserve_ratio < 1.0:
            raise ValueError("final_rmp_reserve_ratio must be in [0, 1).")
        if self.final_rmp_reserve_seconds is not None and self.final_rmp_reserve_seconds < 0.0:
            raise ValueError("final_rmp_reserve_seconds must be nonnegative when provided.")
        if self.final_rmp_time_cap_seconds is not None and self.final_rmp_time_cap_seconds <= 0.0:
            raise ValueError("final_rmp_time_cap_seconds must be positive when provided.")
        if self.hybrid_switch_iteration is not None and self.hybrid_switch_iteration <= 0:
            raise ValueError("hybrid_switch_iteration must be positive when provided.")
        if self.hybrid_switch_iteration is not None and self.resolved_pricing_strategy() != "hybrid":
            raise ValueError("hybrid_switch_iteration is only valid for hybrid pricing_strategy.")

    def resolved_pricing_strategy(self) -> Optional[str]:
        if self.pricing_strategy is not None:
            return self.pricing_strategy
        if self.algorithm in FORMAL_DWCG_ALGORITHMS:
            return expected_dwcg_components(self.algorithm)["pricing_strategy"]
        return None

    @property
    def display_name(self) -> str:
        if self.display_name_override:
            return self.display_name_override
        if self.algorithm == "direct_ilp_acceptance_cold":
            return ALGORITHM_NAMES[self.algorithm]
        base = ALGORITHM_NAMES.get(self.algorithm, self.algorithm)
        variant = self.variant_id.replace("_", "-") if self.variant_id else None
        return f"{base}-{variant}" if variant else base


def expected_dwcg_components(algorithm: str) -> Dict[str, Any]:
    if algorithm == "dwcg_pure":
        return {"use_saginfe_initialization": False, "pricing_strategy": "hybrid", "initialization_source": "pure"}
    if algorithm == "dwcg_warm":
        return {"use_saginfe_initialization": True, "pricing_strategy": "hybrid", "initialization_source": "saginfe"}
    if algorithm == "dwcg_pure_hp":
        return {"use_saginfe_initialization": False, "pricing_strategy": "heuristic_then_exact", "initialization_source": "pure"}
    if algorithm == "dwcg_saginfe":
        return {"use_saginfe_initialization": True, "pricing_strategy": "heuristic_then_exact", "initialization_source": "saginfe"}
    if algorithm == "dwcg_saginfe_pro_no_pfe":
        return {"use_saginfe_initialization": True, "pricing_strategy": "hybrid", "initialization_source": "saginfe"}
    if algorithm == "dwcg_saginfe_pro":
        return {"use_saginfe_initialization": True, "pricing_strategy": "hybrid", "initialization_source": "saginfe"}
    raise ValueError(f"Unsupported DWCG algorithm {algorithm!r}.")


def expected_formal_algorithm_components(config: FormalAlgorithmConfig) -> Dict[str, Any]:
    expected = expected_dwcg_components(config.algorithm)
    if config.algorithm in ("dwcg_pure", "dwcg_saginfe_pro") and config.variant_id in ("EX", "ESV", "HYB_S5", "HYB_S2", "HYB_S7"):
        variant_pricing = {
            "EX": "exact",
            "ESV": "early_stop",
            "HYB_S5": "hybrid",
            "HYB_S2": "hybrid",
            "HYB_S7": "hybrid",
        }[config.variant_id]
        use_saginfe = config.algorithm == "dwcg_saginfe_pro"
        return {
            "use_saginfe_initialization": use_saginfe,
            "pricing_strategy": variant_pricing,
            "initialization_source": "saginfe" if use_saginfe else "pure",
        }
    return expected


@dataclass(frozen=True)
class FormalScenarioConfig:
    scales: Tuple[str, ...]
    time_slots: int
    dynamic_topology: bool
    formal_seeds: Tuple[int, ...]
    loads: Tuple[LoadRegime, ...]
    algorithms: Tuple[FormalAlgorithmConfig, ...]
    traffic_profile: str = "Candidate"
    sfc_profile: str = "Candidate"
    deadline_profile: str = "Balanced"
    flow_policy: str = "F=4N"
    scale_flow_counts: Dict[str, int] = field(default_factory=dict)
    scenario_pairs: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)
    run_matrix: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    output_settings: Dict[str, Any] = field(default_factory=dict)

    def validate(self, smoke: bool = False, pilot: bool = False) -> None:
        if not self.scales:
            raise ValueError("Formal plan must include at least one scale.")
        for scale in self.scales:
            get_candidate_scale(scale)
        for scale, flow_count in self.scale_flow_counts.items():
            if scale not in self.scales:
                raise ValueError(f"scale_flow_counts includes unknown scale {scale!r}.")
            if flow_count <= 0:
                raise ValueError(f"scale_flow_counts for {scale!r} must be positive.")
        get_candidate_traffic_mix_profile(self.traffic_profile)
        get_candidate_sfc_profile(self.sfc_profile)
        deadline_profile = get_candidate_deadline_profile(self.deadline_profile)
        if self.time_slots < 2:
            raise ValueError("time_slots must be >= 2.")
        max_deadline_slack = max(deadline_profile.slack_distribution.probabilities)
        if self.time_slots < max_deadline_slack + 1:
            raise ValueError(
                f"Deadline profile {deadline_profile.name} requires at least "
                f"{max_deadline_slack + 1} time slots."
            )
        if len(set(self.formal_seeds)) != len(self.formal_seeds):
            raise ValueError("formal seeds must be unique.")
        overlap = set(self.formal_seeds) & set(CALIBRATION_SEEDS_V1)
        if overlap:
            raise ValueError(f"Formal plans must not use calibration seeds: {sorted(overlap)}.")
        if smoke:
            if set(self.formal_seeds) != {INTEGRATION_SEED_V1}:
                raise ValueError(f"Smoke/integration plans must use only integration seed {INTEGRATION_SEED_V1}.")
        elif pilot:
            if set(self.formal_seeds) != {PILOT_SEED_V1}:
                raise ValueError(f"Pilot plans must use only pilot seed {PILOT_SEED_V1}.")
        else:
            outside_formal = set(self.formal_seeds) - set(FORMAL_SEEDS_V1)
            if outside_formal:
                raise ValueError(f"Non-smoke formal plans must use only formal paper seeds 0..9; got {sorted(outside_formal)}.")
        if not self.loads:
            raise ValueError("Formal plan must include at least one load.")
        labels = [load.label for load in self.loads]
        if len(set(labels)) != len(labels):
            raise ValueError("Formal load labels must be unique.")
        for load in self.loads:
            if not math.isfinite(load.demand_multiplier) or load.demand_multiplier <= 0.0:
                raise ValueError("Formal demand multipliers must be finite and positive.")
        load_by_label = {load.label: load for load in self.loads}
        for scale, load_label in self.scenario_pairs:
            if scale not in self.scales:
                raise ValueError(f"scenario_pairs includes unknown scale {scale!r}.")
            if load_label not in load_by_label:
                raise ValueError(f"scenario_pairs includes unknown load {load_label!r}.")
        if len(set(self.scenario_pairs)) != len(self.scenario_pairs):
            raise ValueError("scenario_pairs entries must be unique.")
        for algorithm in self.algorithms:
            algorithm.validate()
        enabled_algorithms = [algorithm for algorithm in self.algorithms if algorithm.enabled]
        if not enabled_algorithms:
            raise ValueError("Formal plan must enable at least one algorithm.")
        enabled_keys = {
            (algorithm.algorithm, algorithm.variant_id)
            for algorithm in enabled_algorithms
        }
        enabled_names = {algorithm.algorithm for algorithm in enabled_algorithms}
        seen_run_keys = set()
        for index, run in enumerate(self.run_matrix):
            missing = [
                key for key in ("scale", "load_label", "formal_seed", "algorithm")
                if key not in run
            ]
            if missing:
                raise ValueError(f"run_matrix row {index} is missing keys: {missing}.")
            scale = str(run["scale"])
            load_label = str(run["load_label"])
            seed = int(run["formal_seed"])
            algorithm_name = str(run["algorithm"])
            variant_id = run.get("variant_id")
            variant_key = None if variant_id is None else str(variant_id)
            if scale not in self.scales:
                raise ValueError(f"run_matrix row {index} includes unknown scale {scale!r}.")
            if load_label not in load_by_label:
                raise ValueError(f"run_matrix row {index} includes unknown load {load_label!r}.")
            if seed not in self.formal_seeds:
                raise ValueError(f"run_matrix row {index} includes unknown formal_seed {seed!r}.")
            if variant_key is None:
                if algorithm_name not in enabled_names:
                    raise ValueError(f"run_matrix row {index} includes disabled or unknown algorithm {algorithm_name!r}.")
            elif (algorithm_name, variant_key) not in enabled_keys:
                raise ValueError(
                    f"run_matrix row {index} includes disabled or unknown algorithm variant "
                    f"{algorithm_name!r}/{variant_key!r}."
                )
            run_key = (scale, load_label, seed, algorithm_name, variant_key)
            if run_key in seen_run_keys:
                raise ValueError(f"run_matrix row {index} duplicates {run_key!r}.")
            seen_run_keys.add(run_key)


@dataclass(frozen=True)
class FormalPlan:
    name: str
    description: str
    version: str
    plan_kind: str
    scenario: FormalScenarioConfig
    smoke: bool = False
    integration_only: bool = False
    provisional_limits: bool = True
    include_in_core_ranking: bool = True

    def validate(self) -> None:
        if not self.name:
            raise ValueError("FormalPlan name must be nonempty.")
        if self.plan_kind not in (
            "formal_smoke",
            "core_formal",
            "scaling_formal",
            "spotcheck",
            "formal_pilot",
            "formal_ablation_pilot",
            "formal_ablation",
            "formal_parallel_pricing_pilot",
            "formal_parallel_pricing",
            "formal_pricing_strategy_pilot",
            "formal_pricing_strategy",
            "formal_hybrid_switch_pilot",
            "formal_hybrid_switch",
            "formal_temporal_scalability_pilot",
            "formal_temporal_scalability",
            "formal_deadline_sensitivity_pilot",
            "formal_deadline_sensitivity",
            "formal_same_flow_temporal_control_pilot",
            "formal_same_flow_temporal_control",
            "scaling_runtime_pilot",
        ):
            raise ValueError(f"Unknown formal plan kind {self.plan_kind!r}.")
        if self.plan_kind in (
            "formal_pilot",
            "formal_ablation_pilot",
            "formal_parallel_pricing_pilot",
            "formal_pricing_strategy_pilot",
            "formal_hybrid_switch_pilot",
            "formal_temporal_scalability_pilot",
            "formal_deadline_sensitivity_pilot",
            "formal_same_flow_temporal_control_pilot",
            "scaling_runtime_pilot",
        ):
            if not self.integration_only:
                raise ValueError(f"{self.plan_kind} plans must be integration_only.")
            if self.include_in_core_ranking:
                raise ValueError(f"{self.plan_kind} plans must be excluded from core ranking.")
            if not self.provisional_limits:
                raise ValueError(f"{self.plan_kind} plans must mark provisional_limits true.")
        if self.plan_kind in (
            "formal_ablation",
            "formal_parallel_pricing",
            "formal_pricing_strategy",
            "formal_hybrid_switch",
            "formal_temporal_scalability",
            "formal_deadline_sensitivity",
            "formal_same_flow_temporal_control",
        ) and self.include_in_core_ranking:
            raise ValueError(f"{self.plan_kind} plans must be excluded from core ranking.")
        if self.smoke and not self.integration_only:
            raise ValueError("formal_smoke plans must be integration_only.")
        self.scenario.validate(
            smoke=self.smoke,
            pilot=self.plan_kind in (
                "formal_pilot",
                "formal_ablation_pilot",
                "formal_parallel_pricing_pilot",
                "formal_pricing_strategy_pilot",
                "formal_hybrid_switch_pilot",
                "formal_temporal_scalability_pilot",
                "formal_deadline_sensitivity_pilot",
                "formal_same_flow_temporal_control_pilot",
            ),
        )


@dataclass(frozen=True)
class FormalRunKey:
    scenario_id: str
    scale: str
    time_slots: int
    formal_seed: int
    load_label: str
    demand_multiplier: float
    algorithm: str
    variant_id: Optional[str] = None

    def as_tuple(self) -> Tuple:
        base = (self.scenario_id, self.scale, self.formal_seed, self.load_label, self.algorithm)
        return base if self.variant_id is None else (*base, self.variant_id)


@dataclass(frozen=True)
class FormalRunRecord:
    key: FormalRunKey
    git_commit: str
    total_flow_count: int
    accepted_flow_count: Optional[int]
    acceptance_rate: Optional[float]
    normalized_physical_cost: Optional[float]
    canonical_objective_value: Optional[float]
    path_validity: Optional[bool]
    capacity_validity: Optional[bool]
    termination_status: str
    total_end_to_end_runtime_seconds: float
    error_type: Optional[str]
    error_message: Optional[str]
    topology_fingerprint: str
    base_flow_fingerprint: str
    full_instance_fingerprint: str
    configuration_snapshot: Dict[str, Any]
    objective_globally_optimal: bool = False
    acceptance_count_certified: bool = False
    certification_method: str = "NOT_CERTIFIED"
    best_bound: Optional[float] = None
    mip_gap: Optional[float] = None
    optimality_scope: str = "not_certified"
    direct_ilp_metadata: Dict[str, Any] = field(default_factory=dict)
    dwcg_metadata: Dict[str, Any] = field(default_factory=dict)
    heuristic_metadata: Dict[str, Any] = field(default_factory=dict)
    deadline_profile: Optional[str] = None
    deadline_invariant_flow_fingerprint: Optional[str] = None
    full_flow_fingerprint: Optional[str] = None
    deadline_uniform_fingerprint: Optional[str] = None
    deadline_clipping_count: Optional[int] = None
    latest_source_time: Optional[int] = None
    deadline_slack_histogram: Dict[str, int] = field(default_factory=dict)
    deadline_arrival_time_histogram: Dict[str, int] = field(default_factory=dict)
    deadline_time_histogram: Dict[str, int] = field(default_factory=dict)
    deadline_minimum_slack: Optional[int] = None
    deadline_maximum_slack: Optional[int] = None
    deadline_mean_slack: Optional[float] = None
    deadline_horizon_last_slot_count: Optional[int] = None
    deadline_saturation_count: Optional[int] = None
    physical_base_fingerprint: Optional[str] = None
    temporal_prefix_fingerprint: Optional[str] = None
    common_temporal_prefix_fingerprints: Dict[str, str] = field(default_factory=dict)
    non_temporal_flow_fingerprint: Optional[str] = None
    paired_timing_latent_fingerprint: Optional[str] = None
    full_temporal_flow_fingerprint: Optional[str] = None
    temporal_canonical_time_slots: Optional[int] = None
    temporal_requested_time_slots: Optional[int] = None
    temporal_supported_time_slots: Tuple[int, ...] = field(default_factory=tuple)
    temporal_teg_node_count: Optional[int] = None
    temporal_storage_edge_count: Optional[int] = None
    temporal_communication_edge_count: Optional[int] = None
    temporal_arrival_time_histogram: Dict[str, int] = field(default_factory=dict)
    temporal_slack_histogram: Dict[str, int] = field(default_factory=dict)
    temporal_deadline_time_histogram: Dict[str, int] = field(default_factory=dict)
    temporal_minimum_slack: Optional[int] = None
    temporal_maximum_slack: Optional[int] = None
    temporal_mean_slack: Optional[float] = None
    temporal_clipping_count: Optional[int] = None
    temporal_horizon_last_slot_count: Optional[int] = None
    temporal_fixed_f_average_flows_per_time_slot: Optional[float] = None
    integration_only: bool = False
    run_category: str = "formal_paper"


def formal_algorithm_from_dict(data: Mapping[str, Any]) -> FormalAlgorithmConfig:
    return FormalAlgorithmConfig(
        algorithm=canonical_algorithm(str(data["algorithm"])),
        variant_id=canonical_algorithm(data.get("variant_id")),
        display_name_override=canonical_algorithm(data.get("display_name", data.get("display_name_override"))),
        enabled=bool(data.get("enabled", True)),
        time_limit=data.get("time_limit"),
        threads=int(data.get("threads", 1)),
        mip_gap=data.get("mip_gap"),
        mip_gap_abs=data.get("mip_gap_abs"),
        gurobi_seed=data.get("gurobi_seed"),
        use_saginfe_initialization=bool(data.get("use_saginfe_initialization", False)),
        direct_ilp_warm_start=bool(data.get("direct_ilp_warm_start", False)),
        deterministic_mode=bool(data.get("deterministic_mode", False)),
        algorithm_seed=data.get("algorithm_seed"),
        pricing_worker_count=int(data.get("pricing_worker_count", 1)),
        pricing_strategy=data.get("pricing_strategy"),
        max_cg_iterations=data.get("max_cg_iterations"),
        max_exact_pricing_calls=data.get("max_exact_pricing_calls"),
        max_exact_pricing_calls_by_scale={
            str(key): int(value)
            for key, value in data.get("max_exact_pricing_calls_by_scale", {}).items()
        },
        hard_time_limit_seconds=data.get("hard_time_limit_seconds"),
        disable_hard_time_limit=bool(data.get("disable_hard_time_limit", False)),
        final_rmp_reserve_ratio=data.get("final_rmp_reserve_ratio"),
        final_rmp_reserve_seconds=data.get("final_rmp_reserve_seconds"),
        final_rmp_time_cap_seconds=data.get("final_rmp_time_cap_seconds"),
        hybrid_switch_iteration=data.get("hybrid_switch_iteration"),
    )


def _parse_scenario_pair(item: Any) -> Tuple[str, str]:
    if isinstance(item, Mapping):
        missing = [key for key in ("scale", "load_label") if key not in item]
        if missing:
            raise ValueError(f"scenario_pairs mapping is missing keys: {missing}.")
        return str(item["scale"]), str(item["load_label"])
    if isinstance(item, (list, tuple)):
        if len(item) != 2:
            raise ValueError(f"scenario_pairs pair entries must have length 2, got {len(item)}.")
        return str(item[0]), str(item[1])
    raise ValueError(f"scenario_pairs entries must be mappings or length-2 pairs, got {type(item).__name__}.")


def formal_plan_from_dict(data: Mapping[str, Any]) -> FormalPlan:
    scenario = data["scenario"]
    plan = FormalPlan(
        name=str(data["name"]),
        description=str(data.get("description", "")),
        version=str(data.get("version", "v1")),
        plan_kind=str(data["plan_kind"]),
        smoke=bool(data.get("smoke", False)),
        integration_only=bool(data.get("integration_only", data.get("smoke", False))),
        provisional_limits=bool(data.get("provisional_limits", True)),
        include_in_core_ranking=bool(data.get("include_in_core_ranking", True)),
        scenario=FormalScenarioConfig(
            scales=tuple(str(value) for value in scenario["scales"]),
            time_slots=int(scenario["time_slots"]),
            dynamic_topology=bool(scenario["dynamic_topology"]),
            formal_seeds=tuple(int(value) for value in scenario["formal_seeds"]),
            loads=tuple(LoadRegime(str(item["label"]), float(item["demand_multiplier"])) for item in scenario["loads"]),
            algorithms=tuple(formal_algorithm_from_dict(item) for item in scenario["algorithms"]),
            traffic_profile=str(scenario.get("traffic_profile", "Candidate")),
            sfc_profile=str(scenario.get("sfc_profile", "Candidate")),
            deadline_profile=str(scenario.get("deadline_profile", "Balanced")),
            flow_policy=str(scenario.get("flow_policy", "F=4N")),
            scale_flow_counts={str(key): int(value) for key, value in scenario.get("scale_flow_counts", {}).items()},
            scenario_pairs=tuple(_parse_scenario_pair(item) for item in scenario.get("scenario_pairs", [])),
            run_matrix=tuple({**item, "algorithm": canonical_algorithm(item["algorithm"]),
                              **({"variant_id": canonical_algorithm(item["variant_id"])} if "variant_id" in item else {})}
                             for item in scenario.get("run_matrix", [])),
            output_settings=dict(scenario.get("output_settings", {})),
        ),
    )
    plan.validate()
    return plan

