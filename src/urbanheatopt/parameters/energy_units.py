"""竞赛版经济输入的显式单位标准化工具。"""

from __future__ import annotations

from math import isfinite
from numbers import Real


class EconomicStandardizationError(ValueError):
    """经济输入无法按已声明单位安全标准化。"""


def _finite_real(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise EconomicStandardizationError(f"{field} 必须是有限数值")
    normalized = float(value)
    if not isfinite(normalized):
        raise EconomicStandardizationError(f"{field} 必须是有限数值")
    return normalized


def standardize_gas_price_CNY_per_kWh_LHV(
    gas_price_CNY_per_volume_unit: float,
    gas_lhv_MJ_LHV_per_same_volume_unit: float,
) -> float:
    """把体积气价在输入边界转换一次为 ``CNY/kWh_LHV``。

    两个入参必须采用完全相同的体积基准，例如都以 ``Nm3`` 为分母。
    本函数不猜测体积状态、气质或低位热值，也不在模型内部重复换算。
    """

    volume_price = _finite_real(
        gas_price_CNY_per_volume_unit,
        "gas_price_CNY_per_volume_unit",
    )
    lhv_mj = _finite_real(
        gas_lhv_MJ_LHV_per_same_volume_unit,
        "gas_lhv_MJ_LHV_per_same_volume_unit",
    )
    if volume_price < 0:
        raise EconomicStandardizationError(
            "gas_price_CNY_per_volume_unit 必须大于等于 0"
        )
    if lhv_mj <= 0:
        raise EconomicStandardizationError(
            "gas_lhv_MJ_LHV_per_same_volume_unit 必须大于 0"
        )

    lhv_kwh_per_volume_unit = lhv_mj / 3.6
    return volume_price / lhv_kwh_per_volume_unit
