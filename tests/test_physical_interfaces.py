"""Acceptance tests for externally owned physical preprocessing interfaces."""

from __future__ import annotations

import pandas as pd
import pytest

from competition.core_model import TechnologySpec
from competition.physical_interfaces import (
    FixedV0PerformanceProvider,
    HeatPumpPerformanceCoefficients,
    PhysicalInterfaceError,
    validate_performance_coefficients,
)


def _heat_pump(technology_id: str, scope: str, cop: float) -> TechnologySpec:
    return TechnologySpec(
        technology_id=technology_id,
        technology_type="air_source_heat_pump",
        applicable_scope=scope,
        energy_carrier="electricity",
        cop=cop,
        efficiency=None,
        capacity_min_kW=0,
        capacity_max_kW=100,
        capex_CNY_per_kW=0,
        fixed_maintenance_fraction_per_year=0,
        variable_om_CNY_per_kWh_th=0,
        lifetime_years=20,
        source="synthetic_test",
        assumption_flag="synthetic_test",
    )


def test_fixed_v0_provider_precomputes_complete_immutable_coefficients() -> None:
    technologies = (_heat_pump("central_hp", "central", 4), _heat_pump("local_hp", "local", 3))
    timestamps = (
        pd.Timestamp("2026-01-01T00:00:00+08:00"),
        pd.Timestamp("2026-01-01T01:00:00+08:00"),
    )
    result = FixedV0PerformanceProvider().precompute(
        technologies=technologies,
        hours=(1, 2),
        timestamps=timestamps,
        outdoor_temperature_C=(-5, -4),
        leaving_water_temperature_C=50,
    )
    validate_performance_coefficients(
        result,
        technology_ids=("central_hp", "local_hp"),
        hours=(1, 2),
    )
    assert result.cop_by_technology_hour["central_hp", 2] == pytest.approx(4)
    assert result.capacity_ratio_by_technology_hour["local_hp", 1] == pytest.approx(1)
    with pytest.raises(TypeError):
        result.cop_by_technology_hour["central_hp", 1] = 99


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_performance_interface_rejects_invalid_cop(bad: float) -> None:
    values = HeatPumpPerformanceCoefficients(
        {("hp", 1): bad},
        {("hp", 1): 1.0},
        "test_provider",
        "test-v1",
    )
    with pytest.raises(PhysicalInterfaceError, match="cop"):
        validate_performance_coefficients(values, technology_ids=("hp",), hours=(1,))


def test_performance_interface_rejects_incomplete_or_over_unity_derating() -> None:
    incomplete = HeatPumpPerformanceCoefficients({}, {}, "test_provider", "test-v1")
    with pytest.raises(PhysicalInterfaceError, match="覆盖"):
        validate_performance_coefficients(incomplete, technology_ids=("hp",), hours=(1,))

    invalid_ratio = HeatPumpPerformanceCoefficients(
        {("hp", 1): 3.0},
        {("hp", 1): 1.01},
        "test_provider",
        "test-v1",
    )
    with pytest.raises(PhysicalInterfaceError, match="capacity_ratio"):
        validate_performance_coefficients(invalid_ratio, technology_ids=("hp",), hours=(1,))
