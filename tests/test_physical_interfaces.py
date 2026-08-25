"""Acceptance tests for externally owned physical preprocessing interfaces."""

from __future__ import annotations

import pandas as pd
import pytest

from competition.core_model import TechnologySpec
from competition.physical_interfaces import (
    FixedV0PerformanceProvider,
    HeatPumpPerformanceCoefficients,
    PhysicalInterfaceError,
    TabularASHPPerformanceProvider,
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


def test_tabular_provider_interpolates_without_extrapolation(tmp_path) -> None:
    curve = pd.DataFrame(
        {
            "technology_id": ["ASHP_BASE_01"] * 3,
            "Tout": [-5.0, 0.0, 5.0],
            "Tsupply": [45.0] * 3,
            "PLR": [1.0] * 3,
            "COP": [2.0, 3.0, 4.0],
            "capacity_ratio": [0.8, 0.9, 1.0],
        }
    )
    path = tmp_path / "curve.csv"
    curve.to_csv(path, index=False)
    provider = TabularASHPPerformanceProvider(path)
    result = provider.precompute(
        technologies=(_heat_pump("central_hp", "central", 3.2),),
        hours=(1, 2),
        timestamps=(pd.Timestamp("2021-01-01"), pd.Timestamp("2021-01-01 01:00")),
        outdoor_temperature_C=(-2.5, 5.0),
        leaving_water_temperature_C=45.0,
    )
    assert result.cop_by_technology_hour["central_hp", 1] == pytest.approx(2.5)
    assert result.capacity_ratio_by_technology_hour["central_hp", 1] == pytest.approx(0.85)
    assert result.cop_by_technology_hour["central_hp", 2] == pytest.approx(4.0)
    with pytest.raises(PhysicalInterfaceError, match="extrapolation forbidden"):
        provider.interpolate((5.1,))


def _boundary_test_curve(tmp_path):
    curve = pd.DataFrame(
        {
            "technology_id": ["ASHP_BASE_01"] * 5,
            "Tout": [-5.0, 0.0, 5.0, 10.0, 15.0],
            "Tsupply": [45.0] * 5,
            "PLR": [1.0] * 5,
            "COP": [2.0, 2.5, 3.0, 3.5, 4.0],
            "capacity_ratio": [1.0] * 5,
        }
    )
    path = tmp_path / "boundary_curve.csv"
    curve.to_csv(path, index=False)
    return path


@pytest.mark.parametrize("temperature", [5.0, -5.0, 15.0])
@pytest.mark.parametrize("policy", ["strict", "clip_with_flag"])
def test_tabular_provider_marks_in_range_and_boundary_points_inside(
    tmp_path, temperature: float, policy: str
) -> None:
    provider = TabularASHPPerformanceProvider(
        _boundary_test_curve(tmp_path), performance_boundary_policy=policy
    )
    result = provider.interpolate((temperature,))
    assert result.loc[0, "Tout_raw"] == pytest.approx(temperature)
    assert result.loc[0, "Tout_for_performance"] == pytest.approx(temperature)
    assert result.loc[0, "curve_boundary_flag"] == "inside_curve"


@pytest.mark.parametrize("temperature", [-6.0, 16.0])
def test_tabular_provider_strict_rejects_both_curve_boundaries(
    tmp_path, temperature: float
) -> None:
    provider = TabularASHPPerformanceProvider(_boundary_test_curve(tmp_path))
    with pytest.raises(PhysicalInterfaceError, match="extrapolation forbidden"):
        provider.interpolate((temperature,))


@pytest.mark.parametrize(
    ("raw", "used", "flag", "expected_cop"),
    [
        (-6.0, -5.0, "clipped_low_temperature", 2.0),
        (16.0, 15.0, "clipped_high_temperature", 4.0),
    ],
)
def test_tabular_provider_clips_lookup_only_and_records_provenance(
    tmp_path, raw: float, used: float, flag: str, expected_cop: float
) -> None:
    source = [raw]
    provider = TabularASHPPerformanceProvider(
        _boundary_test_curve(tmp_path), performance_boundary_policy="clip_with_flag"
    )
    result = provider.interpolate(source)
    assert source == [raw]
    assert result.loc[0, "Tout_C"] == pytest.approx(raw)
    assert result.loc[0, "Tout_raw"] == pytest.approx(raw)
    assert result.loc[0, "Tout_for_performance"] == pytest.approx(used)
    assert result.loc[0, "curve_boundary_flag"] == flag
    assert result.loc[0, "COP"] == pytest.approx(expected_cop)
    assert result.loc[0, "capacity_ratio"] == pytest.approx(1.0)
    assert result.loc[0, "lower_source_Tout"] == pytest.approx(used)
    assert result.loc[0, "upper_source_Tout"] == pytest.approx(used)
    assert bool(result.loc[0, "is_exact_source_point"])


def test_tabular_provider_rejects_unknown_boundary_policy(tmp_path) -> None:
    provider = TabularASHPPerformanceProvider(
        _boundary_test_curve(tmp_path), performance_boundary_policy="extrapolate"
    )
    with pytest.raises(PhysicalInterfaceError, match="performance_boundary_policy"):
        provider.interpolate((5.0,))
