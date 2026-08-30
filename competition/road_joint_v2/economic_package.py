"""Read-only, ID-based provisional economics. No value silently becomes formal."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import shutil

PACKAGE_DIRECTORY = "经济参数首版暂行包_20260829"
PACKAGE_FILES = ("economic_parameters_provisional.csv", "economic_parameter_sources.csv",
                 "pending_confirmation_Cai.csv", "README.md")
STATUSES = {
    "debug_only", "policy_applicable_scenario_only", "conditional_policy_value",
    "provisional_reference_pending_contract", "official_default_pending_actual_lhv",
    "derived_from_official_default", "literature_baseline_pending_vendor_quote",
    "provisional_same_baseline_pending_vendor_model", "literature_baseline",
    "literature_performance_baseline", "literature_baseline_pending_vendor_confirmation",
    "historical_cooling_project_sensitivity_only", "pending_confirmation",
}
# ID -> exact source unit, accepted status, internal key, internal unit.
APPLIED = {
    "ashp_central_capex_baseline": ("RMB/kW_heat", "provisional_same_baseline_pending_vendor_model", "central_hp_capex", "CNY/kW_th"),
    "ashp_distributed_capex_baseline": ("RMB/kW_heat", "provisional_same_baseline_pending_vendor_model", "local_hp_capex", "CNY/kW_th"),
    "gas_boiler_capex_baseline": ("RMB/kW_th", "literature_baseline_pending_vendor_quote", "boiler_capex", "CNY/kW_th"),
    "ashp_fixed_om_rate": ("fraction_of_initial_capex_per_year", "literature_baseline", "hp_fixed_om", "fraction/year"),
    "ashp_service_life": ("year", "literature_baseline", "hp_life", "year"),
    "hot_water_tes_charge_efficiency": ("fraction", "literature_performance_baseline", "tes_eta_charge", "fraction"),
    "hot_water_tes_discharge_efficiency": ("fraction", "literature_performance_baseline", "tes_eta_discharge", "fraction"),
    "hot_water_tes_service_life": ("year", "literature_baseline_pending_vendor_confirmation", "tes_life", "year"),
}
REGISTERED = {
    "basic_charge_demand_rate": "RMB/(kW_month)",
    "basic_charge_transformer_rate": "RMB/(kVA_month)",
    "gas_price_public_ceiling_reference": "RMB/m3",
    "gas_lhv_default": "MJ/Nm3", "gas_lhv_default_kwh": "kWh_th/Nm3",
    **{f"pipe_dn{dn}_cost_sensitivity": "RMB/m" for dn in (300, 400, 500)},
    **{f"elec_debug_aug2026_{kind}": "RMB/kWh" for kind in ("flat", "sharp", "peak", "valley")},
}
# All numbers here were explicitly approved as algorithm-test assumptions.
TEST_VALUES = {
    "pipe_type_ids": (["test_small", "test_medium", "test_large"], "ID"),
    "pipe_dn_mm": ([None, None, None], "mm"),
    "pipe_cost": ([500.0, 700.0, 900.0], "CNY/supply_return_route_m"),
    "pipe_life": (30, "year"),
    "pipe_loss": ([0.02, 0.03, 0.04], "kW_th/supply_return_route_m"),
    "pipe_pump": ([1e-5, 1e-5, 1e-5], "kWh_e/(kWh_th*m)"),
    "tes_energy_capex": (50.0, "CNY/kWh_th"),
    "tes_power_capex": (100.0, "CNY/kW_th"),
    "tes_fixed_capex": (1000.0, "CNY/set"),
    "tes_standing_loss": (0.0005, "fraction/hour"),
    "station_capex": (10000.0, "CNY/site"), "station_life": (30, "year"),
    "connection_capex": (1000.0, "CNY/building_excluding_service_pipe"),
    "connection_life": (30, "year"),
    "boiler_life": (15, "year"), "boiler_fixed_om": (0.02, "fraction/year"),
    "separate_variable_om": (0.0, "CNY/kWh_th; represented_in_test_fixed_om"),
    "peak_margin": (0.20, "fraction"), "discount_rate": (0.05, "fraction"),
}


class PackageError(ValueError):
    """Aggregated contract failures; CLI maps this to exit code 2."""


def file_hash(path: Path) -> str:
    result = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _rows(path: Path, required: set[str]) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not required <= set(reader.fieldnames or []):
            raise PackageError(f"{path.name}: 缺字段 {sorted(required - set(reader.fieldnames or []))}")
        rows = list(reader)
    if not rows or any(None in row for row in rows):
        raise PackageError(f"{path.name}: 空表或列数不符")
    return [{key: (value or "").strip() for key, value in row.items()} for row in rows]


def strict_bool(value: str) -> bool:
    if value.casefold() in {"1", "true"}:
        return True
    if value.casefold() in {"0", "false"}:
        return False
    raise PackageError(f"非法 code_use_allowed: {value!r}")


def read_package(root: str | Path) -> dict:
    root = Path(root).resolve()
    required = PACKAGE_FILES[:3]
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise PackageError(f"经济扩展包缺文件: {missing}")
    before = {p.name: file_hash(p) for p in sorted(root.iterdir()) if p.is_file()}
    values = _rows(root / required[0], {"parameter_id", "value", "unit", "parameter_status", "code_use_allowed", "source_id"})
    sources = _rows(root / required[1], {"source_id", "source_title", "code_use_allowed"})
    pending = _rows(root / required[2], {"item_id", "parameter_id", "parameter_name", "value", "unit", "parameter_status", "code_use_allowed", "related_source_id"})
    errors = []
    source_map, parameters = {}, {}
    for row in sources:
        sid = row["source_id"]
        try:
            if not sid or sid in source_map:
                raise PackageError(f"空或重复 source_id: {sid}")
            row["code_use_allowed"] = strict_bool(row["code_use_allowed"])
            source_map[sid] = row
        except PackageError as exc:
            errors.append(str(exc))
    periods = {}
    for row in values:
        pid = row["parameter_id"]
        try:
            if not pid or pid in parameters:
                raise PackageError(f"空或重复 parameter_id: {pid}")
            number = float(row["value"])
            if not isfinite(number) or number < 0:
                raise PackageError(f"{pid}: value 必须有限且非负")
            status = row["parameter_status"]
            if status not in STATUSES:
                raise PackageError(f"{pid}: 未知参数状态 {status}")
            row["code_use_allowed"] = strict_bool(row["code_use_allowed"])
            if row["source_id"] not in source_map:
                raise PackageError(f"{pid}: 未知 source_id")
            expected_unit = APPLIED[pid][0] if pid in APPLIED else REGISTERED.get(pid)
            if pid.startswith("tou_multiplier_"):
                expected_unit = "dimensionless"
                month = int(row.get("applicable_month", row.get("month", "")))
                start, end = int(row["start_hour"]), int(row["end_hour"])
                if month not in (12, 1, 2) or not 0 <= start < end <= 24 or f"m{month:02d}_" not in pid:
                    raise PackageError(f"{pid}: 月份/时段错误")
                periods.setdefault(month, []).append((start, end))
            if expected_unit is None or row["unit"] != expected_unit:
                raise PackageError(f"{pid}: 未登记ID或单位错误 {row['unit']!r}，期望 {expected_unit}")
            if ("fraction" in row["unit"] and number > 1) or (row["unit"] == "year" and (number < 1 or not number.is_integer())):
                raise PackageError(f"{pid}: 比例/寿命范围错误")
            if "efficiency" in pid and number <= 0:
                raise PackageError(f"{pid}: 效率必须大于0")
            row["value"] = number
            row["execution"] = "eligible_for_v2_test" if pid in APPLIED else "registered_not_applied"
            parameters[pid] = row
        except (ValueError, KeyError) as exc:
            errors.append(f"{pid}: {exc}")
    if periods:
        if set(periods) != {12, 1, 2}:
            errors.append("冬季政策时段必须覆盖12、1、2月")
        for month, ranges in periods.items():
            coverage = [0] * 24
            for start, end in ranges:
                for hour in range(start, end):
                    coverage[hour] += 1
            if coverage != [1] * 24:
                errors.append(f"{month}月政策时段存在缺口或重叠")
    ids, item_ids = set(), set()
    for row in pending:
        try:
            if row["parameter_id"] in ids or row["item_id"] in item_ids:
                raise PackageError("待确认表重复ID")
            ids.add(row["parameter_id"])
            item_ids.add(row["item_id"])
            if strict_bool(row["code_use_allowed"]) or row["value"] or row["parameter_status"] != "pending_confirmation":
                raise PackageError(f"{row['item_id']}: 待确认项不得带可执行数值")
            if row["related_source_id"] not in source_map:
                raise PackageError(f"{row['item_id']}: 未知来源")
            row["value"] = None
            row["code_use_allowed"] = False
        except PackageError as exc:
            errors.append(str(exc))
    if "gas_lhv_default" in parameters and "gas_lhv_default_kwh" in parameters:
        if abs(parameters["gas_lhv_default"]["value"] / 3.6 - parameters["gas_lhv_default_kwh"]["value"]) > 1e-8:
            errors.append("LHV登记值的MJ/kWh换算不一致")
    if before != {p.name: file_hash(p) for p in sorted(root.iterdir()) if p.is_file()}:
        errors.append("原始参数包哈希发生变化")
    if errors:
        raise PackageError("经济参数包校验失败：\n" + "\n".join(errors))
    return {"package_id": root.name, "input_sha256": before, "parameters": parameters,
            "sources": source_map, "pending": pending, "formal_use_allowed": False}


def effective_parameters(package: dict, peak_kW: float, *, require_all: bool = True, energy_context: dict | None = None) -> dict:
    if not isfinite(peak_kW) or peak_kW <= 0:
        raise PackageError("全园区峰值必须为正有限数值")
    entries = {key: {"value": val, "unit": unit, "status": "synthetic_test",
                     "source_id": "user_approved_road_v2_test_plan"}
               for key, (val, unit) in TEST_VALUES.items()}
    entries["pipe_capacity_kW_th"] = {
        "value": [round(peak_kW * 1.2 * factor, 10) for factor in (0.6, 1.0, 1.5)],
        "unit": "kW_th", "status": "synthetic_test", "source_id": "full_park_peak_times_1.2_times_grade",
    }
    for pid, (_, status, key, unit) in APPLIED.items():
        row = package["parameters"].get(pid)
        if row is None:
            if require_all:
                raise PackageError(f"实际采用参数缺失: {pid}")
            continue  # Used only by tiny unit fixtures; never inserts a default.
        if not row["code_use_allowed"] or not package["sources"][row["source_id"]]["code_use_allowed"] or row["parameter_status"] != status:
            raise PackageError(f"{pid}: 禁用参数或来源/场景状态不允许采用")
        entries[key] = {"value": row["value"], "unit": unit, "status": status,
                        "source_id": row["source_id"], "parameter_id": pid,
                        "source_unit": row["unit"]}
    result = {"scenario_id": "road_v2_economic_20260829_test", "package": package,
              "energy_policy": "preserve_v03_external_timeseries", "full_park_peak_kW": peak_kW,
              "energy_context": energy_context,
              "entries": entries, "values": {k: v["value"] for k, v in entries.items()},
              "formal_use_allowed": False,
              "variable_om_boundary": "测试中由固定运维总额表示；不代表工程可变运维为零"}
    result["snapshot_sha256"] = sha256(json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return result


def describe_energy_input(frame, lhv_MJ_per_Nm3: float) -> dict:
    """Only describes the existing authoritative time series; never replaces it."""
    return {
        'electricity_prices_CNY_per_kWh_e': sorted(float(x) for x in frame.electricity_price_CNY_per_kWh_e.unique()),
        'gas_prices_CNY_per_Nm3': sorted(float(x) for x in frame.natural_gas_price_CNY_per_Nm3.unique()),
        'LHV_MJ_per_Nm3': float(lhv_MJ_per_Nm3),
        'gas_prices_CNY_per_kWh_LHV': sorted(float(x)/(lhv_MJ_per_Nm3/3.6) for x in frame.natural_gas_price_CNY_per_Nm3.unique()),
        'rule': '逐时绝对价来自v0.3 external_timeseries；此列表仅汇总，不替代时间映射',
    }


# Pending source descriptions are evidence only. Current values come from the
# effective snapshot, never from the source's older temporary_handling prose.
GAP_KEYS = {
    "P005": ["boiler_capex"], "P006": ["central_hp_capex"], "P007": ["central_hp_capex"],
    "P008": ["local_hp_capex"], "P009": ["local_hp_capex"], "P011": ["hp_fixed_om"],
    "P012": ["hp_life"], "P014": ["tes_energy_capex"], "P015": ["tes_power_capex"],
    "P016": ["tes_fixed_capex"], "P017": ["tes_eta_charge"], "P018": ["tes_eta_discharge"],
    "P019": ["tes_standing_loss"], "P020": ["tes_life"],
    "P023": ["pipe_type_ids", "pipe_capacity_kW_th", "pipe_dn_mm", "pipe_cost", "pipe_life", "pipe_loss", "pipe_pump"],
    "P024": ["station_capex", "station_life"], "P025": ["connection_capex", "connection_life"],
}


def gap_records(snapshot: dict) -> list[dict]:
    records = []
    for row in snapshot["package"]["pending"]:
        keys = GAP_KEYS.get(row["item_id"], [])
        component = row.get("system_component", "equipment")
        file = "technology_quotes.csv"
        if component == "heating_network":
            file = "pipe_types.csv"
        elif component in {"station", "building_connection"}:
            file = "station_and_connection_costs.csv"
        elif component in {"electricity", "natural_gas"}:
            file = "project_energy_tariffs.csv"
        entries = {key: snapshot["entries"][key] for key in keys if key in snapshot["entries"]}
        fallback = "未进入模型；不得空值补0"
        if row["item_id"] in {"P001", "P003", "P004"}:
            fallback = "沿用v0.3 external_timeseries；不采用新增参考值。当前能源快照：" + json.dumps(snapshot.get('energy_context'),ensure_ascii=False)
        if row["item_id"] == "P002":
            fallback = "本测试未纳入基本电费；并非项目无需缴纳"
        records.append({"gap_id": row["item_id"], "parameter_id": row["parameter_id"],
                        "name": row["parameter_name"], "file": file, "unit": row["unit"] or "categorical/text",
                        "required_information": row.get("required_information", ""),
                        "reason": row.get("why_needed", ""), "effective": entries,
                        "fallback": fallback, "keys": keys})
    extra = {
        "boiler_life": "锅炉寿命", "boiler_fixed_om": "锅炉固定运维", "separate_variable_om": "可变运维边界",
        "discount_rate": "经济评价折现率签认", "pipe_dn_mm": "真实DN及内径",
        "pipe_loss": "双管单位路由管损", "pipe_pump": "单位路由泵耗系数",
    }
    for i, (key, title) in enumerate(extra.items(), 1):
        records.append({"gap_id": f"V2-{i:03d}", "parameter_id": key, "name": title,
                        "file": "pipe_types.csv" if key.startswith("pipe_") else "technology_quotes.csv",
                        "unit": snapshot["entries"][key]["unit"], "required_information": "同边界实测/厂家或专业签认值、来源日期、签认人",
                        "reason": "现值为软件测试假设", "effective": {key: snapshot["entries"][key]},
                        "fallback": "仅算法验证", "keys": [key]})
    for item in snapshot.get('geometry_failures', []):
        bid=item['building_id']
        records.append({'gap_id':'GIS-'+bid,'parameter_id':bid,'name':'建筑合格接入支线',
            'file':'building_service_connections.geojson','unit':'EPSG:32650 / m',
            'required_information':'FeatureCollection，LineString；properties包含building_id、attachment_node_id、source_id、approved_by；端点分别位于建筑边界与主次干路；不穿楼、入口夹角≥45°',
            'reason':','.join(item.get('reasons',[])), 'effective':{},
            'fallback':'无暂定路径；等待允许避障折线或人工提供接入线；禁止欧氏捷径', 'keys':['geometry','attachment_node_id']})
    return records


def write_gap_report(snapshot: dict, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = path.with_name(f"{path.stem}.{stamp}.bak.md")
        if backup.exists():
            raise FileExistsError(backup)
        shutil.copy2(path, backup)
    lines = ["# 缺失数据清单", ""]
    for row in gap_records(snapshot):
        numeric = row["unit"] != "categorical/text"
        spatial=row['file'].endswith('.geojson')
        lines += [f"## {row['gap_id']} · {row['name']}", "",
                  f"- 稳定参数ID：`{row['parameter_id']}`；替换字段：`{', '.join(row['keys']) or row['parameter_id']}`。",
                  f"- 建议文件：`{row['file']}`；" + ('UTF-8 GeoJSON；主键building_id；几何及attachment_node_id不允许空。' if spatial else 'UTF-8 CSV，逗号分隔；主键为parameter_id（逐时价格另加带Asia/Shanghai时区的timestamp）。'),
                  f"- 类型：{'LineString几何，唯一字符串ID；坐标float64' if spatial else 'float64，有限非负；比例[0,1]，效率(0,1]，寿命正整数' if numeric else 'string，需明确枚举定义'}；单位：`{row['unit']}`；正式值不允许空。",
                  "- 通用必需列：" + ('building_id,attachment_node_id,source_id,source_date,approved_by,geometry。' if spatial else 'parameter_id,value,unit,source_id,source_date,parameter_status,approved_by；设备另含technology_id与报价边界，管型另含pipe_type_id、dn_mm及双管路由计价标志。'),
                  f"- 应补内容：{row['required_information']}。影响：{row['reason']}。",
                  f"- 当前暂定值：`{json.dumps(row['effective'], ensure_ascii=False)}`。" if row["effective"] else f"- 当前处理：{row['fallback']}。",
                  "- 允许用途：V0接口联调、算法验证、敏感性；不代表合同价格、实际气质或施工方案。补数后保留ID，更新value/source_id/状态/签认，重新校验与求解。", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
