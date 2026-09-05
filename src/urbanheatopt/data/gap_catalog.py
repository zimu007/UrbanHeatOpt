"""A-owned data/interface gaps, not an engineering-approval wish list."""
from datetime import datetime
import json
from pathlib import Path


def data_gaps(snapshot=None, errors=(), spatial_status=None):
    effective = (snapshot or {}).get("effective", {})
    items = [
        dict(gap_id="SITE_INPUT", kind="data", requirement="V2地块能力情景阻塞；非施工审批", file="candidate_sites.geojson + site_limits.csv", fields="site_id:string主键; attachment_node_id:string; parcel_id:string; allowed_technology_ids:string分号分隔; heat_capacity_limit_kW_th:float>0; electricity_limit_kW_e:float>0; gas_limit_kW_LHV:float>=0; source_id:string; parameter_status:verified/research_assumption/pending", geometry="地块Polygon/MultiPolygon和站点Point，EPSG:32650；明确边界与接入节点", current="现有五点是算法候选，尚未提供新地块/容量接口文件", fallback="允许老师确认的虚拟地块/容量研究情景；不能空值等于无限", replace="A标准空间接口→B容量及能源约束"),
        dict(gap_id="PIPE_CAPACITY", kind="data", requirement="管型容量选择阻塞；不要求施工级水力", file="pipe_capacity_limits.csv", fields="pipe_type_id:string主键关联pipe_types.csv; capacity_kW_th:float>0; source_id:string; parameter_status:verified/research_assumption/pending", geometry="不适用", current="新包给出DN价格、热损、泵耗，但没有正式热力容量列；不从DN名义值自动推断容量", fallback="B可使用明确批准的峰值比例容量情景，须称容量代理，不称真实管径设计", replace="CaseBundle管型容量字段"),
        dict(gap_id="STATION_SCOPE", kind="boundary", requirement="完整工程成本结论影响；不阻止已批准基础研究情景", file="station_cost_breakdown.csv", fields="site_id:string; cost_component_id:string联合主键; capex_CNY:float>=0; life_years:int>0; scope:string; overlaps_equipment:boolean; source_id:string; parameter_status:string", geometry="不适用", current=str(effective.get("station_cost_boundary", "基础主动未纳入，压力1800万元混合边界")), fallback="基础未纳入并明确不完整；压力情景混合边界潜在重叠，非正式上界", replace="拆清土建/设备等后更新site成本，不回填旧10000"),
        dict(gap_id="VARIABLE_OM_SEMANTICS", kind="semantics", requirement="独立可变运维口径缺失，不是价格数据完全缺失", file="economic_parameters_code_ready.csv（更新同parameter_id）", fields="parameter_id=separate_variable_om; value:float>=0; unit:CNY/kWh_th或专业确认的其他明确计费单位; source_id:string; parameter_status:string; code_use_allowed:boolean", geometry="不适用", current="0.026 model_coefficient只登记，不进入目标；不能称现实可变运维为零", fallback="按批准范围不单独计入，固定运维已明确；需专业方确认物理量和是否重复", replace="有效参数→B可变运维表达式"),
        dict(gap_id="CONTRACT_SCOPE", kind="quality", requirement="项目实际经济结论影响，不阻止冻结研究参数", file="project_tariff_scope.csv", fields="tariff_id:string主键; year:int; months:string; customer_class:string; voltage_kV:float; tax_included:boolean; metering_scope:string; volume_normalization_m3_per_Nm3:float>0; source_id:string; approved_by:string", geometry="不适用", current=("已准备但B尚未消费：" if snapshot else "参数未通过、以下为批准待应用口径：")+"电价0.639525×冬季倍率；月需量42；气价2.97及LHV33.5361按用户冻结研究口径", fallback="现有代理与1:1体积归一化清楚标注，可研究运行；不反称项目合同", replace="有新确认时同参数ID更新，旧结果需重算"),
        dict(gap_id="B_CONSUMER", kind="code", requirement="新情景求解阻塞；不是缺数据", file="src/urbanheatopt/optimization/下游版本化执行适配器", fields="消费CaseBundle/SolveRequest；月度总表需量、有效参数、站房边界、站址容量、TES开关→ResultBundle", geometry="不适用", current="旧模型存在，但新A入口尚无验收通过的B消费适配器；不静默回退", fallback="不允许标为完整应用；prepare可以成功、solve必须阻止", replace="B接口与单例测试通过后A接入"),
        dict(gap_id="C_RESULT_QA", kind="code", requirement="V2结果合格/展示阻塞；不是缺输入", file="src/urbanheatopt/qa/ 与 reporting/ ResultBundle消费模块", fields="point_id、文件hash、实际决策、独立费用/碳/物理重算、渲染证据", geometry="按B真实结果", current="C指导书已交付，本轮不实施图表", fallback="不能用旧图/合成图冒充新结果", replace="C完成后启用report")
    ]
    if spatial_status:
        items[0]["current"] = json.dumps(spatial_status, ensure_ascii=False)
        provided = set(spatial_status.get("provided", []))
        if {"candidate_sites", "site_limits"} <= provided:
            items = [item for item in items if item["gap_id"] != "SITE_INPUT"]
        if "pipe_capacity_limits" in provided:
            items = [item for item in items if item["gap_id"] != "PIPE_CAPACITY"]
    for i, error in enumerate(errors, 1):
        items.append(dict(gap_id=f"VALIDATION_{i:03d}", kind="conflict_or_error", requirement="输入/参数阻塞", file="经济参数完整可运行修订版_20260831/economic_parameters_code_ready.csv 与 economic_parameter_sources_merged.csv（路径错误见具体证据）", fields="保留parameter_id及source_id；code_use_allowed须明确0/1并统一优先级；更正许可、范围或源字段，不改值绕过检查", geometry="不适用", current=str(error), fallback="不允许静默覆盖冲突；已询问用户是否采用新版逐参数允许标记优先", replace="确认后更新版本化选择策略，再validate/prepare"))
    for row in (snapshot or {}).get("pending", []):
        items.append(dict(gap_id="PACKAGE_"+row["parameter_id"], kind="source_registered_not_used", requirement="包内登记；不自动判为V2核心阻塞", file="pending_confirmation_remaining.csv", fields=f"parameter_id={row['parameter_id']}; reason:string; code_use_allowed:boolean; value:明确单位的标量", geometry="不适用", current=row.get("current_value", "")+"；"+row["reason"], fallback="历史/敏感性/非供热边界不参与主情景", replace="仅在专业方明确启用时交A更新选择规则"))
    return items


def render_gap_report(items, snapshot=None):
    lines = ["# 缺失数据清单", "", "自动生成；版本2026-09-05。仅列数据／语义／接口缺口，不代表未提供全部经济参数。", "", "新包未标注暂定的参数按用户决定冻结研究计算值；原代理、来源和边界仍保留。代码未消费不等于缺数据。", "", f"经济快照：{(snapshot or {}).get('snapshot_id', '尚未通过参数校验')}。", ""]
    for item in items:
        lines += [f"## {item['gap_id']}：{item['requirement']}", ""]
        for key, label in (("kind", "类型"), ("file", "建议文件／位置"), ("fields", "字段、类型、单位与主键"), ("geometry", "空间格式"), ("current", "当前缺项／实际采用值"), ("fallback", "允许用途与边界"), ("replace", "补齐后替换位置")):
            lines.append(f"- {label}：{item[key]}")
        lines += ["- 通用格式：CSV为UTF-8带BOM、逗号分隔；ID非空唯一；空白表示未提供，不能自动补0。时序如涉及逐时量须Asia/Shanghai、12月到次年2月2160小时、1h、hour=1…2160；空间数据须声明CRS。", "- 补数验证：在仓库根目录执行 `python run.py validate --config configs/cases/guanggu_v2.yaml`，然后prepare。", ""]
    return "\n".join(lines) + "\n"


def publish_gap_report(path: Path, text: str):
    path = path.resolve()
    if not path.parent.is_dir():
        raise ValueError(f"报告父目录不存在: {path.parent}")
    content = text.encode("utf-8")
    backup = None
    if path.exists() and path.read_bytes() != content:
        backup = path.with_name(f"{path.stem}.backup_{datetime.now():%Y%m%dT%H%M%S%f}.md")
        with backup.open("xb") as stream:
            stream.write(path.read_bytes())
    path.write_bytes(content)
    return str(backup) if backup else None
