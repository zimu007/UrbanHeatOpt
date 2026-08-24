"""Read-only validation for the Guanggu Software Park v0.3 delivery.

This module validates the source delivery itself.  It does not build a Pyomo
model and it never writes into the delivery directory.  Delivery QA reports
are treated as provenance only; every executable invariant is recomputed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
import zipfile

import geopandas as gpd
import numpy as np
import pandas as pd
import yaml


@dataclass(frozen=True, slots=True)
class GuangguV03Issue:
    code: str
    message: str
    path: str | None = None
    severity: str = "error"


@dataclass(slots=True)
class GuangguV03Report:
    source_profile: str
    profile_version: str
    data_version: str
    source_root: Path
    full_audit: bool
    inventory: dict[str, Any] = field(default_factory=dict)
    datasets: dict[str, Any] = field(default_factory=dict)
    file_sha256: dict[str, str] = field(default_factory=dict)
    issues: list[GuangguV03Issue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any(item.severity == "error" for item in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "valid" if self.valid else "invalid",
            "source_validation_passed": self.valid,
            "source_profile": self.source_profile,
            "profile_version": self.profile_version,
            "data_version": self.data_version,
            "source_root": str(self.source_root),
            "full_audit": self.full_audit,
            "inventory": self.inventory,
            "datasets": self.datasets,
            "file_sha256": self.file_sha256,
            "issues": [asdict(item) for item in self.issues],
        }


def _issue(
    report: GuangguV03Report,
    code: str,
    message: str,
    path: Path | None = None,
    severity: str = "error",
) -> None:
    report.issues.append(
        GuangguV03Issue(code, message, str(path) if path is not None else None, severity)
    )


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_profile(profile_path: str | Path | None) -> tuple[Path, dict[str, Any]]:
    path = (
        Path(profile_path)
        if profile_path is not None
        else Path(__file__).resolve().parents[1] / "configs" / "guanggu_v03.yaml"
    ).resolve()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("source_profile") != "guanggu_v03":
        raise ValueError("source profile 必须声明 source_profile: guanggu_v03")
    return path, data


def _read_csv(path: Path, encoding: str | None = None) -> tuple[pd.DataFrame, str]:
    candidates = [encoding] if encoding else []
    candidates.extend(["utf-8-sig", "utf-8", "gb18030"])
    attempted: set[str] = set()
    last_error: Exception | None = None
    for candidate in candidates:
        if candidate is None or candidate in attempted:
            continue
        attempted.add(candidate)
        try:
            return pd.read_csv(path, encoding=candidate), candidate
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise UnicodeError(f"无法确定 CSV 编码: {path}")


def _read_standard(path: Path, spec: dict[str, Any]) -> tuple[pd.DataFrame, str | None]:
    fmt = str(spec["format"]).lower()
    if fmt == "csv":
        return _read_csv(path, spec.get("encoding"))
    if fmt == "parquet":
        return pd.read_parquet(path), None
    if fmt == "geojson":
        return gpd.read_file(path), None
    raise ValueError(f"guanggu_v03 profile 含不支持格式: {fmt}")


def _classify_inventory(
    report: GuangguV03Report,
    root: Path,
    files: list[Path],
    profile: dict[str, Any],
) -> None:
    root_files = set(profile.get("classified_root_files", []))
    directories = profile.get("classified_directories", {})
    category_counts: Counter[str] = Counter()
    unclassified: list[str] = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        parts = relative.split("/")
        if len(parts) == 1 and relative in root_files:
            category_counts["root_delivery"] += 1
        elif parts[0] in directories:
            category_counts[str(directories[parts[0]])] += 1
        else:
            unclassified.append(relative)
            _issue(report, "UNCLASSIFIED_FILE", "文件未被 source profile 分类", path)
    report.inventory["categories"] = dict(sorted(category_counts.items()))
    report.inventory["unclassified_files"] = unclassified


def _validate_inventory(
    report: GuangguV03Report,
    root: Path,
    files: list[Path],
    profile: dict[str, Any],
) -> None:
    extensions = Counter(path.suffix.lower() for path in files)
    report.inventory.update(
        {"total_files": len(files), "extensions": dict(sorted(extensions.items()))}
    )
    expected = profile["expected_inventory"]
    if len(files) != int(expected["total_files"]):
        _issue(
            report,
            "INVENTORY_COUNT_MISMATCH",
            f"期望 {expected['total_files']} 个文件，实际 {len(files)}",
            root,
        )
    for extension, count in expected["extensions"].items():
        actual = extensions.get(extension, 0)
        if actual != int(count):
            _issue(
                report,
                "EXTENSION_COUNT_MISMATCH",
                f"{extension} 期望 {count} 个，实际 {actual}",
                root,
            )
    _classify_inventory(report, root, files, profile)


def _validate_standard_files(
    report: GuangguV03Report,
    root: Path,
    profile: dict[str, Any],
) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for name, spec in profile["standard_files"].items():
        path = root / spec["path"]
        if not path.is_file():
            _issue(report, "FILE_MISSING", f"缺少标准交付文件 {spec['path']}", path)
            continue
        try:
            frame, encoding = _read_standard(path, spec)
        except Exception as exc:
            _issue(report, "FILE_READ_ERROR", f"读取失败: {exc}", path)
            continue
        frames[name] = frame
        missing = sorted(set(spec.get("required_columns", [])) - set(frame.columns))
        if missing:
            _issue(report, "FIELD_MISSING", f"缺少字段: {', '.join(missing)}", path)
        expected_rows = spec.get("expected_rows")
        if expected_rows is not None and len(frame) != int(expected_rows):
            _issue(
                report,
                "ROW_COUNT_MISMATCH",
                f"期望 {expected_rows} 行，实际 {len(frame)} 行",
                path,
            )
        if "data_version" in frame:
            versions = set(frame["data_version"].dropna().astype(str))
            expected_version = {str(profile["data_version"])}
            if frame["data_version"].isna().any() or versions != expected_version:
                _issue(
                    report,
                    "DATA_VERSION_MISMATCH",
                    f"data_version 必须全部为 {profile['data_version']}，实际 {sorted(versions)}",
                    path,
                )
        if isinstance(frame, gpd.GeoDataFrame):
            expected_crs = spec.get("crs")
            if expected_crs and (
                frame.crs is None
                or frame.crs.to_string().upper() != str(expected_crs).upper()
            ):
                _issue(report, "CRS_INVALID", f"期望 {expected_crs}，实际 {frame.crs}", path)
            if (
                frame.geometry.isna().any()
                or frame.geometry.is_empty.any()
                or not frame.geometry.is_valid.all()
            ):
                _issue(report, "GEOMETRY_INVALID", "存在空或无效建筑几何", path)
            if not frame.geom_type.isin(["Polygon", "MultiPolygon"]).all():
                _issue(report, "GEOMETRY_TYPE_INVALID", "建筑几何必须为 Polygon/MultiPolygon", path)
        report.datasets[name] = {
            "path": spec["path"],
            "row_count": len(frame),
            "columns": list(frame.columns),
            "encoding": encoding,
        }
    return frames


def _numeric_series(
    report: GuangguV03Report,
    frame: pd.DataFrame,
    column: str,
    path: Path,
    *,
    nonnegative: bool = False,
    positive: bool = False,
) -> pd.Series | None:
    if column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce")
    if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
        _issue(report, "NUMERIC_VALUE_INVALID", f"{column} 必须为有限数值", path)
        return None
    if nonnegative and (values < 0).any():
        _issue(report, "NEGATIVE_VALUE", f"{column} 不得为负", path)
    if positive and (values <= 0).any():
        _issue(report, "NONPOSITIVE_VALUE", f"{column} 必须大于 0", path)
    return values


def _expected_season_mapping(
    hours: pd.Series, season: dict[str, Any]
) -> tuple[np.ndarray, np.ndarray]:
    values = hours.to_numpy(dtype=np.int64)
    flags = np.zeros(len(values), dtype=np.int8)
    season_hours = np.full(len(values), -1, dtype=np.int64)
    for segment in season["source_segments"]:
        start = int(segment["start"])
        end = int(segment["end"])
        season_start = int(segment["season_start"])
        mask = (values >= start) & (values <= end)
        flags[mask] = 1
        season_hours[mask] = season_start + values[mask] - start
    return flags, season_hours


def _validate_full_year_table(
    report: GuangguV03Report,
    frame: pd.DataFrame,
    path: Path,
    season: dict[str, Any],
    *,
    group_column: str | None = None,
) -> None:
    needed = {"hour", "heating_season_flag", "heating_season_hour"}
    if not needed <= set(frame):
        return
    count = int(season["full_year_hour_count"])
    start = int(season["full_year_hour_start"])
    expected_hours = np.arange(start, start + count, dtype=np.int64)
    groups = [(None, frame)] if group_column is None else frame.groupby(group_column, sort=False)
    for group_name, group in groups:
        hours = pd.to_numeric(group["hour"], errors="coerce")
        label = "" if group_name is None else f"（{group_name}）"
        if (
            len(group) != count
            or hours.isna().any()
            or not np.array_equal(hours.to_numpy(dtype=np.int64), expected_hours)
        ):
            _issue(
                report,
                "FULL_YEAR_HOUR_INVALID",
                f"全年小时必须严格按 {start}..{start + count - 1} 排列{label}",
                path,
            )
            continue
        expected_flag, expected_season_hour = _expected_season_mapping(hours, season)
        actual_flag = pd.to_numeric(group["heating_season_flag"], errors="coerce")
        if actual_flag.isna().any() or not np.array_equal(
            actual_flag.to_numpy(dtype=np.int64), expected_flag.astype(np.int64)
        ):
            _issue(report, "HEATING_SEASON_FLAG_INVALID", f"供暖季标记与源小时不一致{label}", path)
        actual_season = pd.to_numeric(group["heating_season_hour"], errors="coerce")
        in_season = expected_flag == 1
        if (
            actual_season[in_season].isna().any()
            or not np.array_equal(
                actual_season[in_season].to_numpy(dtype=np.int64),
                expected_season_hour[in_season],
            )
            or actual_season[~in_season].notna().any()
        ):
            _issue(
                report,
                "HEATING_SEASON_HOUR_INVALID",
                f"heating_season_hour 必须按 12月后接1—2月映射且非供暖季为空{label}",
                path,
            )


def _validate_buildings_and_mapping(
    report: GuangguV03Report,
    frames: dict[str, pd.DataFrame],
    root: Path,
    profile: dict[str, Any],
) -> None:
    master = frames.get("building_master")
    geo = frames.get("buildings")
    mapping = frames.get("building_archetype_map")
    loads = frames.get("building_hourly_loads")
    sources = [item for item in (master, geo, mapping, loads) if item is not None]
    if not sources or any("building_id" not in item for item in sources):
        return
    sets = [set(item["building_id"].dropna().astype(str)) for item in sources]
    if any(item != sets[0] for item in sets[1:]):
        _issue(report, "BUILDING_ID_SET_MISMATCH", "00、03、04、05 建筑 ID 集合不一致")
    excluded = set(map(str, profile.get("excluded_building_ids", [])))
    present_excluded = sorted(excluded & set().union(*sets))
    if present_excluded:
        _issue(
            report,
            "EXCLUDED_BUILDING_PRESENT",
            f"已排除建筑仍在计算输入中: {', '.join(present_excluded)}",
        )
    for name, frame in (("00", master), ("03", geo)):
        if frame is None:
            continue
        if frame["building_id"].isna().any() or frame["building_id"].duplicated().any():
            _issue(report, "BUILDING_ID_INVALID", f"{name} building_id 必须非空且唯一")
        path = root / profile["standard_files"]["building_master" if name == "00" else "buildings"]["path"]
        _numeric_series(report, frame, "conditioned_area_m2", path, positive=True)
    if master is not None:
        terminal_checks = {
            "terminal_type": "fan_coil",
            "terminal_description": "fan_coil_plus_fresh_air",
            "heating_supply_temperature_C": 45,
            "heating_return_temperature_C": 40,
            "terminal_parameter_status": "teacher_confirmed_baseline",
        }
        for column, expected in terminal_checks.items():
            if column in master and set(master[column].dropna()) != {expected}:
                _issue(report, "TERMINAL_BOUNDARY_INVALID", f"00 {column} 必须全部为 {expected!r}")
        if "load_included" in master and not master["load_included"].map(bool).all():
            _issue(report, "LOAD_SCOPE_INVALID", "00 中所有计算建筑 load_included 必须为 true")
    mapping_columns = {
        "building_id",
        "target_conditioned_area_m2",
        "is_mixed_use",
        "zone_id",
        "zone_use_type",
        "zone_area_m2",
        "zone_archetype_id",
        "zone_scale_factor",
    }
    if (
        master is not None
        and mapping is not None
        and {"building_id", "conditioned_area_m2"} <= set(master)
        and mapping_columns <= set(mapping)
        and not master["building_id"].duplicated().any()
    ):
        master_area = master.set_index("building_id")["conditioned_area_m2"].astype(float)
        for building_id, group in mapping.groupby("building_id", sort=False):
            target = pd.to_numeric(group["target_conditioned_area_m2"], errors="coerce")
            if target.isna().any() or target.nunique() != 1:
                _issue(report, "MAPPING_TARGET_AREA_INVALID", f"04 {building_id} 目标面积必须唯一且有限")
                continue
            if building_id not in master_area.index:
                continue
            if not np.isclose(float(target.iloc[0]), float(master_area.loc[building_id]), rtol=1e-6, atol=1e-6):
                _issue(report, "MAPPING_BUILDING_AREA_MISMATCH", f"04 {building_id} 目标面积与00不一致")
            mixed = group["is_mixed_use"]
            if mixed.dtype != bool:
                mixed = mixed.astype(str).str.lower().map({"true": True, "false": False})
            if mixed.isna().any() or mixed.nunique() != 1:
                _issue(report, "MIXED_USE_FLAG_INVALID", f"04 {building_id} is_mixed_use 无效")
                continue
            if bool(mixed.iloc[0]):
                required = ["zone_id", "zone_use_type", "zone_area_m2", "zone_archetype_id", "zone_scale_factor"]
                if group[required].isna().any().any() or group["zone_id"].duplicated().any():
                    _issue(report, "ZONE_MAPPING_INVALID", f"04 {building_id} 分区主键或字段缺失")
                zone_area = pd.to_numeric(group["zone_area_m2"], errors="coerce")
                if zone_area.isna().any() or (zone_area <= 0).any() or not np.isclose(
                    float(zone_area.sum()), float(target.iloc[0]), rtol=1e-6, atol=1e-6
                ):
                    _issue(report, "ZONE_AREA_MISMATCH", f"04 {building_id} 分区面积和与建筑面积不一致")


def _validate_loads_and_external(
    report: GuangguV03Report,
    frames: dict[str, pd.DataFrame],
    root: Path,
    profile: dict[str, Any],
) -> None:
    loads = frames.get("building_hourly_loads")
    external = frames.get("external_timeseries")
    season = profile["heating_season"]
    if loads is not None:
        path = root / profile["standard_files"]["building_hourly_loads"]["path"]
        if {"building_id", "hour"} <= set(loads) and loads.duplicated(["building_id", "hour"]).any():
            _issue(report, "LOAD_KEY_DUPLICATE", "05 building_id+hour 存在重复", path)
        _numeric_series(report, loads, "heating_kW", path, nonnegative=True)
        if "timestamp" in loads:
            if not isinstance(loads["timestamp"].dtype, pd.DatetimeTZDtype):
                _issue(report, "TIMESTAMP_TIMEZONE_MISSING", "05 timestamp 必须带时区", path)
            elif str(loads["timestamp"].dt.tz) != season["timezone"]:
                _issue(report, "TIMESTAMP_TIMEZONE_INVALID", f"05 timestamp 必须为 {season['timezone']}", path)
        if "dhw_included" in loads and set(loads["dhw_included"].dropna().map(bool)) != {False}:
            _issue(report, "DHW_BOUNDARY_INVALID", "05 dhw_included 必须全部为 false", path)
        _validate_full_year_table(report, loads, path, season, group_column="building_id")
        if "building_id" in loads and "heating_season_flag" in loads:
            season_counts = loads.loc[loads["heating_season_flag"].eq(1)].groupby("building_id").size()
            expected = int(season["season_hour_count"])
            if len(season_counts) == 0 or not season_counts.eq(expected).all():
                _issue(report, "BUILDING_SEASON_COVERAGE_INVALID", f"05 每栋必须有 {expected} 个供暖季小时", path)
    if external is not None:
        path = root / profile["standard_files"]["external_timeseries"]["path"]
        canonical_gas_columns = {
            "gas_price_CNY_per_kWh_LHV",
            "gas_carbon_kgCO2e_per_kWh_LHV",
        }
        ambiguous = sorted(canonical_gas_columns & set(external))
        if ambiguous:
            _issue(
                report,
                "GAS_AUTHORITY_AMBIGUOUS",
                "源 external 同时声明体积口径与标准LHV口径，适配器无法证明未重复换算: "
                + ", ".join(ambiguous),
                path,
            )
        if "hour" in external and external["hour"].duplicated().any():
            _issue(report, "EXTERNAL_HOUR_DUPLICATE", "external hour 存在重复", path)
        _validate_full_year_table(report, external, path, season)
        if "timestamp" in external:
            if not isinstance(external["timestamp"].dtype, pd.DatetimeTZDtype):
                _issue(report, "TIMESTAMP_TIMEZONE_MISSING", "external timestamp 必须带时区", path)
            elif str(external["timestamp"].dt.tz) != season["timezone"]:
                _issue(report, "TIMESTAMP_TIMEZONE_INVALID", f"external timestamp 必须为 {season['timezone']}", path)
        if "heating_season_flag" in external:
            selected = external.loc[external["heating_season_flag"].eq(1)]
            numeric = [
                "outdoor_temperature_C",
                "electricity_base_price_CNY_per_kWh_e",
                "electricity_price_multiplier",
                "electricity_price_CNY_per_kWh_e",
                "grid_carbon_factor_kgCO2e_per_kWh_e",
                "natural_gas_price_CNY_per_Nm3",
                "natural_gas_carbon_factor_kgCO2e_per_Nm3",
                "time_weight_h",
            ]
            for column in numeric:
                values = _numeric_series(report, selected, column, path)
                if values is not None and column != "outdoor_temperature_C" and (values <= 0).any():
                    _issue(report, "EXTERNAL_VALUE_NONPOSITIVE", f"供暖季 {column} 必须大于 0", path)
            required_sources = [column for column in external.columns if column.endswith("_source_id") or column.endswith("_parameter_status")]
            if required_sources and selected[required_sources].isna().any().any():
                _issue(report, "EXTERNAL_PROVENANCE_MISSING", "供暖季外部参数来源或状态存在空值", path)
            if {
                "electricity_base_price_CNY_per_kWh_e",
                "electricity_price_multiplier",
                "electricity_price_CNY_per_kWh_e",
            } <= set(selected):
                expected_price = (
                    selected["electricity_base_price_CNY_per_kWh_e"]
                    * selected["electricity_price_multiplier"]
                )
                if not np.allclose(
                    selected["electricity_price_CNY_per_kWh_e"],
                    expected_price,
                    rtol=0,
                    atol=1e-12,
                ):
                    _issue(report, "ELECTRICITY_PRICE_FORMULA_INVALID", "逐时电价不等于基价×倍率", path)
    if loads is not None and external is not None and {"hour", "timestamp"} <= set(loads) and {"hour", "timestamp"} <= set(external):
        load_time = loads[["hour", "timestamp"]].drop_duplicates().sort_values("hour")
        external_time = external[["hour", "timestamp"]].sort_values("hour")
        if len(load_time) != len(external_time) or not load_time.reset_index(drop=True).equals(
            external_time.reset_index(drop=True)
        ):
            _issue(report, "LOAD_EXTERNAL_TIME_MISMATCH", "05 与 external 的 hour/timestamp 不一致")


def _technology_value(
    frame: pd.DataFrame, technology_id: str, parameter_name: str
) -> tuple[Any, Any] | None:
    rows = frame.loc[
        frame["technology_id"].eq(technology_id)
        & frame["parameter_name"].eq(parameter_name)
    ]
    if len(rows) != 1:
        return None
    return rows.iloc[0]["recommended_value"], rows.iloc[0]["unit"]


def _validate_technologies(
    report: GuangguV03Report,
    frames: dict[str, pd.DataFrame],
    root: Path,
    profile: dict[str, Any],
) -> None:
    technologies = frames.get("technologies")
    performance = frames.get("equipment_performance")
    metadata = frames.get("equipment_metadata")
    external = frames.get("external_timeseries")
    rules = profile["technology_rules"]
    technology_columns = {
        "technology_id",
        "parameter_name",
        "recommended_value",
        "unit",
        "parameter_status",
    }
    if technologies is not None and technology_columns <= set(technologies):
        path = root / profile["standard_files"]["technologies"]["path"]
        if technologies.duplicated(["technology_id", "parameter_name"]).any():
            _issue(report, "TECHNOLOGY_PARAMETER_DUPLICATE", "technologies 技术+参数主键重复", path)
        statuses = set(technologies["parameter_status"].dropna().astype(str))
        unexpected = statuses - set(rules["allowed_parameter_status"])
        if unexpected:
            _issue(report, "PARAMETER_STATUS_INVALID", f"存在未知参数状态: {sorted(unexpected)}", path)
        pending = technologies["parameter_status"].eq("pending_confirmation")
        if technologies.loc[pending, "recommended_value"].notna().any():
            _issue(report, "PENDING_PARAMETER_HAS_VALUE", "pending_confirmation 不得填写推荐值", path)
        lhv = _technology_value(technologies, "GAS_BOILER_BASE_01", "natural_gas_LHV")
        expected_lhv = float(rules["natural_gas_lhv_MJ_per_Nm3"])
        if lhv is None or lhv[1] != "MJ/Nm3" or not np.isclose(float(lhv[0]), expected_lhv):
            _issue(report, "GAS_LHV_INVALID", f"天然气 LHV 必须为 {expected_lhv} MJ/Nm3", path)
        efficiency = _technology_value(
            technologies,
            "GAS_BOILER_BASE_01",
            "thermal_efficiency_conventional_LHV",
        )
        expected_efficiency = float(rules["boiler_efficiency_LHV"])
        if efficiency is None or efficiency[1] != "fraction" or not np.isclose(
            float(efficiency[0]), expected_efficiency
        ):
            _issue(report, "BOILER_LHV_EFFICIENCY_INVALID", f"常规燃气锅炉 LHV 效率必须为 {expected_efficiency}", path)
    performance_columns = {
        "technology_id",
        "technology_type",
        "COP",
        "efficiency",
        "capacity_ratio",
    }
    if performance is not None and performance_columns <= set(performance):
        path = root / profile["standard_files"]["equipment_performance"]["path"]
        coordinate_columns = [
            "technology_id", "operating_mode", "Tout", "Tsource", "Tsupply",
            "Treturn", "PLR", "SOC", "startup_state",
        ]
        if set(coordinate_columns) <= set(performance) and performance.duplicated(
            coordinate_columns
        ).any():
            _issue(report, "EQUIPMENT_POINT_DUPLICATE", "06 设备工况点主键重复", path)
        for flag in ("is_source_point", "is_interpolated", "is_assumption"):
            if flag in performance and not set(performance[flag].dropna()).issubset({0, 1}):
                _issue(report, "EQUIPMENT_FLAG_INVALID", f"06 {flag} 只能为0/1", path)
        ashp = performance[performance["technology_type"].eq("air_source_heat_pump")]
        if len(ashp):
            if ashp["COP"].isna().any() or (ashp["COP"] <= 0).any():
                _issue(report, "ASHP_COP_INVALID", "空气源热泵 COP 必须为有限正数", path)
            if ashp["capacity_ratio"].isna().any() or (ashp["capacity_ratio"] <= 0).any():
                _issue(report, "ASHP_CAPACITY_RATIO_INVALID", "空气源热泵 capacity_ratio 必须为有限正数", path)
        gas = performance[performance["technology_type"].eq("gas_boiler")]
        if len(gas):
            efficiency = pd.to_numeric(gas["efficiency"], errors="coerce")
            if efficiency.isna().any() or (efficiency <= 0).any() or (efficiency > 1).any():
                _issue(report, "GAS_CURVE_EFFICIENCY_INVALID", "06 燃气锅炉效率必须在 (0,1]", path)
            _issue(
                report,
                "GAS_HHV_CURVE_PROVENANCE_ONLY",
                "06 燃气锅炉曲线为 HHV 口径，仅作追溯；执行参数使用 technologies.csv 的 LHV 效率0.94",
                path,
                "warning",
            )
        if metadata is not None and "technology_id" in metadata:
            if set(performance["technology_id"].dropna()) != set(metadata["technology_id"].dropna()):
                _issue(report, "EQUIPMENT_METADATA_ID_MISMATCH", "06 与 06A 技术 ID 集合不一致", path)
        if (
            external is not None
            and len(ashp)
            and {"outdoor_temperature_C", "heating_season_flag", "hour"} <= set(external)
        ):
            season_rows = external.loc[external["heating_season_flag"].eq(1)]
            curve_max = float(rules["ashp_curve_max_temperature_C"])
            above = season_rows["outdoor_temperature_C"].gt(curve_max)
            report.datasets["ashp_curve_coverage"] = {
                "curve_max_temperature_C": curve_max,
                "source_temperature_max_C": float(season_rows["outdoor_temperature_C"].max()),
                "upper_boundary_clamped_hour_count": int(above.sum()),
                "policy": rules["ashp_upper_boundary_policy"],
                "source_hours": season_rows.loc[above, "hour"].astype(int).tolist(),
            }
            if above.any():
                _issue(
                    report,
                    "ASHP_UPPER_BOUNDARY_CLAMP_REQUIRED",
                    f"{int(above.sum())} 个供暖季小时高于 {curve_max}℃，后续适配按边界值封顶且不得外推",
                    path,
                    "warning",
                )


def _validate_scheme_manifest(
    report: GuangguV03Report,
    frames: dict[str, pd.DataFrame],
    root: Path,
) -> None:
    manifest = frames.get("scheme_manifest")
    if manifest is None or "scheme_id" not in manifest:
        return
    expected_schemes = {"centralized", "distributed", "hybrid"}
    if set(manifest["scheme_id"].dropna()) != expected_schemes or manifest["scheme_id"].duplicated().any():
        _issue(report, "SCHEME_SET_INVALID", "scheme manifest 必须恰好包含三种方案")
    shared_columns = [
        "building_load_file", "building_load_value_hash_sha256", "external_timeseries_file",
        "terminal_type", "heating_supply_temperature_C", "heating_return_temperature_C",
        "building_master_file_hash_sha256", "mapping_file_hash_sha256",
        "building_load_file_hash_sha256", "external_timeseries_file_hash_sha256",
        "terminal_parameter_hash_sha256",
    ]
    available_shared = [column for column in shared_columns if column in manifest]
    if len(available_shared) == len(shared_columns) and any(
        manifest[column].nunique(dropna=False) != 1 for column in available_shared
    ):
        _issue(report, "SCHEME_INPUT_NOT_SHARED", "三种方案没有共享完全相同的负荷、外部时序和末端边界")
    hash_rules = {
        "building_master_file_hash_sha256": "00_building_master.csv",
        "mapping_file_hash_sha256": "04_building_archetype_map.csv",
        "building_load_file_hash_sha256": "05_building_hourly_loads.parquet",
        "external_timeseries_file_hash_sha256": "external_timeseries.parquet",
    }
    for column, relative in hash_rules.items():
        if column not in manifest:
            continue
        target = root / relative
        if not target.is_file():
            continue
        actual = _sha256(target)
        declared = set(manifest[column].dropna().astype(str).str.lower())
        if declared != {actual.lower()}:
            _issue(report, "SCHEME_FILE_HASH_MISMATCH", f"{column} 与实际 {relative} SHA-256 不一致", target)


def _validate_building_ts(
    report: GuangguV03Report,
    frames: dict[str, pd.DataFrame],
    root: Path,
    profile: dict[str, Any],
) -> None:
    loads = frames.get("building_hourly_loads")
    wide = frames.get("building_ts")
    if (
        loads is None
        or wide is None
        or "hour" not in wide
        or not {"building_id", "hour", "heating_kW"} <= set(loads)
    ):
        return
    path = root / profile["standard_files"]["building_ts"]["path"]
    expected_hours = np.arange(int(profile["heating_season"]["full_year_hour_count"]))
    hours = pd.to_numeric(wide["hour"], errors="coerce")
    if hours.isna().any() or not np.array_equal(hours.to_numpy(dtype=np.int64), expected_hours):
        _issue(report, "BUILDING_TS_HOUR_INVALID", "Building_TS hour 必须严格为0..8759", path)
        return
    building_columns = list(wide.columns[1:])
    load_ids = sorted(loads["building_id"].unique())
    if set(building_columns) != set(load_ids):
        _issue(report, "BUILDING_TS_ID_MISMATCH", "Building_TS 建筑列与05建筑集合不一致", path)
        return
    numeric = wide[load_ids].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy(dtype=float)).all():
        _issue(report, "BUILDING_TS_VALUE_INVALID", "Building_TS 负荷必须为有限数值", path)
        return
    if loads.duplicated(["building_id", "hour"]).any():
        return
    pivot = loads.pivot(index="hour", columns="building_id", values="heating_kW").sort_index()[load_ids]
    difference = np.abs(numeric.to_numpy(dtype=float) - pivot.to_numpy(dtype=float))
    maximum = float(difference.max(initial=0.0))
    report.datasets["building_ts_reconciliation"] = {"max_abs_error_kW": maximum}
    if maximum > 1e-9:
        _issue(report, "BUILDING_TS_VALUE_MISMATCH", f"Building_TS 与05最大差值 {maximum} kW", path)


def _validate_hour_sequence(
    report: GuangguV03Report,
    path: Path,
    frame: pd.DataFrame,
    hour_column: str,
    start: int,
    count: int,
) -> None:
    if hour_column not in frame:
        _issue(report, "HOUR_FIELD_MISSING", f"缺少小时字段 {hour_column}", path)
        return
    values = pd.to_numeric(frame[hour_column], errors="coerce")
    expected = np.arange(start, start + count, dtype=np.int64)
    if len(values) != count or values.isna().any() or not np.array_equal(
        values.to_numpy(dtype=np.int64), expected
    ):
        _issue(report, "HOUR_INDEX_INVALID", f"小时必须严格为 {start}..{start + count - 1}", path)


def _validate_raw_dest(
    report: GuangguV03Report,
    root: Path,
    profile: dict[str, Any],
) -> None:
    spec = profile["raw_dest"]
    raw_root = root / spec["root"]
    if not raw_root.is_dir():
        _issue(report, "RAW_DEST_MISSING", "缺少 DeST 原型逐时负荷目录", raw_root)
        return
    files = sorted(path for path in raw_root.rglob("*") if path.is_file())
    csv_files = [path for path in files if path.suffix.lower() == ".csv"]
    if len(files) != int(spec["expected_files"]):
        _issue(report, "RAW_FILE_COUNT_MISMATCH", f"DeST 目录期望 {spec['expected_files']} 个文件，实际 {len(files)}", raw_root)
    if len(csv_files) != int(spec["expected_csv_files"]):
        _issue(report, "RAW_CSV_COUNT_MISMATCH", f"DeST 目录期望 {spec['expected_csv_files']} 个 CSV，实际 {len(csv_files)}", raw_root)
    if not report.full_audit:
        report.datasets["raw_dest"] = {"file_count": len(files), "csv_count": len(csv_files), "audit": "inventory_only"}
        return
    weather_suffix = spec["hourly_weather_suffix"]
    load_suffixes = tuple(spec["hourly_load_suffixes"])
    weather_frames: list[tuple[Path, pd.DataFrame]] = []
    hourly_load_count = 0
    readable = 0
    for path in csv_files:
        try:
            frame, _ = _read_csv(path)
        except Exception as exc:
            _issue(report, "RAW_CSV_READ_ERROR", f"CSV 读取失败: {exc}", path)
            continue
        readable += 1
        if path.name.endswith(weather_suffix):
            weather_frames.append((path, frame))
            _validate_hour_sequence(report, path, frame, spec["hour_column"], int(spec["hour_start"]), int(spec["hour_count"]))
        elif path.name.endswith(load_suffixes):
            hourly_load_count += 1
            _validate_hour_sequence(report, path, frame, spec["hour_column"], int(spec["hour_start"]), int(spec["hour_count"]))
    if len(weather_frames) != int(spec["expected_hourly_weather_files"]):
        _issue(report, "RAW_WEATHER_COUNT_MISMATCH", f"逐时气象文件期望 {spec['expected_hourly_weather_files']}，实际 {len(weather_frames)}", raw_root)
    if hourly_load_count != int(spec["expected_hourly_load_files"]):
        _issue(report, "RAW_HOURLY_LOAD_COUNT_MISMATCH", f"逐时负荷文件期望 {spec['expected_hourly_load_files']}，实际 {hourly_load_count}", raw_root)
    if weather_frames:
        reference_path, reference = weather_frames[0]
        for candidate_path, candidate in weather_frames[1:]:
            if list(candidate.columns) != list(reference.columns) or not candidate.equals(reference):
                _issue(report, "RAW_WEATHER_NOT_IDENTICAL", f"气象副本与权威文件不一致: {reference_path.name}", candidate_path)
    report.datasets["raw_dest"] = {
        "file_count": len(files),
        "csv_count": len(csv_files),
        "readable_csv_count": readable,
        "hourly_weather_count": len(weather_frames),
        "hourly_load_count": hourly_load_count,
        "authoritative_weather_path": str(weather_frames[0][0].relative_to(root)) if weather_frames else None,
        "audit": "full",
    }


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeError(f"无法确定文本编码: {path}")


def _audit_supporting_files(
    report: GuangguV03Report,
    root: Path,
    files: list[Path],
    profile: dict[str, Any],
) -> None:
    if not report.full_audit:
        return
    standard = {str(spec["path"]).replace("\\", "/") for spec in profile["standard_files"].values()}
    raw_prefix = str(profile["raw_dest"]["root"]).replace("\\", "/") + "/"
    readable = 0
    workbook_sheets: dict[str, list[str]] = {}
    for path in files:
        relative = path.relative_to(root).as_posix()
        if relative in standard or relative.startswith(raw_prefix):
            continue
        try:
            suffix = path.suffix.lower()
            if suffix == ".csv":
                _read_csv(path)
            elif suffix == ".parquet":
                pd.read_parquet(path)
            elif suffix == ".geojson":
                gpd.read_file(path)
            elif suffix == ".json":
                json.loads(_read_text(path))
            elif suffix in {".md", ".txt"}:
                _read_text(path)
            elif suffix == ".xlsx":
                if not zipfile.is_zipfile(path):
                    raise ValueError("不是有效 OOXML 容器")
                excel = pd.ExcelFile(path)
                workbook_sheets[relative] = list(excel.sheet_names)
                for sheet in excel.sheet_names:
                    excel.parse(sheet_name=sheet)
            else:
                raise ValueError(f"不支持的文件格式 {suffix}")
        except Exception as exc:
            _issue(report, "SUPPORTING_FILE_READ_ERROR", f"追溯/QA文件读取失败: {exc}", path)
            continue
        readable += 1
    report.datasets["supporting_files"] = {
        "readable_file_count": readable,
        "workbook_sheets": workbook_sheets,
        "role": "provenance_only",
    }


def validate_guanggu_v03_delivery(
    source_root: str | Path,
    *,
    full_audit: bool = False,
    profile_path: str | Path | None = None,
) -> GuangguV03Report:
    """Validate and hash a v0.3 delivery without modifying source files."""

    profile_file, profile = _load_profile(profile_path)
    root = Path(source_root).resolve()
    if not root.is_dir():
        raise ValueError(f"交付目录不存在: {root}")
    report = GuangguV03Report(
        source_profile=profile["source_profile"],
        profile_version=profile["profile_version"],
        data_version=profile["data_version"],
        source_root=root,
        full_audit=full_audit,
    )
    files = sorted(
        path for path in root.rglob("*") if path.is_file() and path.resolve() != profile_file
    )
    before = {path: _sha256(path) for path in files}
    report.file_sha256 = {
        path.relative_to(root).as_posix(): digest for path, digest in before.items()
    }
    _validate_inventory(report, root, files, profile)
    frames = _validate_standard_files(report, root, profile)
    _validate_buildings_and_mapping(report, frames, root, profile)
    _validate_loads_and_external(report, frames, root, profile)
    _validate_technologies(report, frames, root, profile)
    _validate_scheme_manifest(report, frames, root)
    _validate_building_ts(report, frames, root, profile)
    _validate_raw_dest(report, root, profile)
    _audit_supporting_files(report, root, files, profile)
    for relative in profile.get("provenance_files", []):
        path = root / relative
        if not path.is_file():
            _issue(report, "PROVENANCE_FILE_MISSING", f"缺少来源/QA文件 {relative}", path)
    for path, digest in before.items():
        if not path.is_file() or _sha256(path) != digest:
            _issue(report, "INPUT_INTEGRITY_CHANGED", "校验过程不得修改源文件", path)
    return report
