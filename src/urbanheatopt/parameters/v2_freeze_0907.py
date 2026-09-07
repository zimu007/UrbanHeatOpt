"""Strict reader for the teacher-confirmed 2026-09-07 V2 parameter patch."""
from __future__ import annotations

import csv
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

from urbanheatopt.parameters.legacy_economics import PackageError, file_hash, strict_bool


PATCH_DIRECTORY = "0907_V2参数冻结补丁"
PATCH_ID = "V2_FREEZE_20260907"
PATCH_DATA_VERSION = "guanggu-v0.3.1-20260823"
PATCH_FILES = (
    "load_peak_check.csv",
    "parameter_sources_v2.csv",
    "pipe_capacity_engineering_reference.csv",
    "pipe_capacity_limits.csv",
    "pipe_types_v2_debug.csv",
    "pipe_types_v2_expansion_check.csv",
    "README.md",
    "scenario_parameter_manifest.csv",
    "station_cost_scenarios.csv",
    "tes_limits.csv",
    "UrbanHeatOpt_V2缺失参数处理与冻结说明_20260906.md",
    "V2参数冻结补丁_QA.xlsx",
)
SCENARIOS = (
    "v2_debug",
    "v2_primary_expansion_check",
    "v2_station_high_cost_stress",
)


def _rows(path: Path, required: set[str]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not required <= set(fields):
            raise PackageError(f"{path.name}: 重复表头或缺字段{sorted(required-set(fields))}")
        result = list(reader)
    if not result or any(None in row or any(value is None for value in row.values()) for row in result):
        raise PackageError(f"{path.name}: 空表或CSV列数错误")
    return [{key: value.strip() for key, value in row.items()} for row in result]


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise PackageError(f"{label}: 必须为有限数值") from exc
    if not math.isfinite(value) or value < 0 or (positive and value <= 0):
        raise PackageError(f"{label}: 必须有限且{'大于零' if positive else '非负'}")
    return value


def _strict_flag(row: dict[str, str], name: str) -> bool:
    try:
        return strict_bool(row[name])
    except (KeyError, ValueError) as exc:
        raise PackageError(f"{name}: 必须为0/1布尔值") from exc


def _identity(row: dict[str, str], file_name: str) -> None:
    if row.get("data_version") != PATCH_DATA_VERSION or row.get("parameter_patch_id") != PATCH_ID:
        raise PackageError(f"{file_name}: 数据版本或补丁ID不一致")


def read_v2_freeze_0907(root: str | Path, scenario_id: str) -> dict[str, Any]:
    root = Path(root).resolve()
    if scenario_id not in SCENARIOS:
        raise PackageError(f"未知V2冻结情景: {scenario_id}")
    if not root.is_dir():
        raise PackageError(f"0907冻结补丁目录不存在: {root}")
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    expected = set(PATCH_FILES)
    if actual != expected:
        raise PackageError(f"0907冻结补丁：缺少{sorted(expected-actual)}；未分类{sorted(actual-expected)}")
    hashes = {name: file_hash(root / name) for name in sorted(actual)}

    sources = {
        row["source_id"]: row for row in _rows(
            root / "parameter_sources_v2.csv", {"source_id", "title", "used_for"}
        )
    }
    if len(sources) != 5 or any(not key for key in sources):
        raise PackageError("parameter_sources_v2.csv: 来源ID空值、重复或数量错误")

    manifest_rows = _rows(root / "scenario_parameter_manifest.csv", {
        "data_version", "parameter_patch_id", "scenario_id", "pipe_capacity_file",
        "pipe_types_file", "tes_limits_file", "station_cost_scenario",
        "allowed_for_primary_economic_conclusion",
    })
    manifest = {row["scenario_id"]: row for row in manifest_rows}
    if set(manifest) != set(SCENARIOS) or len(manifest_rows) != len(manifest):
        raise PackageError("scenario_parameter_manifest.csv: 三个情景必须唯一完整")
    for row in manifest_rows:
        _identity(row, "scenario_parameter_manifest.csv")
        row["allowed_for_primary_economic_conclusion"] = _strict_flag(
            row, "allowed_for_primary_economic_conclusion"
        )
        for field in ("pipe_capacity_file", "pipe_types_file", "tes_limits_file"):
            if row[field] not in expected or not (root / row[field]).is_file():
                raise PackageError(f"scenario_parameter_manifest.csv: {field}引用不存在")
    if manifest["v2_primary_expansion_check"]["allowed_for_primary_economic_conclusion"] is not True:
        raise PackageError("正式经济比较情景必须允许主要经济结论")
    if any(manifest[name]["allowed_for_primary_economic_conclusion"] for name in (
        "v2_debug", "v2_station_high_cost_stress"
    )):
        raise PackageError("调试/压力情景不得单独支撑主要经济结论")

    capacities = _rows(root / "pipe_capacity_limits.csv", {
        "data_version", "parameter_patch_id", "pipe_type_id", "capacity_kW_th",
        "capacity_share_of_design_peak", "design_peak_kW_th", "source_id",
        "parameter_status", "code_use_allowed",
    })
    if len(capacities) != 3 or len({row["pipe_type_id"] for row in capacities}) != 3:
        raise PackageError("pipe_capacity_limits.csv: 必须包含3个唯一管型")
    capacity_by_id = {}
    for row in capacities:
        _identity(row, "pipe_capacity_limits.csv")
        if row["source_id"] not in sources or not _strict_flag(row, "code_use_allowed"):
            raise PackageError("pipe_capacity_limits.csv: 来源缺失或执行被禁用")
        capacity_by_id[row["pipe_type_id"]] = {
            **row,
            "capacity_kW_th": _number(row["capacity_kW_th"], "pipe.capacity", positive=True),
            "capacity_share_of_design_peak": _number(
                row["capacity_share_of_design_peak"], "pipe.share", positive=True
            ),
            "design_peak_kW_th": _number(row["design_peak_kW_th"], "pipe.design_peak", positive=True),
            "code_use_allowed": True,
        }
    ordered = sorted(capacity_by_id.values(), key=lambda row: row["capacity_kW_th"])
    if [row["capacity_share_of_design_peak"] for row in ordered] != [0.25, 0.5, 1.0]:
        raise PackageError("三档管容量比例必须为0.25/0.50/1.00")
    if not all(math.isclose(row["capacity_kW_th"], row["design_peak_kW_th"] * row["capacity_share_of_design_peak"], abs_tol=0.01) for row in ordered):
        raise PackageError("管容量与设计峰值比例不一致")

    selected_manifest = manifest[scenario_id]
    pipe_rows = _rows(root / selected_manifest["pipe_types_file"], {
        "data_version", "parameter_patch_id", "scenario_id", "pipe_type_id",
        "capacity_kW_th", "route_cost_CNY_per_m", "heat_loss_kW_per_m",
        "source_id", "parameter_status", "allowed_for_economic_conclusion",
    })
    if len(pipe_rows) != 3 or {row["pipe_type_id"] for row in pipe_rows} != set(capacity_by_id):
        raise PackageError("所选pipe_types文件与容量表管型ID不一致")
    selected_pipes = []
    # The high-station-cost sensitivity deliberately reuses the same expanded
    # pipe table as the primary scenario.  Its manifest-level publication flag
    # is false because the *station* assumption is a stress case, not because
    # the shared pipe quotation suddenly becomes invalid.
    expected_pipe_scenario = (
        "v2_primary_expansion_check"
        if scenario_id == "v2_station_high_cost_stress"
        else scenario_id
    )
    expected_pipe_permission = selected_manifest["pipe_types_file"] == (
        "pipe_types_v2_expansion_check.csv"
    )
    for row in pipe_rows:
        _identity(row, selected_manifest["pipe_types_file"])
        if row["scenario_id"] != expected_pipe_scenario or row["source_id"] not in sources:
            raise PackageError("所选pipe_types文件情景或来源不一致")
        allowed = _strict_flag(row, "allowed_for_economic_conclusion")
        if allowed != expected_pipe_permission:
            raise PackageError("管型经济结论许可与manifest不一致")
        capacity = _number(row["capacity_kW_th"], "pipe_type.capacity", positive=True)
        if not math.isclose(capacity, capacity_by_id[row["pipe_type_id"]]["capacity_kW_th"], abs_tol=0.01):
            raise PackageError("管型表与容量表数值不一致")
        selected_pipes.append({
            **row,
            "capacity_kW_th": capacity,
            "route_cost_CNY_per_m": _number(row["route_cost_CNY_per_m"], "pipe.cost", positive=True),
            "heat_loss_kW_per_m": _number(row["heat_loss_kW_per_m"], "pipe.loss", positive=True),
            "allowed_for_economic_conclusion": allowed,
        })

    tes_rows = _rows(root / selected_manifest["tes_limits_file"], {
        "data_version", "parameter_patch_id", "technology_id",
        "energy_capacity_upper_kWh_th", "charge_power_upper_kW_th",
        "discharge_power_upper_kW_th", "capacity_margin_offset_allowed",
        "cyclic_boundary", "report_actual_capacity_required",
        "report_actual_peak_charge_required", "report_actual_peak_discharge_required",
        "report_upper_bound_binding_required", "source_id", "parameter_status",
        "code_use_allowed",
    })
    if len(tes_rows) != 1:
        raise PackageError("tes_limits.csv必须且只能有一行")
    tes = tes_rows[0]
    _identity(tes, "tes_limits.csv")
    if tes["source_id"] not in sources or not _strict_flag(tes, "code_use_allowed"):
        raise PackageError("TES来源缺失或执行被禁用")
    for field in (
        "capacity_margin_offset_allowed", "report_actual_capacity_required",
        "report_actual_peak_charge_required", "report_actual_peak_discharge_required",
        "report_upper_bound_binding_required",
    ):
        tes[field] = _strict_flag(tes, field)
    if tes["capacity_margin_offset_allowed"] is not False or tes["cyclic_boundary"] != "SOC_end=SOC_start":
        raise PackageError("TES不得抵扣峰值裕度且必须首末SOC循环")
    for field in (
        "energy_capacity_upper_kWh_th", "charge_power_upper_kW_th",
        "discharge_power_upper_kW_th",
    ):
        tes[field] = _number(tes[field], f"tes.{field}", positive=True)

    station_rows = _rows(root / "station_cost_scenarios.csv", {
        "data_version", "parameter_patch_id", "station_cost_scenario",
        "station_fixed_capex_CNY_per_site", "use_for_primary_result",
        "replaces_other_station_fixed_cost", "add_with_other_station_fixed_cost",
        "charge_if_station_built", "annualize_with_common_CRF", "source_id",
        "parameter_status",
    })
    stations = {row["station_cost_scenario"]: row for row in station_rows}
    if set(stations) != {"base", "high_cost_stress"} or len(station_rows) != 2:
        raise PackageError("station_cost_scenarios.csv必须包含base和high_cost_stress")
    for row in station_rows:
        _identity(row, "station_cost_scenarios.csv")
        if row["source_id"] not in sources:
            raise PackageError("站房情景来源ID不存在")
        for field in (
            "use_for_primary_result", "replaces_other_station_fixed_cost",
            "add_with_other_station_fixed_cost", "charge_if_station_built",
            "annualize_with_common_CRF",
        ):
            row[field] = _strict_flag(row, field)
        row["station_fixed_capex_CNY_per_site"] = _number(
            row["station_fixed_capex_CNY_per_site"], "station.capex", positive=True
        )
        if not row["replaces_other_station_fixed_cost"] or row["add_with_other_station_fixed_cost"]:
            raise PackageError("站房情景必须替换而不得叠加")
        if not row["charge_if_station_built"] or not row["annualize_with_common_CRF"]:
            raise PackageError("站房费用必须仅在建站时计取并使用公共CRF")
    if stations["base"]["station_fixed_capex_CNY_per_site"] != 3_000_000 or stations["high_cost_stress"]["station_fixed_capex_CNY_per_site"] != 18_000_000:
        raise PackageError("站房base/high_cost_stress必须为300万/1800万元")
    if not stations["base"]["use_for_primary_result"] or stations["high_cost_stress"]["use_for_primary_result"]:
        raise PackageError("只有base站房情景用于主要结果")
    station = dict(stations[selected_manifest["station_cost_scenario"]])

    peak_rows = _rows(root / "load_peak_check.csv", {
        "data_version", "parameter_patch_id", "building_count",
        "simultaneous_peak_kW_th", "qa_status",
    })
    if len(peak_rows) != 1:
        raise PackageError("load_peak_check.csv必须且只能有一行")
    peak = peak_rows[0]
    _identity(peak, "load_peak_check.csv")
    if int(peak["building_count"]) != 62 or peak["qa_status"] != "verified":
        raise PackageError("峰值核验建筑数或QA状态错误")
    peak_value = _number(peak["simultaneous_peak_kW_th"], "simultaneous_peak", positive=True)

    engineering = _rows(root / "pipe_capacity_engineering_reference.csv", {
        "data_version", "parameter_patch_id", "pipe_type_id", "capacity_kW_th",
        "parameter_status", "code_use_allowed", "usage",
    })
    if len(engineering) != 3 or {row["pipe_type_id"] for row in engineering} != set(capacity_by_id):
        raise PackageError("工程参考管型ID不完整")
    for row in engineering:
        _identity(row, "pipe_capacity_engineering_reference.csv")
        if _strict_flag(row, "code_use_allowed") or row["usage"] != "engineering_reference_only":
            raise PackageError("DN物理容量必须只作工程参考")

    payload: dict[str, Any] = {
        "patch_version": "v2_parameter_freeze_20260907",
        "patch_id": PATCH_ID,
        "declared_data_version": PATCH_DATA_VERSION,
        "source_data_version_compatibility": "base delivery remains guanggu-v0.3-20260823; patch declares v0.3.1 without changing loads",
        "scenario_id": scenario_id,
        "allowed_for_primary_economic_conclusion": bool(
            selected_manifest["allowed_for_primary_economic_conclusion"]
        ),
        "source_hashes": hashes,
        "manifest": selected_manifest,
        "pipe_capacity_limits": sorted(capacity_by_id.values(), key=lambda row: row["pipe_type_id"]),
        "selected_pipe_types": sorted(selected_pipes, key=lambda row: row["pipe_type_id"]),
        "tes_limits": tes,
        "station_cost": station,
        "load_peak": {"building_count": 62, "simultaneous_peak_kW_th": peak_value},
        "engineering_reference_consumed": False,
        "engineering_reference_rows": engineering,
        "program_feasibility_scope": scenario_id == "v2_debug",
        "economic_conclusion_scope": scenario_id == "v2_primary_expansion_check",
    }
    payload["patch_snapshot_id"] = sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()
    if any(file_hash(root / name) != digest for name, digest in hashes.items()):
        raise PackageError("读取0907冻结补丁期间输入SHA-256发生变化")
    return payload


__all__ = [
    "PATCH_DIRECTORY", "PATCH_ID", "PATCH_DATA_VERSION", "PATCH_FILES",
    "SCENARIOS", "read_v2_freeze_0907",
]
