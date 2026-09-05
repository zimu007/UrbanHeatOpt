"""Read-only validation for the Guanggu Wuhan v0.2 data delivery.

The source profile inventories every delivered file while keeping a strict
boundary between executable inputs (03/04/05 plus one verified weather
series) and provenance-only DeST reports/workbooks.  No source file is ever
rewritten or normalized in place.
"""

from __future__ import annotations

from urbanheatopt.paths import REPOSITORY_ROOT as REPOSITORY_ROOT_PATH, PACKAGE_ROOT

from collections import Counter
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any
import zipfile

import geopandas as gpd
import numpy as np
import pandas as pd
import yaml


@dataclass(frozen=True, slots=True)
class WuhanV02Issue:
    code: str
    message: str
    path: str | None = None
    severity: str = "error"


@dataclass(slots=True)
class WuhanV02Report:
    source_profile: str
    profile_version: str
    data_version: str
    source_root: Path
    full_audit: bool
    inventory: dict[str, Any] = field(default_factory=dict)
    datasets: dict[str, Any] = field(default_factory=dict)
    file_sha256: dict[str, str] = field(default_factory=dict)
    issues: list[WuhanV02Issue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any(item.severity == "error" for item in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "valid" if self.valid else "invalid",
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
    report: WuhanV02Report,
    code: str,
    message: str,
    path: Path | None = None,
    severity: str = "error",
) -> None:
    report.issues.append(
        WuhanV02Issue(code, message, str(path) if path is not None else None, severity)
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
        else PACKAGE_ROOT / "data" / "profile_resources" / "wuhan_v02.yaml"
    ).resolve()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("source_profile") != "wuhan_v02":
        raise ValueError("source profile 必须声明 source_profile: wuhan_v02")
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
    raise ValueError(f"wuhan_v02 profile 含不支持格式: {fmt}")


def _validate_standard_files(
    report: WuhanV02Report, root: Path, profile: dict[str, Any]
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
        report.datasets[name] = {
            "path": spec["path"],
            "row_count": len(frame),
            "columns": list(frame.columns),
            "encoding": encoding,
        }
    return frames


def _validate_hour_sequence(
    report: WuhanV02Report,
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


def _validate_cross_file_rules(
    report: WuhanV02Report,
    frames: dict[str, pd.DataFrame],
    *,
    hour_start: int,
    hour_count: int,
) -> None:
    buildings = frames.get("buildings")
    mapping = frames.get("building_archetype_map")
    loads = frames.get("building_hourly_loads")
    if buildings is not None and "building_id" in buildings:
        if buildings["building_id"].isna().any() or buildings["building_id"].duplicated().any():
            _issue(report, "BUILDING_ID_INVALID", "03 建筑 ID 必须非空且唯一")
        area = pd.to_numeric(buildings.get("conditioned_area_m2"), errors="coerce")
        if area.isna().any() or not np.isfinite(area).all() or (area <= 0).any():
            _issue(report, "BUILDING_AREA_INVALID", "03 conditioned_area_m2 必须为有限正数")
    if mapping is not None:
        required = {
            "building_id",
            "is_mixed_use",
            "archetype_id",
            "target_conditioned_area_m2",
            "zone_id",
            "zone_area_m2",
            "zone_archetype_id",
        }
        if required <= set(mapping):
            mixed_flag = mapping["is_mixed_use"]
            if mixed_flag.dtype != bool:
                normalized = mixed_flag.astype(str).str.lower().map({"true": True, "false": False})
            else:
                normalized = mixed_flag
            if normalized.isna().any():
                _issue(report, "MIXED_USE_FLAG_INVALID", "04 is_mixed_use 必须为布尔值")
            else:
                mixed = mapping.loc[normalized]
                single = mapping.loc[~normalized]
                if single["building_id"].isna().any() or single["building_id"].duplicated().any():
                    _issue(report, "SINGLE_USE_KEY_INVALID", "04 单一用途 building_id 必须非空且唯一")
                if single[["archetype_id", "target_conditioned_area_m2"]].isna().any().any():
                    _issue(report, "SINGLE_USE_ARCHETYPE_MISSING", "04 单一用途建筑缺少建筑级原型或面积")
                mixed_columns = ["building_id", "zone_id", "zone_area_m2", "zone_archetype_id"]
                if mixed[mixed_columns].isna().any().any() or mixed.duplicated(
                    ["building_id", "zone_id"]
                ).any():
                    _issue(report, "ZONE_KEY_INVALID", "04 混合用途 building_id+zone_id 必须非空且唯一")
                zone_area = pd.to_numeric(mixed["zone_area_m2"], errors="coerce")
                if zone_area.isna().any() or not np.isfinite(zone_area).all() or (zone_area <= 0).any():
                    _issue(report, "ZONE_AREA_INVALID", "04 混合用途 zone_area_m2 必须为有限正数")
    if loads is not None:
        required = {"timestamp", "hour", "building_id", "heating_kW"}
        if required <= set(loads):
            if loads.duplicated(["building_id", "timestamp"]).any():
                _issue(report, "LOAD_KEY_DUPLICATE", "05 building_id+timestamp 存在重复")
            heating = pd.to_numeric(loads["heating_kW"], errors="coerce")
            if heating.isna().any() or not np.isfinite(heating).all() or (heating < 0).any():
                _issue(report, "HEATING_LOAD_INVALID", "05 heating_kW 必须为有限非负数")
            timestamps = loads["timestamp"]
            if not isinstance(timestamps.dtype, pd.DatetimeTZDtype):
                _issue(report, "TIMESTAMP_TIMEZONE_MISSING", "05 timestamp 必须带时区")
            elif str(timestamps.dt.tz) != "Asia/Shanghai":
                _issue(report, "TIMESTAMP_TIMEZONE_INVALID", "05 timestamp 必须使用 Asia/Shanghai")
            hours = pd.to_numeric(loads["hour"], errors="coerce")
            coverage = loads.assign(_hour=hours).groupby("building_id", sort=False)["_hour"]
            expected_hours = np.arange(hour_start, hour_start + hour_count)
            invalid_coverage = any(
                len(series) != hour_count
                or not np.array_equal(series.to_numpy(dtype=np.int64), expected_hours)
                for _, series in coverage
            )
            if invalid_coverage:
                _issue(
                    report,
                    "BUILDING_HOUR_COVERAGE_INVALID",
                    f"05 每栋建筑必须覆盖 {hour_start}..{hour_start + hour_count - 1}",
                )
    if buildings is not None and mapping is not None and loads is not None:
        building_ids = set(buildings["building_id"].dropna())
        mapping_ids = set(mapping["building_id"].dropna())
        load_ids = set(loads["building_id"].dropna())
        if building_ids != mapping_ids or building_ids != load_ids:
            _issue(report, "BUILDING_ID_SET_MISMATCH", "03、04、05 建筑 ID 集合不一致")


def _validate_raw_dest(
    report: WuhanV02Report, root: Path, profile: dict[str, Any]
) -> None:
    raw_spec = profile["raw_dest"]
    raw_root = root / raw_spec["root"]
    if not raw_root.is_dir():
        _issue(report, "RAW_DEST_MISSING", "缺少 DeST 原型逐时负荷目录", raw_root)
        return
    files = sorted(path for path in raw_root.rglob("*") if path.is_file())
    csv_files = [path for path in files if path.suffix.lower() == ".csv"]
    if len(files) != int(raw_spec["expected_files"]):
        _issue(report, "RAW_FILE_COUNT_MISMATCH", f"DeST 目录期望 {raw_spec['expected_files']} 个文件，实际 {len(files)}", raw_root)
    if len(csv_files) != int(raw_spec["expected_csv_files"]):
        _issue(report, "RAW_CSV_COUNT_MISMATCH", f"DeST 目录期望 {raw_spec['expected_csv_files']} 个 CSV，实际 {len(csv_files)}", raw_root)
    if not report.full_audit:
        report.datasets["raw_dest"] = {"file_count": len(files), "csv_count": len(csv_files), "audit": "inventory_only"}
        return

    weather_suffix = raw_spec["hourly_weather_suffix"]
    load_suffixes = tuple(raw_spec["hourly_load_suffixes"])
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
            _validate_hour_sequence(report, path, frame, raw_spec["hour_column"], int(raw_spec["hour_start"]), int(raw_spec["hour_count"]))
        elif path.name.endswith(load_suffixes):
            hourly_load_count += 1
            _validate_hour_sequence(report, path, frame, raw_spec["hour_column"], int(raw_spec["hour_start"]), int(raw_spec["hour_count"]))
    if len(weather_frames) != int(raw_spec["expected_hourly_weather_files"]):
        _issue(report, "RAW_WEATHER_COUNT_MISMATCH", f"逐时气象文件期望 {raw_spec['expected_hourly_weather_files']}，实际 {len(weather_frames)}", raw_root)
    if hourly_load_count != int(raw_spec["expected_hourly_load_files"]):
        _issue(report, "RAW_HOURLY_LOAD_COUNT_MISMATCH", f"逐时负荷文件期望 {raw_spec['expected_hourly_load_files']}，实际 {hourly_load_count}", raw_root)
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


def validate_wuhan_v02_delivery(
    source_root: str | Path,
    *,
    full_audit: bool = False,
    profile_path: str | Path | None = None,
) -> WuhanV02Report:
    """Validate and hash a v0.2 delivery without modifying any source file."""

    profile_file, profile = _load_profile(profile_path)
    root = Path(source_root).resolve()
    if not root.is_dir():
        raise ValueError(f"交付目录不存在: {root}")
    report = WuhanV02Report(
        source_profile=profile["source_profile"],
        profile_version=profile["profile_version"],
        data_version=profile["data_version"],
        source_root=root,
        full_audit=full_audit,
    )
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.resolve() != profile_file
    )
    before = {path: _sha256(path) for path in files}
    report.file_sha256 = {str(path.relative_to(root)): digest for path, digest in before.items()}
    extensions = Counter(path.suffix.lower() for path in files)
    report.inventory = {
        "total_files": len(files),
        "extensions": dict(sorted(extensions.items())),
    }
    expected = profile["expected_inventory"]
    if len(files) != int(expected["total_files"]):
        _issue(report, "INVENTORY_COUNT_MISMATCH", f"期望 {expected['total_files']} 个文件，实际 {len(files)}", root)
    for extension, count in expected["extensions"].items():
        if extensions.get(extension, 0) != int(count):
            _issue(report, "EXTENSION_COUNT_MISMATCH", f"{extension} 期望 {count} 个，实际 {extensions.get(extension, 0)}", root)

    frames = _validate_standard_files(report, root, profile)
    raw_spec = profile["raw_dest"]
    _validate_cross_file_rules(
        report,
        frames,
        hour_start=int(raw_spec["hour_start"]),
        hour_count=int(raw_spec["hour_count"]),
    )
    _validate_raw_dest(report, root, profile)
    for relative in profile.get("provenance_files", []):
        path = root / relative
        if not path.is_file():
            _issue(report, "PROVENANCE_FILE_MISSING", f"缺少来源文件 {relative}", path)
        elif path.suffix.lower() == ".xlsx" and not zipfile.is_zipfile(path):
            _issue(report, "WORKBOOK_CONTAINER_INVALID", "工作簿不是有效 OOXML 容器", path)
        elif path.suffix.lower() == ".xlsx":
            _issue(report, "WORKBOOK_PROVENANCE_ONLY", "工作簿仅作来源追溯；运行以 03/04/05 标准文件为准", path, "warning")

    for path, digest in before.items():
        if not path.is_file() or _sha256(path) != digest:
            _issue(report, "INPUT_INTEGRITY_CHANGED", "校验过程不得修改源文件", path)
    return report
