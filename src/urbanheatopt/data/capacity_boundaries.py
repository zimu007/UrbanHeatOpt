"""Build the approved research or teacher-frozen capacity boundary snapshot.

This is an A-owned deterministic projection.  It consumes already validated
canonical load/performance series and the read-only supplement registration;
it does not read a previous run and it does not construct a Pyomo model.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
from typing import Any, Mapping, Sequence

import pandas as pd


CAPACITY_BOUNDARY_VERSION = "capacity_boundaries_1.0.0"
TECHNOLOGY_ROLE_MAPPING = {
    "central_hp": "ASHP_BASE_01",
    "central_boiler": "GAS_BOILER_BASE_01",
    "local_hp": "ASHP_BASE_01",
}


def _positive(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}必须为有限正数")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label}必须为有限正数")
    return result


def _series(values: Mapping[int, float], hours: set[int], label: str) -> dict[int, float]:
    if set(values) != hours:
        raise ValueError(f"{label}必须完整覆盖负荷小时")
    return {int(hour): _positive(value, f"{label}[{hour}]") for hour, value in values.items()}


def _hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def build_capacity_boundaries(
    *,
    loads: pd.DataFrame,
    site_ids: Sequence[str],
    cop_by_hour: Mapping[int, float],
    capacity_ratio_by_hour: Mapping[int, float],
    supplement: Mapping[str, Any],
    v2_freeze_patch: Mapping[str, Any] | None = None,
    peak_capacity_margin_fraction: float = 0.20,
    site_electricity_connection_max_kW_e: float | None = None,
    expected_building_count: int = 62,
    expected_hour_count: int = 2160,
) -> dict[str, Any]:
    """Derive machine-readable planning limits from the frozen research rule."""

    required = {"building_id", "hour", "heating_kW"}
    if not required <= set(loads):
        raise ValueError(f"标准负荷缺字段: {sorted(required-set(loads))}")
    if loads[list(required)].isna().any().any():
        raise ValueError("标准负荷存在空值")
    data = loads.loc[:, ["building_id", "hour", "heating_kW"]].copy()
    data["building_id"] = data["building_id"].astype(str)
    if data["building_id"].str.strip().eq("").any():
        raise ValueError("标准负荷存在空building_id")
    numeric_hour = pd.to_numeric(data["hour"], errors="coerce")
    numeric_load = pd.to_numeric(data["heating_kW"], errors="coerce")
    if numeric_hour.isna().any() or numeric_load.isna().any():
        raise ValueError("标准负荷hour/heating_kW含非数值")
    if (numeric_hour % 1).ne(0).any() or numeric_load.lt(0).any() or not numeric_load.map(math.isfinite).all():
        raise ValueError("标准负荷小时必须为整数，负荷必须有限非负")
    data["hour"] = numeric_hour.astype(int)
    data["heating_kW"] = numeric_load.astype(float)
    buildings = tuple(sorted(data["building_id"].unique()))
    hours = set(range(1, expected_hour_count + 1))
    if len(buildings) != expected_building_count:
        raise ValueError(f"容量边界要求{expected_building_count}栋建筑")
    if len(data) != expected_building_count * expected_hour_count:
        raise ValueError("标准负荷行数与建筑数×小时数不一致")
    counts = data.groupby("building_id")["hour"].agg(["count", "nunique", "min", "max"])
    if not (
        counts["count"].eq(expected_hour_count).all()
        and counts["nunique"].eq(expected_hour_count).all()
        and counts["min"].eq(1).all()
        and counts["max"].eq(expected_hour_count).all()
    ):
        raise ValueError("每栋建筑必须唯一覆盖hour=1…2160")

    margin = float(peak_capacity_margin_fraction)
    if not math.isfinite(margin) or margin < 0:
        raise ValueError("峰值容量裕度必须为有限非负数")
    site_ids = tuple(str(site_id).strip() for site_id in site_ids)
    if len(site_ids) != 5 or len(set(site_ids)) != 5 or any(not site_id for site_id in site_ids):
        raise ValueError("0906研究边界要求5个唯一虚拟候选站")
    cop = _series(cop_by_hour, hours, "cop_by_hour")
    ratio = _series(capacity_ratio_by_hour, hours, "capacity_ratio_by_hour")

    hourly = data.groupby("hour", sort=True)["heating_kW"].sum()
    if set(hourly.index) != hours:
        raise ValueError("全园区小时负荷覆盖不完整")
    peak = float(hourly.max())
    design_peak = (1 + margin) * peak
    electricity_limit = (
        design_peak / min(cop.values())
        if site_electricity_connection_max_kW_e is None
        else _positive(
            site_electricity_connection_max_kW_e,
            "site_electricity_connection_max_kW_e",
        )
    )
    if v2_freeze_patch is not None:
        if v2_freeze_patch.get("engineering_reference_consumed") is not False:
            raise ValueError("0907 DN物理参考容量不得作为执行容量")
        margin_rule = v2_freeze_patch.get("capacity_margin_rule", {})
        frozen_ratio = margin_rule.get("capacity_margin_ratio")
        frozen_design_peak = margin_rule.get(
            "station_total_installed_capacity_upper_kW_th"
        )
        if (
            not isinstance(frozen_ratio, (int, float))
            or not math.isclose(float(frozen_ratio), 1 + margin, abs_tol=1e-12)
            or margin_rule.get("include_network_heat_loss_in_constraint") is not False
            or margin_rule.get("include_tes_discharge_in_constraint") is not False
        ):
            raise ValueError("0907容量裕度规则未冻结为建筑有用热负荷口径")
        patch_peak = v2_freeze_patch.get("load_peak", {}).get("simultaneous_peak_kW_th")
        if not isinstance(patch_peak, (int, float)) or not math.isclose(
            peak, float(patch_peak), abs_tol=0.01
        ):
            raise ValueError("逐时负荷重算峰值与0907冻结峰值不一致")
        if (
            not isinstance(frozen_design_peak, (int, float))
            or not math.isclose(
                float(frozen_design_peak), (1 + margin) * peak, abs_tol=0.01
            )
        ):
            raise ValueError("0907站点安装容量上限与建筑峰值×容量裕度不一致")
        # Use the teacher-frozen full-precision upper bound rather than a
        # independently rounded reconstruction.
        design_peak = float(frozen_design_peak)
        capacity_by_id = {
            row["pipe_type_id"]: _positive(row["capacity_kW_th"], "pipe.capacity")
            for row in v2_freeze_patch.get("pipe_capacity_limits", [])
        }
        if set(capacity_by_id) != {
            "PIPE_SMALL_PROXY", "PIPE_MEDIUM_PROXY", "PIPE_LARGE_PROXY"
        }:
            raise ValueError("0907规划容量档缺失或ID不一致")
        reference_by_id = {
            item["pipe_type_id"]: item
            for item in v2_freeze_patch.get("engineering_reference_rows", [])
        }
        if set(reference_by_id) != set(capacity_by_id):
            raise ValueError("0907 DN工程参考管型不完整")
        evidence_root = str(v2_freeze_patch.get("patch_snapshot_id", ""))
        boundary_status = "teacher_confirmed_research_assumption"
    else:
        supplement_policy = supplement.get("execution_capacity_policy", {})
        if supplement_policy.get("reference_capacity_consumed") is not False:
            raise ValueError("0906 DN物理参考容量不得作为执行容量")
        factors = supplement_policy.get("factors_of_margin_peak")
        if not isinstance(factors, Mapping) or set(factors) != {
            "PIPE_SMALL_PROXY", "PIPE_MEDIUM_PROXY", "PIPE_LARGE_PROXY"
        }:
            raise ValueError("0906规划容量档策略缺失或管型ID不一致")
        reference_by_id = {
            item["pipe_type_id"]: item for item in supplement.get("reference_pipe_capacities", [])
        }
        if set(reference_by_id) != set(factors):
            raise ValueError("0906物理参考管型不完整")
        capacity_by_id = {
            pipe_id: design_peak * _positive(factor, f"{pipe_id}.factor")
            for pipe_id, factor in factors.items()
        }
        evidence_root = str(supplement.get("supplement_id", ""))
        boundary_status = "research_assumption"
    if len(evidence_root) != 64:
        raise ValueError("容量边界证据快照ID缺失")
    site_records = []
    for site_id in site_ids:
        site_records.append({
            "site_id": site_id,
            "allowed_technology_ids": ["central_hp", "central_boiler"],
            "source_technology_ids": {
                "central_hp": TECHNOLOGY_ROLE_MAPPING["central_hp"],
                "central_boiler": TECHNOLOGY_ROLE_MAPPING["central_boiler"],
            },
            "total_heat_capacity_max_kW_th": design_peak,
            "technology_capacity_max_kW_th": {
                "central_hp": design_peak,
                "central_boiler": design_peak,
            },
            "electricity_connection_max_kW_e": electricity_limit,
            "electricity_connection_scope": "central_hp_only",
            "gas_connection_max_kW_LHV": design_peak / 0.94,
            "source": "validated_62_building_load_and_20260906_research_rule",
            "status": boundary_status,
            "evidence_id": f"{evidence_root}:site_capacity",
        })

    local_limits: dict[str, float] = {}
    for building, frame in data.groupby("building_id", sort=True):
        local_limits[str(building)] = (1 + margin) * max(
            float(row.heating_kW) / ratio[int(row.hour)]
            for row in frame.itertuples(index=False)
        )
    pipe_records = []
    for pipe_id, execution_capacity in capacity_by_id.items():
        reference = reference_by_id[pipe_id]
        pipe_records.append({
            "pipe_type_id": pipe_id,
            "capacity_kW_th": execution_capacity,
            "pipe_design_status": "planning_capacity_tier_not_hydraulic_dn",
            "reference_dn_mm": reference["dn_mm"],
            "reference_capacity_kW_th": reference.get(
                "reference_capacity_kW_th", reference.get("capacity_kW_th")
            ),
            "reference_capacity_consumed": False,
            "source": "validated_62_building_peak_and_planning_peak_tiers_20260906",
            "status": boundary_status,
            "evidence_id": f"{evidence_root}:planning_pipe_capacity",
        })

    if v2_freeze_patch is not None:
        source_tes = v2_freeze_patch["tes_limits"]
        tes_energy = source_tes["energy_capacity_upper_kWh_th"]
        tes_charge = source_tes["charge_power_upper_kW_th"]
        tes_discharge = source_tes["discharge_power_upper_kW_th"]
    else:
        tes_energy, tes_charge, tes_discharge = 6.0 * peak, peak, peak
    station_cost = (
        v2_freeze_patch["station_cost"] if v2_freeze_patch is not None else None
    )
    payload: dict[str, Any] = {
        "schema_version": CAPACITY_BOUNDARY_VERSION,
        "decision_version": (
            "v2_teacher_frozen_20260907"
            if v2_freeze_patch is not None
            else "research_boundaries_20260906"
        ),
        "technology_role_mapping": TECHNOLOGY_ROLE_MAPPING,
        "peak_capacity_margin_fraction": margin,
        "capacity_margin_basis": "connected_building_useful_heat_demand",
        "network_heat_loss_in_capacity_margin": False,
        "tes_discharge_in_capacity_margin": False,
        "full_park_peak_kW_th": peak,
        "design_peak_kW_th": design_peak,
        "minimum_cop": min(cop.values()),
        "site_electricity_connection_limit_basis": (
            "hourly_minimum_COP"
            if site_electricity_connection_max_kW_e is None
            else "frozen_research_boundary_20260906"
        ),
        "minimum_capacity_ratio": min(ratio.values()),
        "sites": site_records,
        "pipes": pipe_records,
        "local_hp": {
            "technology_id": "local_hp",
            "source_technology_id": TECHNOLOGY_ROLE_MAPPING["local_hp"],
            "capacity_max_kW_th_by_building": local_limits,
            "source": "validated_building_load_capacity_ratio_and_20pct_margin",
            "status": boundary_status,
            "evidence_id": f"{evidence_root}:local_hp_capacity",
        },
        "tes": {
            "technology_id": "central_tes",
            "energy_capacity_max_kWh_th": tes_energy,
            "charge_capacity_max_kW_th": tes_charge,
            "discharge_capacity_max_kW_th": tes_discharge,
            "source": (
                "teacher_frozen_tes_limits_20260907"
                if v2_freeze_patch is not None
                else "six_hour_peak_storage_20260906"
            ),
            "status": boundary_status,
            "evidence_id": f"{evidence_root}:tes_capacity",
        },
        "station_cost": {
            "boundary": (
                "teacher_confirmed_v2_scenario" if station_cost else "excluded_unseparated"
            ),
            "station_cost_scenario": (
                station_cost["station_cost_scenario"] if station_cost else None
            ),
            "station_fixed_capex_CNY_per_site": (
                station_cost["station_fixed_capex_CNY_per_site"] if station_cost else None
            ),
            "replacement_not_additive": (
                bool(station_cost["replaces_other_station_fixed_cost"])
                and not bool(station_cost["add_with_other_station_fixed_cost"])
                if station_cost else False
            ),
            "research_solve_allowed": True,
            "publication_ready": bool(
                v2_freeze_patch
                and v2_freeze_patch["allowed_for_primary_economic_conclusion"]
            ),
            "reason": (
                "0907老师确认情景；只在建站时按公共CRF年化且替换其他站房固定费"
                if station_cost else "站房固定投资待正式发布前再次确认"
            ),
        },
        "building_count": expected_building_count,
        "hour_count": expected_hour_count,
        "supplement_id": evidence_root,
        "v2_parameter_scenario": (
            v2_freeze_patch.get("scenario_id") if v2_freeze_patch else None
        ),
    }
    payload["capacity_boundary_id"] = _hash(payload)
    return payload
