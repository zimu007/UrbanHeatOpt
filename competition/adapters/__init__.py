"""竞赛标准输入与上游 UrbanHeatOpt 数据结构之间的适配层。"""

from .load_timeseries import (
    CANONICAL_TIMEZONE,
    LoadTimeAdaptation,
    LoadTimeContractError,
    adapt_standard_hourly_loads,
    validate_legacy_hour_index,
)

__all__ = [
    "CANONICAL_TIMEZONE",
    "LoadTimeAdaptation",
    "LoadTimeContractError",
    "adapt_standard_hourly_loads",
    "validate_legacy_hour_index",
]
