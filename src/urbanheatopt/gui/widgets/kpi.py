"""KPI stat tiles: 最佳成本 / 最低碳排 / 膝点均衡 / 求解证据.

Text wears ink tokens only (dataviz rule); status is carried by the badge
with icon + label, never by a colored number.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from urbanheatopt.gui.contracts import KpiCardData
from urbanheatopt.gui.widgets.common import make_badge, set_badge


class KpiCard(QFrame):
    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("KpiCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("KpiTitle")
        layout.addWidget(title_label)
        value_row = QHBoxLayout()
        value_row.setSpacing(6)
        self._value = QLabel("—")
        self._value.setObjectName("KpiValue")
        self._unit = QLabel("")
        self._unit.setObjectName("KpiUnit")
        value_row.addWidget(self._value)
        value_row.addWidget(self._unit)
        value_row.addStretch(1)
        layout.addLayout(value_row)
        self._sub = QLabel("")
        self._sub.setObjectName("KpiSub")
        self._sub.setWordWrap(True)
        layout.addWidget(self._sub)
        self._badge: QLabel | None = None

    def set_value(
        self,
        value: str,
        unit: str = "",
        sub: str = "",
        badge: tuple[str, str] | None = None,
    ) -> None:
        self._value.setText(value)
        self._unit.setText(unit)
        self._sub.setText(sub)
        if badge is not None:
            if self._badge is None:
                self._badge = make_badge(badge[0], badge[1])
                self.layout().addWidget(self._badge)
            else:
                set_badge(self._badge, badge[0], badge[1])
            self._badge.show()
        elif self._badge is not None:
            self._badge.hide()


class KpiCardRow(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.cards = {
            "best_cost": KpiCard("最佳成本"),
            "min_carbon": KpiCard("最低碳排"),
            "knee": KpiCard("膝点均衡"),
            "evidence": KpiCard("求解证据"),
        }
        for card in self.cards.values():
            layout.addWidget(card, 1)

    def reset(self) -> None:
        for card in self.cards.values():
            card.set_value("—", sub="等待求解结果")

    def load_kpis(self, kpis: KpiCardData) -> None:
        """消费 contracts.KpiCardData（真实与 Mock 后端统一产出）."""
        best = kpis.best_cost_cny_per_year
        self.cards["best_cost"].set_value(
            f"{best / 1e4:.1f}" if best is not None else "—", "万元/年",
            sub=f"站点 {kpis.selected_site_id or '—'}",
        )
        min_carbon = kpis.min_carbon_kgco2e_per_year
        self.cards["min_carbon"].set_value(
            f"{min_carbon / 1e3:.1f}" if min_carbon is not None else "—", "tCO2e/年",
            sub="最低碳排（qualified 站点最值）",
        )
        knee_cost = kpis.knee_cost_cny_per_year
        knee_carbon = kpis.knee_carbon_kgco2e_per_year
        if knee_cost is not None and knee_carbon is not None:
            self.cards["knee"].set_value(
                f"{knee_cost / 1e4:.1f} / {knee_carbon / 1e3:.1f}", "万元 · tCO2e",
                sub=f"膝点 {kpis.knee_point_id or '—'}",
            )
        else:
            self.cards["knee"].set_value("—", "万元 · tCO2e", sub="无膝点")
        qa_badge = ("✓ QA 通过", "pass") if kpis.qa_passed else ("✗ QA 未通过", "error")
        gap = kpis.certified_gap
        self.cards["evidence"].set_value(
            f"{gap:.2%}" if gap is not None else "—", "gap",
            sub=(
                f"{kpis.termination_condition or '—'} · incumbent {best / 1e4:.1f} 万元"
                if best is not None else f"{kpis.termination_condition or '—'}"
            ),
            badge=qa_badge,
        )


__all__ = ["KpiCard", "KpiCardRow"]
