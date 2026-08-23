"""Immutable canonical boundary between competition files and the Pyomo core."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

import pandas as pd

from competition.core_model import (
    CoreModelInput,
    EconomicInput,
    PipeLevelSpec,
    SegmentSpec,
    TechnologySpec,
    ThermalStorageSpec,
)
from competition.physical_interfaces import HeatPumpPerformanceCoefficients
from competition.solvers import SolverSettings


V3_DRAFT_CONTRACT = "competition_input_3.0.0-draft.2"
TEST_RELEASE_TRACK = "test_v0"
CANONICAL_MODES = ("central", "distributed", "hybrid")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class StorageSpec:
    """Canonical storage interface retained even before the core consumes it."""

    technology_id: str
    energy_capacity_max_kWh_th: float
    charge_capacity_max_kW_th: float
    discharge_capacity_max_kW_th: float
    charge_efficiency: float
    discharge_efficiency: float
    standing_loss_fraction_per_hour: float
    capex_CNY_per_kWh_th: float
    power_capex_CNY_per_kW_th: float
    fixed_capex_CNY: float
    lifetime_years: int
    source: str
    parameter_version: str


@dataclass(frozen=True, slots=True)
class PipeTypeSpec:
    pipe_type_id: str
    level: int
    capacity_max_kW_th: float
    capex_CNY_per_m: float
    heat_loss_kW_per_m: float
    pumping_kWh_e_per_kWh_th_transferred: float
    lifetime_years: int
    source: str
    parameter_version: str


@dataclass(frozen=True, slots=True)
class CanonicalCaseData:
    """Read-only, unit-explicit snapshot used by every supply mode."""

    contract_version: str
    software_release_track: str
    case_id: str
    scenario_id: str
    data_version: str
    profile: str
    modes: tuple[str, ...]
    timestamps: tuple[pd.Timestamp, ...]
    hours: tuple[int, ...]
    site_node: str
    demand_nodes: tuple[str, ...]
    heat_demand_kW_th: Mapping[tuple[str, int], float]
    technologies: tuple[TechnologySpec, ...]
    storage: StorageSpec
    segments: tuple[SegmentSpec, ...]
    pipe_types: tuple[PipeTypeSpec, ...]
    heat_pump_performance: HeatPumpPerformanceCoefficients
    economics: EconomicInput
    solver: SolverSettings
    input_sha256: Mapping[str, str]
    parameter_versions: Mapping[str, str]
    raw_config: Mapping[str, Any]
    building_archetype_map: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.contract_version != V3_DRAFT_CONTRACT:
            raise ValueError(f"canonical contract_version 必须为 {V3_DRAFT_CONTRACT}")
        if self.software_release_track != TEST_RELEASE_TRACK:
            raise ValueError("canonical software_release_track 必须为 test_v0")
        if self.modes != CANONICAL_MODES:
            raise ValueError("modes 必须按 central, distributed, hybrid 固定排序")
        if self.hours != tuple(range(1, len(self.timestamps) + 1)):
            raise ValueError("hours 必须与 timestamps 一一映射为 1...N")
        if len(set(self.demand_nodes)) != len(self.demand_nodes):
            raise ValueError("demand_nodes 不得重复")
        object.__setattr__(self, "heat_demand_kW_th", _freeze(dict(self.heat_demand_kW_th)))
        object.__setattr__(self, "input_sha256", _freeze(dict(self.input_sha256)))
        object.__setattr__(self, "parameter_versions", _freeze(dict(self.parameter_versions)))
        object.__setattr__(self, "raw_config", _freeze(dict(self.raw_config)))
        object.__setattr__(self, "building_archetype_map", _freeze(self.building_archetype_map))

    def to_core_input(self, mode: str) -> CoreModelInput:
        """Project one common canonical snapshot into the existing unified core."""

        if mode not in self.modes:
            raise ValueError(f"未知供热模式：{mode}")
        return CoreModelInput(
            mode=mode,
            hours=self.hours,
            site_node=self.site_node,
            demand_nodes=self.demand_nodes,
            heat_demand_kW=self.heat_demand_kW_th,
            technologies=self.technologies,
            segments=self.segments,
            economics=self.economics,
            storage=ThermalStorageSpec(
                technology_id=self.storage.technology_id,
                energy_capacity_max_kWh_th=self.storage.energy_capacity_max_kWh_th,
                charge_capacity_max_kW_th=self.storage.charge_capacity_max_kW_th,
                discharge_capacity_max_kW_th=self.storage.discharge_capacity_max_kW_th,
                charge_efficiency=self.storage.charge_efficiency,
                discharge_efficiency=self.storage.discharge_efficiency,
                standing_loss_fraction_per_hour=self.storage.standing_loss_fraction_per_hour,
                capex_CNY_per_kWh_th=self.storage.capex_CNY_per_kWh_th,
                power_capex_CNY_per_kW_th=self.storage.power_capex_CNY_per_kW_th,
                fixed_capex_CNY=self.storage.fixed_capex_CNY,
                lifetime_years=self.storage.lifetime_years,
            ),
            pipe_levels=tuple(
                PipeLevelSpec(
                    pipe_type_id=item.pipe_type_id,
                    level=item.level,
                    capacity_max_kW_th=item.capacity_max_kW_th,
                    capex_CNY_per_m=item.capex_CNY_per_m,
                    lifetime_years=item.lifetime_years,
                    heat_loss_kW_per_m=item.heat_loss_kW_per_m,
                    pumping_kWh_e_per_kWh_th_transferred=(
                        item.pumping_kWh_e_per_kWh_th_transferred
                    ),
                )
                for item in self.pipe_types
            ),
            heat_pump_cop_by_hour=self.heat_pump_performance.cop_by_technology_hour,
            heat_pump_capacity_ratio_by_hour=(
                self.heat_pump_performance.capacity_ratio_by_technology_hour
            ),
            allow_unserved=self.profile != "v1-full",
        )
