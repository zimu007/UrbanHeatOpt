"""Stable interfaces for externally owned physical preprocessing modules.

The Pyomo core consumes only validated linear coefficients. Candidate generation
and temperature-performance algorithms stay outside the optimization model and
must return these explicit boundary objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from numbers import Real
from pathlib import Path
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


@dataclass(frozen=True, slots=True)
class TabularASHPPerformanceProvider:
    """Precompute linear ASHP coefficients from a validated operating-point table.

    V0.1 deliberately selects one supply-temperature and one performance PLR
    layer, then interpolates only on outdoor temperature.  No optimization PLR
    variable is introduced and extrapolation is rejected.
    """

    curve_path: str | Path
    performance_technology_id: str = "ASHP_BASE_01"
    supply_temperature_C: float = 45.0
    plr_layer: float = 1.0
    provider_name: str = "tabular_ashp_tout_linear"
    parameter_version: str = "guanggu-v0.3-20260823"

    def _selected_curve(self) -> pd.DataFrame:
        table = pd.read_csv(Path(self.curve_path), encoding="utf-8-sig")
        aliases = {
            "technology_id": ("technology_id",),
            "Tout_C": ("Tout_C", "Tout", "outdoor_temperature_C"),
            "Tsupply_C": ("Tsupply_C", "Tsupply", "supply_temperature_C"),
            "PLR": ("PLR", "plr"),
            "COP": ("COP", "cop"),
            "capacity_ratio": ("capacity_ratio",),
        }
        resolved: dict[str, str] = {}
        for standard, candidates in aliases.items():
            match = next((name for name in candidates if name in table.columns), None)
            if match is None:
                raise PhysicalInterfaceError(f"performance table missing {standard}")
            resolved[standard] = match
        selected = table[
            table[resolved["technology_id"]].astype(str).eq(self.performance_technology_id)
            & pd.to_numeric(table[resolved["Tsupply_C"]], errors="coerce").eq(
                float(self.supply_temperature_C)
            )
            & pd.to_numeric(table[resolved["PLR"]], errors="coerce").eq(
                float(self.plr_layer)
            )
        ].copy()
        selected = selected.rename(columns={value: key for key, value in resolved.items()})
        selected = selected[["Tout_C", "COP", "capacity_ratio"]].apply(
            pd.to_numeric, errors="coerce"
        ).sort_values("Tout_C")
        if len(selected) < 2 or selected.isna().any().any() or selected["Tout_C"].duplicated().any():
            raise PhysicalInterfaceError("selected ASHP curve must contain at least two unique finite Tout points")
        if (selected[["COP", "capacity_ratio"]] <= 0).any().any():
            raise PhysicalInterfaceError("ASHP COP and capacity_ratio must be positive")
        if (selected["capacity_ratio"] > 1).any():
            raise PhysicalInterfaceError("ASHP capacity_ratio must not exceed 1")
        return selected.reset_index(drop=True)

    def interpolate(self, outdoor_temperature_C: Sequence[float]) -> pd.DataFrame:
        import numpy as np

        curve = self._selected_curve()
        source_t = curve["Tout_C"].to_numpy(dtype=float)
        target = np.asarray(tuple(float(value) for value in outdoor_temperature_C), dtype=float)
        if not np.isfinite(target).all():
            raise PhysicalInterfaceError("outdoor temperature must be finite")
        outside = (target < source_t[0]) | (target > source_t[-1])
        if outside.any():
            values = sorted(set(target[outside].tolist()))
            raise PhysicalInterfaceError(
                f"ASHP Tout outside [{source_t[0]}, {source_t[-1]}] degC; extrapolation forbidden: {values}"
            )
        upper_index = np.searchsorted(source_t, target, side="left")
        upper_index = np.clip(upper_index, 0, len(source_t) - 1)
        exact = source_t[upper_index] == target
        lower_index = np.where(exact, upper_index, upper_index - 1)
        return pd.DataFrame(
            {
                "Tout_C": target,
                "COP": np.interp(target, source_t, curve["COP"].to_numpy(dtype=float)),
                "capacity_ratio": np.interp(
                    target, source_t, curve["capacity_ratio"].to_numpy(dtype=float)
                ),
                "lower_source_Tout": source_t[lower_index],
                "upper_source_Tout": source_t[upper_index],
                "is_exact_source_point": exact,
            }
        )

    def precompute(
        self,
        *,
        technologies: tuple[TechnologySpec, ...],
        hours: tuple[int, ...],
        timestamps: tuple[pd.Timestamp, ...],
        outdoor_temperature_C: tuple[float, ...],
        leaving_water_temperature_C: float,
    ) -> HeatPumpPerformanceCoefficients:
        if not (
            len(hours) == len(timestamps) == len(outdoor_temperature_C)
        ):
            raise PhysicalInterfaceError("hours, timestamps and Tout lengths must match")
        if abs(float(leaving_water_temperature_C) - float(self.supply_temperature_C)) > 1e-9:
            raise PhysicalInterfaceError("case supply temperature does not match selected ASHP layer")
        interpolated = self.interpolate(outdoor_temperature_C)
        heat_pumps = tuple(
            spec for spec in technologies if spec.technology_type == AIR_SOURCE_HEAT_PUMP
        )
        cop = {
            (spec.technology_id, hour): float(interpolated.iloc[index]["COP"])
            for spec in heat_pumps for index, hour in enumerate(hours)
        }
        ratio = {
            (spec.technology_id, hour): float(interpolated.iloc[index]["capacity_ratio"])
            for spec in heat_pumps for index, hour in enumerate(hours)
        }
        return HeatPumpPerformanceCoefficients(
            cop, ratio, provider_name=self.provider_name, parameter_version=self.parameter_version
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
