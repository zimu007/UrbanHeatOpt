"""Model-readiness gate and human-readable guidance for Guanggu v0.3."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

import pandas as pd

from competition.adapters.guanggu_v03 import (
    GuangguV03Adaptation,
    adapt_guanggu_v03_sources,
)


@dataclass(frozen=True, slots=True)
class ReadinessItem:
    item_id: str
    title: str
    status: str
    reason: str
    current_evidence: str
    required_files: tuple[str, ...] = ()
    required_fields: tuple[str, ...] = ()
    units: tuple[str, ...] = ()
    resolution: str = ""


@dataclass(frozen=True, slots=True)
class ModelReadinessReport:
    source_validation_passed: bool
    canonical_validation_passed: bool
    model_ready: bool
    solver_executed: bool
    data_version: str
    contract_version: str
    source_profile: str
    items: tuple[ReadinessItem, ...]

    @property
    def blockers(self) -> tuple[ReadinessItem, ...]:
        return tuple(item for item in self.items if item.status == "blocked")

    @property
    def provisional_items(self) -> tuple[ReadinessItem, ...]:
        return tuple(item for item in self.items if item.status == "provisional")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_validation_passed": self.source_validation_passed,
            "canonical_validation_passed": self.canonical_validation_passed,
            "model_ready": self.model_ready,
            "solver_executed": self.solver_executed,
            "data_version": self.data_version,
            "contract_version": self.contract_version,
            "source_profile": self.source_profile,
            "blocker_count": len(self.blockers),
            "provisional_item_count": len(self.provisional_items),
            "items": [asdict(item) for item in self.items],
        }


@dataclass(frozen=True, slots=True)
class GuangguV03InputValidationRun:
    adaptation: GuangguV03Adaptation
    readiness: ModelReadinessReport
    readiness_report_path: Path
    guidance_report_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "valid_not_model_ready" if not self.readiness.model_ready else "model_ready",
            **self.adaptation.canonical_data.to_summary(),
            **self.readiness.to_dict(),
            "output_dir": str(self.adaptation.output_dir),
            "source_validation_report": str(self.adaptation.source_report_path),
            "canonical_validation_report": str(self.adaptation.canonical_report_path),
            "model_readiness_report": str(self.readiness_report_path),
            "guidance_report": str(self.guidance_report_path),
        }


def assess_guanggu_v03_model_readiness(
    adaptation: GuangguV03Adaptation,
) -> ModelReadinessReport:
    """Assess missing model inputs/code without running or instantiating a solver."""

    data = adaptation.canonical_data
    external = data.external_timeseries
    items: list[ReadinessItem] = [
        ReadinessItem(
            "source_delivery",
            "v0.3源数据自主校验",
            "ready" if adaptation.source_report.valid else "blocked",
            "412个文件必须由代码独立读取、分类、哈希和交叉校验。",
            f"识别{adaptation.source_report.inventory.get('total_files')}个文件；错误数="
            f"{sum(issue.severity == 'error' for issue in adaptation.source_report.issues)}。",
        ),
        ReadinessItem(
            "canonical_heating_season",
            "2160小时标准数据",
            "ready" if adaptation.canonical_report.valid else "blocked",
            "62栋完整供暖季必须在构模前完成时间、ID、单位和LHV复验。",
            f"{data.building_count}栋、{data.hour_count}小时、{data.load_row_count}行；"
            f"供暖季热量相对误差={data.adaptation_metadata['load_reconciliation_relative_error']:.3e}。",
        ),
        ReadinessItem(
            "shared_scheme_boundary",
            "三方案共用输入边界",
            "ready",
            "集中、分布、混合不得使用不同负荷、外部时序和末端边界。",
            "scheme_input_manifest.csv三行关键SHA-256一致，末端均为fan_coil、45/40℃。",
        ),
        ReadinessItem(
            "lhv_boundary",
            "燃气LHV统一边界",
            "ready",
            "体积价格和体积碳因子只能在输入边界转换一次。",
            f"LHV={external['natural_gas_lhv_MJ_per_Nm3'].iloc[0]:.6f} MJ/Nm³；"
            f"气价={external['gas_price_CNY_per_kWh_LHV'].iloc[0]:.12f} CNY/kWh_LHV。",
        ),
        ReadinessItem(
            "external_parameter_status",
            "能源价格与碳因子签认状态",
            "provisional",
            "v0.3值可用于调试，但交付README明确其为暂行情景值。",
            "电价、3.8 CNY/Nm³气价和碳因子均已完整进入标准时序，来源/状态未丢失。",
            resolution="V1.0前由项目组以合同、账单或正式政策来源签认或覆盖。",
        ),
        ReadinessItem(
            "ashp_curve_coverage",
            "温度相关COP与低温能力衰减",
            "blocked",
            "06曲线已读取，但当前核心只有固定V0 Provider，尚无v0.3表格曲线Provider接线。",
            f"曲线最高15℃；{int(external['cop_boundary_clamped'].sum())}小时按15℃封顶。"
            "当前供暖季最低温在曲线范围内，capacity_ratio仍需由新Provider逐时传入Core。",
            required_files=("06_equipment_performance.csv",),
            required_fields=("technology_id", "Tout", "Tsupply", "PLR", "COP", "capacity_ratio"),
            units=("degC", "fraction", "W/W"),
            resolution="实现并测试表格插值/边界封顶Provider，再由v0.3 Case Builder调用。",
        ),
        ReadinessItem(
            "road_candidate_network",
            "道路约束候选站与候选管网",
            "blocked",
            "交付GIS为建筑来源材料，没有正式道路/可建设空间、候选站和物理候选管段接口。",
            "历史V0存在provisional_geometric_mst，但明确非道路优化、非施工可实施方案。",
            required_files=(
                "roads_or_feasible_space.geojson",
                "candidate_sites.geojson",
                "candidate_network.geojson",
            ),
            required_fields=(
                "feature_id/spatial_role/geometry",
                "site_id/geometry",
                "segment_id/node_from/node_to/length_m/geometry",
            ),
            units=("EPSG:4326 input", "EPSG:32650 internal", "m"),
            resolution="由候选网模块输出道路约束候选，不直接决定最终建设方案。",
        ),
        ReadinessItem(
            "pipe_types",
            "三档管径、管损与泵耗",
            "blocked",
            "v0.3没有可执行pipe_types.csv；因此不能计算正式管网投资、热损和泵耗。",
            "Core已预留三档管径、线性热损和泵耗系数接口，但本交付没有对应输入。",
            required_files=("pipe_types.csv",),
            required_fields=(
                "pipe_type_id", "level", "capacity_max_kW_th", "capex_CNY_per_m",
                "heat_loss_kW_per_m", "pumping_kWh_e_per_kWh_th_transferred",
                "lifetime_years", "source", "parameter_version",
            ),
            units=("kW_th", "CNY/m", "kW/m", "kWh_e/kWh_th", "year"),
            resolution="至少提供低/中/高三档并完成单段手算、方向和一次计费测试。",
        ),
        ReadinessItem(
            "station_connection_economics",
            "能源站与接入经济参数",
            "blocked",
            "v0.3没有站点固定投资和逐需求节点接入投资/寿命的权威落点。",
            "当前标准快照不生成case_config，避免以0或旧模板值静默补齐。",
            required_files=("case_config.yaml",),
            required_fields=(
                "economics.station_fixed_capex_CNY", "economics.station_lifetime_years",
                "economics.connection_capex_CNY_per_demand_node",
                "economics.connection_lifetime_years",
            ),
            units=("CNY", "year"),
            resolution="可先采用明确标记scenario_assumption的统一值联调；正式结果前签认。",
        ),
        ReadinessItem(
            "technology_economics",
            "热泵、锅炉和储热可执行经济参数",
            "blocked",
            "长表可读取，但尚不能无歧义生成Core要求的逐设备技术表。",
            "ASHP缺固定/可变运维；锅炉variable_maintenance_coefficient=0.026单位为"
            "model_coefficient；8个扩展技术参数仍pending_confirmation。",
            required_files=("technologies.csv",),
            required_fields=(
                "capex_CNY_per_kW_th", "fixed_maintenance_fraction_per_year",
                "variable_om_CNY_per_kWh_th", "lifetime_years",
                "capacity_min_kW_th", "capacity_max_kW_th", "parameter_status",
            ),
            units=("CNY/kW_th", "fraction/year", "CNY/kWh_th", "year", "kW_th"),
            resolution="明确字段映射和0.026量纲；缺失值不得自动补0。",
        ),
        ReadinessItem(
            "storage_input_and_wiring",
            "水蓄热输入与SOC接线",
            "blocked",
            "06提供首版效率/损失假设，但缺少完整可执行容量、功率、投资映射和v0.3构造器接线。",
            "Core已有线性SOC、循环边界和容量接口；当前标准快照只保留来源表。",
            required_files=("technologies.csv", "06_equipment_performance.csv", "case_config.yaml"),
            required_fields=(
                "energy_capacity_max_kWh_th", "charge_capacity_max_kW_th",
                "discharge_capacity_max_kW_th", "charge_efficiency",
                "discharge_efficiency", "standing_loss_fraction_per_hour",
                "capex_CNY_per_kWh_th", "power_capex_CNY_per_kW_th",
            ),
            units=("kWh_th", "kW_th", "fraction", "fraction/hour", "CNY/kWh_th", "CNY/kW_th"),
            resolution="完成长表到StorageSpec映射和24小时/2160小时SOC闭合测试。",
        ),
        ReadinessItem(
            "capacity_margin_wiring",
            "20%峰值容量裕度接线",
            "blocked",
            "统一Core已实现该约束，但v0.3尚未构造完整CanonicalCaseData，因而尚未实际传入。",
            "CanonicalSeasonData已记录peak_capacity_margin_fraction=0.20；不含N-1且储热不计入。",
            required_files=("case_config.yaml",),
            required_fields=("planning.peak_capacity_margin_fraction",),
            units=("fraction",),
            resolution="v0.3 Case Builder必须显式传入0.20，并用100kW→120kW手算复验。",
        ),
        ReadinessItem(
            "v03_case_builder",
            "v0.3标准快照到统一Pyomo Core连接层",
            "blocked",
            "当前只完成CanonicalSeasonData；尚未将空间、设备、储热和经济参数组合为CanonicalCaseData。",
            "正式入口必须等本项和全部上游必需项通过后才能实例化求解器。",
            required_files=("case_config.yaml",),
            required_fields=("完整V3 files/run/spatial/network/planning/economics/performance/pareto配置",),
            resolution="实现唯一Case Builder；禁止调用旧model.run_model或自动回退V0固定假设。",
        ),
        ReadinessItem(
            "full_season_solve_qa",
            "62栋×2160小时求解与独立QA",
            "blocked",
            "本轮按批准边界不运行求解器；尚无全季三模式、Pareto和QA证据。",
            "数据规模已完成标准化，但solver_executed=false。",
            required_files=("模型就绪后的标准结果目录",),
            required_fields=("三模式状态", "热平衡", "容量裕度", "连通", "SOC", "成本", "碳排", "Pareto"),
            resolution="前述阻塞解除后再运行；全部QA通过前不得称正式运行完成。",
        ),
    ]
    model_ready = not any(item.status == "blocked" for item in items)
    return ModelReadinessReport(
        source_validation_passed=adaptation.source_report.valid,
        canonical_validation_passed=adaptation.canonical_report.valid,
        model_ready=model_ready,
        solver_executed=False,
        data_version=data.data_version,
        contract_version=data.contract_version,
        source_profile=data.source_profile,
        items=tuple(items),
    )


def _markdown_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def _inventory_role(relative: str) -> str:
    prefix_roles = {
        "Wuhan_DeST_Models_映射原型逐时负荷/": "DeST原型追溯",
        "DeST原型映射与conditioned_area缩放工作簿/": "原型映射追溯",
        "光谷软件园GIS成果/": "GIS来源追溯",
        "光谷软件园真实属性补充/": "建筑属性追溯",
        "UrbanHeatOpt/": "旧格式兼容",
    }
    for prefix, role in prefix_roles.items():
        if relative.startswith(prefix):
            return role
    return "根目录交付/QA"


def _read_text_fallback(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeError(f"无法确定说明文件编码: {path}")


def render_guanggu_v03_guidance(
    run: GuangguV03InputValidationRun,
    *,
    workspace_root: Path,
) -> str:
    """Render one detailed report from machine-readable validation results."""

    adaptation = run.adaptation
    readiness = run.readiness
    data = adaptation.canonical_data
    source = adaptation.source_report.source_root
    lines = [
        "# 光谷 v0.3 输入校验与模型就绪状态",
        "",
        f"> 自动生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        "> 本文件由校验结果自动生成；交付方QA只作旁证，结论来自代码独立重算。",
        "",
        "## 1. 结论",
        "",
        "| 状态 | 结果 | 含义 |",
        "|---|---:|---|",
        f"| source_validation_passed | `{str(readiness.source_validation_passed).lower()}` | v0.3源文件是否通过自主读取、哈希和交叉校验 |",
        f"| canonical_validation_passed | `{str(readiness.canonical_validation_passed).lower()}` | 2160小时标准数据是否通过复验 |",
        f"| model_ready | `{str(readiness.model_ready).lower()}` | 是否已具备构造正式Pyomo模型的全部输入和代码 |",
        f"| solver_executed | `{str(readiness.solver_executed).lower()}` | 本轮是否执行过求解器 |",
        "",
        f"当前有 **{len(readiness.blockers)}** 项模型阻塞、**{len(readiness.provisional_items)}** 项暂行参数。",
        "输入校验通过不等于模型运行完成；只要 `model_ready=false`，正式入口必须在求解前停止。",
        "本次仅执行读取、校验、适配和报告生成，未调用旧模型或求解器。",
        "",
        "## 2. 本次输入与标准化结果",
        "",
        f"- 源目录：`{source}`",
        f"- 数据版本：`{data.data_version}`",
        f"- 输入契约：`{data.contract_version}`",
        f"- 源配置：`{data.source_profile}` / `{adaptation.source_report.profile_version}`",
        f"- 文件：{adaptation.source_report.inventory['total_files']} 个，未分类 {len(adaptation.source_report.inventory['unclassified_files'])} 个",
        f"- 建筑：{data.building_count} 栋；全年负荷 543120 行；供暖季标准负荷 {data.load_row_count} 行",
        f"- 供暖季：{data.hour_count} 小时，模型小时1..{data.hour_count}",
        f"- 适配前后供暖季热量相对误差：`{data.adaptation_metadata['load_reconciliation_relative_error']:.3e}`",
        f"- COP 15℃封顶小时数：{data.adaptation_metadata['ashp_upper_boundary_clamped_hour_count']}（V0临时规则，不外推）",
        f"- 天然气LHV：{data.adaptation_metadata['natural_gas_lhv_MJ_per_Nm3']:.6f} MJ/Nm³",
        f"- 标准气价：{data.external_timeseries['gas_price_CNY_per_kWh_LHV'].iloc[0]:.12f} CNY/kWh_LHV",
        f"- 标准燃气碳因子：{data.external_timeseries['gas_carbon_kgCO2e_per_kWh_LHV'].iloc[0]:.12f} kgCO2e/kWh_LHV",
        "",
        "### 时间边界",
        "",
        "| 源hour | heating_season_hour | 模型hour | 标准timestamp |",
        "|---:|---:|---:|---|",
    ]
    time_map = data.timestamp_hour_map
    source_hours = time_map["source_hour"].astype(int)
    transition_positions = [
        int(position)
        for position in range(1, len(source_hours))
        if int(source_hours.iloc[position]) != int(source_hours.iloc[position - 1]) + 1
    ]
    boundary_positions = {0, len(time_map) - 1}
    for position in transition_positions:
        boundary_positions.update((position - 1, position))
    for index in sorted(boundary_positions):
        row = time_map.iloc[index]
        lines.append(
            f"| {int(row.source_hour)} | {int(row.heating_season_hour)} | {int(row.hour)} | {row.timestamp} |"
        )
    clamped = data.external_timeseries.loc[
        data.external_timeseries["cop_boundary_clamped"].map(bool),
        [
            "source_hour",
            "heating_season_hour",
            "hour",
            "timestamp",
            "outdoor_temperature_C",
            "cop_lookup_temperature_C",
        ],
    ]
    lines.extend(
        [
            "",
            "### 15℃ COP 边界封顶小时",
            "",
            "下列小时保留原始室外温度，但查表温度固定为15℃；这是V0临时边界，未做线性外推。",
            "",
            "| source_hour | heating_season_hour | 模型hour | timestamp | 原温度℃ | 查表温度℃ |",
            "|---:|---:|---:|---|---:|---:|",
        ]
    )
    for row in clamped.itertuples(index=False):
        lines.append(
            f"| {int(row.source_hour)} | {int(row.heating_season_hour)} | {int(row.hour)} | "
            f"{row.timestamp} | {float(row.outdoor_temperature_C):.6g} | "
            f"{float(row.cop_lookup_temperature_C):.6g} |"
        )
    if clamped.empty:
        lines.append("| — | — | — | — | — | — |")
    lines.extend(
        [
            "",
            "## 3. 字段转换与统一单位",
            "",
            "| 源文件/字段 | 标准文件/字段 | 规则 | 单位 |",
            "|---|---|---|---|",
        ]
    )
    field_mapping = pd.read_csv(
        adaptation.field_mapping_path,
        encoding="utf-8-sig",
    )
    for row in field_mapping.itertuples(index=False):
        lines.append(
            f"| `{_markdown_escape(row.source_file)} / {_markdown_escape(row.source_field)}` | "
            f"`{_markdown_escape(row.canonical_file)} / {_markdown_escape(row.canonical_field)}` | "
            f"{_markdown_escape(row.rule)} | `{_markdown_escape(row.unit)}` |"
        )
    lines.extend(
        [
            "",
            "关键统一口径：负荷保持浮点 `kW_th`，不缩放；时间权重为 `h/year`；"
            "燃气只使用LHV；体积字段保留为追溯值但不再作为模型权威输入。",
            "",
            "## 4. 源数据集字段与读取结果",
            "",
            "| 数据集 | 路径 | 行数 | 字段 |",
            "|---|---|---:|---|",
        ]
    )
    for name, dataset in adaptation.source_report.datasets.items():
        if not isinstance(dataset, dict) or "path" not in dataset:
            continue
        columns = ", ".join(f"`{column}`" for column in dataset.get("columns", []))
        lines.append(
            f"| {name} | `{_markdown_escape(dataset['path'])}` | {dataset.get('row_count', '')} | {columns} |"
        )
    lines.extend(
        [
            "",
            "### 自主校验问题清单",
            "",
            "| 层级 | 严重度 | 代码 | 文件 | 证据 |",
            "|---|---|---|---|---|",
        ]
    )
    reported_issue_count = 0
    for issue in adaptation.source_report.issues:
        reported_issue_count += 1
        lines.append(
            f"| source | {issue.severity} | `{issue.code}` | "
            f"`{_markdown_escape(issue.path or '—')}` | {_markdown_escape(issue.message)} |"
        )
    for issue in adaptation.canonical_report.issues:
        reported_issue_count += 1
        lines.append(
            f"| canonical | {issue.severity} | `{issue.code}` | — | {_markdown_escape(issue.message)} |"
        )
    if reported_issue_count == 0:
        lines.append("| source/canonical | passed | — | — | 未发现错误或警告 |")
    lines.extend(
        [
            "",
            "## 5. 模型就绪项、缺失文件和未实现功能",
            "",
            "| ID | 状态 | 内容 | 当前证据/原因 | 所需文件与格式 | 解除条件 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for item in readiness.items:
        files = "<br>".join(f"`{value}`" for value in item.required_files) or "—"
        fields = "<br>".join(f"`{value}`" for value in item.required_fields)
        units = "<br>".join(f"`{value}`" for value in item.units)
        required = files
        if fields:
            required += "<br>字段：" + fields
        if units:
            required += "<br>单位：" + units
        lines.append(
            f"| `{item.item_id}` | **{item.status}** | {_markdown_escape(item.title)} | "
            f"{_markdown_escape(item.current_evidence)}<br>{_markdown_escape(item.reason)} | "
            f"{required} | {_markdown_escape(item.resolution or '已通过')} |"
        )
    lines.extend(
        [
            "",
            "## 6. 在 VS Code 中复现",
            "",
            f"1. 在 VS Code 打开 `{workspace_root}`。",
            "2. 打开“终端 → 新建终端”，确认当前目录是仓库根目录，不是在某个Python文件内部运行。",
            "3. 执行环境检查：",
            "",
            "```powershell",
            "conda run --no-capture-output -n urbanheatopt_env python scripts/check_environment.py",
            "```",
            "",
            "4. 执行源校验、供暖季适配和标准数据复验：",
            "",
            "```powershell",
            "conda run --no-capture-output -n urbanheatopt_env python scripts/validate_inputs.py `",
            f"  --delivery-root \"{source}\" `",
            "  --source-profile guanggu_v03 `",
            "  --scope heating-season `",
            "  --full-audit",
            "```",
            "",
            "成功完成输入验收时退出码为0；输入/契约错误为2；程序异常为1。"
            "退出码0只表示源与标准数据有效，不表示模型已经运行。",
            "",
            "5. 验证正式运行门禁：",
            "",
            "```powershell",
            "conda run --no-capture-output -n urbanheatopt_env python scripts/run_case.py `",
            f"  --delivery-root \"{source}\" `",
            "  --source-profile guanggu_v03 `",
            "  --profile v1-full",
            "```",
            "",
            "在上述阻塞解除前，该命令应以退出码2明确停止，且不得调用旧模型或求解器。",
            "",
            "## 7. 输出文件",
            "",
            f"- 本次标准化目录：`{adaptation.output_dir}`",
            f"- 源校验JSON：`{adaptation.source_report_path}`",
            f"- 标准复验JSON：`{adaptation.canonical_report_path}`",
            f"- 模型就绪JSON：`{run.readiness_report_path}`",
            f"- 本说明：`{run.guidance_report_path}`",
            "",
            "源目录只读；校验和适配前后412个源文件SHA-256保持一致。",
            "",
            "## 附录A：交付数据字典原文",
            "",
            "以下内容自动读取自交付目录 `07_data_dictionary.md`，用于完整保留00—09字段、"
            "语义与单位；代码仍以 source profile 和实际文件重算结果作为验收依据。",
            "",
        ]
    )
    dictionary_path = source / "07_data_dictionary.md"
    if dictionary_path.is_file():
        lines.append(_read_text_fallback(dictionary_path).rstrip())
    else:
        lines.append("`07_data_dictionary.md` 缺失。")
    lines.extend(
        [
            "",
            "## 附录B：全部源文件分类、读取状态与SHA-256",
            "",
            "| 角色 | 相对路径 | 读取状态 | SHA-256 |",
            "|---|---|---|---|",
        ]
    )
    for relative, digest in sorted(adaptation.source_report.file_sha256.items()):
        lines.append(
            f"| {_inventory_role(relative)} | `{_markdown_escape(relative)}` | passed | `{digest}` |"
        )
    return "\n".join(lines) + "\n"


def run_guanggu_v03_input_validation(
    source_root: str | Path,
    output_dir: str | Path,
    guidance_report_path: str | Path,
    *,
    full_audit: bool = True,
    profile_path: str | Path | None = None,
    workspace_root: str | Path | None = None,
) -> GuangguV03InputValidationRun:
    """Run validation/adaptation/readiness only; never call a solver."""

    adaptation = adapt_guanggu_v03_sources(
        source_root,
        output_dir,
        profile_path=profile_path,
        full_audit=full_audit,
    )
    readiness = assess_guanggu_v03_model_readiness(adaptation)
    readiness_path = adaptation.output_dir / "model_readiness_report.json"
    readiness_path.write_text(
        json.dumps(readiness.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    guidance = Path(guidance_report_path).resolve()
    if not guidance.parent.is_dir():
        raise ValueError(f"说明文件父目录不存在: {guidance.parent}")
    provisional_run = GuangguV03InputValidationRun(
        adaptation=adaptation,
        readiness=readiness,
        readiness_report_path=readiness_path,
        guidance_report_path=guidance,
    )
    workspace = (
        Path(workspace_root).resolve()
        if workspace_root is not None
        else Path(__file__).resolve().parents[1]
    )
    guidance.write_text(
        render_guanggu_v03_guidance(provisional_run, workspace_root=workspace),
        encoding="utf-8",
    )
    return provisional_run


def default_audit_output_dir(output_root: str | Path) -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f")
    return Path(output_root).resolve() / "input_validation" / f"guanggu_v03_{timestamp}"


def default_guidance_report_path() -> Path:
    return Path.home() / "Desktop" / "光谷v0.3输入校验与模型就绪状态.md"
