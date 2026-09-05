"""Immutable canonical boundary between competition files and the Pyomo core."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

import pandas as pd

from urbanheatopt.model.reference_core import (
    CoreModelInput,
    EconomicInput,
    PipeLevelSpec,
    SegmentSpec,
    TechnologySpec,
    ThermalStorageSpec,
)
from urbanheatopt.model.physical_interfaces import HeatPumpPerformanceCoefficients
from urbanheatopt.optimization.solvers import SolverSettings


V3_DRAFT_CONTRACT = "competition_input_3.0.0-draft.2"
V3_FINAL_CONTRACT = "competition_input_3.0.0"
TEST_RELEASE_TRACK = "test_v0"
FORMAL_RELEASE_TRACK = "formal_v1"
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
class CanonicalSeasonData:
    """Immutable accepted-data snapshot before model-readiness enrichment.

    DataFrames are copied on construction and public accessors return another
    deep copy.  This keeps source acceptance independent from missing network,
    storage, economic or other model inputs.
    """

    source_profile: str
    contract_version: str
    data_version: str
    _buildings: pd.DataFrame = field(repr=False)
    _building_archetype_map: pd.DataFrame = field(repr=False)
    _loads: pd.DataFrame = field(repr=False)
    _external_timeseries: pd.DataFrame = field(repr=False)
    _technology_parameters: pd.DataFrame = field(repr=False)
    _equipment_performance: pd.DataFrame = field(repr=False)
    _timestamp_hour_map: pd.DataFrame = field(repr=False)
    input_sha256: Mapping[str, str]
    adaptation_metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        for field_name in (
            "_buildings",
            "_building_archetype_map",
            "_loads",
            "_external_timeseries",
            "_technology_parameters",
            "_equipment_performance",
            "_timestamp_hour_map",
        ):
            object.__setattr__(self, field_name, getattr(self, field_name).copy(deep=True))
        object.__setattr__(self, "input_sha256", _freeze(dict(self.input_sha256)))
        object.__setattr__(
            self,
            "adaptation_metadata",
            _freeze(dict(self.adaptation_metadata)),
        )

    @property
    def buildings(self) -> pd.DataFrame:
        return self._buildings.copy(deep=True)

    @property
    def building_archetype_map(self) -> pd.DataFrame:
        return self._building_archetype_map.copy(deep=True)

    @property
    def loads(self) -> pd.DataFrame:
        return self._loads.copy(deep=True)

    @property
    def external_timeseries(self) -> pd.DataFrame:
        return self._external_timeseries.copy(deep=True)

    @property
    def technology_parameters(self) -> pd.DataFrame:
        return self._technology_parameters.copy(deep=True)

    @property
    def equipment_performance(self) -> pd.DataFrame:
        return self._equipment_performance.copy(deep=True)

    @property
    def timestamp_hour_map(self) -> pd.DataFrame:
        return self._timestamp_hour_map.copy(deep=True)

    @property
    def building_count(self) -> int:
        return len(self._buildings)

    @property
    def hour_count(self) -> int:
        return len(self._external_timeseries)

    @property
    def load_row_count(self) -> int:
        return len(self._loads)

    def to_summary(self) -> dict[str, Any]:
        return {
            "source_profile": self.source_profile,
            "contract_version": self.contract_version,
            "data_version": self.data_version,
            "building_count": self.building_count,
            "hour_count": self.hour_count,
            "load_row_count": self.load_row_count,
            "technology_parameter_count": len(self._technology_parameters),
            "equipment_performance_point_count": len(self._equipment_performance),
            "input_file_count": len(self.input_sha256),
        }


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
    max_charge_ratio_per_hour: float | None = None
    max_discharge_ratio_per_hour: float | None = None


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
    heat_loss_fraction_per_m: float = 0.0


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
    site_node: str | None
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
    peak_capacity_margin_fraction: float = 0.0
    candidate_station_nodes: tuple[str, ...] = ()
    max_built_stations: int = 1

    def __post_init__(self) -> None:
        allowed_pairs = {
            (V3_DRAFT_CONTRACT, TEST_RELEASE_TRACK),
            (V3_FINAL_CONTRACT, FORMAL_RELEASE_TRACK),
        }
        if (self.contract_version, self.software_release_track) not in allowed_pairs:
            raise ValueError("contract_version 与 software_release_track 组合无效")
        if self.profile == "v1-full" and self.contract_version != V3_FINAL_CONTRACT:
            raise ValueError("v1-full 必须使用冻结的 competition_input_3.0.0 契约")
        if self.profile != "v1-full" and self.contract_version != V3_DRAFT_CONTRACT:
            raise ValueError("V0 调试配置必须使用 draft.2 契约")
        if self.modes != CANONICAL_MODES:
            raise ValueError("modes 必须按 central, distributed, hybrid 固定排序")
        if self.hours != tuple(range(1, len(self.timestamps) + 1)):
            raise ValueError("hours 必须与 timestamps 一一映射为 1...N")
        if len(set(self.demand_nodes)) != len(self.demand_nodes):
            raise ValueError("demand_nodes 不得重复")
        stations = self.candidate_station_nodes or (
            (self.site_node,) if self.site_node is not None else ()
        )
        if not stations or len(set(stations)) != len(stations):
            raise ValueError("candidate_station_nodes must be non-empty and unique")
        if set(stations).intersection(self.demand_nodes):
            raise ValueError("candidate station IDs must not overlap demand_nodes")
        if len(stations) == 1:
            if self.site_node is not None and self.site_node != stations[0]:
                raise ValueError("singleton site_node must match candidate_station_nodes")
            object.__setattr__(self, "site_node", stations[0])
        elif self.site_node is not None:
            raise ValueError("site_node must be None for multiple candidate stations")
        if self.max_built_stations != 1:
            raise ValueError("MULTI_STATION_OPERATION_OUT_OF_SCOPE_FOR_V1")
        object.__setattr__(self, "candidate_station_nodes", tuple(stations))
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
                max_charge_ratio_per_hour=self.storage.max_charge_ratio_per_hour,
                max_discharge_ratio_per_hour=self.storage.max_discharge_ratio_per_hour,
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
                    heat_loss_fraction_per_m=item.heat_loss_fraction_per_m,
                )
                for item in self.pipe_types
            ),
            heat_pump_cop_by_hour=self.heat_pump_performance.cop_by_technology_hour,
            heat_pump_capacity_ratio_by_hour=(
                self.heat_pump_performance.capacity_ratio_by_technology_hour
            ),
            allow_unserved=self.profile != "v1-full",
            peak_capacity_margin_fraction=self.peak_capacity_margin_fraction,
            candidate_station_nodes=self.candidate_station_nodes,
            max_built_stations=self.max_built_stations,
        )
