"""Validate competition case inputs before legacy UrbanHeatOpt adaptation."""

from __future__ import annotations

from urbanheatopt.paths import REPOSITORY_ROOT as REPOSITORY_ROOT_PATH, PACKAGE_ROOT

from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import geopandas as gpd
from jsonschema import Draft202012Validator, FormatChecker
import numpy as np
import pandas as pd
import yaml

from urbanheatopt.data.adapters.load_timeseries import (
    CANONICAL_TIMEZONE,
    LoadTimeContractError,
    adapt_standard_hourly_loads,
)


REPOSITORY_ROOT = REPOSITORY_ROOT_PATH
ACTIVE_CASE_SCHEMA_PATH = REPOSITORY_ROOT / "src" / "urbanheatopt" / "data" / "schemas" / "case_config.schema.json"
LEGACY_V1_TOP_LEVEL_FIELDS = {
    "contract_version", "case_id", "scenario_id", "data_version",
    "data_classification", "time", "units", "crs", "files", "clustering",
    "spatial", "demand", "dhw", "features", "network", "planning",
    "enabled_technology_ids", "solver", "qa",
}
LEGACY_V1_UNITS = {
    "heating_power": "kW", "heating_energy": "kWh", "currency": "CNY",
    "area": "m2", "length": "m", "temperature": "degC", "carbon": "kgCO2e",
}
SUPPORTED_P0_TECHNOLOGY_TYPES = {"fixed_heat_source"}
REQUIRED_BUILDING_COLUMNS = ("building_id", "use_type", "heated_area_m2", "archetype_id")
REQUIRED_TECHNOLOGY_COLUMNS = (
    "technology_id",
    "technology_type",
    "applicable_scope",
    "energy_carrier",
    "cop",
    "efficiency",
    "capacity_min_kW",
    "capacity_max_kW",
    "capex_CNY_per_kW",
    "capex_basis",
    "fixed_om_CNY_per_kW_year",
    "variable_om_CNY_per_kWh_heat",
    "lifetime_years",
    "source",
    "assumption_flag",
)
REQUIRED_ROAD_COLUMNS = ("feature_id", "spatial_role")
ALLOWED_TECHNOLOGY_TYPES = {
    "air_source_heat_pump",
    "water_source_heat_pump",
    "ground_source_heat_pump",
    "waste_heat_heat_pump",
    "electric_boiler",
    "gas_boiler",
    "fixed_heat_source",
    "thermal_storage",
}
ALLOWED_SCOPES = {"local", "central", "both"}
ALLOWED_CARRIERS = {"electricity", "gas", "waste_heat", "synthetic_heat", "none"}
ALLOWED_ASSUMPTIONS = {
    "measured",
    "manufacturer",
    "literature",
    "project_confirmed",
    "scenario_assumption",
    "synthetic_test",
}


def _stable_error_code(message: str) -> str:
    """Map existing human-readable validation messages to stable public codes."""

    rules = (
        (("只读", "修改了案例输入"), "INPUT_INTEGRITY_CHANGED"),
        (("不支持", "当前只可执行", "storage_enabled", "waste_heat_enabled"), "FEATURE_NOT_IMPLEMENTED"),
        (("重复键",), "CONFIG_DUPLICATE_KEY"),
        (("schema", "不符合契约", "顶层必须"), "CONFIG_SCHEMA_ERROR"),
        (("缺少",), "FILE_OR_FIELD_MISSING"),
        (("CRS",), "CRS_INVALID"),
        (("geometry",), "GEOMETRY_INVALID"),
        (("timestamp", "时区", "整点", "逐时范围"), "TIME_INVALID"),
        (("ID", "building_id", "technology_id", "feature_id"), "ID_INVALID"),
        (("data_version",), "DATA_VERSION_MISMATCH"),
        (("dhw", "DHW"), "DHW_MISMATCH"),
        (("不得为负", "大于 0", "数值", "NaN", "无穷"), "VALUE_INVALID"),
    )
    for terms, code in rules:
        if any(term in message for term in terms):
            return code
    return "INPUT_CONTRACT_ERROR"


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that refuses duplicate mapping keys."""


def _construct_unique_mapping(
    loader: yaml.SafeLoader, node: yaml.MappingNode
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=False)
        if key in mapping:
            raise ValueError(f"重复键：{key!r}")
        mapping[key] = loader.construct_object(value_node, deep=False)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


class InputValidationError(ValueError):
    """Raised when a case input bundle violates the competition contract."""

    def __init__(self, errors: list[str]) -> None:
        self.messages = list(errors)
        self.issues = [
            {"code": _stable_error_code(message), "message": message}
            for message in errors
        ]
        # Keep the established ``errors`` API as the original message list.
        # Stable machine-readable codes are exposed separately through ``issues``.
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


@dataclass(frozen=True)
class CaseInputs:
    """Validated case inputs loaded from disk."""

    case_dir: Path
    config: dict[str, Any]
    buildings: gpd.GeoDataFrame
    loads: pd.DataFrame
    technologies: pd.DataFrame
    roads_or_feasible_space: gpd.GeoDataFrame
    external_timeseries: pd.DataFrame
    resource_anchors: gpd.GeoDataFrame | None
    legacy_loads_kW: pd.DataFrame
    timestamp_hour_map: pd.DataFrame
    # Default preserves compatibility for callers that construct CaseInputs
    # directly while validators populate hashes for normal runtime use.
    file_sha256: dict[str, str] = field(default_factory=dict)


def _snapshot_files(case_dir: Path) -> dict[str, str]:
    """Return hashes for every case file without writing to the case directory."""

    if not case_dir.is_dir():
        return {}
    return {
        path.relative_to(case_dir).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(case_dir.rglob("*"))
        if path.is_file()
    }


def _configured_input_hashes(
    case_dir: Path, config: dict[str, Any], snapshot: dict[str, str]
) -> dict[str, str]:
    names = {"case_config.yaml"}
    for value in config.get("files", {}).values():
        if isinstance(value, str):
            names.add(Path(value).as_posix())
    return {name: snapshot[name] for name in sorted(names) if name in snapshot}


def _is_clean_string_series(series: pd.Series) -> pd.Series:
    return series.map(lambda value: isinstance(value, str) and value != "" and value == value.strip())


def _finite_numeric(series: pd.Series, column: str, errors: list[str]) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.isna().any():
        errors.append(f"{column} 必须是数值且不得为空")
    elif not np.isfinite(numeric.to_numpy(dtype="float64")).all():
        errors.append(f"{column} 不得包含 NaN 或无穷值")
    return numeric


def _load_case_config(case_dir: Path, errors: list[str]) -> dict[str, Any]:
    config_path = case_dir / "case_config.yaml"
    if not config_path.is_file():
        errors.append("缺少 case_config.yaml")
        return {}
    try:
        config = yaml.load(
            config_path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader
        )
    except Exception as exc:
        errors.append(f"case_config.yaml 无法读取或解析：{exc}")
        return {}
    if not isinstance(config, dict):
        errors.append("case_config.yaml 顶层必须是映射对象")
        return {}
    contract_version = config.get("contract_version")
    if contract_version == "competition_input_v1":
        _validate_legacy_v1_config(config, errors)
    elif contract_version == "competition_input_v2_1":
        try:
            schema = json.loads(ACTIVE_CASE_SCHEMA_PATH.read_text(encoding="utf-8"))
            validator = Draft202012Validator(schema, format_checker=FormatChecker())
            for error in sorted(validator.iter_errors(config), key=lambda item: list(item.path)):
                path = ".".join(map(str, error.path)) or "$"
                errors.append(f"case_config.yaml 字段 {path} 不符合契约：{error.message}")
        except Exception as exc:
            errors.append(f"case_config schema 校验失败：{exc}")
    else:
        errors.append(
            "case_config.yaml 字段 contract_version 不符合契约："
            "legacy 入口只接受 competition_input_v1 或 competition_input_v2_1"
        )
    return config


def _validate_legacy_v1_config(config: dict[str, Any], errors: list[str]) -> None:
    """Validate the frozen v1 smoke envelope separately from the active schema."""

    fields = set(config)
    missing = sorted(LEGACY_V1_TOP_LEVEL_FIELDS - fields)
    extra = sorted(fields - LEGACY_V1_TOP_LEVEL_FIELDS)
    if missing:
        errors.append("case_config.yaml 字段 $ 不符合契约：缺少 " + ", ".join(missing))
    if extra:
        errors.append("case_config.yaml 字段 $ 不符合契约：包含未知字段 " + ", ".join(extra))

    units = config.get("units")
    if not isinstance(units, dict):
        errors.append("case_config.yaml 字段 units 不符合契约：必须是映射对象")
        return
    missing_units = sorted(set(LEGACY_V1_UNITS) - set(units))
    extra_units = sorted(set(units) - set(LEGACY_V1_UNITS))
    if missing_units:
        errors.append("case_config.yaml 字段 units 不符合契约：缺少 " + ", ".join(missing_units))
    if extra_units:
        errors.append("case_config.yaml 字段 units 不符合契约：包含未知字段 " + ", ".join(extra_units))
    for field, expected in LEGACY_V1_UNITS.items():
        if field in units and units[field] != expected:
            errors.append(
                f"case_config.yaml 字段 units.{field} 不符合契约：必须为 {expected!r}"
            )


def _read_buildings(case_dir: Path, config: dict[str, Any], errors: list[str]) -> gpd.GeoDataFrame:
    path = case_dir / str(config.get("files", {}).get("buildings", "buildings.geojson"))
    if not path.is_file():
        errors.append(f"缺少建筑文件：{path.name}")
        return gpd.GeoDataFrame()
    try:
        buildings = gpd.read_file(path)
    except Exception as exc:
        errors.append(f"建筑 GeoJSON 无法读取：{exc}")
        return gpd.GeoDataFrame()

    missing = [column for column in REQUIRED_BUILDING_COLUMNS if column not in buildings.columns]
    if missing:
        errors.append(f"建筑 GeoJSON 缺少字段：{', '.join(missing)}")
        return buildings
    for column in ("building_id", "use_type", "archetype_id"):
        if not _is_clean_string_series(buildings[column]).all():
            errors.append(f"建筑字段 {column} 必须是非空且无首尾空白的字符串")
    if buildings["building_id"].duplicated().any():
        errors.append("建筑 building_id 不得重复")
    heated_area = _finite_numeric(buildings["heated_area_m2"], "heated_area_m2", errors)
    if (heated_area <= 0).any():
        errors.append("heated_area_m2 必须大于 0")
    expected_crs = config.get("crs", {}).get("input")
    if expected_crs and str(buildings.crs) != expected_crs:
        errors.append(f"建筑 CRS 必须为 {expected_crs}，实际为 {buildings.crs}")
    if buildings.geometry.isna().any() or buildings.geometry.is_empty.any():
        errors.append("建筑 geometry 不得为空")
    if not buildings.empty and not buildings.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        errors.append("建筑 geometry 只能是 Polygon 或 MultiPolygon")
    if not buildings.empty and not buildings.geometry.is_valid.all():
        errors.append("建筑 geometry 必须全部有效")
    return buildings


def _read_loads(case_dir: Path, config: dict[str, Any], errors: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    path = case_dir / str(config.get("files", {}).get("building_hourly_loads", "building_hourly_loads.parquet"))
    if not path.is_file():
        errors.append(f"缺少建筑逐时负荷文件：{path.name}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    try:
        loads = pd.read_parquet(path)
        adapted = adapt_standard_hourly_loads(loads[["timestamp", "building_id", "heating_kW"]].copy())
    except LoadTimeContractError as exc:
        errors.append(f"建筑逐时负荷时间/单位契约错误：{exc}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    except Exception as exc:
        errors.append(f"建筑逐时负荷无法读取或适配：{exc}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    for column in ("dhw_included", "data_version"):
        if column not in loads.columns:
            errors.append(f"建筑逐时负荷缺少字段：{column}")
    if "dhw_included" in loads.columns:
        if loads["dhw_included"].isna().any() or not loads["dhw_included"].map(lambda value: isinstance(value, (bool, np.bool_))).all():
            errors.append("dhw_included 必须是布尔列")
        elif loads["dhw_included"].nunique() != 1:
            errors.append("dhw_included 必须在同一案例内保持一致")
    if "data_version" in loads.columns:
        if not _is_clean_string_series(loads["data_version"]).all():
            errors.append("data_version 必须是非空字符串")
        elif loads["data_version"].nunique() != 1:
            errors.append("data_version 必须在同一案例内保持一致")
    if "cooling_kW" in loads.columns:
        cooling = _finite_numeric(loads["cooling_kW"], "cooling_kW", errors)
        if (cooling < 0).any():
            errors.append("cooling_kW 不得为负")

    return loads, adapted.legacy_wide_kW, adapted.timestamp_hour_map


def _read_technologies(case_dir: Path, config: dict[str, Any], errors: list[str]) -> pd.DataFrame:
    path = case_dir / str(config.get("files", {}).get("technologies", "technologies.csv"))
    if not path.is_file():
        errors.append(f"缺少技术文件：{path.name}")
        return pd.DataFrame()
    try:
        technologies = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        errors.append(f"技术 CSV 无法读取：{exc}")
        return pd.DataFrame()
    missing = [column for column in REQUIRED_TECHNOLOGY_COLUMNS if column not in technologies.columns]
    if missing:
        errors.append(f"技术 CSV 缺少字段：{', '.join(missing)}")
        return technologies
    if technologies["technology_id"].duplicated().any():
        errors.append("technology_id 不得重复")
    if not _is_clean_string_series(technologies["technology_id"]).all():
        errors.append("technology_id 必须是非空且无首尾空白的字符串")
    if not technologies["technology_type"].isin(ALLOWED_TECHNOLOGY_TYPES).all():
        errors.append("technology_type 包含契约外类型")
    if not technologies["applicable_scope"].isin(ALLOWED_SCOPES).all():
        errors.append("applicable_scope 包含非法取值")
    if not technologies["energy_carrier"].isin(ALLOWED_CARRIERS).all():
        errors.append("energy_carrier 包含非法取值")
    if not technologies["capex_basis"].isin({"one_time_capex", "annualized"}).all():
        errors.append("capex_basis 必须为 one_time_capex 或 annualized")
    if not technologies["assumption_flag"].isin(ALLOWED_ASSUMPTIONS).all():
        errors.append("assumption_flag 包含非法取值")
    if not _is_clean_string_series(technologies["source"]).all():
        errors.append("source 必须是非空字符串")

    numeric_columns = [
        "capacity_min_kW",
        "capacity_max_kW",
        "capex_CNY_per_kW",
        "fixed_om_CNY_per_kW_year",
        "variable_om_CNY_per_kWh_heat",
    ]
    for column in numeric_columns:
        values = _finite_numeric(technologies[column], column, errors)
        if column == "capacity_max_kW":
            if (values <= 0).any():
                errors.append("capacity_max_kW 必须大于 0")
        elif (values < 0).any():
            errors.append(f"{column} 不得为负")
    if (pd.to_numeric(technologies["capacity_max_kW"], errors="coerce") < pd.to_numeric(technologies["capacity_min_kW"], errors="coerce")).any():
        errors.append("capacity_max_kW 必须大于等于 capacity_min_kW")
    lifetime = pd.to_numeric(technologies["lifetime_years"], errors="coerce")
    if lifetime.isna().any() or (lifetime < 1).any() or (lifetime % 1 != 0).any():
        errors.append("lifetime_years 必须是大于等于 1 的整数")

    cop = pd.to_numeric(technologies["cop"], errors="coerce")
    efficiency = pd.to_numeric(technologies["efficiency"], errors="coerce")
    heat_pump = technologies["technology_type"].str.contains("heat_pump", na=False)
    boiler = technologies["technology_type"].isin(["electric_boiler", "gas_boiler"])
    fixed = technologies["technology_type"].eq("fixed_heat_source")
    if (heat_pump & ~(cop > 0)).any():
        errors.append("启用热泵技术必须提供 cop > 0")
    if (boiler & ~((efficiency > 0) & (efficiency <= 1))).any():
        errors.append("启用锅炉技术必须提供 0 < efficiency <= 1")
    if (fixed & technologies["energy_carrier"].ne("synthetic_heat")).any():
        errors.append("fixed_heat_source 的 energy_carrier 必须为 synthetic_heat")
    if (fixed & (technologies["cop"].notna() | technologies["efficiency"].notna())).any():
        errors.append("fixed_heat_source 的 cop 和 efficiency 必须留空")
    return technologies


def _read_roads(case_dir: Path, config: dict[str, Any], errors: list[str]) -> gpd.GeoDataFrame:
    path = case_dir / str(config.get("files", {}).get("roads_or_feasible_space", "roads_or_feasible_space.geojson"))
    if not path.is_file():
        errors.append(f"缺少道路/可建设空间文件：{path.name}")
        return gpd.GeoDataFrame()
    try:
        roads = gpd.read_file(path)
    except Exception as exc:
        errors.append(f"道路/可建设空间 GeoJSON 无法读取：{exc}")
        return gpd.GeoDataFrame()
    missing = [column for column in REQUIRED_ROAD_COLUMNS if column not in roads.columns]
    if missing:
        errors.append(f"道路/可建设空间缺少字段：{', '.join(missing)}")
        return roads
    if roads["feature_id"].duplicated().any():
        errors.append("道路/可建设空间 feature_id 不得重复")
    if not _is_clean_string_series(roads["feature_id"]).all():
        errors.append("feature_id 必须是非空字符串")
    expected_crs = config.get("crs", {}).get("input")
    if expected_crs and str(roads.crs) != expected_crs:
        errors.append(f"道路/可建设空间 CRS 必须为 {expected_crs}，实际为 {roads.crs}")
    if roads.geometry.isna().any() or roads.geometry.is_empty.any():
        errors.append("道路/可建设空间 geometry 不得为空")
    if not roads.empty and not roads.geometry.is_valid.all():
        errors.append("道路/可建设空间 geometry 必须全部有效")
    mode = config.get("spatial", {}).get("input_mode")
    if mode == "roads":
        if not roads["spatial_role"].eq("road").all():
            errors.append("input_mode=roads 时 spatial_role 必须全部为 road")
        if not roads.geometry.geom_type.isin(["LineString", "MultiLineString"]).all():
            errors.append("input_mode=roads 时 geometry 只能是 LineString 或 MultiLineString")
    elif mode == "feasible_space":
        if "road" in set(roads["spatial_role"]):
            errors.append("input_mode=feasible_space 时 spatial_role 不得为 road")
        if "allowed" not in set(roads["spatial_role"]):
            errors.append("input_mode=feasible_space 时至少需要一个 allowed 面")
        if not roads.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
            errors.append("input_mode=feasible_space 时 geometry 只能是 Polygon 或 MultiPolygon")
    return roads


def _read_external_timeseries(case_dir: Path, config: dict[str, Any], errors: list[str]) -> pd.DataFrame:
    path = case_dir / str(config.get("files", {}).get("external_timeseries", "external_timeseries.parquet"))
    if not path.is_file():
        errors.append(f"缺少外部逐时文件：{path.name}")
        return pd.DataFrame()
    try:
        external = pd.read_parquet(path)
    except Exception as exc:
        errors.append(f"外部逐时 Parquet 无法读取：{exc}")
        return pd.DataFrame()
    for column in ("timestamp", "data_version"):
        if column not in external.columns:
            errors.append(f"外部逐时缺少字段：{column}")
    if "timestamp" in external.columns:
        timestamps = external["timestamp"]
        if not isinstance(timestamps.dtype, pd.DatetimeTZDtype):
            errors.append("外部逐时 timestamp 必须带时区")
        elif str(timestamps.dt.tz) != CANONICAL_TIMEZONE:
            errors.append(f"外部逐时 timestamp 时区必须为 {CANONICAL_TIMEZONE}")
        elif timestamps.isna().any():
            errors.append("外部逐时 timestamp 不得为空")
        elif not timestamps.equals(timestamps.dt.floor("h")):
            errors.append("外部逐时 timestamp 必须位于整点")
        elif not pd.DatetimeIndex(timestamps).is_monotonic_increasing:
            errors.append("外部逐时 timestamp 必须升序")
        elif timestamps.duplicated().any():
            errors.append("外部逐时 timestamp 不得重复")
    if "data_version" in external.columns:
        if not _is_clean_string_series(external["data_version"]).all():
            errors.append("外部逐时 data_version 必须是非空字符串")
        elif external["data_version"].nunique() != 1:
            errors.append("外部逐时 data_version 必须在同一案例内保持一致")
    return external


def _read_resource_anchors(case_dir: Path, config: dict[str, Any], errors: list[str]) -> gpd.GeoDataFrame | None:
    resource_path = config.get("files", {}).get("resource_anchors")
    if resource_path is None:
        return None
    errors.append("P0 当前不支持 resource_anchors；请在 case_config 中设为 null")
    path = case_dir / str(resource_path)
    if not path.is_file():
        return None
    try:
        return gpd.read_file(path)
    except Exception:
        return None


def _cross_file_checks(inputs: CaseInputs, errors: list[str]) -> None:
    config = inputs.config
    if not inputs.buildings.empty and not inputs.loads.empty:
        building_ids = set(inputs.buildings["building_id"])
        load_ids = set(inputs.loads["building_id"])
        if building_ids != load_ids:
            missing_loads = sorted(building_ids - load_ids)
            extra_loads = sorted(load_ids - building_ids)
            if missing_loads:
                errors.append(f"负荷缺少建筑：{', '.join(missing_loads)}")
            if extra_loads:
                errors.append(f"负荷包含建筑表不存在的 ID：{', '.join(extra_loads)}")

    # File readers already report their own failures. Without a valid config,
    # semantic cross-file checks would only add misleading default-value errors.
    if not config:
        return

    data_version = config.get("data_version")
    if "data_version" in inputs.loads.columns and inputs.loads["data_version"].nunique() == 1:
        actual = inputs.loads["data_version"].iloc[0]
        if data_version != actual:
            errors.append(f"负荷 data_version={actual} 与 case_config data_version={data_version} 不一致")
    if "data_version" in inputs.external_timeseries.columns and inputs.external_timeseries["data_version"].nunique() == 1:
        actual = inputs.external_timeseries["data_version"].iloc[0]
        if data_version != actual:
            errors.append(f"外部逐时 data_version={actual} 与 case_config data_version={data_version} 不一致")
    if "dhw_included" in inputs.loads.columns and inputs.loads["dhw_included"].nunique() == 1:
        actual = bool(inputs.loads["dhw_included"].iloc[0])
        expected = bool(config.get("dhw", {}).get("input_includes_dhw"))
        if actual != expected:
            errors.append(f"dhw_included={actual} 与 case_config dhw.input_includes_dhw={expected} 不一致")
    if (
        not inputs.external_timeseries.empty
        and "timestamp" in inputs.external_timeseries.columns
        and not inputs.loads.empty
        and config.get("time", {}).get("start")
        and config.get("time", {}).get("end")
    ):
        load_times = pd.DatetimeIndex(inputs.loads["timestamp"].drop_duplicates())
        external_times = pd.DatetimeIndex(inputs.external_timeseries["timestamp"])
        if not load_times.equals(external_times):
            errors.append("建筑负荷 timestamp 集合必须与 external_timeseries 完全一致")
        timezone = config.get("time", {}).get("timezone", CANONICAL_TIMEZONE)
        start = pd.Timestamp(config.get("time", {}).get("start")).tz_convert(timezone)
        end = pd.Timestamp(config.get("time", {}).get("end")).tz_convert(timezone)
        expected_times = pd.date_range(start=start, end=end, freq="h", inclusive="left")
        if not load_times.equals(expected_times):
            errors.append("建筑负荷 timestamp 必须等于 case_config time.start/end 定义的左闭右开逐时范围")

    if config.get("demand", {}).get("area_scaling_already_applied") is not True:
        errors.append("case_config 必须确认 demand.area_scaling_already_applied=true")
    if config.get("dhw", {}).get("add_in_adapter") is not False:
        errors.append("P0 适配器不得额外添加 DHW")
    if config.get("features", {}).get("storage_enabled") or config.get("features", {}).get("waste_heat_enabled"):
        errors.append("P0 当前要求 storage_enabled=false 且 waste_heat_enabled=false")

    if not inputs.technologies.empty and "technology_id" in inputs.technologies.columns:
        technology_ids = set(inputs.technologies["technology_id"])
        enabled = set(config.get("enabled_technology_ids", []))
        missing = enabled - technology_ids
        if missing:
            errors.append(f"enabled_technology_ids 引用了 technologies.csv 不存在的技术：{', '.join(sorted(missing))}")
        enabled_rows = inputs.technologies[inputs.technologies["technology_id"].isin(enabled)]
        unsupported = sorted(set(enabled_rows["technology_type"]) - SUPPORTED_P0_TECHNOLOGY_TYPES)
        if unsupported:
            errors.append(f"P0 当前只可执行 fixed_heat_source，不支持：{', '.join(unsupported)}")
        if config.get("data_classification") == "synthetic_test":
            bad = enabled_rows[enabled_rows["assumption_flag"].ne("synthetic_test")]
            if not bad.empty:
                errors.append("synthetic_test 案例启用技术的 assumption_flag 必须为 synthetic_test")
    if not inputs.buildings.empty:
        cluster_count = int(config.get("clustering", {}).get("cluster_count", 0))
        if cluster_count < 1:
            errors.append("clustering.cluster_count 必须大于等于 1")
        elif cluster_count > len(inputs.buildings):
            errors.append("clustering.cluster_count 不能大于建筑数量")


def validate_case_inputs(case_dir: str | Path) -> CaseInputs:
    """Load and validate a complete P0 case input bundle."""

    case_path = Path(case_dir).resolve()
    before_snapshot = _snapshot_files(case_path)
    errors: list[str] = []
    config = _load_case_config(case_path, errors)
    buildings = _read_buildings(case_path, config, errors)
    loads, legacy_loads, timestamp_hour_map = _read_loads(case_path, config, errors)
    technologies = _read_technologies(case_path, config, errors)
    roads = _read_roads(case_path, config, errors)
    external = _read_external_timeseries(case_path, config, errors)
    resource_anchors = _read_resource_anchors(case_path, config, errors)
    candidate = CaseInputs(
        case_dir=case_path,
        config=config,
        buildings=buildings,
        loads=loads,
        technologies=technologies,
        roads_or_feasible_space=roads,
        external_timeseries=external,
        resource_anchors=resource_anchors,
        legacy_loads_kW=legacy_loads,
        timestamp_hour_map=timestamp_hour_map,
        file_sha256=_configured_input_hashes(case_path, config, before_snapshot),
    )
    _cross_file_checks(candidate, errors)
    after_snapshot = _snapshot_files(case_path)
    if before_snapshot != after_snapshot:
        errors.append("校验过程修改了案例输入文件；validation 必须保持只读")
    if errors:
        raise InputValidationError(errors)
    return candidate
