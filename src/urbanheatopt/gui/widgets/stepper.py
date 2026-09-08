"""Four-step workflow stepper: 数据输入 → 校验适配 → HiGHS求解 → 结果输出."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, Qt, QVariantAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from urbanheatopt.gui.theme import C
from urbanheatopt.gui.widgets.common import repolish

STEPS = (
    ("数据输入", "导入并准备 CaseBundle"),
    ("校验适配", "维度与约束完整性校验"),
    ("HiGHS 求解", "候选站枚举与合成 gap"),
    ("结果输出", "Pareto 前沿与 QA 报告"),
)

_STATES = ("pending", "active", "done", "failed")


class WorkflowStepper(QWidget):
    """Horizontal step bar; each step shows circle + title + sub-state text."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._circles: list[QLabel] = []
        self._titles: list[QLabel] = []
        self._subs: list[QLabel] = []
        self._connectors: list[QFrame] = []
        self._pulse: QVariantAnimation | None = None

        for index, (title, sub) in enumerate(STEPS):
            if index > 0:
                connector = QFrame()
                connector.setObjectName("StepConnector")
                connector.setProperty("connState", "pending")
                connector.setFixedHeight(2)
                layout.addWidget(connector, 1)
                self._connectors.append(connector)
            layout.addWidget(self._make_step(index, title, sub))

    def _make_step(self, index: int, title: str, sub: str) -> QWidget:
        step = QWidget()
        row = QHBoxLayout(step)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        circle = QLabel(str(index + 1))
        circle.setObjectName("StepCircle")
        circle.setProperty("stepState", "pending")
        circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        circle.setFixedSize(26, 26)
        text_box = QWidget()
        text_layout = QVBoxLayout(text_box)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)
        title_label = QLabel(title)
        title_label.setObjectName("StepTitle")
        title_label.setProperty("stepState", "pending")
        sub_label = QLabel(sub)
        sub_label.setObjectName("StepSub")
        text_layout.addWidget(title_label)
        text_layout.addWidget(sub_label)
        row.addWidget(circle)
        row.addWidget(text_box)
        self._circles.append(circle)
        self._titles.append(title_label)
        self._subs.append(sub_label)
        return step

    def set_state(self, index: int, state: str, sub_text: str | None = None) -> None:
        if state not in _STATES:
            raise ValueError(f"未知步骤状态: {state}")
        circle, title = self._circles[index], self._titles[index]
        circle.setText({"pending": str(index + 1), "active": str(index + 1),
                        "done": "✓", "failed": "✗"}[state])
        for widget in (circle, title):
            widget.setProperty("stepState", state)
            repolish(widget)
        if sub_text is not None:
            self._subs[index].setText(sub_text)
        # A connector is green only when its left step is finished.
        for connector_index, connector in enumerate(self._connectors):
            left_done = index > connector_index and state == "done"
            done = left_done or self._circles[connector_index].property("stepState") == "done"
            connector.setProperty("connState", "done" if done else "pending")
            repolish(connector)
        self._update_pulse(state)

    def reset(self) -> None:
        for index in range(len(STEPS)):
            self.set_state(index, "pending")

    def _update_pulse(self, active_state: str) -> None:
        if self._pulse is not None:
            self._pulse.stop()
            self._pulse.deleteLater()
            self._pulse = None
        active_index = next(
            (i for i, circle in enumerate(self._circles)
             if circle.property("stepState") == "active"),
            None,
        )
        if active_index is None or active_state != "active":
            return
        circle = self._circles[active_index]
        self._pulse = QVariantAnimation(self)
        self._pulse.setStartValue(QColor(C["primary"]))
        self._pulse.setEndValue(QColor(C["accent"]))
        self._pulse.setDuration(900)
        self._pulse.setLoopCount(-1)
        self._pulse.setEasingCurve(QEasingCurve.Type.InOutSine)

        def apply(color: QColor) -> None:
            circle.setStyleSheet(
                f"#StepCircle {{ background: {color.name()};"
                f" border: 1px solid {color.name()}; color: #FFFFFF;"
                " border-radius: 13px; }}"
            )

        self._pulse.valueChanged.connect(apply)
        self._pulse.start()


__all__ = ["STEPS", "WorkflowStepper"]
