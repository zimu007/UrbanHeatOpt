"""Left control panel: 数据输入 / 场景参数 / 校验状态 / HiGHS 配置 / 求解控制."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox, QVBoxLayout,
    QWidget,
)

from urbanheatopt.gui.contracts import ScenarioParams, SolverConfig
from urbanheatopt.gui.widgets.common import Card, field_row, make_badge, section_header

_MODE_ITEMS = (("集中式 (central)", "central"), ("分布式 (distributed)", "distributed"),
               ("混合式 (hybrid)", "hybrid"))
_OBJECTIVE_ITEMS = (("成本最小 (cost)", "cost"), ("碳排最小 (carbon)", "carbon"))
_SOLVER_ITEMS = (("HiGHS", "highs"), ("Gurobi", "gurobi"), ("自动", "auto"))


class SidePanel(QScrollArea):
    """All user inputs live here; the panel only emits signals, never solves."""

    file_selected = Signal(str)
    validate_requested = Signal()
    solve_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setMinimumWidth(300)
        self.setMaximumWidth(380)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("AppRoot")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(self._card_input())
        layout.addWidget(self._card_scenario())
        layout.addWidget(self._card_checks())
        layout.addWidget(self._card_solver())
        layout.addWidget(self._card_control())
        layout.addStretch(1)
        self.setWidget(body)

    # -- helpers ----------------------------------------------------------

    def _vbox_card(self) -> tuple[Card, QVBoxLayout]:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        return card, layout

    # -- cards ------------------------------------------------------------

    def _card_input(self) -> Card:
        card, layout = self._vbox_card()
        layout.addWidget(section_header("① 数据输入", "配置文件 / 数据文件（CSV · Excel · JSON）"))
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        self.file_edit.setPlaceholderText("尚未选择数据文件或 CaseBundle")
        layout.addWidget(self.file_edit)
        button_row = QHBoxLayout()
        pick_button = QPushButton("选择文件…")
        pick_button.clicked.connect(self._pick_file)
        button_row.addWidget(pick_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        layout.addWidget(make_badge("等待导入", "info"))
        return card

    def _card_scenario(self) -> Card:
        card, layout = self._vbox_card()
        layout.addWidget(section_header("② 场景参数", "对应 SolveRequest 顶层字段"))
        self.mode_combo = QComboBox()
        for text, value in _MODE_ITEMS:
            self.mode_combo.addItem(text, value)
        layout.addWidget(field_row("供热模式", self.mode_combo))
        self.objective_combo = QComboBox()
        for text, value in _OBJECTIVE_ITEMS:
            self.objective_combo.addItem(text, value)
        self.objective_combo.currentIndexChanged.connect(self._sync_epsilon_enabled)
        layout.addWidget(field_row("优化目标", self.objective_combo))
        self.epsilon_spin = QDoubleSpinBox()
        self.epsilon_spin.setRange(0.0, 100000.0)
        self.epsilon_spin.setDecimals(2)
        self.epsilon_spin.setSuffix(" tCO2e")
        self.epsilon_spin.setSpecialValueText("不限制")
        self.epsilon_spin.setValue(0.0)
        layout.addWidget(field_row("ε 碳上限", self.epsilon_spin))
        self.tes_check = QCheckBox("启用 TES 储热")
        self.tes_check.setToolTip("首轮验收固定关闭；TES 配对任务启用")
        layout.addWidget(self.tes_check)
        self._sync_epsilon_enabled()
        return card

    def _card_checks(self) -> Card:
        card, layout = self._vbox_card()
        layout.addWidget(section_header("③ 校验状态", "维度检查 · 约束完整性 · 哈希复验"))
        self.checks_box = QWidget()
        checks_layout = QVBoxLayout(self.checks_box)
        checks_layout.setContentsMargins(0, 0, 0, 0)
        checks_layout.setSpacing(6)
        self.check_badges = [make_badge("未运行校验", "info")]
        for badge in self.check_badges:
            checks_layout.addWidget(badge)
        layout.addWidget(self.checks_box)
        self.validate_button = QPushButton("运行校验与适配")
        self.validate_button.setToolTip("完整输入流水线（validate + prepare，数分钟）")
        self.validate_button.clicked.connect(self.validate_requested)
        layout.addWidget(self.validate_button)
        return card

    def _card_solver(self) -> Card:
        card, layout = self._vbox_card()
        layout.addWidget(section_header("④ HiGHS 配置", "对应 SolveRequest.solver 字段"))
        self.solver_combo = QComboBox()
        for text, value in _SOLVER_ITEMS:
            self.solver_combo.addItem(text, value)
        layout.addWidget(field_row("求解器", self.solver_combo))
        self.threads_combo = QComboBox()
        for value in (1, 4, 8):  # SolveRequest.solver.threads 仅允许 1/4/8
            self.threads_combo.addItem(str(value), value)
        self.threads_combo.setCurrentIndex(2)
        layout.addWidget(field_row("线程数", self.threads_combo))
        self.gap_spin = QDoubleSpinBox()
        self.gap_spin.setRange(0.0001, 1.0)
        self.gap_spin.setDecimals(4)
        self.gap_spin.setSingleStep(0.0001)
        self.gap_spin.setValue(0.01)
        layout.addWidget(field_row("MIP 容差", self.gap_spin))
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(0.0, 86400.0)
        self.timeout_spin.setDecimals(0)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.setSpecialValueText("不限时")
        self.timeout_spin.setValue(0.0)
        layout.addWidget(field_row("超时阈值", self.timeout_spin))
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 2**31 - 1)
        self.seed_spin.setValue(0)
        layout.addWidget(field_row("随机种子", self.seed_spin))
        return card

    def _card_control(self) -> Card:
        card, layout = self._vbox_card()
        layout.addWidget(section_header("⑤ 求解控制", "异步执行 · 界面不阻塞"))
        self.solve_button = QPushButton("启动求解")
        self.solve_button.setProperty("variant", "primary")
        self.solve_button.setEnabled(False)  # 校验与适配完成前不可求解
        self.solve_button.setToolTip("完成「运行校验与适配」并生成 CaseBundle 后方可求解")
        self.solve_button.clicked.connect(self.solve_requested)
        self.cancel_button = QPushButton("终止求解")
        self.cancel_button.setProperty("variant", "danger")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_requested)
        row = QHBoxLayout()
        row.addWidget(self.solve_button, 1)
        row.addWidget(self.cancel_button, 1)
        layout.addLayout(row)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        self.control_status = QLabel("就绪")
        self.control_status.setObjectName("SectionHint")
        layout.addWidget(self.control_status)
        return card

    # -- behavior ---------------------------------------------------------

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择数据文件",
            "",
            "数据文件 (*.yaml *.yml *.csv *.xlsx *.json);;CaseBundle (*.json);;所有文件 (*)",
        )
        if path:
            self.file_edit.setText(path)
            self.file_selected.emit(path)

    def pick_file(self) -> None:
        """Public entry used by the menu bar."""
        self._pick_file()

    def set_checks(self, checks) -> None:
        """Replace the check badge list; checks are (id, level, text) tuples."""
        box_layout = self.checks_box.layout()
        while box_layout.count():
            item = box_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.check_badges = []
        for _check_id, level, text in checks:
            badge = make_badge(text, level)
            self.check_badges.append(badge)
            box_layout.addWidget(badge)

    def _sync_epsilon_enabled(self) -> None:
        is_cost = self.objective_combo.currentData() == "cost"
        self.epsilon_spin.setEnabled(is_cost)
        self.epsilon_spin.setToolTip(
            "ε-约束法碳上限；仅成本最小目标启用" if is_cost else "仅成本最小目标启用"
        )

    def selected_scenario(self) -> ScenarioParams:
        epsilon_t = self.epsilon_spin.value()
        return ScenarioParams(
            mode=str(self.mode_combo.currentData()),
            objective=str(self.objective_combo.currentData()),
            epsilon_carbon_kg=epsilon_t * 1000.0 if epsilon_t > 0 else None,
            tes_enabled=self.tes_check.isChecked(),
        )

    def selected_solver(self) -> SolverConfig:
        timeout = self.timeout_spin.value()
        return SolverConfig(
            name=str(self.solver_combo.currentData()),
            threads=int(self.threads_combo.currentData()),
            random_seed=self.seed_spin.value(),
            mip_gap=self.gap_spin.value(),
            time_limit_s=timeout if timeout > 0 else None,
        )

    def set_solve_enabled(self, enabled: bool) -> None:
        """仅当 CaseBundle 就绪时由主窗口启用求解."""
        self.solve_button.setEnabled(enabled)


__all__ = ["SidePanel"]
