from __future__ import annotations

import math

import pandas as pd
import pytest

from competition.adapters.provisional_v0 import (
    build_provisional_external_timeseries,
    electricity_price_by_hour,
    gas_carbon_kgCO2_per_kWh_LHV,
    gas_price_CNY_per_kWh_LHV,
    load_provisional_v0_profile,
)


def test_user_confirmed_tariff_covers_every_hour_exactly() -> None:
    prices = electricity_price_by_hour(load_provisional_v0_profile())
    assert prices == (
        0.48, 0.48, 0.48, 0.48, 0.48, 0.48,
        1.0, 1.0, 1.0, 1.0, 1.0, 1.0,
        0.48, 0.48, 1.0, 1.0,
        1.49, 1.49, 1.49, 1.49,
        2.0, 2.0, 1.49, 1.49,
    )


def test_gas_volume_price_is_converted_once_to_lhv_energy_price() -> None:
    profile = load_provisional_v0_profile()
    assert gas_price_CNY_per_kWh_LHV(profile) == pytest.approx(
        0.327348386, rel=0, abs=1e-9
    )
    assert gas_carbon_kgCO2_per_kWh_LHV(profile) == pytest.approx(
        0.199944, rel=0, abs=1e-12
    )


def test_external_timeseries_preserves_lhv_units_and_one_common_hour_axis() -> None:
    timestamps = pd.date_range("2026-01-01", periods=24, freq="h", tz="Asia/Shanghai")
    external, assumptions = build_provisional_external_timeseries(
        timestamps,
        range(24),
        data_version="test-v02",
    )
    assert list(external["electricity_price_CNY_per_kWh_e"]) == list(
        electricity_price_by_hour(load_provisional_v0_profile())
    )
    assert set(external["time_weight_h_per_year"]) == {1.0}
    assert set(external["data_version"]) == {"test-v02"}
    assert assumptions["derived"]["gas_conversion_count"] == 1
    assert assumptions["natural_gas"]["model_unit"] == "CNY_per_kWh_LHV"
    assert math.isfinite(assumptions["derived"]["gas_price_CNY_per_kWh_LHV"])


def test_external_timeseries_rejects_naive_or_gapped_timestamps() -> None:
    naive = pd.date_range("2026-01-01", periods=2, freq="h")
    with pytest.raises(ValueError, match="时区"):
        build_provisional_external_timeseries(naive, [0, 1], data_version="x")
    gapped = pd.date_range("2026-01-01", periods=3, freq="h", tz="Asia/Shanghai").delete(1)
    with pytest.raises(ValueError, match="连续"):
        build_provisional_external_timeseries(gapped, [0, 1], data_version="x")
