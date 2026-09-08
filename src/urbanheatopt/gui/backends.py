"""阶段三真实接线：子进程驱动的输入流水线与求解生命周期（方案 A · 零侵入）。

- PipelineBackend        ``run.py validate|prepare`` 子进程；解析 run_summary.json
- SubprocessSolverBackend ``run.py solve`` 子进程；轮询 task_status.json；kill 终止；
                         run_manifest.json / result_bundle.json 定局
- MockSolverBackend      QTimer 驱动生命周期（无子进程 UI 联调，URBANHEAT_GUI_MOCK=1）

全部为 QObject 信号驱动，GUI 线程永不阻塞；本模块不 import 任何 pyomo 链。
"""
from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QElapsedTimer, QObject, QProcess, QProcessEnvironment, QTimer, Signal,
)

from urbanheatopt.data.bundles import (
    INTERFACE_VERSION, CaseBundle, ResultBundle, SolveRequest,
)
from urbanheatopt.gui import mock_data
from urbanheatopt.gui.contracts import (
    CheckLevel, CheckResult, ImportedData, KpiCardData, ParetoPointView,
    SolveJob, SolveOutcome, SolveProgressEvent, SolveState, WorkflowStage,
)
from urbanheatopt.paths import REPOSITORY_ROOT

# 与 optimization/solve_executor 的冻结常量保持一致（此处硬编码以避开 pyomo 导入链）
MODEL_PROFILE = "compact_five_tree_fullseason_v2"
OPTIMIZATION_SCOPE = "five_candidate_shortest_path_trees"

INPUT_OUTPUT_ROOT = "runs/gui_inputs"   # run_input_pipeline 要求位于仓库 work/ 或 runs/
SOLVE_OUTPUT_ROOT = "runs/gui_solves"   # run.py solve 要求位于仓库 runs/ 目录
SCRATCH = REPOSITORY_ROOT / "work/gui_scratch"

_CANDIDATE_COUNT = 5


def scratch_dir(*parts: str) -> Path:
    path = SCRATCH.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_run_id(mode: str, objective: str) -> str:
    """runs/gui_solves 下唯一 run_id（executor 校验 [A-Za-z0-9_-]{1,100}）."""
    stamp = datetime.now().strftime("%Y%m%d")
    base = f"GUI_{mode.upper()}_{objective.upper()}_{stamp}"
    index = 1
    while (REPOSITORY_ROOT / SOLVE_OUTPUT_ROOT / f"{base}_R{index}").exists():
        index += 1
    return f"{base}_R{index}"


def _child_env() -> QProcessEnvironment:
    env = QProcessEnvironment.systemEnvironment()
    env.insert("PYTHONUTF8", "1")  # 子进程 stdout 统一 UTF-8，避免 GBK 乱码
    return env


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# 输入流水线（validate / prepare）
# ---------------------------------------------------------------------------


class PipelineBackend(QObject):
    """``run.py validate|prepare`` 子进程封装；解析产物并转为 ImportedData."""

    log_line = Signal(str)
    progress = Signal(int, str)   # 阶段百分比 + 文本（来自 [n/4] 打印）
    inspect_done = Signal(object)  # ImportedData
    prepare_done = Signal(object)  # ImportedData
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._command = ""
        self._config_path: Path | None = None
        self._run_id = ""
        self._stderr_tail: list[str] = []

    @property
    def busy(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    @staticmethod
    def config_copy(config_path: Path) -> Path:
        """工作副本：desktop_gap_report=false，避免 GUI 运行向桌面发布报告."""
        import yaml

        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("配置文件不是 YAML 映射")
        config["desktop_gap_report"] = False
        target = scratch_dir() / "gui_config.yaml"
        target.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
        return target

    def start_inspect(self, config_path: Path) -> None:
        self._launch("validate", config_path)

    def start_prepare(self, config_path: Path) -> None:
        self._launch("prepare", config_path)

    def _launch(self, command: str, config_path: Path) -> None:
        if self.busy:
            self.failed.emit("输入流水线仍在运行，请等待完成")
            return
        try:
            config = self.config_copy(config_path)
        except (OSError, ValueError) as exc:
            self.failed.emit(f"配置文件副本失败：{exc}")
            return
        self._command = command
        self._config_path = config_path.resolve()
        self._run_id = f"GUI_{command.upper()}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        self._stderr_tail.clear()
        proc = QProcess(self)
        proc.setWorkingDirectory(str(REPOSITORY_ROOT))
        proc.setProcessEnvironment(_child_env())
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        proc.readyReadStandardOutput.connect(self._read_stdout)
        proc.readyReadStandardError.connect(self._read_stderr)
        proc.errorOccurred.connect(self._on_proc_error)
        proc.finished.connect(self._finalize)
        self._proc = proc
        proc.start(sys.executable, [
            str(REPOSITORY_ROOT / "run.py"), command,
            "--config", str(config),
            "--run-id", self._run_id,
            "--output-root", INPUT_OUTPUT_ROOT,
        ])

    def _read_stdout(self) -> None:
        raw = bytes(self._proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            self.log_line.emit(line)
            match = re.match(r"^\[(\d)/4\]", line)
            if match:
                self.progress.emit(int(match.group(1)) * 25, line)

    def _read_stderr(self) -> None:
        raw = bytes(self._proc.readAllStandardError()).decode("utf-8", errors="replace")
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            self._stderr_tail.append(line)
            self.log_line.emit(f"[stderr] {line}")

    def _on_proc_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.failed.emit(f"子进程无法启动（{sys.executable}）")

    def _finalize(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        if self._proc is None:
            return
        self._proc.deleteLater()
        self._proc = None
        output = REPOSITORY_ROOT / INPUT_OUTPUT_ROOT / self._run_id
        summary = _read_json(output / "run_summary.json")
        if summary is None:
            tail = "；".join(self._stderr_tail[-6:])
            self.failed.emit(f"输入流水线异常退出（code={exit_code}）：{tail or '无错误输出'}")
            return
        data = self._to_imported(summary, output)
        self.progress.emit(100, f"[{self._command}] 完成")
        (self.prepare_done if self._command == "prepare" else self.inspect_done).emit(data)

    def _to_imported(self, summary: dict[str, Any], output: Path) -> ImportedData:
        bundle_path = output / "case_bundle.json"
        bundle_id = None
        if self._command == "prepare" and bundle_path.is_file():
            try:
                bundle_id = CaseBundle.read(bundle_path).bundle_id
            except ValueError:
                bundle_id = None
        valid = bool(
            summary.get("input_valid") and summary.get("parameter_valid")
            and summary.get("input_hashes_unchanged") and not summary.get("errors")
        )
        if self._command == "prepare":
            valid = valid and bundle_path.is_file() and bool(
                summary.get("road_case_ready") and summary.get("solver_pipeline_ready")
            )
        return ImportedData(
            source=self._config_path,
            kind="yaml",
            checks=tuple(self._build_checks(summary)),
            bundle_path=bundle_path if (self._command == "prepare" and bundle_path.is_file()) else None,
            bundle_id=bundle_id,
            valid=valid,
        )

    @staticmethod
    def _build_checks(summary: dict[str, Any]) -> list[CheckResult]:
        def gate(check_id: str, label: str, ready: Any, *, fatal: bool = False) -> CheckResult:
            level = CheckLevel.PASS if ready else (CheckLevel.ERROR if fatal else CheckLevel.WARN)
            return CheckResult(check_id, level, f"{label}：{'通过' if ready else '未通过'}")

        checks = [
            gate("input_valid", "输入源完整", summary.get("input_valid"), fatal=True),
            gate("parameter_valid", "经济参数有效", summary.get("parameter_valid"), fatal=True),
            gate("canonical_valid", "62栋×2160h 适配复验", summary.get("canonical_valid")),
            gate("snapshot_complete", "快照完整", summary.get("snapshot_complete")),
            gate("input_hashes_unchanged", "输入哈希稳定", summary.get("input_hashes_unchanged"), fatal=True),
            gate("network_ready", "网络产物就绪", summary.get("network_ready")),
            gate("road_case_ready", "RoadCase 构建", summary.get("road_case_ready")),
            gate("solver_pipeline_ready", "求解执行器接通", summary.get("solver_pipeline_ready")),
            gate("research_solve_ready", "研究求解门禁", summary.get("research_solve_ready")),
        ]
        for error in (summary.get("errors") or [])[:3]:
            checks.append(CheckResult("pipeline_error", CheckLevel.ERROR, f"错误：{str(error)[:96]}"))
        return checks


# ---------------------------------------------------------------------------
# 求解生命周期（方案 A：子进程 + 轮询产物）
# ---------------------------------------------------------------------------


def _cast_number(value: str) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def build_outcome(result: ResultBundle, output_dir: Path) -> SolveOutcome:
    """ResultBundle + 运行目录产物 → GUI 只读 SolveOutcome."""
    payload = result.to_dict()
    artifacts = {row["role"]: Path(row["path"]) for row in payload["artifacts"]}
    summary = _read_json(output_dir / "solution_summary.json") or {}
    evidence = payload["solve_evidence"]
    qa = payload.get("qa") or {}
    rows: list[dict[str, Any]] = []
    comparison = output_dir / "candidate_site_comparison.csv"
    if comparison.is_file():
        with comparison.open(encoding="utf-8-sig", newline="") as stream:
            rows = [
                {key: _cast_number(value) for key, value in row.items()}
                for row in csv.DictReader(stream)
            ]
    # 真实候选站散点：每个 qualified 站点一个点，选中站即膝点高亮。
    selected_site = evidence.get("selected_site_id")
    sites: list[ParetoPointView] = []
    for row in rows:
        if row.get("status") != "qualified":
            continue
        site_id = str(row.get("site_id") or "")
        reported_gap = row.get("reported_gap")
        sites.append(
            ParetoPointView(
                point_id=f"{site_id}:{row.get('mode')}",
                mode=str(row.get("mode") or ""),
                labels=("site",),
                epsilon_kgCO2e_per_year=None,
                cost_cny_per_year=float(row["annual_real_cost_CNY_per_year"]),
                carbon_kgco2e_per_year=float(row["annual_operating_carbon_kgCO2e_per_year"]),
                is_knee=bool(site_id and site_id == selected_site),
                termination_condition=str(row.get("termination_condition") or ""),
                reported_mip_gap=(
                    float(reported_gap) if reported_gap not in (None, "") else None
                ),
            )
        )
    knee_site = next((p for p in sites if p.is_knee), None)
    kpis = KpiCardData(
        best_cost_cny_per_year=float(evidence["incumbent"]),
        min_carbon_kgco2e_per_year=(
            min(p.carbon_kgco2e_per_year for p in sites) if sites else None
        ),
        knee_point_id=evidence.get("selected_site_id"),
        knee_cost_cny_per_year=(knee_site.cost_cny_per_year if knee_site else None),
        knee_carbon_kgco2e_per_year=(knee_site.carbon_kgco2e_per_year if knee_site else None),
        selected_site_id=evidence.get("selected_site_id"),
        certified_gap=float(evidence["certified_gap"]),
        termination_condition=payload["termination_condition"],
        qa_passed=qa.get("passed") is True,
        solver_executed=bool(payload["solver_executed"]),
    )
    log_paths = (
        artifacts.get("solver_log"),
        artifacts.get("run_manifest"),
        artifacts.get("independent_qa"),
    )
    return SolveOutcome(
        run_id=payload["run_id"],
        qualified=True,
        result_bundle_path=output_dir / "result_bundle.json",
        artifacts=artifacts,
        summary=summary,
        kpis=kpis,
        pareto=tuple(sites),
        table_rows=tuple(rows),
        log_paths=tuple(path for path in log_paths if path is not None),
    )


def _failure_reason(output_dir: Path, statuses: list[dict[str, Any]], manifest: dict[str, Any]) -> str:
    errors = [str(item.get("error")) for item in statuses if item.get("error")]
    if errors:
        return f"{len(errors)} 个候选站未合格：{errors[0][:120]}"
    comparison = output_dir / "candidate_site_comparison.csv"
    if comparison.is_file():
        try:
            with comparison.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            for row in rows:
                if row.get("status") != "qualified" and row.get("error"):
                    return row["error"][:120]
        except OSError:
            pass
    return f"result_qualified=false（qualified_candidate_count={manifest.get('qualified_candidate_count')}）"


class SubprocessSolverBackend(QObject):
    """方案 A：``run.py solve`` 子进程 + 产物轮询 + kill 终止."""

    log_line = Signal(str)
    finished = Signal(object)  # SolveOutcome | None（qualified 时为对象）

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._job: SolveJob | None = None
        self._request_path: Path | None = None
        self._elapsed = QElapsedTimer()
        self._cancel_requested = False
        self._terminal = False
        self._outcome: SolveOutcome | None = None
        self._stderr_tail: list[str] = []
        self._pending: list[SolveProgressEvent] = []

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.state() == QProcess.ProcessState.Running

    def start(self, job: SolveJob) -> None:
        if self.running:
            raise RuntimeError("求解已在运行")
        # CaseBundle / SolveRequest 均为纯标准库交接对象；非法配置在此拦截，不进子进程
        bundle = CaseBundle.read(job.bundle_path)
        request = SolveRequest.from_dict({
            "interface_version": INTERFACE_VERSION,
            "case_bundle_id": bundle.bundle_id,
            "model_profile": MODEL_PROFILE,
            "optimization_scope": OPTIMIZATION_SCOPE,
            "mode": job.params.mode,
            "objective": job.params.objective,
            "epsilon_carbon_kg": job.params.epsilon_carbon_kg,
            "tes_enabled": job.params.tes_enabled,
            "allow_unserved": False,
            "solver": job.solver.to_payload(),
        })
        request_path = scratch_dir("requests") / f"{job.run_id}.json"
        request.write(request_path, exclusive=False)
        config = job.config_path or (scratch_dir() / "gui_config.yaml")
        if not config.is_file():
            raise ValueError("缺少求解配置文件（请先运行校验与适配）")
        self._job = job
        self._request_path = request_path
        self._cancel_requested = False
        self._terminal = False
        self._outcome = None
        self._stderr_tail.clear()
        self._pending.clear()
        self._elapsed.restart()
        proc = QProcess(self)
        proc.setWorkingDirectory(str(REPOSITORY_ROOT))
        proc.setProcessEnvironment(_child_env())
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        proc.readyReadStandardOutput.connect(self._read_stdout)
        proc.readyReadStandardError.connect(self._read_stderr)
        proc.errorOccurred.connect(self._on_proc_error)
        proc.start(sys.executable, [
            str(REPOSITORY_ROOT / "run.py"), "solve",
            "--config", str(config),
            "--bundle", str(job.bundle_path.resolve()),
            "--run-id", job.run_id,
            "--output-root", SOLVE_OUTPUT_ROOT,
            "--request", str(request_path),
        ])
        self._proc = proc

    def _read_stdout(self) -> None:
        raw = bytes(self._proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in raw.splitlines():
            line = line.strip()
            if line:
                self.log_line.emit(line)

    def _read_stderr(self) -> None:
        raw = bytes(self._proc.readAllStandardError()).decode("utf-8", errors="replace")
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            self._stderr_tail.append(line)
            self.log_line.emit(f"[stderr] {line}")

    def _on_proc_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart and not self._terminal:
            self._terminal = True
            self._pending.append(SolveProgressEvent(
                WorkflowStage.SOLVE, SolveState.FAILED, 100.0,
                f"子进程无法启动（{sys.executable}）",
                elapsed_seconds=self._elapsed.elapsed() / 1000.0,
            ))
            self.finished.emit(None)

    def poll(self) -> SolveProgressEvent | None:
        if self._proc is None:
            return None
        if self._pending:
            return self._pending.pop(0)
        if self._terminal:
            return None
        proc_running = self._proc.state() != QProcess.ProcessState.NotRunning
        statuses = self._collect_statuses()
        if proc_running:
            total = self._total_sites()
            finished = sum(1 for item in statuses if item.get("status") != "running")
            percent = 5.0 + 65.0 * finished / total
            latest = next((item for item in statuses if item.get("status") != "running"), None)
            if latest is None:
                message = f"候选站求解中（0/{total}）" if statuses else "子进程启动中…"
                site_id = None
            else:
                site_id = str(latest["site_id"])
                message = f"候选站 {finished}/{total} 定局（{site_id}：{latest['status']}）"
            return SolveProgressEvent(
                WorkflowStage.SOLVE, SolveState.RUNNING, percent, message,
                site_id=site_id, elapsed_seconds=self._elapsed.elapsed() / 1000.0,
            )
        return self._finalize(statuses)

    def _collect_statuses(self) -> list[dict[str, Any]]:
        if self._job is None:
            return []
        root = self._job.output_dir / "candidate_tasks"
        if not root.is_dir():
            return []
        statuses = []
        for path in sorted(root.iterdir()):
            if path.is_dir():
                payload = _read_json(path / "task_status.json")
                if payload is not None:
                    statuses.append(payload)
        return statuses

    def _total_sites(self) -> int:
        if self._job is not None and self._job.params.mode == "distributed":
            return 1
        return _CANDIDATE_COUNT

    def _finalize(self, statuses: list[dict[str, Any]]) -> SolveProgressEvent:
        self._terminal = True
        exit_code = self._proc.exitCode()
        crashed = self._proc.exitStatus() != QProcess.ExitStatus.NormalExit
        elapsed = self._elapsed.elapsed() / 1000.0
        detail: dict[str, Any] = {}
        if self._cancel_requested:
            state, message, percent = (
                SolveState.CANCELLED, "求解已终止（子进程被终止）", 100.0,
            )
        elif crashed or exit_code != 0:
            tail = "；".join(self._stderr_tail[-6:])
            state, message, percent = (
                SolveState.FAILED,
                f"求解子进程异常退出（code={exit_code}）：{tail or '无错误输出'}",
                100.0,
            )
        else:
            manifest = _read_json(self._job.output_dir / "run_manifest.json") or {}
            if manifest.get("result_qualified") is True:
                try:
                    result_path = self._job.output_dir / "result_bundle.json"
                    self._outcome = build_outcome(ResultBundle.read(result_path), self._job.output_dir)
                    state, message, percent = (
                        SolveState.COMPLETED,
                        "求解完成：ResultBundle 已认证（QA 独立复算通过）",
                        100.0,
                    )
                    detail = {"qualified": True}
                except (OSError, ValueError) as exc:
                    state, message, percent = SolveState.FAILED, f"ResultBundle 解析失败：{exc}", 100.0
            else:
                state, message, percent = (
                    SolveState.FAILED,
                    f"求解未合格：{_failure_reason(self._job.output_dir, statuses, manifest)}",
                    100.0,
                )
        event = SolveProgressEvent(WorkflowStage.SOLVE, state, percent, message,
                                   elapsed_seconds=elapsed, detail=detail)
        self.finished.emit(self._outcome)
        return event

    def cancel(self) -> None:
        if self._proc is None or self._terminal:
            return
        self._cancel_requested = True
        self._proc.kill()
        self._proc.waitForFinished(1500)

    def outcome(self) -> SolveOutcome:
        if not self._terminal or self._outcome is None:
            raise ValueError("求解尚未产出合格 ResultBundle")
        return self._outcome


class MockSolverBackend(QObject):
    """QTimer 驱动的假求解生命周期；仅用于 UI 联调（URBANHEAT_GUI_MOCK=1）."""

    log_line = Signal(str)
    finished = Signal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._tick)
        self._job: SolveJob | None = None
        self._sites: list[str] = []
        self._index = 0
        self._elapsed = QElapsedTimer()
        self._terminal = False
        self._cancelled = False
        self._outcome: SolveOutcome | None = None
        self._pending: list[SolveProgressEvent] = []

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def start(self, job: SolveJob) -> None:
        self._job = job
        total = 1 if job.params.mode == "distributed" else _CANDIDATE_COUNT
        self._sites = [f"candidate_station_{index:02d}" for index in range(1, total + 1)]
        self._index = 0
        self._elapsed.restart()
        self._terminal = False
        self._cancelled = False
        self._outcome = None
        self._pending = [SolveProgressEvent(
            WorkflowStage.SOLVE, SolveState.RUNNING, 1.0,
            "Mock：子进程启动（演示）", elapsed_seconds=0.0,
        )]
        self._timer.start()

    def poll(self) -> SolveProgressEvent | None:
        if self._pending:
            return self._pending.pop(0)
        return None

    def _tick(self) -> None:
        if self._job is None or self._terminal:
            return
        self._index += 1
        total = len(self._sites)
        if self._index <= total:
            site = self._sites[self._index - 1]
            percent = 5.0 + 65.0 * self._index / total
            self._pending.append(SolveProgressEvent(
                WorkflowStage.SOLVE, SolveState.RUNNING, percent,
                f"Mock：候选站 {self._index}/{total} 完成（{site}：optimal）",
                site_id=site, elapsed_seconds=self._elapsed.elapsed() / 1000.0,
            ))
            self.log_line.emit(f"[mock] {site}: optimal (gap 0.82%)")
            return
        self._timer.stop()
        self._terminal = True
        if self._cancelled:
            self._pending.append(SolveProgressEvent(
                WorkflowStage.SOLVE, SolveState.CANCELLED, 100.0,
                "Mock：求解已终止", elapsed_seconds=self._elapsed.elapsed() / 1000.0,
            ))
            self.finished.emit(None)
            return
        kpis = mock_data.DEMO_KPIS_CARD
        pareto = tuple(
            ParetoPointView(
                point_id=point[0], mode=point[1], labels=tuple(point[2]),
                epsilon_kgCO2e_per_year=(point[3] * 1e6) if point[3] is not None else None,
                cost_cny_per_year=point[4] * 1e4,
                carbon_kgco2e_per_year=point[5] * 1e3,
                is_knee=point[6],
                termination_condition="optimal",
                reported_mip_gap=0.0082,
            )
            for point in mock_data.DEMO_PARETO_POINTS
        )
        self._outcome = SolveOutcome(
            run_id=self._job.run_id, qualified=True, result_bundle_path=None,
            artifacts={}, summary={}, kpis=kpis, pareto=pareto,
        )
        self._pending.append(SolveProgressEvent(
            WorkflowStage.SOLVE, SolveState.COMPLETED, 100.0,
            "Mock：求解完成（演示数据）",
            elapsed_seconds=self._elapsed.elapsed() / 1000.0,
        ))
        self.finished.emit(self._outcome)

    def cancel(self) -> None:
        if self.running:
            self._cancelled = True

    def outcome(self) -> SolveOutcome:
        if self._outcome is None:
            raise ValueError("Mock 求解尚未定局")
        return self._outcome


__all__ = [
    "INPUT_OUTPUT_ROOT",
    "MODEL_PROFILE",
    "OPTIMIZATION_SCOPE",
    "SOLVE_OUTPUT_ROOT",
    "MockSolverBackend",
    "PipelineBackend",
    "SubprocessSolverBackend",
    "build_outcome",
    "make_run_id",
    "scratch_dir",
]
