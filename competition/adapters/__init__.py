"""竞赛标准输入与上游 UrbanHeatOpt 数据结构之间的适配层。"""

from .load_timeseries import (
    CANONICAL_TIMEZONE,
    LoadTimeAdaptation,
    LoadTimeContractError,
    adapt_standard_hourly_loads,
    validate_legacy_hour_index,
)
from .wuhan_v02 import WuhanV02Adaptation, adapt_wuhan_v02_sources
from .provisional_v0 import (
    build_provisional_external_timeseries,
    electricity_price_by_hour,
    gas_carbon_kgCO2_per_kWh_LHV,
    gas_price_CNY_per_kWh_LHV,
    load_provisional_v0_profile,
)
from competition.provisional_spatial import (
    CANDIDATE_SOURCE,
    PROJECTED_CRS,
    ProvisionalSpatialError,
    ProvisionalSpatialResult,
    build_provisional_geometric_network,
    write_provisional_spatial_outputs,
)

__all__ = [
    "CANONICAL_TIMEZONE",
    "LoadTimeAdaptation",
    "LoadTimeContractError",
    "adapt_standard_hourly_loads",
    "validate_legacy_hour_index",
    "WuhanV02Adaptation",
    "adapt_wuhan_v02_sources",
    "build_provisional_external_timeseries",
    "electricity_price_by_hour",
    "gas_carbon_kgCO2_per_kWh_LHV",
    "gas_price_CNY_per_kWh_LHV",
    "load_provisional_v0_profile",
    "CANDIDATE_SOURCE",
    "PROJECTED_CRS",
    "ProvisionalSpatialError",
    "ProvisionalSpatialResult",
    "build_provisional_geometric_network",
    "write_provisional_spatial_outputs",
]
