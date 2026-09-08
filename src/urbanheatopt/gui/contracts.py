"""Decoupling contract between the GUI and the solve backend.

Phase-1 template (docs/design/GUI_PHASE1_BLUEPRINT_CN.md §3), landed as code.
The GUI consumes only these shapes; backend wiring means implementing
DataImportHook / SolveBackendHook / ExportHook, or using the built-in
SubprocessSolverBackend (Phase 3, zero-invasion: runs ``run.py solve`` as a
child process and polls the artifact files the executor already writes).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol

CONTRACT_VERSION = "gui_contracts_1.0.0"
# GUI 仅消费 handoff_1.1.0 的 CaseBundle / SolveRequest / ResultBundle 交接对象。
HANDOFF_INTERFACE_VERSION = "handoff_1.1.0"


class WorkflowStage(str, Enum):
    INPUT = "input"
    VALIDATE = "validate"
    SOLVE = "solve"
    RESULTS = "results"


class SolveState(str, Enum):
    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CheckLevel(str, Enum):
    PASS = "pass"
    WARN = "warn"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Input schema (UI -> backend)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SolverConfig:
    """HiGHS 配置 —— 与 SolveRequest.solver 字段一一对应.

    多目标走 ε-约束法，无加权权重字段（2026-09-07 已确认）。
    """

    name: str = "highs"  # highs / gurobi / auto（不代表许可证可用）
    threads: int = 8
    random_seed: int = 0
    mip_gap: float = 0.01  # [0,1]：验收合成 gap 阈值
    time_limit_s: float | None = None  # 超时阈值；None = 不限时

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "threads": self.threads,
            "random_seed": self.random_seed,
            "mip_gap": self.mip_gap,
            "time_limit_s": self.time_limit_s,
        }


@dataclass(frozen=True, slots=True)
class ScenarioParams:
    """场景参数 —— 与 SolveRequest 顶层字段一一对应."""

    mode: str = "central"  # central / distributed / hybrid
    objective: str = "cost"  # cost / carbon
    epsilon_carbon_kg: float | None = None  # 仅 cost 目标使用的碳上限（ε-约束法）
    tes_enabled: bool = False
    allow_unserved: bool = False  # 生产固定 false，UI 禁用

    def to_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "objective": self.objective,
            "epsilon_carbon_kg": self.epsilon_carbon_kg,
            "tes_enabled": self.tes_enabled,
            "allow_unserved": False,
        }


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_id: str
    level: CheckLevel
    message: str


@dataclass(frozen=True, slots=True)
class ImportedData:
    """文件导入 + 校验适配结果."""

    source: Path  # 原始文件/配置
    kind: str  # yaml / csv / excel / json / case_bundle
    checks: tuple[CheckResult, ...]
    bundle_path: Path | None = None  # prepare 产出的 case_bundle.json
    bundle_id: str | None = None
    valid: bool = False


class DataImportHook(Protocol):
    """文件导入与校验接口.

    真实实现：``run_input_pipeline`` + ``CaseBundle.verify_artifacts`` +
    ``ready_report``（阶段三接线）。
    """

    def inspect(self, path: Path) -> ImportedData: ...

    def prepare(
        self, config_path: Path, *, run_id: str, output_root: Path | None = None
    ) -> ImportedData: ...


# ---------------------------------------------------------------------------
# HiGHS controller hook (solve lifecycle; UI never blocks on any of these)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SolveProgressEvent:
    stage: WorkflowStage
    state: SolveState
    percent: float  # 0..100
    message: str
    site_id: str | None = None  # 候选站子任务（集中/混合式 5 站枚举）
    elapsed_seconds: float = 0.0
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SolveJob:
    bundle_path: Path  # case_bundle.json 绝对路径
    params: ScenarioParams
    solver: SolverConfig
    run_id: str
    output_dir: Path
    config_path: Path | None = None  # run.py solve 要求的 --config（GUI 工作副本）


class SolveBackendHook(Protocol):
    """求解生命周期. UI 侧用 QTimer/QThread 调用 poll()，不得阻塞 UI 线程."""

    def start(self, job: SolveJob) -> None: ...

    def poll(self) -> SolveProgressEvent | None: ...  # 无新事件返回 None

    def cancel(self) -> None: ...  # 终止（子进程实现 = 杀进程）

    def outcome(self) -> SolveOutcome: ...  # 终态后调用


# ---------------------------------------------------------------------------
# Output schema (backend -> UI)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParetoPointView:
    """由 pareto.ParetoPoint（point_to_dict）转换而来，UI 只读."""

    point_id: str
    mode: str
    labels: tuple[str, ...]
    epsilon_kgCO2e_per_year: float | None
    cost_cny_per_year: float
    carbon_kgco2e_per_year: float
    is_knee: bool
    termination_condition: str
    reported_mip_gap: float | None


@dataclass(frozen=True, slots=True)
class KpiCardData:
    """四张 KPI 卡：最佳成本 / 最低碳排 / 膝点均衡 / 求解证据."""

    best_cost_cny_per_year: float | None
    min_carbon_kgco2e_per_year: float | None
    knee_point_id: str | None
    knee_cost_cny_per_year: float | None
    knee_carbon_kgco2e_per_year: float | None
    selected_site_id: str | None
    certified_gap: float | None
    termination_condition: str | None
    qa_passed: bool
    solver_executed: bool


@dataclass(frozen=True, slots=True)
class SolveOutcome:
    """求解完成后的完整结果视图（UI 只读，字段全部来自生产产物）."""

    run_id: str
    qualified: bool
    result_bundle_path: Path | None
    artifacts: Mapping[str, Path]  # role -> 文件（solver_log / independent_qa / ...）
    summary: Mapping[str, Any]  # solution_summary.json 原样透传
    kpis: KpiCardData
    pareto: tuple[ParetoPointView, ...] = ()
    table_rows: tuple[Mapping[str, Any], ...] = ()  # candidate_site_comparison.csv
    log_paths: tuple[Path, ...] = ()


class ExportHook(Protocol):
    """QA 报告 / 方案明细导出（PDF 用 QTextDocument+QPrinter；Excel 用 openpyxl）."""

    def export_excel(self, outcome: SolveOutcome, target: Path) -> Path: ...

    def export_pdf(self, outcome: SolveOutcome, target: Path) -> Path: ...

    def export_png(self, outcome: SolveOutcome, figure_name: str, target: Path) -> Path: ...


__all__ = [
    "CONTRACT_VERSION",
    "HANDOFF_INTERFACE_VERSION",
    "WorkflowStage",
    "SolveState",
    "CheckLevel",
    "SolverConfig",
    "ScenarioParams",
    "CheckResult",
    "ImportedData",
    "DataImportHook",
    "SolveProgressEvent",
    "SolveJob",
    "SolveBackendHook",
    "ParetoPointView",
    "KpiCardData",
    "SolveOutcome",
    "ExportHook",
]
