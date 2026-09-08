"""Central visual canvas: Pareto scatter + KPI row + solution detail table.

Chart rules (dataviz skill): series hues are the validated all-pairs trio on
the card surface (#3987E5 / #199E70 / #D95926); the knee point is highlighted
with shape + white ring + direct label instead of a fourth hue; text wears
ink tokens only; a legend is always present for >= 2 series.
"""
from __future__ import annotations

from typing import Sequence

import matplotlib

matplotlib.use("qtagg")

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QSplitter, QTableView,
    QVBoxLayout, QWidget,
)

from urbanheatopt.gui import mock_data
from urbanheatopt.gui.theme import C
from urbanheatopt.gui.widgets.common import Card
from urbanheatopt.gui.widgets.kpi import KpiCardRow

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
matplotlib.rcParams["axes.unicode_minus"] = False

_CHIP_FIELDS = (
    (C["series_aqua"], "成本端点"),
    (C["series_orange"], "碳端点"),
    (C["series_blue"], "ε 点"),
)

# 真实求解结果：候选站对比（无端点/ε 概念，选中站即膝点）。
_SITE_CHIP_FIELDS = (
    (C["series_blue"], "已认证候选站"),
)


class VisualCanvas(QWidget):
    """Pareto figure + KPI row + candidate comparison table."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.figure = Figure(
            figsize=(9, 4.4), dpi=100, facecolor=C["bg_card"], layout="constrained"
        )
        self.canvas = FigureCanvasQTAgg(self.figure)
        self._ax = self.figure.add_subplot(111)

        chart_card = Card()
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(12, 10, 12, 6)
        chart_layout.setSpacing(6)
        chart_layout.addWidget(self._chart_header())
        chart_layout.addWidget(self.canvas, 1)

        self.kpi_row = KpiCardRow()
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(chart_card)
        splitter.addWidget(self.kpi_row)
        splitter.addWidget(self._table_card())
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([480, 110, 210])
        layout.addWidget(splitter, 1)

        self.draw_idle()
        self.kpi_row.reset()

    # -- construction -----------------------------------------------------

    def _chart_header(self) -> QWidget:
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self._chart_title = QLabel("Pareto 最优前沿 · 经济-碳排双目标")
        self._chart_title.setObjectName("SectionTitle")
        row.addWidget(self._chart_title)
        row.addStretch(1)
        self._chip_labels: list[QLabel] = []
        for color, text in _CHIP_FIELDS:
            chip = QLabel(f'<span style="color:{color};">●</span> {text}')
            chip.setObjectName("SectionHint")
            self._chip_labels.append(chip)
            row.addWidget(chip)
        self._knee_chip = QLabel('<span style="color:#FFFFFF;">★</span> 膝点高亮')
        self._knee_chip.setObjectName("SectionHint")
        row.addWidget(self._knee_chip)
        return header

    def _set_chart_meta(self, title: str, chips, knee_text: str) -> None:
        """切换图头文案：演示态（Pareto 前沿）↔ 真实态（候选站对比）."""
        self._chart_title.setText(title)
        for chip_label, (color, text) in zip(self._chip_labels, chips):
            chip_label.setText(f'<span style="color:{color};">●</span> {text}')
        self._knee_chip.setText(f'<span style="color:#FFFFFF;">★</span> {knee_text}')

    def _table_card(self) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        title = QLabel("方案明细 · 候选站对比")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        self.table = QTableView()
        self.model = QStandardItemModel(self.table)
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.model.setHorizontalHeaderLabels(list(mock_data.DEMO_TABLE_HEADERS))
        layout.addWidget(self.table, 1)
        return card

    # -- figure -----------------------------------------------------------

    def _style_axes(self) -> None:
        ax = self._ax
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

    def draw_idle(self) -> None:
        self._ax.clear()
        self._style_axes()
        self._ax.text(
            0.5, 0.5,
            "求解完成后在此展示\nPareto 经济-碳排最优前沿与膝点",
            ha="center", va="center", transform=self._ax.transAxes,
            color=C["text_muted"], fontsize=13,
        )
        self._ax.set_xlim(0, 1)
        self._ax.set_ylim(0, 1)
        self.canvas.draw_idle()

    def draw_pareto(self, points: Sequence[tuple]) -> None:
        """points: (point_id, mode, labels, epsilon_t, cost_wan, carbon_t, is_knee)."""
        ax = self._ax
        ax.clear()
        self._style_axes()
        self._set_chart_meta("Pareto 最优前沿 · 经济-碳排双目标", _CHIP_FIELDS, "膝点高亮")
        frontier = sorted(points, key=lambda p: p[5])
        xs = [p[5] for p in frontier]
        ys = [p[4] for p in frontier]
        ax.plot(xs, ys, color=C["series_blue"], alpha=0.5, lw=1.2, ls="--", zorder=1)
        knee = None
        for p in frontier:
            _, _, labels, _, cost, carbon, is_knee = p
            if is_knee:
                knee = p
                ax.scatter(
                    [carbon], [cost], marker="*", s=230,
                    facecolor=C["series_blue"], edgecolor="#FFFFFF",
                    linewidth=1.2, zorder=4,
                )
            elif "cost_endpoint" in labels:
                ax.scatter([carbon], [cost], facecolor=C["series_aqua"], s=95, zorder=3)
            elif "carbon_endpoint" in labels:
                ax.scatter([carbon], [cost], facecolor=C["series_orange"], s=95, zorder=3)
            else:
                ax.scatter([carbon], [cost], facecolor=C["series_blue"], s=60, zorder=3)

        label_kw = dict(fontsize=10, color=C["text"])
        cost_point = min(frontier, key=lambda p: p[4])
        carbon_point = min(frontier, key=lambda p: p[5])
        ax.annotate(
            "最低成本端点", (cost_point[5], cost_point[4]),
            xytext=(-10, 14), textcoords="offset points", **label_kw,
        )
        ax.annotate(
            "最低碳排端点", (carbon_point[5], carbon_point[4]),
            xytext=(10, -18), textcoords="offset points", **label_kw,
        )
        if knee is not None:
            ax.annotate(
                "★ 膝点", (knee[5], knee[4]), xytext=(16, 18),
                textcoords="offset points",
                arrowprops=dict(arrowstyle="->", color=C["text_muted"], lw=0.9),
                **label_kw,
            )
        legend_handles = [
            Line2D([0], [0], marker="o", color="none",
                   markerfacecolor=C["series_aqua"], markersize=8, label="成本端点"),
            Line2D([0], [0], marker="o", color="none",
                   markerfacecolor=C["series_orange"], markersize=8, label="碳端点"),
            Line2D([0], [0], marker="o", color="none",
                   markerfacecolor=C["series_blue"], markersize=7, label="ε 点"),
            Line2D([0], [0], marker="*", color="none",
                   markerfacecolor=C["series_blue"], markeredgecolor="#FFFFFF",
                   markersize=13, label="膝点"),
        ]
        legend = ax.legend(handles=legend_handles, loc="upper right",
                           frameon=False, fontsize=9)
        for text in legend.get_texts():
            text.set_color(C["text_muted"])
        x_margin = (max(xs) - min(xs)) * 0.08
        y_margin = (max(ys) - min(ys)) * 0.10
        ax.set_xlim(min(xs) - x_margin, max(xs) + x_margin)
        ax.set_ylim(min(ys) - y_margin, max(ys) + y_margin)
        self.canvas.draw_idle()

    def draw_sites(self, sites: Sequence) -> None:
        """真实求解结果：候选站对比散点（x=碳排 tCO2e/年, y=成本 万元/年）。

        sites: ParetoPointView 序列（build_outcome 从 candidate_site_comparison
        的 qualified 行构建）；选中站（is_knee）以 ★ 高亮。
        """
        ax = self._ax
        ax.clear()
        self._style_axes()
        self._set_chart_meta("候选站方案对比 · 经济-碳排双目标", _SITE_CHIP_FIELDS, "选中站")
        points = sorted(sites, key=lambda p: p.cost_cny_per_year)
        xs = [p.carbon_kgco2e_per_year / 1000.0 for p in points]
        ys = [p.cost_cny_per_year / 1e4 for p in points]
        knee = None
        for point, x, y in zip(points, xs, ys):
            if point.is_knee:
                knee = (point, x, y)
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
                "★ 选中站", (knee[1], knee[2]), xytext=(16, 18),
                textcoords="offset points",
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
        # 单点/多点通用边距：跨度为零时按数值 10% 保底，避免 xlim 退化。
        x_span = (max(xs) - min(xs)) or (max(abs(max(xs)), abs(min(xs))) * 0.1 or 10.0)
        y_span = (max(ys) - min(ys)) or (max(abs(max(ys)), abs(min(ys))) * 0.1 or 1.0)
        ax.set_xlim(min(xs) - x_span * 0.08, max(xs) + x_span * 0.08)
        ax.set_ylim(min(ys) - y_span * 0.10, max(ys) + y_span * 0.10)
        self.canvas.draw_idle()

    # -- state entry points ------------------------------------------------

    def load_rows(self, rows: Sequence[Sequence[str]]) -> None:
        self.model.removeRows(0, self.model.rowCount())
        for row in rows:
            items = [QStandardItem(str(cell)) for cell in row]
            for index, item in enumerate(items):
                if index >= 2:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
            self.model.appendRow(items)

    def load_demo(self) -> None:
        self.draw_pareto(mock_data.DEMO_PARETO_POINTS)
        self.kpi_row.load_kpis(mock_data.DEMO_KPIS_CARD)
        self.load_rows(mock_data.DEMO_TABLE_ROWS)

    def reset_idle(self) -> None:
        self.draw_idle()
        self.kpi_row.reset()
        self.model.removeRows(0, self.model.rowCount())


__all__ = ["VisualCanvas"]
