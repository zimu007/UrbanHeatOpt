"""Guanggu v0.3 delivery to an accepted 2160-hour canonical snapshot."""

from __future__ import annotations

from urbanheatopt.paths import REPOSITORY_ROOT as REPOSITORY_ROOT_PATH, PACKAGE_ROOT

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import yaml

from urbanheatopt.data.canonical import CanonicalSeasonData, V3_DRAFT_CONTRACT
from urbanheatopt.data.intake import GuangguV03Report, validate_guanggu_v03_delivery
from urbanheatopt.data.intake.guanggu_v03 import (
    EQUIPMENT_PATCH_FILES,
    resolve_guanggu_v03_source_roots,
)


@dataclass(frozen=True, slots=True)
class CanonicalSeasonIssue:
    code: str
    message: str
    severity: str = "error"


@dataclass(slots=True)
class CanonicalSeasonValidationReport:
    data_version: str
    issues: list[CanonicalSeasonIssue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "valid" if self.valid else "invalid",
            "canonical_validation_passed": self.valid,
            "data_version": self.data_version,
            "issues": [asdict(issue) for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class GuangguV03Adaptation:
    output_dir: Path
    canonical_data: CanonicalSeasonData
    source_report: GuangguV03Report
    canonical_report: CanonicalSeasonValidationReport
    source_report_path: Path
    canonical_report_path: Path
    adaptation_report_path: Path
    field_mapping_path: Path
    buildings_path: Path
    archetype_map_path: Path
    loads_path: Path
    external_timeseries_path: Path
    technology_parameters_path: Path
    equipment_performance_path: Path
    timestamp_hour_map_path: Path


def _issue(
    report: CanonicalSeasonValidationReport,
    code: str,
    message: str,
    severity: str = "error",
) -> None:
    report.issues.append(CanonicalSeasonIssue(code, message, severity))


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_profile(profile_path: str | Path | None) -> dict[str, Any]:
    path = (
        Path(profile_path)
        if profile_path is not None
        else PACKAGE_ROOT / "data" / "profile_resources" / "guanggu_v03.yaml"
    )
    profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(profile, dict) or profile.get("source_profile") != "guanggu_v03":
        raise ValueError("source profile 必须是 guanggu_v03")
    return profile


def _prepare_output(source: Path, output: Path) -> None:
    if output == source or output.is_relative_to(source):
        raise ValueError("适配输出目录不得位于源交付目录内部")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"适配输出目录必须为空或不存在: {output}")
    output.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _source_hour_order(profile: dict[str, Any]) -> tuple[int, ...]:
    result: list[int] = []
    for segment in profile["heating_season"]["source_segments"]:
        result.extend(range(int(segment["start"]), int(segment["end"]) + 1))
    expected = int(profile["heating_season"]["season_hour_count"])
    if len(result) != expected or len(set(result)) != expected:
        raise ValueError("source profile 的供暖季小时片段无效")
    return tuple(result)


def _normalize_mapping(source: pd.DataFrame) -> pd.DataFrame:
    def strict_bool(value: Any) -> bool:
        if isinstance(value, (bool, np.bool_)):
            return bool(value)
        normalized = str(value).strip().casefold()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
        raise ValueError(f"is_mixed_use 含非法布尔值: {value!r}")

    records: list[dict[str, Any]] = []
    for row in source.itertuples(index=False):
        mixed = strict_bool(row.is_mixed_use)
        records.append(
            {
                "building_id": str(row.building_id),
                "zone_id": str(row.zone_id) if mixed else f"{row.building_id}-01",
                "zone_use_type": str(row.zone_use_type) if mixed else str(row.use_type),
                "zone_area_m2": float(row.zone_area_m2) if mixed else float(row.target_conditioned_area_m2),
                "zone_archetype_id": str(row.zone_archetype_id) if mixed else str(row.archetype_id),
                "zone_scale_factor": float(row.zone_scale_factor) if mixed else float(row.scale_factor),
                "heating_setpoint_C": float(row.heating_setpoint_C),
                "data_version": str(row.data_version),
            }
        )
    return pd.DataFrame.from_records(records).sort_values(
        ["building_id", "zone_id"], kind="stable"
    ).reset_index(drop=True)


def _build_timestamp_map(
    external_source: pd.DataFrame,
    source_hours: tuple[int, ...],
    timezone: str,
) -> pd.DataFrame:
    indexed = external_source.set_index("hour", drop=False)
    selected = indexed.loc[list(source_hours)]
    source_timestamps = pd.DatetimeIndex(selected["timestamp"])
    if source_timestamps.tz is None or str(source_timestamps.tz) != timezone:
        raise ValueError(f"external source timestamp 必须为 {timezone}")
    canonical = pd.date_range(
        start=source_timestamps[0],
        periods=len(source_hours),
        freq="h",
        tz=timezone,
    )
    return pd.DataFrame(
        {
            "timestamp": canonical,
            "hour": np.arange(1, len(source_hours) + 1, dtype=np.int32),
            "source_timestamp": source_timestamps,
            "source_hour": np.asarray(source_hours, dtype=np.int32),
            "heating_season_hour": np.arange(len(source_hours), dtype=np.int32),
        }
    )


def _canonical_buildings(source: gpd.GeoDataFrame, data_version: str) -> gpd.GeoDataFrame:
    columns = [
        "building_id",
        "use_type",
        "conditioned_area_m2",
        "terminal_type",
        "terminal_description",
        "heating_supply_temperature_C",
        "heating_return_temperature_C",
        "terminal_parameter_status",
        "geometry",
    ]
    result = source[columns].rename(columns={"conditioned_area_m2": "heated_area_m2"}).copy()
    result = result.sort_values("building_id", kind="stable").reset_index(drop=True)
    result["ventilation_system"] = "dedicated_fresh_air"
    result["fresh_air_load_included"] = True
    result["data_version"] = data_version
    return gpd.GeoDataFrame(result, geometry="geometry", crs=source.crs)


def _canonical_loads(
    source: pd.DataFrame,
    timestamp_map: pd.DataFrame,
    data_version: str,
) -> pd.DataFrame:
    mapping = timestamp_map.set_index("source_hour")
    selected = source[source["hour"].isin(mapping.index)].copy()
    selected["source_timestamp"] = selected["timestamp"]
    selected["source_hour"] = selected["hour"].astype(np.int32)
    selected["heating_season_hour"] = selected["source_hour"].map(
        mapping["heating_season_hour"]
    ).astype(np.int32)
    selected["hour"] = selected["source_hour"].map(mapping["hour"]).astype(np.int32)
    selected["timestamp"] = selected["source_hour"].map(mapping["timestamp"])
    selected["data_version"] = data_version
    columns = [
        "timestamp",
        "hour",
        "source_timestamp",
        "source_hour",
        "heating_season_hour",
        "building_id",
        "heating_kW",
        "dhw_included",
        "quality_flag",
        "data_version",
    ]
    return selected[columns].sort_values(
        ["hour", "building_id"], kind="stable"
    ).reset_index(drop=True)


def _technology_numeric_value(
    table: pd.DataFrame,
    technology_id: str,
    parameter_name: str,
) -> float:
    rows = table.loc[
        table["technology_id"].eq(technology_id)
        & table["parameter_name"].eq(parameter_name)
    ]
    if len(rows) != 1:
        raise ValueError(f"technologies 缺少唯一参数 {technology_id}.{parameter_name}")
    return float(rows.iloc[0]["recommended_value"])


def _canonical_external(
    source: pd.DataFrame,
    timestamp_map: pd.DataFrame,
    technologies: pd.DataFrame,
    profile: dict[str, Any],
    data_version: str,
) -> tuple[pd.DataFrame, dict[str, float]]:
    mapping = timestamp_map.set_index("source_hour")
    selected = source[source["hour"].isin(mapping.index)].copy()
    selected["source_timestamp"] = selected["timestamp"]
    selected["source_hour"] = selected["hour"].astype(np.int32)
    selected["heating_season_hour"] = selected["source_hour"].map(
        mapping["heating_season_hour"]
    ).astype(np.int32)
    selected["hour"] = selected["source_hour"].map(mapping["hour"]).astype(np.int32)
    selected["timestamp"] = selected["source_hour"].map(mapping["timestamp"])
    lhv_mj = _technology_numeric_value(
        technologies,
        "GAS_BOILER_BASE_01",
        "natural_gas_LHV",
    )
    lhv_kwh = lhv_mj / 3.6
    selected["natural_gas_lhv_MJ_per_Nm3"] = lhv_mj
    selected["natural_gas_lhv_kWh_LHV_per_Nm3"] = lhv_kwh
    selected["gas_price_CNY_per_kWh_LHV"] = (
        selected["natural_gas_price_CNY_per_Nm3"] / lhv_kwh
    )
    selected["gas_carbon_kgCO2e_per_kWh_LHV"] = (
        selected["natural_gas_carbon_factor_kgCO2e_per_Nm3"] / lhv_kwh
    )
    selected["electricity_carbon_kgCO2e_per_kWh_e"] = selected[
        "grid_carbon_factor_kgCO2e_per_kWh_e"
    ]
    selected["time_weight_h_per_year"] = selected["time_weight_h"]
    curve_max = float(profile["technology_rules"]["ashp_curve_max_temperature_C"])
    selected["cop_lookup_temperature_C"] = selected["outdoor_temperature_C"].clip(
        upper=curve_max
    )
    selected["cop_boundary_clamped"] = selected["outdoor_temperature_C"].gt(curve_max)
    selected["gas_energy_basis"] = "LHV"
    selected["gas_conversion_applied"] = True
    selected["data_version"] = data_version
    selected = selected.sort_values("hour", kind="stable").reset_index(drop=True)
    return selected, {
        "natural_gas_lhv_MJ_per_Nm3": lhv_mj,
        "natural_gas_lhv_kWh_LHV_per_Nm3": lhv_kwh,
    }


def _canonical_technology_parameters(source: pd.DataFrame) -> pd.DataFrame:
    result = source.copy()
    roles = {
        "air_source_heat_pump": "central_and_local_candidate",
        "gas_boiler": "central_candidate",
        "short_term_thermal_storage": "central_storage_candidate",
    }
    result["canonical_role"] = result["technology_type"].map(roles).fillna(
        "not_in_current_core_scope"
    )
    result["executable_in_lhv_core"] = True
    hhv = result["parameter_name"].astype(str).str.contains("HHV", case=False, na=False)
    result.loc[hhv, "executable_in_lhv_core"] = False
    result["energy_basis"] = np.where(
        result["parameter_name"].astype(str).str.contains("LHV", case=False, na=False),
        "LHV",
        np.where(hhv, "HHV", "not_applicable"),
    )
    return result.sort_values(["technology_id", "parameter_name"], kind="stable").reset_index(drop=True)


def _canonical_equipment_performance(source: pd.DataFrame) -> pd.DataFrame:
    result = source.copy()
    gas = result["technology_type"].eq("gas_boiler")
    gas_lhv = gas & result.get(
        "applicable_range", pd.Series(index=result.index, dtype=object)
    ).astype(str).str.contains("LHV", case=False, na=False)
    result["energy_basis"] = np.where(
        gas_lhv, "LHV", np.where(gas, "HHV_provenance_only", "not_applicable")
    )
    result["executable_in_lhv_core"] = ~gas | gas_lhv
    result["cop_upper_boundary_policy"] = np.where(
        result["technology_type"].eq("air_source_heat_pump"),
        "clamp_to_15C_no_extrapolation",
        "not_applicable",
    )
    return result.sort_values(
        ["technology_id", "operating_mode", "Tout", "Tsupply", "PLR"],
        kind="stable",
        na_position="last",
    ).reset_index(drop=True)


def validate_canonical_season_data(
    data: CanonicalSeasonData,
    *,
    expected_building_count: int,
    expected_hour_count: int,
) -> CanonicalSeasonValidationReport:
    """Revalidate the accepted snapshot independently of the source adapter."""

    report = CanonicalSeasonValidationReport(data.data_version)
    buildings = data.buildings
    mapping = data.building_archetype_map
    loads = data.loads
    external = data.external_timeseries
    time_map = data.timestamp_hour_map
    building_ids = set(buildings["building_id"]) if "building_id" in buildings else set()
    if len(buildings) != expected_building_count or len(building_ids) != expected_building_count:
        _issue(report, "CANONICAL_BUILDING_COUNT_INVALID", f"标准建筑必须为 {expected_building_count} 栋且ID唯一")
    required_buildings = {
        "building_id", "heated_area_m2", "terminal_type", "ventilation_system",
        "fresh_air_load_included", "heating_supply_temperature_C",
        "heating_return_temperature_C", "data_version",
    }
    if not required_buildings <= set(buildings):
        _issue(report, "CANONICAL_BUILDING_FIELDS_MISSING", f"标准建筑缺字段: {sorted(required_buildings - set(buildings))}")
    elif (
        set(buildings["terminal_type"]) != {"fan_coil"}
        or set(buildings["ventilation_system"]) != {"dedicated_fresh_air"}
        or set(buildings["heating_supply_temperature_C"]) != {45}
        or set(buildings["heating_return_temperature_C"]) != {40}
    ):
        _issue(report, "CANONICAL_TERMINAL_BOUNDARY_INVALID", "标准末端必须为风机盘管+新风及45/40℃")
    if len(external) != expected_hour_count or len(time_map) != expected_hour_count:
        _issue(report, "CANONICAL_HOUR_COUNT_INVALID", f"标准外部时序和映射必须为 {expected_hour_count} 小时")
    expected_hours = np.arange(1, expected_hour_count + 1)
    if "hour" not in external or not np.array_equal(external["hour"].to_numpy(), expected_hours):
        _issue(report, "CANONICAL_HOUR_INDEX_INVALID", f"标准小时必须严格为1..{expected_hour_count}")
    timestamps = pd.DatetimeIndex(external["timestamp"]) if "timestamp" in external else pd.DatetimeIndex([])
    if (
        len(timestamps) != expected_hour_count
        or timestamps.tz is None
        or str(timestamps.tz) != "Asia/Shanghai"
        or not timestamps.equals(pd.date_range(timestamps[0], periods=len(timestamps), freq="h", tz="Asia/Shanghai"))
    ):
        _issue(report, "CANONICAL_TIMESTAMP_INVALID", "标准 timestamp 必须为 Asia/Shanghai 严格连续小时")
    expected_rows = expected_building_count * expected_hour_count
    if len(loads) != expected_rows or loads.duplicated(["building_id", "hour"]).any():
        _issue(report, "CANONICAL_LOAD_SHAPE_INVALID", f"标准负荷必须为 {expected_rows} 行且主键唯一")
    if set(loads.get("building_id", [])) != building_ids:
        _issue(report, "CANONICAL_LOAD_ID_MISMATCH", "标准负荷建筑ID与建筑主表不一致")
    coverage = loads.groupby("building_id")["hour"] if {"building_id", "hour"} <= set(loads) else []
    if any(not np.array_equal(group.to_numpy(), expected_hours) for _, group in coverage):
        _issue(report, "CANONICAL_LOAD_HOUR_COVERAGE_INVALID", "每栋标准负荷必须严格覆盖1..2160")
    heating = pd.to_numeric(loads.get("heating_kW"), errors="coerce")
    if heating.isna().any() or not np.isfinite(heating).all() or (heating < 0).any():
        _issue(report, "CANONICAL_LOAD_VALUE_INVALID", "标准供热负荷必须为有限非负kW_th")
    required_external = {
        "timestamp", "hour", "source_timestamp", "source_hour", "heating_season_hour",
        "outdoor_temperature_C", "time_weight_h_per_year",
        "electricity_price_CNY_per_kWh_e", "gas_price_CNY_per_kWh_LHV",
        "electricity_carbon_kgCO2e_per_kWh_e", "gas_carbon_kgCO2e_per_kWh_LHV",
        "natural_gas_price_CNY_per_Nm3", "natural_gas_carbon_factor_kgCO2e_per_Nm3",
        "natural_gas_lhv_kWh_LHV_per_Nm3", "gas_energy_basis",
        "gas_conversion_applied", "cop_boundary_clamped", "data_version",
    }
    missing = required_external - set(external)
    if missing:
        _issue(report, "CANONICAL_EXTERNAL_FIELDS_MISSING", f"标准外部时序缺字段: {sorted(missing)}")
    else:
        lhv = external["natural_gas_lhv_kWh_LHV_per_Nm3"].astype(float)
        expected_gas_price = external["natural_gas_price_CNY_per_Nm3"].astype(float) / lhv
        expected_gas_carbon = external["natural_gas_carbon_factor_kgCO2e_per_Nm3"].astype(float) / lhv
        if not np.allclose(external["gas_price_CNY_per_kWh_LHV"], expected_gas_price, rtol=0, atol=1e-12):
            _issue(report, "CANONICAL_GAS_PRICE_CONVERSION_INVALID", "LHV气价没有按体积价/LHV转换一次")
        if not np.allclose(external["gas_carbon_kgCO2e_per_kWh_LHV"], expected_gas_carbon, rtol=0, atol=1e-12):
            _issue(report, "CANONICAL_GAS_CARBON_CONVERSION_INVALID", "LHV碳因子没有按体积因子/LHV转换一次")
        conversion_flags = external["gas_conversion_applied"]
        if not pd.api.types.is_bool_dtype(conversion_flags.dtype):
            conversion_flags = (
                conversion_flags.astype("string")
                .str.strip()
                .str.casefold()
                .map({"true": True, "false": False, "1": True, "0": False})
            )
        if (
            set(external["gas_energy_basis"]) != {"LHV"}
            or conversion_flags.isna().any()
            or set(conversion_flags.astype(bool)) != {True}
        ):
            _issue(report, "CANONICAL_GAS_BASIS_INVALID", "标准燃气边界必须明确LHV且仅转换一次")
        expected_clamped = external["outdoor_temperature_C"].gt(15.0)
        if not external["cop_boundary_clamped"].map(bool).equals(expected_clamped):
            _issue(report, "CANONICAL_COP_CLAMP_FLAG_INVALID", "COP边界封顶标记与室外温度不一致")
    if set(mapping.get("building_id", [])) != building_ids or mapping.duplicated(["building_id", "zone_id"]).any():
        _issue(report, "CANONICAL_MAPPING_ID_INVALID", "标准原型映射ID集合或主键无效")
    if not set(data.technology_parameters.get("energy_basis", [])) >= {"LHV", "HHV"}:
        _issue(report, "CANONICAL_TECHNOLOGY_BASIS_MISSING", "技术参数登记表未区分LHV与HHV")
    gas_equipment = data.equipment_performance.loc[
        data.equipment_performance["technology_type"].eq("gas_boiler")
    ]
    if len(gas_equipment):
        expected = gas_equipment["energy_basis"].eq("LHV")
        if not gas_equipment["executable_in_lhv_core"].map(bool).equals(expected):
            _issue(
                report,
                "CANONICAL_GAS_CURVE_BASIS_INVALID",
                "Only LHV gas-boiler rows may be executable in the LHV core",
            )
    return report


def adapt_guanggu_v03_sources(
    source_root: str | Path,
    output_dir: str | Path,
    *,
    profile_path: str | Path | None = None,
    full_audit: bool = True,
    equipment_patch_root: str | Path | None = None,
) -> GuangguV03Adaptation:
    """Validate and standardize the complete heating season without solving."""

    resolved = resolve_guanggu_v03_source_roots(source_root)
    source = resolved.delivery_root
    output = Path(output_dir).resolve()
    profile = _load_profile(profile_path)
    effective_patch_root = (
        Path(equipment_patch_root).resolve()
        if equipment_patch_root is not None
        else resolved.equipment_patch_root
    )
    source_report = validate_guanggu_v03_delivery(
        resolved.requested_root,
        full_audit=full_audit,
        profile_path=profile_path,
        equipment_patch_root=effective_patch_root,
    )
    if not source_report.valid:
        errors = [issue.message for issue in source_report.issues if issue.severity == "error"]
        raise ValueError("v0.3 源数据校验失败，禁止适配：" + "; ".join(errors))
    _prepare_output(source, output)

    files = profile["standard_files"]
    data_version = str(profile["data_version"])
    geo_source = gpd.read_file(source / files["buildings"]["path"])
    mapping_source = pd.read_csv(
        source / files["building_archetype_map"]["path"], encoding="utf-8-sig"
    )
    loads_source = pd.read_parquet(source / files["building_hourly_loads"]["path"])
    external_source = pd.read_parquet(source / files["external_timeseries"]["path"])
    technologies_source = pd.read_csv(
        source / files["technologies"]["path"], encoding="utf-8-sig"
    )
    patch = effective_patch_root
    equipment_source_path = (
        patch / "06_equipment_performance.csv"
        if patch is not None
        else source / files["equipment_performance"]["path"]
    )
    equipment_source = pd.read_csv(equipment_source_path, encoding="utf-8-sig")
    source_hours = _source_hour_order(profile)
    time_map = _build_timestamp_map(
        external_source,
        source_hours,
        str(profile["heating_season"]["timezone"]),
    )
    buildings = _canonical_buildings(geo_source, data_version)
    mapping = _normalize_mapping(mapping_source)
    loads = _canonical_loads(loads_source, time_map, data_version)
    external, gas_metadata = _canonical_external(
        external_source,
        time_map,
        technologies_source,
        profile,
        data_version,
    )
    technologies = _canonical_technology_parameters(technologies_source)
    equipment = _canonical_equipment_performance(equipment_source)
    source_total = float(
        loads_source.loc[loads_source["hour"].isin(source_hours), "heating_kW"].sum()
    )
    canonical_total = float(loads["heating_kW"].sum())
    absolute_error = abs(source_total - canonical_total)
    relative_error = absolute_error / max(abs(source_total), 1.0)
    metadata = {
        "source_validation_passed": True,
        "canonical_validation_passed": True,
        "model_ready": False,
        "solver_executed": False,
        "source_profile": source_report.source_profile,
        "profile_version": source_report.profile_version,
        "data_version": data_version,
        "contract_version": V3_DRAFT_CONTRACT,
        "source_hour_order": list(source_hours),
        "source_heating_total_kWh_th": source_total,
        "canonical_heating_total_kWh_th": canonical_total,
        "load_reconciliation_abs_error_kWh_th": absolute_error,
        "load_reconciliation_relative_error": relative_error,
        "fresh_air_load_added_by_adapter": False,
        "terminal_type": "fan_coil",
        "heating_supply_temperature_C": 45,
        "heating_return_temperature_C": 40,
        "peak_capacity_margin_fraction": 0.20,
        "ashp_upper_boundary_policy": "clamp_to_15C_no_extrapolation",
        "ashp_upper_boundary_clamped_hour_count": int(external["cop_boundary_clamped"].sum()),
        "gas_energy_basis": "LHV",
        "base_data_version": data_version,
        "base_root_identifier": source.name,
        "equipment_patch_applied": patch is not None,
        "equipment_patch_identifier": patch.name if patch is not None else None,
        "equipment_patch_files_sha256": (
            {
                name: _sha256(patch / name)
                for name in EQUIPMENT_PATCH_FILES
            }
            if patch is not None else {}
        ),
        "equipment_patch_provenance_only_files": (
            ["06C_equipment_curve_method.md"] if patch is not None else []
        ),
        **gas_metadata,
    }
    canonical = CanonicalSeasonData(
        source_profile=source_report.source_profile,
        contract_version=V3_DRAFT_CONTRACT,
        data_version=data_version,
        _buildings=buildings,
        _building_archetype_map=mapping,
        _loads=loads,
        _external_timeseries=external,
        _technology_parameters=technologies,
        _equipment_performance=equipment,
        _timestamp_hour_map=time_map,
        input_sha256=source_report.file_sha256,
        adaptation_metadata=MappingProxyType(metadata),
    )
    canonical_report = validate_canonical_season_data(
        canonical,
        expected_building_count=int(files["building_master"]["expected_rows"]),
        expected_hour_count=int(profile["heating_season"]["season_hour_count"]),
    )
    if not np.isclose(source_total, canonical_total, rtol=1e-12, atol=1e-6):
        _issue(
            canonical_report,
            "CANONICAL_LOAD_RECONCILIATION_FAILED",
            f"适配前后供暖季总热量误差 {absolute_error} kWh_th",
        )
    if not canonical_report.valid:
        errors = [issue.message for issue in canonical_report.issues if issue.severity == "error"]
        raise ValueError("v0.3 标准数据复验失败：" + "; ".join(errors))
    source_report_path = output / "source_validation_report.json"
    canonical_report_path = output / "canonical_validation_report.json"
    adaptation_report_path = output / "adaptation_report.json"
    field_mapping_path = output / "field_mapping.csv"
    buildings_path = output / "buildings.geojson"
    archetype_map_path = output / "building_archetype_map.csv"
    loads_path = output / "building_hourly_loads.parquet"
    external_path = output / "external_timeseries.parquet"
    technology_path = output / "technology_parameter_registry.csv"
    equipment_path = output / "equipment_performance.csv"
    time_map_path = output / "timestamp_hour_map.csv"
    buildings.to_file(buildings_path, driver="GeoJSON")
    mapping.to_csv(archetype_map_path, index=False, encoding="utf-8-sig")
    loads.to_parquet(loads_path, index=False)
    external.to_parquet(external_path, index=False)
    technologies.to_csv(technology_path, index=False, encoding="utf-8-sig")
    equipment.to_csv(equipment_path, index=False, encoding="utf-8-sig")
    time_map.to_csv(time_map_path, index=False, encoding="utf-8-sig")
    _write_json(source_report_path, source_report.to_dict())
    _write_json(canonical_report_path, canonical_report.to_dict())
    _write_json(adaptation_report_path, {**canonical.to_summary(), **metadata})
    pd.DataFrame(
        [
            {"source_file": "03_buildings.geojson", "source_field": "conditioned_area_m2", "canonical_file": "buildings.geojson", "canonical_field": "heated_area_m2", "rule": "rename_without_scaling", "unit": "m2"},
            {"source_file": "04_building_archetype_map.csv", "source_field": "building/zone archetype fields", "canonical_file": "building_archetype_map.csv", "canonical_field": "zone_*", "rule": "preserve_mixed_zones_expand_single_use", "unit": "mixed"},
            {"source_file": "05_building_hourly_loads.parquet", "source_field": "hour", "canonical_file": "timestamp_hour_map.csv", "canonical_field": "source_hour/heating_season_hour/hour", "rule": "8016..8759_then_0..1415_to_1..2160", "unit": "integer"},
            {"source_file": "05_building_hourly_loads.parquet", "source_field": "heating_kW", "canonical_file": "building_hourly_loads.parquet", "canonical_field": "heating_kW", "rule": "identity_no_rescaling", "unit": "kW_th"},
            {"source_file": "external_timeseries.parquet", "source_field": "time_weight_h", "canonical_file": "external_timeseries.parquet", "canonical_field": "time_weight_h_per_year", "rule": "rename", "unit": "h_per_year"},
            {"source_file": "external_timeseries.parquet", "source_field": "grid_carbon_factor_kgCO2e_per_kWh_e", "canonical_file": "external_timeseries.parquet", "canonical_field": "electricity_carbon_kgCO2e_per_kWh_e", "rule": "rename", "unit": "kgCO2e_per_kWh_e"},
            {"source_file": "external_timeseries.parquet + technologies.csv", "source_field": "natural_gas_price_CNY_per_Nm3 / natural_gas_LHV", "canonical_file": "external_timeseries.parquet", "canonical_field": "gas_price_CNY_per_kWh_LHV", "rule": "divide_by_LHV_kWh_per_Nm3_once", "unit": "CNY_per_kWh_LHV"},
            {"source_file": "external_timeseries.parquet + technologies.csv", "source_field": "natural_gas_carbon_factor_kgCO2e_per_Nm3 / natural_gas_LHV", "canonical_file": "external_timeseries.parquet", "canonical_field": "gas_carbon_kgCO2e_per_kWh_LHV", "rule": "divide_by_LHV_kWh_per_Nm3_once", "unit": "kgCO2e_per_kWh_LHV"},
            {"source_file": "external_timeseries.parquet", "source_field": "outdoor_temperature_C", "canonical_file": "external_timeseries.parquet", "canonical_field": "cop_lookup_temperature_C", "rule": "clip_upper_to_15C_no_extrapolation", "unit": "degC"},
        ]
    ).to_csv(field_mapping_path, index=False, encoding="utf-8-sig")

    for relative, digest in source_report.file_sha256.items():
        if relative.startswith("equipment_patch/"):
            if patch is None:
                raise RuntimeError("patch provenance exists without equipment_patch_root")
            path = patch / relative.removeprefix("equipment_patch/")
        else:
            path = source / Path(relative)
        if not path.is_file() or _sha256(path) != digest:
            raise RuntimeError(f"适配过程修改了源文件: {relative}")
    return GuangguV03Adaptation(
        output_dir=output,
        canonical_data=canonical,
        source_report=source_report,
        canonical_report=canonical_report,
        source_report_path=source_report_path,
        canonical_report_path=canonical_report_path,
        adaptation_report_path=adaptation_report_path,
        field_mapping_path=field_mapping_path,
        buildings_path=buildings_path,
        archetype_map_path=archetype_map_path,
        loads_path=loads_path,
        external_timeseries_path=external_path,
        technology_parameters_path=technology_path,
        equipment_performance_path=equipment_path,
        timestamp_hour_map_path=time_map_path,
    )
