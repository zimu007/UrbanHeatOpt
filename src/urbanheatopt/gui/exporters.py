"""Phase-4 exporters: QA report PDF / site-detail Excel / Pareto PNG.

零 GUI 依赖（不 import 任何 QWidget），全部为纯函数：输入 SolveOutcome，
写入目标文件。PDF 用 matplotlib PdfPages，Excel 用 openpyxl，PNG 用
matplotlib savefig —— 均与界面画布同款样式（dataviz 色板/ink 规则）。

- export_png   : 候选站对比散点图（x=碳排 tCO2e/年, y=成本 万元/年）
- export_excel : 三工作表（方案明细 / 求解摘要 / QA 结果）
- export_pdf   : 三页 QA 报告（关键指标与证据 / 站点对比图 / 方案明细表）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib

matplotlib.use("agg")  # 导出不依赖任何 GUI 后端

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D

from urbanheatopt.gui.contracts import KpiCardData, ParetoPointView, SolveOutcome
from urbanheatopt.gui.theme import C

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
matplotlib.rcParams["axes.unicode_minus"] = False

# -- 共用画法 ---------------------------------------------------------------


def _site_coords(sites: Sequence[ParetoPointView]) -> tuple[list[float], list[float]]:
    """ParetoPointView → (碳排 tCO2e/年, 成本 万元/年) 坐标列表."""
    xs = [p.carbon_kgco2e_per_year / 1000.0 for p in sites]
    ys = [p.cost_cny_per_year / 1e4 for p in sites]
    return xs, ys


def _style_axes(ax) -> None:
    ax.set_facecolor(C["bg_card"])
    for spine in ax.spines.values():
        spine.set_color(C["border_strong"])
    ax.tick_params(colors=C["text_muted"], labelsize=9)
    ax.xaxis.label.set_color(C["text_muted"])
    ax.yaxis.label.set_color(C["text_muted"])
    ax.set_xlabel("运行碳排 (tCO2e / 年)")
    ax.set_ylabel("年化真实成本 (万元 / 年)")
    ax.grid(True, color=C["grid"], alpha=0.35, linewidth=0.6)
    ax.set_axisbelow(True)


def _draw_sites_axes(ax, sites: Sequence[ParetoPointView]) -> None:
    """候选站对比散点：选中站 ★ 高亮（与 VisualCanvas.draw_sites 同款）."""
    points = sorted(sites, key=lambda p: p.cost_cny_per_year)
    xs, ys = _site_coords(points)
    if not points:
        return
    knee = None
    for point, x, y in zip(points, xs, ys):
        if point.is_knee:
            knee = (x, y)
            ax.scatter(
                [x], [y], marker="*", s=230,
                facecolor=C["series_blue"], edgecolor="#FFFFFF",
                linewidth=1.2, zorder=4,
            )
        else:
            ax.scatter([x], [y], facecolor=C["series_blue"], s=95, zorder=3)
        site_number = point.point_id.split(":")[0].split("_")[-1]
        ax.annotate(
            f"站 {site_number}", (x, y), xytext=(10, 8),
            textcoords="offset points", fontsize=10, color=C["text"],
        )
    if knee is not None:
        ax.annotate(
            "★ 选中站", knee, xytext=(16, 18), textcoords="offset points",
            arrowprops=dict(arrowstyle="->", color=C["text_muted"], lw=0.9),
            fontsize=10, color=C["text"],
        )
    legend_handles = [
        Line2D([0], [0], marker="o", color="none",
               markerfacecolor=C["series_blue"], markersize=8, label="候选站"),
        Line2D([0], [0], marker="*", color="none",
               markerfacecolor=C["series_blue"], markeredgecolor="#FFFFFF",
               markersize=13, label="选中站（膝点）"),
    ]
    legend = ax.legend(handles=legend_handles, loc="upper right",
                       frameon=False, fontsize=9)
    for text in legend.get_texts():
        text.set_color(C["text_muted"])
    x_span = (max(xs) - min(xs)) or (max(abs(max(xs)), abs(min(xs))) * 0.1 or 10.0)
    y_span = (max(ys) - min(ys)) or (max(abs(max(ys)), abs(min(ys))) * 0.1 or 1.0)
    ax.set_xlim(min(xs) - x_span * 0.08, max(xs) + x_span * 0.08)
    ax.set_ylim(min(ys) - y_span * 0.10, max(ys) + y_span * 0.10)


# -- KPI 文案 ---------------------------------------------------------------


def _fmt_money(cny: float | None) -> str:
    return f"{cny / 1e4:,.2f} 万元/年" if cny is not None else "—"


def _fmt_carbon(kg: float | None) -> str:
    return f"{kg / 1e3:,.2f} tCO2e/年" if kg is not None else "—"


def _fmt_gap(gap: float | None) -> str:
    return f"{gap * 100:.4f}%" if gap is not None else "—"


def _kpi_lines(kpis: KpiCardData) -> list[tuple[str, str]]:
    return [
        ("最佳年化成本", _fmt_money(kpis.best_cost_cny_per_year)),
        ("最低年运行碳排", _fmt_carbon(kpis.min_carbon_kgco2e_per_year)),
        ("选中站点", kpis.selected_site_id or "—"),
        ("验收合成 gap", _fmt_gap(kpis.certified_gap)),
        ("终止条件", kpis.termination_condition or "—"),
        ("独立 QA", "通过" if kpis.qa_passed else "未通过"),
    ]


# -- PNG --------------------------------------------------------------------


def export_png(outcome: SolveOutcome, target: Path) -> Path:
    figure = plt.figure(figsize=(10, 6), dpi=100, facecolor=C["bg_card"])
    ax = figure.add_subplot(111)
    _style_axes(ax)
    _draw_sites_axes(ax, outcome.pareto)
    figure.suptitle(
        "候选站方案对比 · 经济-碳排双目标", color=C["text"], fontsize=14, y=0.97,
    )
    figure.savefig(target, dpi=150, facecolor=C["bg_card"])
    plt.close(figure)
    return target


# -- Excel ------------------------------------------------------------------


def _flatten(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    """嵌套 dict 展平为 (键路径, 值) 行；标量直接显示，序列取摘要."""
    if isinstance(value, Mapping):
        rows: list[tuple[str, str]] = []
        for key, item in value.items():
            rows.extend(_flatten(item, f"{prefix}{key}." if prefix else f"{key}."))
        return rows
    if isinstance(value, (list, tuple)):
        if not value:
            return [(prefix.rstrip("."), "[]")]
        return [(prefix.rstrip("."), f"[{len(value)} 项] " + json.dumps(value[:3], ensure_ascii=False)[:80])]
    if isinstance(value, float):
        return [(prefix.rstrip("."), f"{value:,.6g}")]
    return [(prefix.rstrip("."), str(value))]


def _autofit(sheet, widths: Mapping[str, int]) -> None:
    from openpyxl.utils import get_column_letter

    for column, width in widths.items():
        sheet.column_dimensions[get_column_letter(column)].width = width


def _rgb(token: str) -> str:
    """主题色 "#3987E5" → openpyxl 要求的 aRGB "3987E5"."""
    return token.lstrip("#")


def export_excel(outcome: SolveOutcome, target: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    workbook = Workbook()
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor=_rgb(C["primary"]))
    wrap = Alignment(vertical="top")

    # Sheet 1：方案明细（candidate_site_comparison.csv 原样透传）
    sheet = workbook.active
    sheet.title = "方案明细"
    rows = list(outcome.table_rows)
    if rows:
        headers = list(rows[0].keys())
        for column, name in enumerate(headers, start=1):
            cell = sheet.cell(row=1, column=column, value=name)
            cell.font = header_font
            cell.fill = header_fill
        for row_index, row in enumerate(rows, start=2):
            for column, name in enumerate(headers, start=1):
                sheet.cell(row=row_index, column=column, value=row.get(name))
        _autofit(sheet, {column: min(max(len(str(name)) * 2 + 4, 14), 40)
                         for column, name in enumerate(headers, start=1)})
    else:
        sheet.cell(row=1, column=1, value="无 qualified 站点明细")

    # Sheet 2：求解摘要（solution_summary.json 展平）
    summary_sheet = workbook.create_sheet("求解摘要")
    summary_sheet.cell(row=1, column=1, value="字段").font = header_font
    summary_sheet.cell(row=1, column=1).fill = header_fill
    summary_sheet.cell(row=1, column=2, value="值").font = header_font
    summary_sheet.cell(row=1, column=2).fill = header_fill
    for row_index, (key, value) in enumerate(_flatten(outcome.summary), start=2):
        summary_sheet.cell(row=row_index, column=1, value=key).alignment = wrap
        summary_sheet.cell(row=row_index, column=2, value=value).alignment = wrap
    _autofit(summary_sheet, {1: 44, 2: 60})

    # Sheet 3：QA 结果（independent_qa.json / 兜底 kpis.qa_passed）
    qa_sheet = workbook.create_sheet("QA 结果")
    qa_sheet.cell(row=1, column=1, value="检查项").font = header_font
    qa_sheet.cell(row=1, column=1).fill = header_fill
    qa_sheet.cell(row=1, column=2, value="结果").font = header_font
    qa_sheet.cell(row=1, column=2).fill = header_fill
    qa_path = outcome.artifacts.get("independent_qa")
    qa_rows: list[tuple[str, str]] = []
    if qa_path is not None and Path(qa_path).is_file():
        try:
            qa = json.loads(Path(qa_path).read_text(encoding="utf-8"))
            qa_rows.append(("passed", str(qa.get("passed"))))
            for key, value in (qa.get("max_errors") or {}).items():
                qa_rows.append((f"max_errors.{key}", f"{value:,.6g}"))
            for key, value in (qa.get("timing_seconds") or {}).items():
                qa_rows.append((f"timing_seconds.{key}", f"{value:,.3f} s"))
        except (OSError, json.JSONDecodeError):
            qa_rows = []
    if not qa_rows:
        qa_rows.append(("passed", str(outcome.kpis.qa_passed)))
    for row_index, (key, value) in enumerate(qa_rows, start=2):
        qa_sheet.cell(row=row_index, column=1, value=key)
        cell = qa_sheet.cell(row=row_index, column=2, value=value)
        if key == "passed":
            cell.font = Font(
                bold=True,
                color=_rgb(C["good"]) if value == "True" else _rgb(C["critical"]),
            )
    _autofit(qa_sheet, {1: 40, 2: 30})

    workbook.save(target)
    return target


# -- PDF --------------------------------------------------------------------


def export_pdf(outcome: SolveOutcome, target: Path) -> Path:
    kpis = outcome.kpis
    with PdfPages(target) as pdf:
        # 页 1：关键指标与求解证据
        figure = plt.figure(figsize=(11.69, 8.27), facecolor=C["bg_card"])  # A4 横向
        figure.text(
            0.5, 0.94,
            f"UrbanHeatOpt · 求解质量保证（QA）报告 · run：{outcome.run_id}",
            ha="center", fontsize=16, color=C["text"], weight="bold",
        )
        figure.text(
            0.5, 0.90,
            f"qualified={outcome.qualified} · 接口 {outcome.kpis.termination_condition or '—'} · 独立 QA "
            + ("通过" if kpis.qa_passed else "未通过"),
            ha="center", fontsize=11,
            color=C["good"] if kpis.qa_passed else C["critical"],
        )
        y = 0.80
        for key, value in _kpi_lines(kpis):
            figure.text(0.16, y, key, fontsize=12, color=C["text_muted"])
            figure.text(0.45, y, value, fontsize=12, color=C["text"])
            y -= 0.065
        figure.text(
            0.16, y - 0.03,
            "注：成本为年化真实成本（含 HNS 罚项），碳排为年运行碳排；"
            "gap 为验收合成 gap（incumbent 与 best_bound 相对差）。",
            fontsize=9, color=C["text_muted"],
        )
        figure.text(0.98, 0.02, "1 / 3", ha="right", fontsize=9, color=C["text_muted"])
        pdf.savefig(figure)
        plt.close(figure)

        # 页 2：候选站对比图
        figure = plt.figure(figsize=(11.69, 8.27), facecolor=C["bg_card"])
        ax = figure.add_subplot(111)
        _style_axes(ax)
        _draw_sites_axes(ax, outcome.pareto)
        ax.set_title("候选站方案对比 · 经济-碳排双目标", color=C["text"], fontsize=14, pad=12)
        figure.text(0.98, 0.02, "2 / 3", ha="right", fontsize=9, color=C["text_muted"])
        pdf.savefig(figure)
        plt.close(figure)

        # 页 3：方案明细表（精选列）
        figure = plt.figure(figsize=(11.69, 8.27), facecolor=C["bg_card"])
        ax = figure.add_subplot(111)
        ax.axis("off")
        rows = list(outcome.table_rows)
        columns = [
            ("site_id", "站点"),
            ("mode", "模式"),
            ("status", "状态"),
            ("annual_real_cost_CNY_per_year", "成本 (万元/年)"),
            ("annual_operating_carbon_kgCO2e_per_year", "碳排 (tCO2e/年)"),
            ("reported_gap", "报告 gap"),
            ("termination_condition", "终止条件"),
            ("selected", "选中"),
        ]
        cell_text = []
        for row in rows:
            line = []
            for key, _ in columns:
                value = row.get(key)
                if key == "annual_real_cost_CNY_per_year" and isinstance(value, float):
                    value = f"{value / 1e4:,.2f}"
                elif key == "annual_operating_carbon_kgCO2e_per_year" and isinstance(value, float):
                    value = f"{value / 1e3:,.2f}"
                elif key == "reported_gap" and isinstance(value, float):
                    value = f"{value * 100:.6f}%"
                elif key == "selected":
                    value = "★" if value else ""
                line.append(str(value))
            cell_text.append(line)
        table = ax.table(
            cellText=cell_text, colLabels=[name for _, name in columns],
            colWidths=[0.24, 0.12, 0.12, 0.15, 0.15, 0.10, 0.14, 0.06],
            loc="center",
        )
        table.auto_set_font_size(False)
        table.set_fontsize(10)
        for (row, column), cell in table.get_celld().items():
            cell.set_edgecolor(C["border_strong"])
            if row == 0:
                cell.set_text_props(color="#FFFFFF", weight="bold")
                cell.set_facecolor(C["primary"])
            elif row % 2 == 0:
                cell.set_facecolor(C["bg_card"])
            else:
                cell.set_facecolor(C["grid"])
        ax.set_title("方案明细 · 候选站对比", color=C["text"], fontsize=14, pad=12)
        figure.text(0.98, 0.02, "3 / 3", ha="right", fontsize=9, color=C["text_muted"])
        pdf.savefig(figure)
        plt.close(figure)
    return target


__all__ = ["export_png", "export_excel", "export_pdf"]
