"""竞赛负荷的最小单位与时间索引适配。

本模块只处理已经完成面积缩放的标准逐时热负荷：数值始终保持浮点
``kW``，真实时间使用 ``Asia/Shanghai`` 的 ``timestamp``，旧模型兼容
编号仅为严格连续的 ``hour=1...N``。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from pandas.api.types import is_integer_dtype, is_numeric_dtype


CANONICAL_TIMEZONE = "Asia/Shanghai"
REQUIRED_LOAD_COLUMNS = ("timestamp", "building_id", "heating_kW")


class LoadTimeContractError(ValueError):
    """标准负荷或旧小时编号不满足冻结契约。"""


@dataclass(frozen=True)
class LoadTimeAdaptation:
    """只读标准长表的一次适配结果。"""

    timestamp_hour_map: pd.DataFrame
    legacy_wide_kW: pd.DataFrame


def _require_columns(loads: pd.DataFrame) -> None:
    missing = [column for column in REQUIRED_LOAD_COLUMNS if column not in loads.columns]
    if missing:
        raise LoadTimeContractError(f"标准负荷缺少必需列：{', '.join(missing)}")


def _validate_standard_loads(loads: pd.DataFrame) -> None:
    _require_columns(loads)
    if loads.empty:
        raise LoadTimeContractError("标准负荷不能为空")

    timestamps = loads["timestamp"]
    if not isinstance(timestamps.dtype, pd.DatetimeTZDtype):
        raise LoadTimeContractError("timestamp 必须是带时区的 Parquet 时间戳")
    if str(timestamps.dt.tz) != CANONICAL_TIMEZONE:
        raise LoadTimeContractError(
            f"timestamp 时区必须为 {CANONICAL_TIMEZONE}，实际为 {timestamps.dt.tz}"
        )
    if timestamps.isna().any():
        raise LoadTimeContractError("timestamp 不得为空")
    if not timestamps.equals(timestamps.dt.floor("h")):
        raise LoadTimeContractError("timestamp 必须位于整点并表示小时区间起点")

    building_ids = loads["building_id"]
    if building_ids.isna().any() or not building_ids.map(lambda value: isinstance(value, str)).all():
        raise LoadTimeContractError("building_id 必须是非空字符串")
    if building_ids.map(lambda value: not value or value != value.strip()).any():
        raise LoadTimeContractError("building_id 不得为空或包含首尾空白")

    heating = loads["heating_kW"]
    if not is_numeric_dtype(heating.dtype) or pd.api.types.is_bool_dtype(heating.dtype):
        raise LoadTimeContractError("heating_kW 必须是数值列")
    numeric_heating = heating.to_numpy(dtype="float64", copy=False)
    if not np.isfinite(numeric_heating).all():
        raise LoadTimeContractError("heating_kW 不得包含 NaN 或无穷值")
    if (numeric_heating < 0).any():
        raise LoadTimeContractError("heating_kW 不得为负")

    if loads.duplicated(["timestamp", "building_id"]).any():
        raise LoadTimeContractError("(timestamp, building_id) 不得重复")

    observed_order = loads.loc[:, ["timestamp", "building_id"]].reset_index(drop=True)
    canonical_order = (
        loads.sort_values(["timestamp", "building_id"], kind="mergesort")
        .loc[:, ["timestamp", "building_id"]]
        .reset_index(drop=True)
    )
    if not observed_order.equals(canonical_order):
        raise LoadTimeContractError("标准负荷必须按 timestamp、building_id 升序排列")

    unique_timestamps = pd.DatetimeIndex(timestamps.drop_duplicates())
    expected_timestamps = pd.date_range(
        start=unique_timestamps[0], periods=len(unique_timestamps), freq="h"
    )
    if not unique_timestamps.equals(expected_timestamps):
        raise LoadTimeContractError("timestamp 必须严格连续，相邻间隔为 1 小时")

    expected_count = len(unique_timestamps)
    coverage = loads.groupby("building_id", sort=False)["timestamp"].nunique()
    if not coverage.eq(expected_count).all():
        raise LoadTimeContractError("所有建筑必须覆盖完全相同的逐小时时间集合")


def adapt_standard_hourly_loads(loads: pd.DataFrame) -> LoadTimeAdaptation:
    """将标准长表稳定映射为旧模型宽表，不执行任何功率单位换算。"""

    _validate_standard_loads(loads)
    timestamps = pd.DatetimeIndex(loads["timestamp"].drop_duplicates())
    hours = np.arange(1, len(timestamps) + 1, dtype=np.int64)
    timestamp_hour_map = pd.DataFrame({"timestamp": timestamps, "hour": hours})

    building_ids = sorted(loads["building_id"].unique())
    wide = loads.pivot(index="timestamp", columns="building_id", values="heating_kW")
    wide = wide.reindex(index=timestamps, columns=building_ids).astype("float64")
    wide.columns.name = None
    wide.insert(0, "hour", hours)
    wide.reset_index(drop=True, inplace=True)

    return LoadTimeAdaptation(
        timestamp_hour_map=timestamp_hour_map,
        legacy_wide_kW=wide,
    )


def validate_legacy_hour_index(hourly_wide: pd.DataFrame) -> None:
    """拒绝不是严格整数 ``1...N`` 的旧模型小时索引。"""

    if "hour" not in hourly_wide.columns:
        raise LoadTimeContractError("旧模型宽表缺少 hour 列")
    if "timestamp" in hourly_wide.columns:
        raise LoadTimeContractError("旧模型宽表只能使用 hour，不能同时包含 timestamp")
    hours = hourly_wide["hour"]
    if hours.empty or not is_integer_dtype(hours.dtype):
        raise LoadTimeContractError("hour 必须是非空整数列")
    expected = np.arange(1, len(hours) + 1, dtype=np.int64)
    if not np.array_equal(hours.to_numpy(), expected):
        raise LoadTimeContractError("hour 必须严格连续且等于 1...N")
