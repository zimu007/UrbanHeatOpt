"""Main application window: header, menu bar, three-pane layout, status bar.

软著截图固定要素：标题栏系统名+版本徽章、菜单栏四项、四步工作流、状态栏
接口版本与版权行——均在此处集中构建，不可隐藏。

阶段三接线（方案 A）：
- 文件选择 → PipelineBackend 自动 validate（子进程 run.py validate）
- 运行校验与适配 → PipelineBackend prepare（子进程 run.py prepare）
- 启动求解 → SubprocessSolverBackend（子进程 run.py solve + QTimer 轮询产物）
- 终止求解 → kill 子进程；UI 线程永不阻塞
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QSplitter,
    QVBoxLayout, QWidget,
)

from urbanheatopt.gui import GUI_CONTRACT_VERSION, GUI_VERSION, SYSTEM_NAME
from urbanheatopt.gui import mock_data
from urbanheatopt.gui.backends import (
    SOLVE_OUTPUT_ROOT, MockSolverBackend, PipelineBackend, SubprocessSolverBackend,
    make_run_id, scratch_dir,
)
from urbanheatopt.gui.exporters import export_excel, export_pdf, export_png
from urbanheatopt.gui.contracts import (
    HANDOFF_INTERFACE_VERSION, ImportedData, SolveJob, SolveOutcome, SolveState,
)
from urbanheatopt.gui.theme import C, FONT_MONO
from urbanheatopt.gui.widgets.canvas_panel import VisualCanvas
from urbanheatopt.gui.widgets.common import StatusLED
from urbanheatopt.gui.widgets.log_panel import LogPanel
from urbanheatopt.gui.widgets.sidebar import SidePanel
from urbanheatopt.gui.widgets.stepper import WorkflowStepper
from urbanheatopt.paths import REPOSITORY_ROOT


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{SYSTEM_NAME} v{GUI_VERSION}")
        self.resize(1440, 900)
        self.setMinimumSize(1280, 800)

        central = QWidget()
        central.setObjectName("AppRoot")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter = self.splitter
        splitter.setChildrenCollapsible(False)
        self.side_panel = SidePanel()
        self.stepper = WorkflowStepper()
        self.canvas = VisualCanvas()
        self.log_panel = LogPanel()
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(12, 10, 12, 10)
        center_layout.setSpacing(8)
        center_layout.addWidget(self.stepper)
        center_layout.addWidget(self.canvas, 1)
        splitter.addWidget(self.side_panel)
        splitter.addWidget(center)
        splitter.addWidget(self.log_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([320, 840, 280])
        root.addWidget(splitter)
        self.setCentralWidget(central)

        self._build_menus()
        self._build_status_bar()

        # -- 阶段三状态 --------------------------------------------------
        self.pipeline = PipelineBackend(self)
        self.solver: SubprocessSolverBackend | MockSolverBackend | None = None
        self.last_outcome: SolveOutcome | None = None
        self.current_config: Path | None = None
        self.current_bundle: ImportedData | None = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._poll_solver)

        self._wire_backends()

    # -- chrome -----------------------------------------------------------

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("HeaderBar")
        header.setFixedHeight(56)
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 0, 16, 0)
        row.setSpacing(10)
        logo = QLabel("◆")
        logo.setStyleSheet(f"color: {C['primary']}; font-size: 18px;")
        name = QLabel(SYSTEM_NAME)
        name.setObjectName("AppName")
        version = QLabel(f"v{GUI_VERSION}")
        version.setObjectName("VersionBadge")
        row.addWidget(logo)
        row.addWidget(name)
        row.addWidget(version)
        row.addStretch(1)
        self.header_led = StatusLED()
        self.header_status = QLabel("求解状态：就绪")
        self.header_status.setObjectName("HeaderStatus")
        row.addWidget(self.header_led)
        row.addWidget(self.header_status)
        return header

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("文件(&F)")
        act_import = QAction("导入数据…", self)
        act_import.setShortcut(QKeySequence("Ctrl+O"))
        act_import.triggered.connect(self.side_panel.pick_file)
        act_demo = QAction("载入演示结果", self)
        act_demo.setShortcut(QKeySequence("Ctrl+D"))
        act_demo.triggered.connect(self.load_demo)
        act_quit = QAction("退出", self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_import)
        file_menu.addAction(act_demo)
        file_menu.addSeparator()
        file_menu.addAction(act_quit)

        data_menu = bar.addMenu("数据(&D)")
        act_validate = QAction("运行校验", self)
        act_validate.triggered.connect(self.side_panel.validate_requested)
        data_menu.addAction(act_validate)

        solve_menu = bar.addMenu("求解(&S)")
        act_solve = QAction("启动求解", self)
        act_solve.setShortcut(QKeySequence("Ctrl+R"))
        act_solve.triggered.connect(self.side_panel.solve_requested)
        act_cancel = QAction("终止求解", self)
        act_cancel.triggered.connect(self.side_panel.cancel_requested)
        solve_menu.addAction(act_solve)
        solve_menu.addAction(act_cancel)

        report_menu = bar.addMenu("报告(&R)")
        self.act_export_pdf = QAction("导出 QA 报告（PDF）", self)
        self.act_export_pdf.triggered.connect(lambda: self._on_export("pdf"))
        self.act_export_pdf.setToolTip("三页 QA 报告：关键指标与证据 / 站点对比图 / 方案明细表")
        self.act_export_excel = QAction("导出方案明细（Excel）", self)
        self.act_export_excel.triggered.connect(lambda: self._on_export("excel"))
        self.act_export_excel.setToolTip("三工作表：方案明细 / 求解摘要 / QA 结果")
        self.act_export_png = QAction("导出 Pareto 图（PNG）", self)
        self.act_export_png.triggered.connect(lambda: self._on_export("png"))
        self.act_export_png.setToolTip("候选站对比散点图（150 dpi）")
        for action in (self.act_export_pdf, self.act_export_excel, self.act_export_png):
            action.setEnabled(False)
            report_menu.addAction(action)

        help_menu = bar.addMenu("帮助(&H)")
        act_about = QAction("关于", self)
        act_about.triggered.connect(self._about)
        act_contract = QAction("接口版本", self)
        act_contract.triggered.connect(self._show_contract)
        help_menu.addAction(act_about)
        help_menu.addAction(act_contract)

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        self.status_led = StatusLED()
        self.status_text = QLabel("就绪")
        self.bundle_label = QLabel("bundle：—")
        self.run_label = QLabel("run：—")
        self.contract_label = QLabel(
            f"{HANDOFF_INTERFACE_VERSION} · {GUI_CONTRACT_VERSION}"
        )
        copyright_label = QLabel("© 2026 UrbanHeatOpt 项目组")
        for label in (self.bundle_label, self.run_label, self.contract_label):
            label.setFont(QFont(FONT_MONO, 9))
        bar.addWidget(self.status_led)
        bar.addWidget(self.status_text)
        bar.addPermanentWidget(self.bundle_label)
        bar.addPermanentWidget(self.run_label)
        bar.addPermanentWidget(self.contract_label)
        bar.addPermanentWidget(copyright_label)

    # -- 阶段三：真实接线 --------------------------------------------------

    def _wire_backends(self) -> None:
        self.pipeline.log_line.connect(lambda line: self.log_panel.append("validation", line))
        self.pipeline.progress.connect(self._on_pipeline_progress)
        self.pipeline.inspect_done.connect(self._on_inspect_done)
        self.pipeline.prepare_done.connect(self._on_prepare_done)
        self.pipeline.failed.connect(self._on_pipeline_failed)
        self.side_panel.file_selected.connect(self._on_file_selected)
        self.side_panel.validate_requested.connect(self._on_validate)
        self.side_panel.solve_requested.connect(self._on_solve)
        self.side_panel.cancel_requested.connect(self._on_cancel)

    def _on_file_selected(self, path: str) -> None:
        selected = Path(path)
        if selected.suffix.lower() not in (".yaml", ".yml"):
            self.log_panel.append(
                "validation", f"[文件] {path} 不是 .yaml 配置；请选择 guanggu_v2 配置文件"
            )
            self.side_panel.set_checks([("config", "error", "需选择 .yaml 配置文件")])
            return
        self.current_config = selected
        self.current_bundle = None
        self.side_panel.set_solve_enabled(False)
        self.side_panel.set_checks([
            ("config", "pass", f"配置文件：{selected.name}"),
            ("pipeline", "info", "自动校验（validate）运行中…"),
        ])
        self._reset_stepper(0)
        self.side_panel.progress_bar.setValue(0)
        self.header_led.set_state("running")
        self.header_status.setText("校验运行中（validate）…")
        self.status_led.set_state("running")
        self.status_text.setText("校验运行中…")
        self.log_panel.append(
            "validation", f"[文件] 已选择：{selected}（自动启动 validate）"
        )
        self.pipeline.start_inspect(selected)

    def _on_pipeline_progress(self, percent: int, line: str) -> None:
        self.side_panel.progress_bar.setValue(percent)
        self.side_panel.control_status.setText(line)

    def _apply_checks(self, data: ImportedData) -> None:
        self.side_panel.set_checks([
            (check.check_id, check.level.value, check.message) for check in data.checks
        ])

    def _on_inspect_done(self, data: ImportedData) -> None:
        self._apply_checks(data)
        self.stepper.set_state(0, "done" if data.valid else "failed")
        self.header_led.set_state("ok" if data.valid else "error")
        self.header_status.setText("校验完成" if data.valid else "校验未通过")
        self.status_led.set_state("ok" if data.valid else "error")
        self.status_text.setText(
            "校验完成（可运行校验与适配）" if data.valid else "校验未通过，见校验日志"
        )
        self.log_panel.append(
            "validation",
            f"[validate] 完成：{'通过' if data.valid else '未通过'}（{len(data.checks)} 项检查）",
        )

    def _on_prepare_done(self, data: ImportedData) -> None:
        self._apply_checks(data)
        if data.valid and data.bundle_path is not None:
            self.current_bundle = data
            self.side_panel.set_solve_enabled(True)
            self.side_panel.file_edit.setText(f"{data.source}（CaseBundle 已就绪）")
            self.stepper.set_state(0, "done")
            self.stepper.set_state(1, "done")
            self.bundle_label.setText(f"bundle：{data.bundle_id[:12]}…")
            self.header_led.set_state("ok")
            self.header_status.setText("校验与适配完成，可启动求解")
            self.status_led.set_state("ok")
            self.status_text.setText("准备完成 · 可求解")
            self.log_panel.append("validation", f"[prepare] CaseBundle 就绪：{data.bundle_path}")
        else:
            self.side_panel.set_solve_enabled(False)
            self.stepper.set_state(0, "failed")
            self.stepper.set_state(1, "failed")
            self.header_led.set_state("error")
            self.header_status.setText("校验与适配未通过")
            self.status_led.set_state("error")
            self.status_text.setText("准备未通过，见校验日志")
            self.log_panel.append(
                "validation",
                f"[prepare] 未通过：{'；'.join(c.message for c in data.checks if c.level.value != 'pass')[:200]}",
            )

    def _on_pipeline_failed(self, message: str) -> None:
        self.side_panel.set_checks([("pipeline", "error", f"流水线异常：{message[:80]}")])
        self.stepper.set_state(0, "failed")
        self.header_led.set_state("error")
        self.header_status.setText("流水线异常")
        self.status_led.set_state("error")
        self.status_text.setText("流水线异常")
        self.log_panel.append("validation", f"[错误] {message}")

    def _on_validate(self) -> None:
        if self.current_config is None:
            self.log_panel.append(
                "validation", "请先通过「文件 → 导入数据」选择 .yaml 配置文件"
            )
            self.side_panel.set_checks([("config", "error", "尚未选择配置文件")])
            return
        if self.pipeline.busy:
            self.log_panel.append("validation", "输入流水线仍在运行，请等待")
            return
        self.side_panel.set_solve_enabled(False)
        self.current_bundle = None
        self.side_panel.progress_bar.setValue(0)
        self.side_panel.control_status.setText("校验与适配运行中（数分钟）…")
        self._reset_stepper(0)
        self.header_led.set_state("running")
        self.header_status.setText("校验与适配运行中（prepare）…")
        self.status_led.set_state("running")
        self.status_text.setText("prepare 运行中…")
        self.log_panel.append(
            "validation", "[prepare] 启动完整输入流水线（validate + adapt + road case）"
        )
        self.pipeline.start_prepare(self.current_config)

    def _on_solve(self) -> None:
        if self.current_bundle is None or self.current_bundle.bundle_path is None:
            self.log_panel.append("solver", "CaseBundle 未就绪；请先运行「校验与适配」")
            return
        if self.solver is not None and self.solver.running:
            self.log_panel.append("solver", "求解已在运行")
            return
        params = self.side_panel.selected_scenario()
        solver_config = self.side_panel.selected_solver()
        run_id = make_run_id(params.mode, params.objective)
        job = SolveJob(
            bundle_path=self.current_bundle.bundle_path,
            params=params,
            solver=solver_config,
            run_id=run_id,
            output_dir=REPOSITORY_ROOT / SOLVE_OUTPUT_ROOT / run_id,
            config_path=scratch_dir() / "gui_config.yaml",
        )
        if os.environ.get("URBANHEAT_GUI_MOCK") == "1":
            backend = MockSolverBackend(self)
        else:
            backend = SubprocessSolverBackend(self)
        backend.log_line.connect(lambda line: self.log_panel.append("solver", line))
        try:
            backend.start(job)
        except (ValueError, OSError) as exc:
            self.log_panel.append("solver", f"[求解] 启动失败：{exc}")
            self.side_panel.set_checks([("solve_request", "error", f"请求无效：{exc}")])
            return
        self.solver = backend
        self.side_panel.set_solve_enabled(False)
        self._set_exports_enabled(False)
        self.side_panel.cancel_button.setEnabled(True)
        self.side_panel.progress_bar.setValue(0)
        self.side_panel.control_status.setText("子进程启动中…")
        self._reset_stepper(2)
        self.stepper.set_state(0, "done")
        self.stepper.set_state(1, "done")
        self.header_led.set_state("running")
        self.header_status.setText("求解运行中（HiGHS 子进程）…")
        self.status_led.set_state("running")
        self.status_text.setText("求解运行中…")
        self.run_label.setText(f"run：{run_id}")
        self.log_panel.append(
            "solver", f"[求解] 启动（方案 A）：run.py solve --run-id {run_id}"
        )
        self._poll_timer.start()

    def _poll_solver(self) -> None:
        if self.solver is None:
            self._poll_timer.stop()
            return
        event = self.solver.poll()
        if event is None:
            return
        self.side_panel.progress_bar.setValue(int(event.percent))
        self.side_panel.control_status.setText(
            f"{event.message}（{event.elapsed_seconds:.0f}s）"
        )
        if event.state == SolveState.RUNNING:
            return
        self._poll_timer.stop()
        self.side_panel.cancel_button.setEnabled(False)
        self.side_panel.set_solve_enabled(True)
        if event.state != SolveState.COMPLETED:
            self._set_exports_enabled(False)
        if event.state == SolveState.COMPLETED:
            outcome = self.solver.outcome()
            self.last_outcome = outcome
            self._draw_outcome(outcome)
            self._set_exports_enabled(True)
            self.stepper.set_state(2, "done")
            self.stepper.set_state(3, "done")
            self.header_led.set_state("ok")
            self.header_status.setText("求解完成 · qualified")
            self.status_led.set_state("ok")
            self.status_text.setText("完成 · qualified")
            self.log_panel.append(
                "solver",
                f"[求解] 完成：{outcome.run_id} qualified"
                f"（gap {outcome.kpis.certified_gap:.4%}，站点 {outcome.kpis.selected_site_id}）",
            )
        elif event.state == SolveState.CANCELLED:
            self.stepper.set_state(2, "failed")
            self.stepper.set_state(3, "pending")
            self.header_led.set_state("error")
            self.header_status.setText("求解已终止")
            self.status_led.set_state("error")
            self.status_text.setText("已终止")
            self.log_panel.append("solver", "[求解] 已终止（子进程被终止）")
        else:
            self.stepper.set_state(2, "failed")
            self.stepper.set_state(3, "pending")
            self.header_led.set_state("error")
            self.header_status.setText("求解失败")
            self.status_led.set_state("error")
            self.status_text.setText("失败 · 见求解日志")
            self.log_panel.append("solver", f"[求解] 失败：{event.message}")
        self.log_panel.append(
            "solver", f"[求解] 终态：{event.state.value}（{event.percent:.0f}%）"
        )

    def _on_cancel(self) -> None:
        if self.solver is None or not self.solver.running:
            self.log_panel.append("solver", "求解未运行，无需终止")
            return
        self.solver.cancel()
        self.status_text.setText("正在终止子进程…")
        self.log_panel.append("solver", "[求解] 终止请求已发送（kill 子进程）")

    def _reset_stepper(self, active_index: int) -> None:
        for index in range(4):
            self.stepper.set_state(index, "active" if index == active_index else "pending")

    # -- outcome rendering (阶段四) -----------------------------------------

    def _draw_outcome(self, outcome) -> None:
        """真实站点散点 → draw_sites；演示端点 → draw_pareto；空 → idle."""
        self.canvas.kpi_row.load_kpis(outcome.kpis)
        self.canvas.load_rows(outcome.table_rows)
        if not outcome.pareto:
            self.canvas.draw_idle()
        elif any("site" in p.labels for p in outcome.pareto):
            self.canvas.draw_sites(outcome.pareto)
        else:
            self.canvas.draw_pareto([
                (
                    p.point_id, p.mode, p.labels,
                    p.epsilon_kgCO2e_per_year / 1e6
                    if p.epsilon_kgCO2e_per_year is not None else None,
                    p.cost_cny_per_year / 1e4,
                    p.carbon_kgco2e_per_year / 1e3,
                    p.is_knee,
                )
                for p in outcome.pareto
            ])

    # -- exports (阶段四) ---------------------------------------------------

    def _set_exports_enabled(self, enabled: bool) -> None:
        for action in (self.act_export_pdf, self.act_export_excel, self.act_export_png):
            action.setEnabled(enabled)

    def _export_target(self, default_name: str, file_filter: str) -> Path | None:
        target_dir = REPOSITORY_ROOT / "runs/gui_exports"
        target_dir.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(
            self, "导出文件", str(target_dir / default_name), file_filter
        )
        return Path(path) if path else None

    def _on_export(self, kind: str) -> None:
        """导出分发：pdf / excel / png；仅求解完成后可用（菜单已禁用保护）."""
        if self.last_outcome is None:
            return
        if kind == "pdf":
            suffix, file_filter, exporter = "_qa_report.pdf", "PDF 报告 (*.pdf)", export_pdf
        elif kind == "excel":
            suffix, file_filter, exporter = "_site_detail.xlsx", "Excel 工作簿 (*.xlsx)", export_excel
        else:
            suffix, file_filter, exporter = "_pareto.png", "PNG 图片 (*.png)", export_png
        target = self._export_target(f"{self.last_outcome.run_id}{suffix}", file_filter)
        if target is None:
            return
        try:
            exporter(self.last_outcome, target)
        except Exception as exc:  # 导出失败仅提示，不影响界面
            QMessageBox.warning(self, "导出失败", f"导出失败：{exc}")
            return
        self.status_text.setText(f"已导出：{target.name}")
        self.log_panel.append("solver", f"[导出] {target}")

    # -- demo state (软著截图) ---------------------------------------------

    def load_demo(self) -> None:
        self._poll_timer.stop()
        self.solver = None
        self.last_outcome = None
        self._set_exports_enabled(False)
        self.side_panel.set_checks(
            [(check_id, level, f"✓ {text}") for check_id, level, text in mock_data.DEMO_CHECKS]
        )
        for index in range(4):
            self.stepper.set_state(index, "done")
        self.canvas.load_demo()
        self.log_panel.load_demo()
        self.side_panel.file_edit.setText(
            "configs/cases/guanggu_v2.yaml → case_bundle.json（演示数据）"
        )
        self.side_panel.progress_bar.setValue(100)
        self.side_panel.control_status.setText("已完成 · qualified=true（演示数据）")
        self.header_led.set_state("ok")
        self.header_status.setText("求解状态：已完成（演示数据）")
        self.status_led.set_state("ok")
        self.status_text.setText("完成 · qualified")
        self.bundle_label.setText(f"bundle：{mock_data.DEMO_BUNDLE_ID[:12]}…")
        self.run_label.setText(f"run：{mock_data.DEMO_RUN_ID}")

    # -- dialogs ------------------------------------------------------------

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "关于",
            f"<b>{SYSTEM_NAME}</b><br>"
            f"版本 v{GUI_VERSION}<br><br>"
            "框架：PySide6 (LGPL) + Matplotlib<br>"
            f"交接契约：{HANDOFF_INTERFACE_VERSION}<br>"
            f"GUI 契约：{GUI_CONTRACT_VERSION}<br><br>"
            "© 2026 UrbanHeatOpt 项目组",
        )

    def _show_contract(self) -> None:
        QMessageBox.information(
            self,
            "接口版本",
            f"交接契约（A/B/C）：{HANDOFF_INTERFACE_VERSION}\n"
            f"GUI 适配契约：{GUI_CONTRACT_VERSION}\n"
            "求解接线：方案 A · 子进程零侵入（QProcess + 轮询产物）",
        )


__all__ = ["MainWindow"]
