"""Configuration schema for candidate SAGIN topology and flow generation."""

from dataclasses import dataclass
import math
from typing import Dict, Optional, Tuple


LINK_TYPES = ("SS", "SA", "AA", "AG", "GG")
LAYERS = ("Space", "Air", "Ground")
TRAFFIC_CATEGORIES = ("cross_region_ground", "same_region_ground", "ground_nonground")
LOAD_PROFILES = {"Low": 0.8, "Medium": 1.0, "High": 1.2}
PROBABILITY_TOLERANCE = 1e-9


@dataclass(frozen=True)
class NetworkScaleConfig:
    name: str
    space_nodes: int
    air_nodes: int
    ground_nodes: int
    ground_regions: int = 4

    def validate(self) -> None:
        for field_name in ("space_nodes", "air_nodes", "ground_nodes", "ground_regions"):
            value = getattr(self, field_name)
            if value <= 0:
                raise ValueError(f"{field_name} must be positive, got {value}.")
        if self.ground_nodes % self.ground_regions != 0:
            raise ValueError(
                f"ground_nodes {self.ground_nodes} must be divisible by "
                f"ground_regions {self.ground_regions}."
            )


@dataclass(frozen=True)
class CapacityRange:
    low: float
    high: float

    def validate(self, name: str) -> None:
        if self.low <= 0.0:
            raise ValueError(f"{name} capacity low must be positive, got {self.low}.")
        if self.high < self.low:
            raise ValueError(f"{name} capacity high {self.high} must be >= low {self.low}.")


@dataclass(frozen=True)
class CapacityProfile:
    node_capacity_by_layer: Dict[str, CapacityRange]
    link_capacity_by_type: Dict[str, CapacityRange]

    def validate(self) -> None:
        for layer in LAYERS:
            if layer not in self.node_capacity_by_layer:
                raise ValueError(f"Missing node capacity range for layer {layer}.")
            self.node_capacity_by_layer[layer].validate(f"{layer} node")
        for link_type in LINK_TYPES:
            if link_type not in self.link_capacity_by_type:
                raise ValueError(f"Missing link capacity range for type {link_type}.")
            self.link_capacity_by_type[link_type].validate(f"{link_type} link")


@dataclass(frozen=True)
class DynamicsProfile:
    survival_probability_by_type: Dict[str, float]
    node_capacity_variation_ratio: float = 0.10
    link_capacity_variation_ratio: float = 0.10
    max_connectivity_resamples: int = 100

    def validate(self) -> None:
        for link_type in LINK_TYPES:
            if link_type not in self.survival_probability_by_type:
                raise ValueError(f"Missing survival probability for link type {link_type}.")
            probability = self.survival_probability_by_type[link_type]
            if not 0.0 <= probability <= 1.0:
                raise ValueError(f"{link_type} survival probability must be in [0, 1].")
        for field_name in ("node_capacity_variation_ratio", "link_capacity_variation_ratio"):
            value = getattr(self, field_name)
            if not 0.0 <= value < 1.0:
                raise ValueError(f"{field_name} must be in [0, 1), got {value}.")
        if self.max_connectivity_resamples <= 0:
            raise ValueError("max_connectivity_resamples must be positive.")


@dataclass(frozen=True)
class TopologyConfig:
    scale: NetworkScaleConfig
    time_slots: int
    seed: int
    resource_weight: float = 0.5
    dynamic_topology: bool = False
    capacity_profile: CapacityProfile = None
    dynamics_profile: DynamicsProfile = None

    def __post_init__(self):
        if self.capacity_profile is None:
            object.__setattr__(self, "capacity_profile", candidate_capacity_profile())
        if self.dynamics_profile is None:
            object.__setattr__(self, "dynamics_profile", candidate_dynamics_profile())

    def validate(self) -> None:
        self.scale.validate()
        if self.time_slots < 2:
            raise ValueError(f"time_slots must be >= 2, got {self.time_slots}.")
        if not 0.0 <= self.resource_weight <= 1.0:
            raise ValueError(f"resource_weight must be in [0, 1], got {self.resource_weight}.")
        self.capacity_profile.validate()
        self.dynamics_profile.validate()


@dataclass(frozen=True)
class WeightedIntegerDistribution:
    probabilities: Dict[int, float]

    def validate(self, name: str = "distribution") -> None:
        if not self.probabilities:
            raise ValueError(f"{name} must not be empty.")
        total = 0.0
        for outcome, probability in self.probabilities.items():
            if not isinstance(outcome, int) or isinstance(outcome, bool):
                raise ValueError(f"{name} outcome {outcome!r} must be an integer.")
            if not isinstance(probability, (int, float)) or not math.isfinite(probability):
                raise ValueError(f"{name} probability for {outcome} must be finite.")
            if probability < 0.0:
                raise ValueError(f"{name} probability for {outcome} must be nonnegative.")
            total += float(probability)
        if abs(total - 1.0) > PROBABILITY_TOLERANCE:
            raise ValueError(f"{name} probabilities must sum to 1, got {total}.")

    def as_dict(self) -> Dict[int, float]:
        return {int(k): float(v) for k, v in self.probabilities.items()}


@dataclass(frozen=True)
class TrafficMixProfile:
    name: str
    cross_region_ground_ratio: float
    same_region_ground_ratio: float
    ground_nonground_ratio: float

    def validate(self) -> None:
        if not self.name:
            raise ValueError("Traffic mix profile name must be explicit.")
        values = (
            self.cross_region_ground_ratio,
            self.same_region_ground_ratio,
            self.ground_nonground_ratio,
        )
        if any((not isinstance(value, (int, float))) or not math.isfinite(value) or value < 0.0 for value in values):
            raise ValueError("Traffic mix ratios must be finite and nonnegative.")
        total = sum(values)
        if abs(total - 1.0) > PROBABILITY_TOLERANCE:
            raise ValueError(f"Traffic mix ratios must sum to 1, got {total}.")

    def probabilities(self) -> Dict[str, float]:
        return {
            "cross_region_ground": float(self.cross_region_ground_ratio),
            "same_region_ground": float(self.same_region_ground_ratio),
            "ground_nonground": float(self.ground_nonground_ratio),
        }


@dataclass(frozen=True)
class SFCProfile:
    name: str
    stage_count_distribution: WeightedIntegerDistribution
    vnf_type_catalog: Tuple[str, ...]
    processing_coefficient_low: float = 2.5
    processing_coefficient_high: float = 5.0
    sample_with_replacement: bool = True

    def validate(self) -> None:
        if not self.name:
            raise ValueError("SFC profile name must be explicit.")
        self.stage_count_distribution.validate("SFC stage count distribution")
        if not self.vnf_type_catalog:
            raise ValueError("VNF type catalog must not be empty.")
        if len(set(self.vnf_type_catalog)) != len(self.vnf_type_catalog):
            raise ValueError("VNF type catalog must not contain duplicate labels.")
        if self.processing_coefficient_low < 0.0:
            raise ValueError("processing_coefficient_low must be nonnegative.")
        if self.processing_coefficient_high < self.processing_coefficient_low:
            raise ValueError("processing_coefficient_high must be >= low.")
        if not self.sample_with_replacement:
            max_stage_count = max(self.stage_count_distribution.probabilities)
            if max_stage_count > len(self.vnf_type_catalog):
                raise ValueError("VNF catalog is too small for sampling without replacement.")


@dataclass(frozen=True)
class DeadlineProfile:
    name: str
    slack_distribution: WeightedIntegerDistribution

    def validate(self) -> None:
        if not self.name:
            raise ValueError("Deadline profile name must be explicit.")
        self.slack_distribution.validate("deadline slack distribution")
        for slack in self.slack_distribution.probabilities:
            if slack < 1:
                raise ValueError(f"Deadline slack {slack} must be at least 1.")


@dataclass(frozen=True)
class FlowGenerationConfig:
    seed: int
    num_flows: Optional[int] = None
    base_demand_low: int = 12
    base_demand_high: int = 24
    demand_multiplier: float = 1.0
    traffic_mix_profile: TrafficMixProfile = None
    sfc_profile: SFCProfile = None
    deadline_profile: DeadlineProfile = None
    flow_id_start: int = 0
    load_profile_name: str = "Medium"

    def __post_init__(self):
        if self.traffic_mix_profile is None:
            object.__setattr__(self, "traffic_mix_profile", get_candidate_traffic_mix_profile("Candidate"))
        if self.sfc_profile is None:
            object.__setattr__(self, "sfc_profile", get_candidate_sfc_profile("Candidate"))
        if self.deadline_profile is None:
            object.__setattr__(self, "deadline_profile", get_candidate_deadline_profile("Balanced"))

    def validate(self) -> None:
        if self.num_flows is not None and self.num_flows < 0:
            raise ValueError("num_flows must be nonnegative when provided.")
        if self.base_demand_low <= 0:
            raise ValueError("base_demand_low must be positive.")
        if self.base_demand_high < self.base_demand_low:
            raise ValueError("base_demand_high must be >= base_demand_low.")
        if self.demand_multiplier <= 0.0 or not math.isfinite(self.demand_multiplier):
            raise ValueError("demand_multiplier must be finite and positive.")
        if self.flow_id_start < 0:
            raise ValueError("flow_id_start must be nonnegative.")
        self.traffic_mix_profile.validate()
        self.sfc_profile.validate()
        self.deadline_profile.validate()


@dataclass(frozen=True)
class InstanceConfig:
    topology_config: TopologyConfig
    flow_generation_config: FlowGenerationConfig
    scenario_seed: Optional[int] = None
    topology_seed: Optional[int] = None
    flow_seed: Optional[int] = None
    scale_profile_name: Optional[str] = None
    load_profile_name: Optional[str] = None
    traffic_mix_profile_name: Optional[str] = None
    sfc_profile_name: Optional[str] = None
    deadline_profile_name: Optional[str] = None

    def validate(self) -> None:
        self.topology_config.validate()
        self.flow_generation_config.validate()
        if self.scenario_seed is None and (self.topology_seed is None or self.flow_seed is None):
            raise ValueError("InstanceConfig requires scenario_seed or explicit topology_seed and flow_seed.")


_CANDIDATE_SCALES = {
    "S20": NetworkScaleConfig("S20", 8, 4, 8, 4),
    "S30": NetworkScaleConfig("S30", 12, 6, 12, 4),
    "S40": NetworkScaleConfig("S40", 16, 8, 16, 4),
    "S50": NetworkScaleConfig("S50", 20, 10, 20, 4),
}


def get_candidate_scale(name: str) -> NetworkScaleConfig:
    try:
        return _CANDIDATE_SCALES[name]
    except KeyError:
        raise ValueError(f"Unknown candidate scale {name!r}.") from None


def candidate_capacity_profile() -> CapacityProfile:
    return CapacityProfile(
        node_capacity_by_layer={
            "Space": CapacityRange(64, 96),
            "Air": CapacityRange(24, 48),
            "Ground": CapacityRange(80, 120),
        },
        link_capacity_by_type={
            "SS": CapacityRange(64, 96),
            "SA": CapacityRange(28, 56),
            "AA": CapacityRange(16, 40),
            "AG": CapacityRange(21, 42),
            "GG": CapacityRange(64, 120),
        },
    )


def candidate_dynamics_profile() -> DynamicsProfile:
    return DynamicsProfile(
        survival_probability_by_type={
            "SS": 0.95,
            "SA": 0.85,
            "AA": 0.80,
            "AG": 0.80,
            "GG": 0.95,
        },
        node_capacity_variation_ratio=0.10,
        link_capacity_variation_ratio=0.10,
        max_connectivity_resamples=100,
    )


def get_candidate_load_profile(name: str) -> float:
    try:
        return LOAD_PROFILES[name]
    except KeyError:
        raise ValueError(f"Unknown candidate load profile {name!r}.") from None


def get_candidate_traffic_mix_profile(name: str) -> TrafficMixProfile:
    if name != "Candidate":
        raise ValueError(f"Unknown candidate traffic mix profile {name!r}.")
    return TrafficMixProfile(
        name="Candidate",
        cross_region_ground_ratio=0.70,
        same_region_ground_ratio=0.20,
        ground_nonground_ratio=0.10,
    )


def get_candidate_sfc_profile(name: str) -> SFCProfile:
    if name != "Candidate":
        raise ValueError(f"Unknown candidate SFC profile {name!r}.")
    return SFCProfile(
        name="Candidate",
        stage_count_distribution=WeightedIntegerDistribution({2: 0.20, 3: 0.50, 4: 0.30}),
        vnf_type_catalog=("FW", "NAT", "DPI", "IDS", "CACHE", "TRANSCODER"),
        processing_coefficient_low=2.5,
        processing_coefficient_high=5.0,
        sample_with_replacement=True,
    )


def get_candidate_deadline_profile(name: str) -> DeadlineProfile:
    profiles = {
        "RealTime": {1: 0.50, 2: 0.50},
        "Balanced": {2: 0.30, 3: 0.40, 4: 0.30},
        "Flexible": {3: 0.30, 4: 0.40, 5: 0.30},
    }
    try:
        distribution = profiles[name]
    except KeyError:
        raise ValueError(f"Unknown candidate deadline profile {name!r}.") from None
    return DeadlineProfile(name=name, slack_distribution=WeightedIntegerDistribution(distribution))

