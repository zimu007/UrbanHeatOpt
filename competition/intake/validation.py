"""Read-only, manifest-driven validation of external data deliveries.

This layer validates heterogeneous files before a source-specific adapter turns
them into ``competition_input_v1``.  It deliberately does not replace the
strict runtime case validator in :mod:`competition.validation`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import yaml


class IntakeValidationError(ValueError):
    """The delivery manifest itself is invalid or cannot be evaluated."""


@dataclass(frozen=True)
class IntakeIssue:
    code: str
    message: str
    dataset: str | None = None
    path: str | None = None
    severity: str = "error"


@dataclass
class IntakeReport:
    manifest_version: str
    source_root: Path
    datasets: dict[str, dict[str, Any]] = field(default_factory=dict)
    issues: list[IntakeIssue] = field(default_factory=list)
    normalizations: list[dict[str, Any]] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "valid" if self.valid else "invalid",
            "manifest_version": self.manifest_version,
            "source_root": str(self.source_root),
            "datasets": self.datasets,
            "issues": [asdict(issue) for issue in self.issues],
            "normalizations": self.normalizations,
        }


_TOP_KEYS = {"manifest_version", "source_root", "datasets", "relations"}
_DATASET_KEYS = {
    "name", "path", "glob", "format", "encoding", "sheet_name",
    "required_columns", "primary_key", "row_count", "numeric",
    "null_policy", "time_index", "geometry", "units", "layout",
    "allowed_values",
}


class _UniqueKeyLoader(yaml.SafeLoader):
    """Reject ambiguous manifests instead of silently taking the last key."""


def _construct_unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=False)
        if key in mapping:
            raise IntakeValidationError(f"manifest YAML 键重复: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=False)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _issue(report: IntakeReport, code: str, message: str, dataset: str | None = None,
           path: Path | None = None, severity: str = "error") -> None:
    report.issues.append(IntakeIssue(code, message, dataset, str(path) if path else None, severity))


def _read(path: Path, spec: dict[str, Any]) -> pd.DataFrame:
    fmt = spec.get("format", path.suffix.lstrip(".")).lower()
    if fmt == "csv":
        return pd.read_csv(path, encoding=spec.get("encoding", "utf-8"))
    if fmt == "parquet":
        return pd.read_parquet(path)
    if fmt in {"xlsx", "excel"}:
        return pd.read_excel(path, sheet_name=spec.get("sheet_name", 0))
    if fmt in {"geojson", "gpkg", "shp"}:
        return gpd.read_file(path)
    raise IntakeValidationError(f"不支持的格式: {fmt}")


def _paths(root: Path, spec: dict[str, Any]) -> list[Path]:
    pattern = spec.get("path") or spec.get("glob")
    if not isinstance(pattern, str) or not pattern:
        raise IntakeValidationError(f"数据集 {spec.get('name')!r} 必须声明 path 或 glob")
    matches = sorted(root.glob(pattern)) if "glob" in spec else [root / pattern]
    resolved: list[Path] = []
    for path in matches:
        candidate = path.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise IntakeValidationError(f"数据路径越过 source_root: {pattern}") from exc
        if candidate.is_file():
            resolved.append(candidate)
    return resolved


def _validate_frame(report: IntakeReport, name: str, path: Path, frame: pd.DataFrame,
                    spec: dict[str, Any]) -> None:
    required = set(spec.get("required_columns", []))
    missing = sorted(required - set(frame.columns))
    if missing:
        _issue(report, "FIELD_MISSING", f"缺少字段: {', '.join(missing)}", name, path)
        return

    expected_rows = spec.get("row_count")
    if expected_rows is not None and len(frame) != int(expected_rows):
        _issue(report, "ROW_COUNT_MISMATCH", f"期望 {expected_rows} 行，实际 {len(frame)} 行", name, path)

    keys = spec.get("primary_key", [])
    if keys and set(keys) <= set(frame.columns):
        if frame[keys].isna().any().any():
            _issue(report, "PRIMARY_KEY_NULL", "主键包含空值", name, path)
        if frame.duplicated(keys).any():
            _issue(report, "PRIMARY_KEY_DUPLICATE", "主键存在重复", name, path)

    null_policy = spec.get("null_policy", {})
    for column in required & set(frame.columns):
        count = int(frame[column].isna().sum())
        if not count:
            continue
        policy = null_policy.get(column, null_policy.get("*", "reject"))
        if policy == "fill_zero":
            report.normalizations.append({"dataset": name, "path": str(path), "column": column,
                                          "action": "fill_zero", "count": count})
        elif policy != "allow":
            _issue(report, "NULL_VALUE", f"字段 {column} 有 {count} 个空值且未声明允许策略", name, path)

    for column, rule in spec.get("numeric", {}).items():
        if column not in frame:
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        declared_nulls = frame[column].isna()
        invalid = values.isna() & ~declared_nulls
        if invalid.any():
            _issue(report, "VALUE_NOT_NUMERIC", f"字段 {column} 有 {int(invalid.sum())} 个非数值", name, path)
            continue
        finite = values.dropna().to_numpy(dtype=float)
        if finite.size and not np.isfinite(finite).all():
            _issue(report, "VALUE_NOT_FINITE", f"字段 {column} 包含 NaN/Inf", name, path)
        tolerance = float(rule.get("negative_tolerance", 0.0))
        small = values.lt(0) & values.ge(-tolerance)
        if small.any() and rule.get("small_negative_policy") == "clip_zero":
            report.normalizations.append({"dataset": name, "path": str(path), "column": column,
                                          "action": "clip_zero", "count": int(small.sum()),
                                          "tolerance": tolerance})
        minimum = rule.get("min")
        if minimum is not None:
            invalid_low = values.lt(float(minimum)) & ~small
            if invalid_low.any():
                _issue(report, "VALUE_BELOW_MIN", f"字段 {column} 有 {int(invalid_low.sum())} 个值低于 {minimum}", name, path)
        maximum = rule.get("max")
        if maximum is not None and values.gt(float(maximum)).any():
            _issue(report, "VALUE_ABOVE_MAX", f"字段 {column} 有 {int(values.gt(float(maximum)).sum())} 个值高于 {maximum}", name, path)

    for column, allowed_values in spec.get("allowed_values", {}).items():
        if column in frame:
            unexpected = set(frame[column].dropna()) - set(allowed_values)
            if unexpected:
                _issue(report, "VALUE_NOT_ALLOWED", f"字段 {column} 含未声明值: {sorted(map(str, unexpected))}", name, path)

    units = spec.get("units")
    if units:
        parameter_column = units.get("parameter_column")
        unit_column = units.get("unit_column")
        expected = units.get("expected", {})
        if not parameter_column or not unit_column or parameter_column not in frame or unit_column not in frame:
            _issue(report, "UNIT_RULE_INVALID", "单位规则缺少有效 parameter_column/unit_column", name, path)
        else:
            for parameter, unit in frame[[parameter_column, unit_column]].itertuples(index=False, name=None):
                allowed = expected.get(parameter)
                if allowed is None:
                    if not units.get("allow_unlisted", False):
                        _issue(report, "UNIT_PARAMETER_UNKNOWN", f"参数 {parameter!r} 未声明单位", name, path)
                    continue
                allowed_set = {allowed} if isinstance(allowed, str) else set(allowed)
                if unit not in allowed_set:
                    _issue(report, "UNIT_MISMATCH", f"参数 {parameter!r} 的单位 {unit!r} 不属于 {sorted(allowed_set)}", name, path)

    time_rule = spec.get("time_index")
    if time_rule:
        column = time_rule["column"]
        if column in frame:
            values = pd.to_numeric(frame[column], errors="coerce")
            if values.isna().any() or values.duplicated().any():
                _issue(report, "TIME_INDEX_INVALID", f"字段 {column} 必须为唯一数值", name, path)
            else:
                expected = np.arange(float(time_rule.get("start", values.iloc[0])),
                                     float(time_rule.get("start", values.iloc[0])) + len(values) * float(time_rule.get("step", 1)),
                                     float(time_rule.get("step", 1)))
                if len(expected) != len(values) or not np.allclose(values.to_numpy(dtype=float), expected):
                    _issue(report, "TIME_NOT_CONTINUOUS", f"字段 {column} 不连续", name, path)

    geometry = spec.get("geometry")
    if geometry:
        if not isinstance(frame, gpd.GeoDataFrame):
            _issue(report, "GEOMETRY_REQUIRED", "数据不是空间表", name, path)
        else:
            expected_crs = geometry.get("crs")
            if expected_crs and (frame.crs is None or frame.crs.to_string().upper() != str(expected_crs).upper()):
                _issue(report, "CRS_INVALID", f"期望 CRS {expected_crs}，实际 {frame.crs}", name, path)
            allowed = set(geometry.get("types", []))
            if allowed and not frame.geom_type.isin(allowed).all():
                _issue(report, "GEOMETRY_TYPE_INVALID", f"几何类型必须属于 {sorted(allowed)}", name, path)
            if frame.geometry.isna().any() or frame.geometry.is_empty.any() or not frame.geometry.is_valid.all():
                _issue(report, "GEOMETRY_INVALID", "存在空或无效几何", name, path)


def validate_delivery(manifest_path: str | Path, source_root: str | Path | None = None) -> IntakeReport:
    """Validate a heterogeneous delivery without modifying source files."""

    manifest_file = Path(manifest_path).resolve()
    manifest = yaml.load(manifest_file.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    if not isinstance(manifest, dict):
        raise IntakeValidationError("manifest 顶层必须是映射")
    unknown = set(manifest) - _TOP_KEYS
    if unknown:
        raise IntakeValidationError(f"manifest 含未知键: {', '.join(sorted(unknown))}")
    version = manifest.get("manifest_version")
    if version != "intake_manifest_v1":
        raise IntakeValidationError("manifest_version 必须是 intake_manifest_v1")
    root = Path(source_root).resolve() if source_root else (manifest_file.parent / manifest.get("source_root", ".")).resolve()
    report = IntakeReport(version, root)
    frames: dict[str, list[pd.DataFrame]] = {}
    snapshots: dict[Path, str] = {}

    for spec in manifest.get("datasets", []):
        if not isinstance(spec, dict):
            raise IntakeValidationError("datasets 中每一项必须是映射")
        unknown = set(spec) - _DATASET_KEYS
        if unknown:
            raise IntakeValidationError(f"数据集 {spec.get('name')!r} 含未知键: {', '.join(sorted(unknown))}")
        name = spec.get("name")
        if not isinstance(name, str) or not name or name in frames:
            raise IntakeValidationError("数据集 name 必须是非空且唯一的字符串")
        paths = _paths(root, spec)
        if not paths:
            _issue(report, "FILE_MISSING", "path/glob 未匹配文件", name)
            frames[name] = []
            continue
        frames[name] = []
        rows = 0
        hashes: dict[str, str] = {}
        for path in paths:
            before = sha256(path.read_bytes()).hexdigest(); snapshots[path] = before
            try:
                frame = _read(path, spec)
            except Exception as exc:
                _issue(report, "FILE_READ_ERROR", str(exc), name, path)
                continue
            frames[name].append(frame); rows += len(frame); hashes[str(path)] = before
            _validate_frame(report, name, path, frame, spec)
        report.datasets[name] = {"file_count": len(paths), "row_count": rows, "sha256": hashes,
                                 "layout": spec.get("layout")}

    for relation in manifest.get("relations", []):
        if not isinstance(relation, dict):
            raise IntakeValidationError("relations 中每一项必须是映射")
        left, right = relation.get("left"), relation.get("right")
        left_column = relation.get("left_column", relation.get("column"))
        right_column = relation.get("right_column", relation.get("column"))
        if left not in frames or right not in frames or not left_column or not right_column:
            _issue(report, "RELATION_INVALID", "关系引用了未知数据集或缺少列")
            continue
        left_values = {value for frame in frames[left] if left_column in frame for value in frame[left_column].dropna()}
        right_values = {value for frame in frames[right] if right_column in frame for value in frame[right_column].dropna()}
        mode = relation.get("mode", "equal")
        if mode not in {"equal", "subset"}:
            raise IntakeValidationError(f"不支持的关系 mode: {mode}")
        invalid = left_values != right_values if mode == "equal" else not left_values <= right_values
        if invalid:
            _issue(report, "ID_SET_MISMATCH", f"{left}.{left_column} 与 {right}.{right_column} 不满足 {mode} 关系")

    for path, before in snapshots.items():
        if not path.is_file() or sha256(path.read_bytes()).hexdigest() != before:
            _issue(report, "INPUT_INTEGRITY_CHANGED", "接收校验不得修改源文件", path=path)
    return report

