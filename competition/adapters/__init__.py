"""竞赛标准输入与上游 UrbanHeatOpt 数据结构之间的适配层。"""

from .load_timeseries import (
    CANONICAL_TIMEZONE,
    LoadTimeAdaptation,
    LoadTimeContractError,
    adapt_standard_hourly_loads,
    validate_legacy_hour_index,
)
from .wuhan_v02 import WuhanV02Adaptation, adapt_wuhan_v02_sources
from .guanggu_v03 import (
    CanonicalSeasonValidationReport,
    GuangguV03Adaptation,
    adapt_guanggu_v03_sources,
    validate_canonical_season_data,
)
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
from .wuhan_v02_case import (
    PreparedWuhanV02Case,
    SmokeScope,
    WuhanV02CaseError,
    prepare_wuhan_v02_v0_case,
    select_v0_smoke_scope,
)
from .guanggu_v03_case import (
    GuangguV03CaseError,
    PreparedGuangguV03Case,
    V03RunScope,
    prepare_guanggu_v03_v0_case,
    select_v03_run_scope,
)

__all__ = [
    "CANONICAL_TIMEZONE",
    "LoadTimeAdaptation",
    "LoadTimeContractError",
    "adapt_standard_hourly_loads",
    "validate_legacy_hour_index",
    "WuhanV02Adaptation",
    "CanonicalSeasonValidationReport",
    "GuangguV03Adaptation",
    "adapt_guanggu_v03_sources",
    "validate_canonical_season_data",
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
    "PreparedWuhanV02Case",
    "SmokeScope",
    "WuhanV02CaseError",
    "prepare_wuhan_v02_v0_case",
    "select_v0_smoke_scope",
    "GuangguV03CaseError",
    "PreparedGuangguV03Case",
    "V03RunScope",
    "prepare_guanggu_v03_v0_case",
    "select_v03_run_scope",
]
