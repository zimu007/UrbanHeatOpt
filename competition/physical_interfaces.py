"""Stable interfaces for externally owned physical preprocessing modules.

The Pyomo core consumes only validated linear coefficients. Candidate generation
and temperature-performance algorithms stay outside the optimization model and
must return these explicit boundary objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from numbers import Real
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

import geopandas as gpd
import pandas as pd

from competition.core_model import AIR_SOURCE_HEAT_PUMP, TechnologySpec


class PhysicalInterfaceError(ValueError):
    """An external physical module returned an invalid or incomplete boundary."""


@dataclass(frozen=True, slots=True)
class HeatPumpPerformanceCoefficients:
    """Precomputed linear COP and available-capacity coefficients.

    Keys are ``(technology_id, hour)``. COP is dimensionless and positive;
    capacity ratio is dimensionless in ``(0, 1]``.
    """

    cop_by_technology_hour: Mapping[tuple[str, int], float]
    capacity_ratio_by_technology_hour: Mapping[tuple[str, int], float]
    provider_name: str
    parameter_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "cop_by_technology_hour",
            MappingProxyType(dict(self.cop_by_technology_hour)),
        )
        object.__setattr__(
            self,
            "capacity_ratio_by_technology_hour",
            MappingProxyType(dict(self.capacity_ratio_by_technology_hour)),
        )
        for field_name in ("provider_name", "parameter_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise PhysicalInterfaceError(f"{field_name} 必须是无首尾空白的非空字符串")


@runtime_checkable
class HeatPumpPerformanceProvider(Protocol):
    """Interface owned by the temperature-COP module responsible person."""

    def precompute(
        self,
        *,
        technologies: tuple[TechnologySpec, ...],
        hours: tuple[int, ...],
        timestamps: tuple[pd.Timestamp, ...],
        outdoor_temperature_C: tuple[float, ...],
        leaving_water_temperature_C: float,
    ) -> HeatPumpPerformanceCoefficients:
        """Return one coefficient pair per heat pump and hour."""


@dataclass(frozen=True, slots=True)
class FixedV0PerformanceProvider:
    """V0-only provider: use each technology's fixed COP and no derating."""

    provider_name: str = "fixed_v0"
    parameter_version: str = "fixed-v0-1"

    def precompute(
        self,
        *,
        technologies: tuple[TechnologySpec, ...],
        hours: tuple[int, ...],
        timestamps: tuple[pd.Timestamp, ...],
        outdoor_temperature_C: tuple[float, ...],
        leaving_water_temperature_C: float,
    ) -> HeatPumpPerformanceCoefficients:
        if len(hours) != len(timestamps) or len(hours) != len(outdoor_temperature_C):
            raise PhysicalInterfaceError("V0 性能输入的 hours、timestamps 和室外温度长度必须一致")
        if not isfinite(float(leaving_water_temperature_C)):
            raise PhysicalInterfaceError("leaving_water_temperature_C 必须是有限数值")
        heat_pumps = tuple(
            spec for spec in technologies if spec.technology_type == AIR_SOURCE_HEAT_PUMP
        )
        cop: dict[tuple[str, int], float] = {}
        ratio: dict[tuple[str, int], float] = {}
        for spec in heat_pumps:
            if spec.cop is None or not isfinite(float(spec.cop)) or float(spec.cop) <= 0:
                raise PhysicalInterfaceError(f"{spec.technology_id} 缺少 V0 固定 COP")
            for hour in hours:
                cop[spec.technology_id, hour] = float(spec.cop)
                ratio[spec.technology_id, hour] = 1.0
        return HeatPumpPerformanceCoefficients(
            cop,
            ratio,
            provider_name=self.provider_name,
            parameter_version=self.parameter_version,
        )


def validate_performance_coefficients(
    coefficients: HeatPumpPerformanceCoefficients,
    *,
    technology_ids: Sequence[str],
    hours: Sequence[int],
) -> None:
    """Reject incomplete, non-finite, or out-of-range provider output."""

    expected = {(technology_id, hour) for technology_id in technology_ids for hour in hours}
    mappings = (
        (coefficients.cop_by_technology_hour, "cop", False),
        (coefficients.capacity_ratio_by_technology_hour, "capacity_ratio", True),
    )
    for values, name, is_ratio in mappings:
        if set(values) != expected:
            raise PhysicalInterfaceError(f"{name} 必须且只能覆盖全部热泵 technology_id × hours")
        for key in expected:
            value = values[key]
            if isinstance(value, bool) or not isinstance(value, Real):
                raise PhysicalInterfaceError(f"{name}{key} 必须是有限数值")
            number = float(value)
            if not isfinite(number) or number <= 0 or (is_ratio and number > 1):
                interval = "(0, 1]" if is_ratio else "大于 0"
                raise PhysicalInterfaceError(f"{name}{key} 必须为 {interval}")


@dataclass(frozen=True, slots=True)
class CandidateLayout:
    """Candidate generator output; it is validated before canonical projection."""

    sites: gpd.GeoDataFrame
    physical_segments: gpd.GeoDataFrame
    provider_name: str
    parameter_version: str

    def snapshot(self) -> "CandidateLayout":
        return CandidateLayout(
            self.sites.copy(deep=True),
            self.physical_segments.copy(deep=True),
            self.provider_name,
            self.parameter_version,
        )


@runtime_checkable
class CandidateNetworkProvider(Protocol):
    """Interface for candidate-site and road-aware candidate-network modules."""

    def generate(
        self,
        *,
        buildings: gpd.GeoDataFrame,
        roads_or_feasible_space: gpd.GeoDataFrame,
        projected_crs: str,
        spatial_config: Mapping[str, Any],
        random_seed: int,
    ) -> CandidateLayout:
        """Return candidates only; the Pyomo model decides what is built."""
