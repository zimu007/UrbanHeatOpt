"""20260831 input registration only: no objective or physical model formulas."""
from __future__ import annotations

import csv
from hashlib import sha256
import json
import math
from pathlib import Path
import re

import pandas as pd

from urbanheatopt.paths import PACKAGE_ROOT
from urbanheatopt.parameters.legacy_economics import PackageError, file_hash, strict_bool
from urbanheatopt.parameters.capacity_supplement_0906 import (
    SUPPLEMENT_DIRECTORY,
    read_capacity_supplement_0906,
)

SPEC = json.loads((PACKAGE_ROOT / "data/profile_resources/revised_20260831.json").read_text(encoding="utf-8"))
PACKAGE_DIRECTORY = SPEC["package_directory"]
PACKAGE_FILES = tuple(SPEC["files"])
STRICT_SOURCE_POLICY = "require_consistent"
FINAL_QUOTE_POLICY = "final_quote_override_20260905"
# Exact conflicts approved by the user; never a blanket override for disabled sources.
FINAL_QUOTE_OVERRIDES = {
    "hot_water_tes_tank_body_cost_per_m3": "SRC_HOT_TES_HOSPITAL_PRICE",
    "hot_water_tes_effective_energy_cost": "SRC_HOT_TES_HOSPITAL_PRICE",
    "hot_water_tes_fixed_bop_cost": "SRC_HOT_TES_HOSPITAL_PRICE",
    "hot_water_tes_service_life_actual": "SRC_HOT_TES_HOSPITAL_SCOPE",
    "hot_water_tes_pressure_type": "SRC_HOT_TES_HOSPITAL_PRICE",
    "hot_water_tes_quote_boundary": "SRC_HOT_TES_HOSPITAL_PRICE",
    "heating_pipe_mixed_dn_installed_reference_2017": "SRC_WB_HEBEI_CLEAN_HEATING_2017",
    "building_heat_exchange_station_package_reference_2019": "SRC_WB_HEBEI_CLEAN_HEATING_2017",
}


def validate_source_policy(policy):
    if not isinstance(policy, str) or policy not in {STRICT_SOURCE_POLICY, FINAL_QUOTE_POLICY}:
        raise PackageError(f"未知来源许可策略: {policy}")


# Source ID -> stable handoff key. This is selection, not a cost expression.
SELECT = {
    "ashp_central_installed_cost": "central_hp_capex_CNY_per_kW_th",
    "ashp_distributed_installed_cost": "local_hp_capex_CNY_per_kW_th",
    "ashp_actual_service_life": "hp_life_years",
    "ashp_actual_fixed_om_rate": "hp_fixed_om_fraction_per_year",
    "gas_boiler_capex_actual": "boiler_capex_CNY_per_kW_th",
    "boiler_life": "boiler_life_years", "boiler_fixed_om": "boiler_fixed_om_fraction_per_year",
    "hot_water_tes_effective_energy_cost": "tes_energy_capex_CNY_per_kWh_th",
    "hot_water_tes_charge_discharge_power_cost": "tes_power_capex_CNY_per_kW_th",
    "hot_water_tes_fixed_bop_cost": "tes_fixed_capex_CNY",
    "hot_water_tes_charge_efficiency_actual": "tes_eta_charge",
    "hot_water_tes_discharge_efficiency_actual": "tes_eta_discharge",
    "hot_water_tes_heat_loss_rate": "tes_loss_fraction_per_hour",
    "hot_water_tes_service_life_actual": "tes_life_years",
    "building_connection_actual_cost": "connection_capex_CNY_per_building",
    "building_connection_service_life_proxy": "connection_life_years",
    "heating_network_service_life_proxy": "pipe_life_years",
    "energy_station_service_life_proxy": "station_life_years",
    "discount_rate": "discount_rate",
    "project_electricity_contract_price": "electricity_flat_CNY_per_kWh_e",
    "electricity_basic_charge_max_demand_2025_12": "monthly_demand_CNY_per_kW_month",
    "project_gas_contract_price_peak_2025": "gas_price_CNY_per_m3",
    "project_gas_lhv_actual": "gas_lhv_MJ_per_Nm3",
}
# Existing winter policy intervals. New package leaves start/end blank; no guessed prices.
PERIODS = {"valley_1": (0, 6), "flat_1": (6, 12), "valley_2": (12, 14),
           "flat_2": (14, 16), "peak_1": (16, 18), "sharp": (18, 20), "peak_2": (20, 24)}
COMPOSITE = {"pipe_three_sizes_actual_quotes", "pipe_dn_mm", "pipe_loss"}


def rows(path: Path, required: set[str]) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if len(reader.fieldnames or []) != len(set(reader.fieldnames or [])):
            raise PackageError(f"{path.name}: CSV重复表头")
        if not required <= set(reader.fieldnames or []):
            raise PackageError(f"{path.name}: 缺字段 {sorted(required-set(reader.fieldnames or []))}")
        result = list(reader)
    if not result or any(None in row or any(v is None for v in row.values()) for row in result):
        raise PackageError(f"{path.name}: 空表或CSV列数错误")
    return [{k: v.strip() for k, v in row.items()} for row in result]


def number(value, label: str, *, positive: bool = False) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise PackageError(f"{label}: 缺失或非数值") from exc
    if not math.isfinite(value) or value < 0 or (positive and value <= 0):
        raise PackageError(f"{label}: 必须有限且{'大于零' if positive else '非负'}")
    return value


def freeze_classification(row: dict) -> str:
    # User confirmation freezes use for this study, NOT geography/quote authenticity.
    declaration = " ".join(row.get(k, "") for k in ("parameter_status", "approval_status", "value_review_status"))
    if re.search(r"暂定|暂行|待确认|待核实|pending|provisional|debug|assumption", declaration, re.I):
        return "explicitly_provisional"
    return "user_frozen_for_current_study"


def _validate_row(row: dict, expected_unit: str, sources: dict,
                  source_permission_policy: str = STRICT_SOURCE_POLICY) -> dict:
    validate_source_policy(source_permission_policy)
    row = dict(row)
    pid = row["parameter_id"]
    if row["unit"] != expected_unit:
        raise PackageError(f"{pid}: 单位错误 {row['unit']}，期望 {expected_unit}")
    if row["parameter_status"] not in SPEC["statuses"]:
        raise PackageError(f"{pid}: 未知参数状态 {row['parameter_status']}")
    source = sources.get(row["source_id"])
    if source is None:
        raise PackageError(f"{pid}: 来源ID不存在 {row['source_id']}")
    allowed = strict_bool(row["code_use_allowed"])
    row["code_use_allowed"] = allowed
    row["source_code_use_allowed"] = source["code_use_allowed"]
    row["permission_resolution"] = "consistent"
    if allowed and not source["code_use_allowed"]:
        permitted = (
            source_permission_policy == FINAL_QUOTE_POLICY
            and FINAL_QUOTE_OVERRIDES.get(pid) == row["source_id"]
            and source.get("reference_usage") in {"audit_reference", "audit_price_reference_not_code", "sensitivity_only"}
        )
        if not permitted:
            raise PackageError(f"{pid}: 参数允许执行但来源禁止执行")
        row["permission_resolution"] = "user_approved_final_quote"
        row["permission_authority"] = "用户20260905：仅做审计参考的直接按照最终报价；数值仍取新版主表，来源边界保留"
    if not row["value"]:
        raise PackageError(f"{pid}: 空白不等于0")
    if pid in COMPOSITE or row["unit"].startswith("categorical"):
        value = row["value"]
    elif row["unit"] == "boolean":
        value = strict_bool(row["value"])
    else:
        value = number(row["value"], pid)
        if value == 0 and row["parameter_status"] != "explicit_zero_to_avoid_double_counting":
            raise PackageError(f"{pid}: 非明确零值被拒绝")
        if row["unit"].startswith("fraction") and value > 1:
            raise PackageError(f"{pid}: 比例超过1")
        if "efficiency" in pid and value <= 0:
            raise PackageError(f"{pid}: 效率必须大于0")
        if row["unit"] == "year" and (value < 1 or not value.is_integer()):
            raise PackageError(f"{pid}: 寿命必须为正整数年")
        low, high = row.get("value_low", ""), row.get("value_high", "")
        if low and number(low, pid+".low") > value:
            raise PackageError(f"{pid}: 低于范围")
        if high and number(high, pid+".high") < value:
            raise PackageError(f"{pid}: 高于范围")
    row.update(value=value, numerical_freeze=freeze_classification(row),
               freeze_authority="user_20260905_unmarked_provisional_rule",
               model_consumed=False, result_verified=False)
    return row


def read_revised_package(root: Path | str, scenario: str = "revised_base", *,
                         source_permission_policy: str = STRICT_SOURCE_POLICY) -> dict:
    validate_source_policy(source_permission_policy)
    root = Path(root).resolve()
    if scenario not in {"revised_base", "station_mixed_scope_high"}:
        raise PackageError(f"未知经济情景: {scenario}")
    actual_all = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    supplement_prefix = SUPPLEMENT_DIRECTORY + "/"
    actual = {name for name in actual_all if not name.startswith(supplement_prefix)}
    if actual != set(PACKAGE_FILES):
        raise PackageError(f"经济扩展包文件：缺少{sorted(set(PACKAGE_FILES)-actual)}；未分类{sorted(actual-set(PACKAGE_FILES))}")
    hashes = {name: file_hash(root / name) for name in sorted(actual)}
    supplement = None
    supplement_root = root / SUPPLEMENT_DIRECTORY
    if supplement_root.exists():
        supplement = read_capacity_supplement_0906(
            supplement_root, authoritative_pipe_types=root / "pipe_types.csv"
        )
    errors = []
    sources = {}
    for row in rows(root / "economic_parameter_sources_merged.csv", {"source_id", "code_use_allowed", "source_title"}):
        try:
            sid = row["source_id"]
            if not sid or sid in sources or not row["source_title"]:
                raise PackageError(f"来源空ID/重复ID/标题缺失: {sid}")
            row["code_use_allowed"] = strict_bool(row["code_use_allowed"])
            sources[sid] = row
        except ValueError as exc:
            errors.append(str(exc))
    registry = {}
    required = {"parameter_id", "value", "unit", "source_id", "parameter_status", "code_use_allowed"}
    raw = rows(root / "economic_parameters_code_ready.csv", required)
    for row in raw:
        pid = row["parameter_id"]
        try:
            if pid in registry or pid not in SPEC["parameter_units"]:
                raise PackageError(f"重复或未知parameter_id: {pid}")
            registry[pid] = _validate_row(row, SPEC["parameter_units"][pid], sources, source_permission_policy)
        except ValueError as exc:
            errors.append(str(exc))
    missing = set(SPEC["parameter_units"]) - set(registry)
    if missing:
        errors.append(f"缺少合法登记参数: {sorted(missing)}")
    pipes = []
    for table in ("project_energy_tariffs", "technology_quotes", "pipe_types"):
        values = rows(root / f"{table}.csv", required)
        if {r["parameter_id"] for r in values} != set(SPEC["table_ids"][table]):
            errors.append(f"{table}: parameter_id集合与接口不符")
        if len(values) != SPEC["counts"][table]:
            errors.append(f"{table}: 行数{len(values)}，期望{SPEC['counts'][table]}")
        seen = set()
        for row in values:
            pid = row["parameter_id"]
            try:
                if pid in seen:
                    raise PackageError(f"{table}: 重复ID {pid}")
                seen.add(pid)
                unit = "CNY/supply_return_route_m" if table == "pipe_types" else SPEC["parameter_units"].get(pid, "UNKNOWN")
                checked = _validate_row(row, unit, sources, source_permission_policy)
                if table != "pipe_types":
                    ref = registry.get(pid, {})
                    for key in ("value", "unit", "source_id", "parameter_status", "code_use_allowed"):
                        if checked[key] != ref.get(key):
                            raise PackageError(f"{table}.{pid}: 与主表{key}冲突")
                    if pid.startswith("tou_multiplier_"):
                        month, period = re.fullmatch(r"tou_multiplier_m(\d\d)_(.+)", pid).groups()
                        start, end = PERIODS[period]
                        for key, expected in (("start_hour", start), ("end_hour", end)):
                            if row.get(key) and number(row[key], pid+key) != expected:
                                raise PackageError(f"{pid}: 时段与冻结政策映射冲突")
                        if row.get("applicable_range") != str(int(month)):
                            raise PackageError(f"{pid}: applicable_range月份不匹配")
                else:
                    if not checked["code_use_allowed"]:
                        raise PackageError(f"{pid}: 禁用管型")
                    if not row.get("pipe_type_id") or not strict_bool(row.get("supply_return_route_pricing", "")):
                        raise PackageError(f"{pid}: 缺少管型ID或不是双管路由计价")
                    if row.get("single_or_double_pipe") != "supply_return_route":
                        raise PackageError(f"{pid}: 管线计价边界错误")
                    for key in ("dn_mm", "inner_diameter_mm", "heat_loss_kW_per_route_m", "pump_coefficient"):
                        checked[key] = number(row.get(key), pid+key, positive=True)
                    checked["capacity_kW_th"] = None
                    checked["capacity_status"] = "missing_thermal_capacity_not_inferred_from_DN"
                    pipes.append(checked)
            except (ValueError, KeyError) as exc:
                errors.append(str(exc))
    if len({p.get("pipe_type_id") for p in pipes}) != 3:
        errors.append("三档pipe_type_id缺失或重复")
    if len(pipes) == 3 and COMPOSITE <= set(registry):
        try:
            price_parts = registry["pipe_three_sizes_actual_quotes"]["value"].split(";")
            prices = {int(k.removeprefix("DN")): number(v, "pipe_quote", positive=True)
                      for k, v in (s.split(":") for s in price_parts)}
            dns = [int(s.removeprefix("DN")) for s in registry["pipe_dn_mm"]["value"].split(";")]
            losses = {k: number(v, "pipe_loss", positive=True)
                      for k, v in (s.split(":") for s in registry["pipe_loss"]["value"].split(";"))}
            ordered = sorted(pipes, key=lambda p: p["dn_mm"])
            if len(price_parts) != 3 or len(prices) != 3 or len(dns) != 3 or len(set(dns)) != 3 or set(losses) != {"small", "medium", "large"}:
                raise PackageError("管型复合字段格式/重复错误")
            if set(dns) != {p["dn_mm"] for p in pipes} or set(prices) != set(dns):
                raise PackageError("DN登记与管型表不一致")
            for size, pipe in zip(("small", "medium", "large"), ordered):
                if prices[pipe["dn_mm"]] != pipe["value"] or losses[size] != pipe["heat_loss_kW_per_route_m"] or registry["pipe_pump"]["value"] != pipe["pump_coefficient"]:
                    raise PackageError("管型报价/热损/泵耗与主表冲突")
        except (ValueError, KeyError) as exc:
            errors.append(f"管型复合字段: {exc}")
    pending = rows(root / "pending_confirmation_remaining.csv", {"parameter_id", "reason", "code_use_allowed"})
    if len({r["parameter_id"] for r in pending}) != len(pending):
        errors.append("待确认表重复parameter_id")
    for row in pending:
        try:
            if strict_bool(row["code_use_allowed"]):
                errors.append(f"待确认表{row['parameter_id']}不应作为可执行值")
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise PackageError("\n".join(errors))
    effective = {}
    for pid, row in registry.items():
        selected = pid in SELECT or pid.startswith("tou_multiplier_") or pid == "project_basic_charge_mode"
        if pid == "station_fixed_capex_actual":
            selected = scenario == "station_mixed_scope_high"
        row["selection"] = "handoff_selected" if selected else "registered_not_applied"
        row["selection_reason"] = (
            "明确选择安装价/新版同口径字段供B消费" if selected else
            "旧值、替代计费、来源代理或重复边界；不同时应用"
        )
        row["quotation_display_status"] = (
            "final_value_for_current_study"
            if (row["numerical_freeze"] == "user_frozen_for_current_study"
                or (source_permission_policy == FINAL_QUOTE_POLICY
                    and row["permission_resolution"] == "user_approved_final_quote"))
            else "explicitly_provisional_scenario_value"
        )
        if pid == "separate_variable_om":
            row["selection_reason"] = "model_coefficient语义不足，不解释为可变运维单价"
        if pid == "station_fixed_capex_actual":
            row["selection_reason"] = "混合边界压力情景，可能与设备重复" if selected else "主情景主动未纳入未拆清站房费用，不等于工程零费用"
        if selected:
            if not row["code_use_allowed"]:
                raise PackageError(f"选择的参数{pid}被禁用")
            effective[SELECT.get(pid, pid)] = row["value"]
    if effective["project_basic_charge_mode"] != "maximum_demand":
        raise PackageError("本研究冻结为虚拟总表maximum_demand，不自动切换计费方式")
    lhv = number(effective["gas_lhv_MJ_per_Nm3"], "LHV", positive=True) / 3.6
    gas = effective["gas_price_CNY_per_m3"] / lhv
    if not math.isclose(lhv, registry["project_gas_lhv_kwh_per_Nm3"]["value"], abs_tol=1e-10) or not math.isclose(gas, registry["project_gas_cost_per_kWh_LHV_model"]["value"], abs_tol=1e-10):
        raise PackageError("天然气衍生值与源价格/LHV独立重算不一致")
    if registry["national_pipeline_fee_included"]["value"] is not True:
        raise PackageError("管输费包含口径改变，需重新确认，不重复加费")
    effective.update(gas_price_CNY_per_kWh_LHV=gas, gas_lhv_kWh_LHV_per_Nm3=lhv,
                     station_cost_boundary="excluded_unseparated" if scenario == "revised_base" else "mixed_scope_sensitivity",
                     station_capex_CNY=None if scenario == "revised_base" else registry["station_fixed_capex_actual"]["value"],
                     demand_meter_scope="virtual_park_heating_total", peak_capacity_margin_fraction=0.20)
    resolutions = [{"parameter_id": pid, "source_id": row["source_id"],
                    "original_source_code_use_allowed": row["source_code_use_allowed"],
                    "parameter_code_use_allowed": row["code_use_allowed"],
                    "resolution": row["permission_resolution"], "selection": row["selection"],
                    "authority": row["permission_authority"]}
                   for pid, row in registry.items() if row["permission_resolution"] == "user_approved_final_quote"]
    payload = dict(package_version=SPEC["package_version"], selection_version="a_selection_1.1.1",
                   source_permission_policy=source_permission_policy, permission_resolutions=resolutions,
                   quotation_display_policy="用户20260905：新包审计报价按本研究最终参数展示；保留原来源，不将历史替代报价叠加计费，不等于已求解结果",
                   scenario=scenario, package_root=str(root), source_hashes=hashes, registry=registry,
                   sources=sources, pipes=pipes, pending=pending, effective=effective,
                   capacity_supplement=supplement,
                   period_map=PERIODS, period_map_source="20260829同ID政策时段，20260831时段字段为空，显式版本化继承；倍率只来自新包",
                   volume_normalization="CNY/m3与MJ/Nm3按包说明1:1研究归一化；非气质实测保证",
                   warnings=["跨月份公开平价与政策倍率组合为研究电价，不等于项目结算单",
                             "冻结计算值不改变其代理来源/适用范围；没有进行模型求解"],
                   parameter_valid=True, model_consumed=False)
    for name, digest in hashes.items():
        if file_hash(root / name) != digest:
            raise PackageError(f"读取过程中输入发生变化: {name}")
    if supplement is not None:
        for name, digest in supplement["source_hashes"].items():
            if file_hash(supplement_root / name) != digest:
                raise PackageError(f"读取过程中0906补充输入发生变化: {name}")
    payload["snapshot_id"] = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return payload


def revised_timeseries(canonical_source: pd.DataFrame, snapshot: dict) -> pd.DataFrame:
    """Replace boundary prices from original volume values, never rescale energy price."""
    if "economic_snapshot_id" in canonical_source:
        raise PackageError("已应用经济快照，禁止重复转换")
    out = canonical_source.copy(deep=True)
    required = {"timestamp", "hour", "source_hour", "time_weight_h_per_year",
                "natural_gas_carbon_factor_kgCO2e_per_Nm3", "electricity_price_CNY_per_kWh_e"}
    if not required <= set(out):
        raise PackageError(f"标准外部时序缺字段: {sorted(required-set(out))}")
    try:
        stamps = pd.DatetimeIndex(out["timestamp"])
    except (TypeError, ValueError) as exc:
        raise PackageError("timestamp无法解析") from exc
    if str(stamps.tz) != "Asia/Shanghai" or len(stamps) != 2160 or not stamps.equals(pd.date_range(stamps[0], periods=2160, freq="h")):
        raise PackageError("需连续2160小时Asia/Shanghai时序")
    if list(out["hour"]) != list(range(1, 2161)) or stamps[0].strftime("%m-%d %H") != "12-01 00" or stamps[-1].strftime("%m-%d %H") != "02-28 23":
        raise PackageError("供暖季小时/边界错误")
    if out["time_weight_h_per_year"].dtype == bool or not pd.api.types.is_numeric_dtype(out["time_weight_h_per_year"]) or not out["time_weight_h_per_year"].eq(1).all():
        raise PackageError("供暖季公共时间权重必须为1h")
    if list(out["source_hour"]) != list(range(8016, 8760)) + list(range(1416)):
        raise PackageError("源hour顺序错误")
    effective = snapshot["effective"]
    multipliers, ids = [], []
    for t in stamps:
        matches = [p for p, (lo, hi) in snapshot["period_map"].items() if lo <= t.hour < hi]
        if len(matches) != 1:
            raise PackageError("分时电价区间缺口或重叠")
        pid = f"tou_multiplier_m{t.month:02d}_{matches[0]}"
        ids.append(pid)
        multipliers.append(effective[pid])
    for column in ("electricity_price_CNY_per_kWh_e", "gas_price_CNY_per_kWh_LHV", "gas_carbon_kgCO2e_per_kWh_LHV", "natural_gas_lhv_MJ_per_Nm3", "natural_gas_lhv_kWh_LHV_per_Nm3"):
        if column in out:
            out[f"source_v03_{column}"] = out[column]
    out["electricity_base_price_CNY_per_kWh_e"] = effective["electricity_flat_CNY_per_kWh_e"]
    out["electricity_price_multiplier"] = multipliers
    out["electricity_price_parameter_id"] = ids
    out["electricity_price_CNY_per_kWh_e"] = out["electricity_price_multiplier"] * effective["electricity_flat_CNY_per_kWh_e"]
    out["gas_price_CNY_per_kWh_LHV"] = effective["gas_price_CNY_per_kWh_LHV"]
    out["gas_carbon_kgCO2e_per_kWh_LHV"] = out["natural_gas_carbon_factor_kgCO2e_per_Nm3"] / effective["gas_lhv_kWh_LHV_per_Nm3"]
    out["natural_gas_lhv_MJ_per_Nm3"] = effective["gas_lhv_MJ_per_Nm3"]
    out["natural_gas_lhv_kWh_LHV_per_Nm3"] = effective["gas_lhv_kWh_LHV_per_Nm3"]
    out["gas_volume_price_CNY_per_m3"] = effective["gas_price_CNY_per_m3"]
    # Preserve the old volume-price column with an explicit source prefix, not two authoritative prices.
    if "natural_gas_price_CNY_per_Nm3" in out:
        out = out.rename(columns={"natural_gas_price_CNY_per_Nm3": "source_v03_natural_gas_price_CNY_per_Nm3"})
    out["economic_snapshot_id"] = snapshot["snapshot_id"]
    out["gas_conversion_count_from_volume"] = 1
    out["billing_month"] = stamps.strftime("%Y-%m")
    for column in ("gas_carbon_kgCO2e_per_kWh_LHV", "gas_price_CNY_per_kWh_LHV", "electricity_price_CNY_per_kWh_e"):
        if not out[column].map(lambda x: math.isfinite(x) and x >= 0).all():
            raise PackageError(f"{column}非有限或负值")
    return out
