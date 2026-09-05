"""Traceable V0-only energy price and operating-carbon assumptions."""

from __future__ import annotations

from urbanheatopt.paths import REPOSITORY_ROOT as REPOSITORY_ROOT_PATH, PACKAGE_ROOT

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import yaml


PROFILE_PATH = PACKAGE_ROOT / "data" / "profile_resources" / "provisional_v0.yaml"


def load_provisional_v0_profile(path: str | Path | None = None) -> dict[str, Any]:
    profile_path = Path(path).resolve() if path is not None else PROFILE_PATH
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    if not isinstance(profile, dict) or profile.get("assumption_profile") != "provisional_v0":
        raise ValueError("假设 profile 必须声明 assumption_profile: provisional_v0")
    if profile.get("classification") != "scenario_assumption":
        raise ValueError("V0 profile 必须标记 classification: scenario_assumption")
    _validate_tariff(profile["electricity_tariff"]["periods"])
    gas = profile["natural_gas"]
    for key in ("raw_price_CNY_per_Nm3", "LHV_MJ_per_Nm3"):
        value = gas.get(key)
        if isinstance(value, bool) or not np.isfinite(float(value)) or float(value) <= 0:
            raise ValueError(f"natural_gas.{key} 必须为有限正数")
    return deepcopy(profile)


def _validate_tariff(periods: Iterable[dict[str, Any]]) -> None:
    assigned: list[float | None] = [None] * 24
    for period in periods:
        start = period.get("start_hour")
        end = period.get("end_hour")
        price = period.get("price")
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
            raise ValueError("电价时段 start_hour/end_hour 必须为整数")
        if not 0 <= start < end <= 24:
            raise ValueError("电价时段必须位于 [0,24] 且左闭右开")
        if isinstance(price, bool) or not np.isfinite(float(price)) or float(price) < 0:
            raise ValueError("电价必须为有限非负数")
        for hour in range(start, end):
            if assigned[hour] is not None:
                raise ValueError(f"电价时段在 {hour}:00 重叠")
            assigned[hour] = float(price)
    if any(value is None for value in assigned):
        raise ValueError("电价时段必须无缺口覆盖 00:00–24:00")


def electricity_price_by_hour(profile: dict[str, Any]) -> tuple[float, ...]:
    periods = profile["electricity_tariff"]["periods"]
    _validate_tariff(periods)
    prices = [0.0] * 24
    for period in periods:
        for hour in range(int(period["start_hour"]), int(period["end_hour"])):
            prices[hour] = float(period["price"])
    return tuple(prices)


def gas_price_CNY_per_kWh_LHV(profile: dict[str, Any]) -> float:
    gas = profile["natural_gas"]
    energy_kWh_LHV_per_Nm3 = float(gas["LHV_MJ_per_Nm3"]) / 3.6
    return float(gas["raw_price_CNY_per_Nm3"]) / energy_kWh_LHV_per_Nm3


def gas_carbon_kgCO2_per_kWh_LHV(profile: dict[str, Any]) -> float:
    # t/GJ × 1000 kg/t × 0.0036 GJ/kWh
    return float(profile["carbon"]["gas_tCO2_per_GJ_LHV"]) * 3.6


def build_provisional_external_timeseries(
    timestamps: Iterable[pd.Timestamp],
    outdoor_temperature_C: Iterable[float],
    *,
    data_version: str,
    profile_path: str | Path | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    profile = load_provisional_v0_profile(profile_path)
    times = tuple(pd.Timestamp(item) for item in timestamps)
    temperatures = tuple(float(item) for item in outdoor_temperature_C)
    if not times or len(times) != len(temperatures):
        raise ValueError("timestamp 与室外温度必须非空且长度一致")
    index = pd.DatetimeIndex(times)
    if index.tz is None or str(index.tz) != "Asia/Shanghai":
        raise ValueError("timestamp 必须使用 Asia/Shanghai 时区")
    if index.has_duplicates or not index.is_monotonic_increasing:
        raise ValueError("timestamp 必须唯一且严格升序")
    if len(index) > 1 and not np.all(np.diff(index.asi8) == pd.Timedelta(hours=1).value):
        raise ValueError("timestamp 必须严格连续 1 小时")
    temperature_array = np.asarray(temperatures, dtype=float)
    if not np.isfinite(temperature_array).all():
        raise ValueError("室外温度必须为有限数值")
    price_by_hour = electricity_price_by_hour(profile)
    gas_price = gas_price_CNY_per_kWh_LHV(profile)
    gas_carbon = gas_carbon_kgCO2_per_kWh_LHV(profile)
    external = pd.DataFrame(
        {
            "timestamp": index,
            "outdoor_temperature_C": temperature_array,
            "time_weight_h_per_year": np.ones(len(index), dtype=float),
            "electricity_price_CNY_per_kWh_e": [price_by_hour[item.hour] for item in index],
            "gas_price_CNY_per_kWh_LHV": np.full(len(index), gas_price),
            "electricity_carbon_kgCO2e_per_kWh_e": np.full(
                len(index), float(profile["carbon"]["electricity_kgCO2e_per_kWh_e"])
            ),
            "gas_carbon_kgCO2e_per_kWh_LHV": np.full(len(index), gas_carbon),
            "data_version": [data_version] * len(index),
        }
    )
    assumptions = deepcopy(profile)
    assumptions["derived"] = {
        "gas_energy_kWh_LHV_per_Nm3": float(profile["natural_gas"]["LHV_MJ_per_Nm3"]) / 3.6,
        "gas_price_CNY_per_kWh_LHV": gas_price,
        "gas_carbon_kgCO2_per_kWh_LHV": gas_carbon,
        "gas_conversion_count": 1,
        "gas_carbon_output_field_note": "V0数值为直接燃烧CO2；正式CO2e范围仍受发布门阻止",
    }
    return external, assumptions
