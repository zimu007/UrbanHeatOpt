"""Read-only registration of the 2026-09-06 capacity supplement.

The hydraulic DN values in the supplement are provenance/audit evidence.  The
executable planning capacities are deliberately derived later from the
validated case peak, so a DN reference can never be consumed silently as the
Road V2 capacity boundary.
"""
from __future__ import annotations

import csv
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

from urbanheatopt.parameters.legacy_economics import PackageError, file_hash


SUPPLEMENT_DIRECTORY = "0906回复缺失清单"
SUPPLEMENT_VERSION = "capacity_supplement_20260906"
SUPPLEMENT_FILES = (
    "pipe_capacity_limits.csv",
    "pipe_types_reviewed_v2.csv",
    "pipe_types.csv",
    "UrbanHeatOpt_V2缺失参数处理与冻结说明_20260906.md",
)
PIPE_IDS = ("PIPE_SMALL_PROXY", "PIPE_MEDIUM_PROXY", "PIPE_LARGE_PROXY")
PLANNING_CAPACITY_FACTORS = {
    "PIPE_SMALL_PROXY": 0.6,
    "PIPE_MEDIUM_PROXY": 1.0,
    "PIPE_LARGE_PROXY": 1.5,
}


def _rows(path: Path, required: set[str]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)):
            raise PackageError(f"{path.name}: CSV重复表头")
        if not required <= set(fields):
            raise PackageError(f"{path.name}: 缺字段 {sorted(required-set(fields))}")
        values = list(reader)
    if not values or any(None in row or any(value is None for value in row.values()) for row in values):
        raise PackageError(f"{path.name}: 空表或CSV列数错误")
    return [{key: value.strip() for key, value in row.items()} for row in values]


def _positive(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PackageError(f"{label}: 缺失或非数值") from exc
    if not math.isfinite(result) or result <= 0:
        raise PackageError(f"{label}: 必须为有限正数")
    return result


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def read_capacity_supplement_0906(
    root: str | Path,
    *,
    authoritative_pipe_types: str | Path | None = None,
) -> dict[str, Any]:
    """Validate the supplement without turning hydraulic references into limits."""

    root = Path(root).resolve()
    if not root.is_dir():
        raise PackageError(f"0906容量补充目录不存在: {root}")
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    expected = set(SUPPLEMENT_FILES)
    if actual != expected:
        raise PackageError(
            f"0906容量补充文件：缺少{sorted(expected-actual)}；未分类{sorted(actual-expected)}"
        )

    hashes_before = {name: file_hash(root / name) for name in sorted(actual)}
    if authoritative_pipe_types is not None:
        authoritative = Path(authoritative_pipe_types).resolve()
        if not authoritative.is_file():
            raise PackageError(f"权威pipe_types.csv不存在: {authoritative}")
        if file_hash(root / "pipe_types.csv") != file_hash(authoritative):
            raise PackageError("0906副本pipe_types.csv与20260831权威表SHA-256不一致")

    capacity_required = {
        "pipe_type_id", "dn_mm", "working_pipe_od_mm", "wall_thickness_mm",
        "inner_diameter_mm", "design_deltaT_K", "capacity_kW_th",
        "capacity_upper_100Pa_m_kW_th", "parameter_status", "source_id",
        "geometry_source_url", "hydraulic_standard_url",
    }
    capacity_rows = _rows(root / "pipe_capacity_limits.csv", capacity_required)
    reviewed_required = {
        "pipe_type_id", "dn_mm", "working_pipe_od_mm_recommended",
        "wall_thickness_mm_recommended", "inner_diameter_mm_recommended",
        "design_deltaT_K", "capacity_baseline_kW_th", "capacity_upper_kW_th",
        "geometry_source_url", "hydraulic_standard_url", "review_status",
    }
    reviewed_rows = _rows(root / "pipe_types_reviewed_v2.csv", reviewed_required)

    by_id: dict[str, dict[str, Any]] = {}
    reviewed_by_id = {row["pipe_type_id"]: row for row in reviewed_rows}
    if len(reviewed_by_id) != len(reviewed_rows):
        raise PackageError("pipe_types_reviewed_v2.csv存在重复pipe_type_id")
    if set(reviewed_by_id) != set(PIPE_IDS):
        raise PackageError("审核管型ID必须与20260831三档管型完全一致")
    for row in capacity_rows:
        pipe_id = row["pipe_type_id"]
        if not pipe_id or pipe_id in by_id:
            raise PackageError(f"pipe_capacity_limits.csv空或重复pipe_type_id: {pipe_id}")
        if pipe_id not in PIPE_IDS:
            raise PackageError(f"pipe_capacity_limits.csv未知pipe_type_id: {pipe_id}")
        if row["parameter_status"] != "research_reference":
            raise PackageError(f"{pipe_id}: 物理管容量只允许标记research_reference")
        if not row["source_id"] or not row["geometry_source_url"] or not row["hydraulic_standard_url"]:
            raise PackageError(f"{pipe_id}: 物理参考来源不完整")
        checked = {
            "pipe_type_id": pipe_id,
            "dn_mm": _positive(row["dn_mm"], f"{pipe_id}.dn_mm"),
            "working_pipe_od_mm": _positive(row["working_pipe_od_mm"], f"{pipe_id}.working_pipe_od_mm"),
            "wall_thickness_mm": _positive(row["wall_thickness_mm"], f"{pipe_id}.wall_thickness_mm"),
            "inner_diameter_mm": _positive(row["inner_diameter_mm"], f"{pipe_id}.inner_diameter_mm"),
            "design_deltaT_K": _positive(row["design_deltaT_K"], f"{pipe_id}.design_deltaT_K"),
            "reference_capacity_kW_th": _positive(row["capacity_kW_th"], f"{pipe_id}.capacity_kW_th"),
            "reference_capacity_upper_kW_th": _positive(
                row["capacity_upper_100Pa_m_kW_th"], f"{pipe_id}.capacity_upper"
            ),
            "source_id": row["source_id"],
            "source_status": row["parameter_status"],
            "geometry_source_url": row["geometry_source_url"],
            "hydraulic_standard_url": row["hydraulic_standard_url"],
            "execution_use_allowed": False,
            "execution_exclusion_reason": (
                "DN物理容量仅作审计参考；执行容量由62栋峰值和已确认规划档系数生成"
            ),
        }
        reviewed = reviewed_by_id[pipe_id]
        comparisons = (
            (checked["dn_mm"], _positive(reviewed["dn_mm"], f"{pipe_id}.reviewed.dn_mm"), "dn_mm"),
            (checked["working_pipe_od_mm"], _positive(reviewed["working_pipe_od_mm_recommended"], f"{pipe_id}.reviewed.od"), "working_pipe_od_mm"),
            (checked["wall_thickness_mm"], _positive(reviewed["wall_thickness_mm_recommended"], f"{pipe_id}.reviewed.wall"), "wall_thickness_mm"),
            (checked["inner_diameter_mm"], _positive(reviewed["inner_diameter_mm_recommended"], f"{pipe_id}.reviewed.inner"), "inner_diameter_mm"),
            (checked["design_deltaT_K"], _positive(reviewed["design_deltaT_K"], f"{pipe_id}.reviewed.deltaT"), "design_deltaT_K"),
            (checked["reference_capacity_kW_th"], _positive(reviewed["capacity_baseline_kW_th"], f"{pipe_id}.reviewed.capacity"), "capacity_baseline_kW_th"),
            (checked["reference_capacity_upper_kW_th"], _positive(reviewed["capacity_upper_kW_th"], f"{pipe_id}.reviewed.upper"), "capacity_upper_kW_th"),
        )
        for left, right, label in comparisons:
            if not math.isclose(left, right, rel_tol=0, abs_tol=1e-9):
                raise PackageError(f"{pipe_id}: pipe_capacity_limits与reviewed表的{label}不一致")
        if reviewed["review_status"] != "recommended_planning_reference":
            raise PackageError(f"{pipe_id}: reviewed表状态不是recommended_planning_reference")
        by_id[pipe_id] = checked

    ordered = [by_id[pipe_id] for pipe_id in PIPE_IDS]
    reference_capacities = [row["reference_capacity_kW_th"] for row in ordered]
    if reference_capacities != sorted(reference_capacities) or len(set(reference_capacities)) != 3:
        raise PackageError("物理参考容量必须按小/中/大档严格递增")

    hashes_after = {name: file_hash(root / name) for name in sorted(actual)}
    if hashes_before != hashes_after:
        raise PackageError("读取0906补充包期间输入SHA-256发生变化")
    payload: dict[str, Any] = {
        "supplement_version": SUPPLEMENT_VERSION,
        "supplement_root": str(root),
        "source_hashes": hashes_before,
        "reference_pipe_capacities": ordered,
        "execution_capacity_policy": {
            "policy_id": "planning_peak_tiers_20260906",
            "factors_of_margin_peak": PLANNING_CAPACITY_FACTORS,
            "status": "research_assumption",
            "pipe_design_status": "planning_capacity_tier_not_hydraulic_dn",
            "reference_capacity_consumed": False,
        },
        "station_boundary_policy": {
            "policy_id": "five_virtual_sites_margin_peak_20260906",
            "candidate_site_count": 5,
            "status": "research_assumption",
        },
        "tes_boundary_policy": {
            "policy_id": "six_hour_peak_storage_20260906",
            "energy_hours_at_peak": 6.0,
            "charge_power_factor_of_peak": 1.0,
            "discharge_power_factor_of_peak": 1.0,
            "status": "research_assumption",
        },
        "station_cost_policy": {
            "revised_base": "excluded_unseparated",
            "publication_ready": False,
        },
    }
    payload["supplement_id"] = _canonical_hash(payload)
    return payload
