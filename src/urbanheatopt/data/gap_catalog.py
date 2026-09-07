"""A-owned data/interface gaps, not an engineering-approval wish list."""
from datetime import datetime
from pathlib import Path


def data_gaps(snapshot=None, errors=(), spatial_status=None):
    effective = (snapshot or {}).get("effective", {})
    patch = (snapshot or {}).get("v2_freeze_patch") or {}
    station_confirmed = (
        effective.get("station_cost_boundary") == "teacher_confirmed_v2_scenario"
        and isinstance(effective.get("station_capex_CNY"), (int, float))
        and effective["station_capex_CNY"] > 0
        and patch.get("station_cost", {}).get("replaces_other_station_fixed_cost") is True
        and patch.get("station_cost", {}).get("add_with_other_station_fixed_cost") is False
    )
    items = [] if station_confirmed else [
        dict(gap_id="STATION_SCOPE", kind="boundary", requirement="完整工程成本结论影响；不阻止已批准基础研究情景", file="station_cost_breakdown.csv", fields="site_id:string; cost_component_id:string联合主键; capex_CNY:float>=0; life_years:int>0; scope:string; overlaps_equipment:boolean; source_id:string; parameter_status:string", geometry="不适用", current=str(effective.get("station_cost_boundary", "基础主动未纳入，压力1800万元混合边界")), fallback="基础未纳入并明确不完整；压力情景混合边界潜在重叠，非正式上界", replace="拆清土建/设备等后更新site成本，不回填旧10000"),
    ]
    for i, error in enumerate(errors, 1):
        items.append(dict(gap_id=f"VALIDATION_{i:03d}", kind="conflict_or_error", requirement="输入/参数阻塞", file="经济参数完整可运行修订版_20260831/economic_parameters_code_ready.csv 与 economic_parameter_sources_merged.csv（路径错误见具体证据）", fields="保留parameter_id及source_id；code_use_allowed须明确0/1并统一优先级；更正许可、范围或源字段，不改值绕过检查", geometry="不适用", current=str(error), fallback="仅用户20260905已确认的参数/审计来源绑定可按最终报价覆盖旧来源禁用；其他新冲突仍阻止，不扩大授权", replace="核实具体错误或新冲突；确认后更新版本化选择策略，再validate/prepare"))
    # Historical alternatives remain in the parameter snapshot, not in the active
    # missing-input list. B/C code work remains in model_readiness_report.json.
    return items


def render_gap_report(items, snapshot=None):
    lines = ["# 缺失数据清单", "", "自动生成；版本2026-09-07。仅列当前仍需用户、老师或参数组确认的外部数据，不增加模型变量、时间点或工程审批要求。", "", "新经济包、0906补充包与0907教师冻结补丁已有值不再重复列为缺失。CaseBundle和SolveRequest由代码自动生成，也不属于外部缺失数据。", "", f"当前共{len(items)}项。若为0，表示本轮A/B输入交接没有外部参数缺口；这不代表求解已执行，也不代表经济结果已经通过独立复算。", "", f"经济快照：{(snapshot or {}).get('snapshot_id', '尚未通过参数校验')}。", ""]
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
