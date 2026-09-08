# 阶段一：界面蓝图与原型架构设计（GUI 软著前端）

- 文档版本：`gui_phase1_blueprint_1.0.0`
- 日期：2026-09-07
- 状态：阶段一全部决策已确认（2026-09-07）；阶段二脚手架已落地并通过 18 项离屏验证（2026-09-07）
- 修订记录：§2.3 状态色与图表系列色于阶段二按 dataviz 校验器校准（`--mode dark --surface 14233C` 全对配通过，含色盲安全），膝点以形状+白描边+直接标签高亮而非第四色
- 相关契约：`handoff_1.1.0`（[HANDOFF_CONTRACT_CN.md](../architecture/HANDOFF_CONTRACT_CN.md)）、[B 组三层接线后续任务书](../team/UrbanHeatOpt_B_三层接线后续任务书.md)

---

## 0. 背景与对齐结论

界面服务于 B 组三层接线完成后的生产求解链，对接以下**已存在**实体，接口模板全部按其真实字段定义：

| 后端实体（已存在） | 位置 | 界面消费方式 |
| --- | --- | --- |
| `CaseBundle` / `SolveRequest` / `ResultBundle`（`handoff_1.1.0`） | `src/urbanheatopt/data/bundles.py` | 导入/校验/结果证据 |
| `solve_executor`（5 候选站枚举 + 合成 gap + QA 门禁） | `src/urbanheatopt/optimization/solve_executor.py` | HiGHS 求解生命周期 |
| `ParetoPoint` / 膝点标记 / 代表点选择 | `src/urbanheatopt/optimization/pareto.py` | Pareto 散点图 + KPI 卡 |
| 导出明细 + 独立 QA | `src/urbanheatopt/qa/road_results.py` | 方案明细表 / QA 报告 |
| CLI 生产入口（`run.py solve`） | `src/urbanheatopt/cli.py` | 求解子进程接线（见 §4） |

**关键结论**：`SolveRequest.solver` 的真实字段是 `mip_gap / time_limit_s / threads / random_seed / name`，**没有加权权重字段**——多目标方法为 ε-约束法（`epsilon_carbon_kg`）。HiGHS 配置面板按真实字段设计，ε 值归入场景参数。（已与用户确认，2026-09-07）

---

## 1. 技术栈选型（已确认：PySide6）

| 维度 | PySide6（Qt Widgets + QSS）✅选定 | PyQt6 | Streamlit |
| --- | --- | --- | --- |
| 许可 | **LGPL**（Qt 官方绑定，软著/商用零风险） | GPLv3（商用需 Riverbank 付费授权） | Apache-2.0 |
| 软著观感 | 原生桌面工业软件（菜单栏/标题栏/状态栏/多面板） | 同左 | 浏览器 Web 质感，无原生菜单栏/标题栏 |
| 异步求解 | QThread + 信号槽成熟；可起子进程 | 同左 | rerun 模型，长求解进度推送别扭 |
| 图表 | Matplotlib `FigureCanvasQTAgg`（项目已依赖） | 同左 | Plotly 体验好，导出/嵌入受限 |
| 导出 | QTextDocument+QPrinter 原生 PDF；openpyxl Excel | 同左 | 仅 HTML/下载 |
| 部署 | PyInstaller 单机 exe（约 150MB） | 同左 | 需终端起服务 |

**选定方案**：PySide6 6.7+（Qt Widgets + QSS 扁平化），加入现有 `urbanheatopt_env` conda 环境。

- 图表：Matplotlib `FigureCanvasQTAgg`（与 `reporting/reference_figures.py` 同源风格）；否决 Plotly（需 QtWebEngine，+200MB，违背轻量约束）。
- 导出：PDF 用 QTextDocument+QPrinter；Excel 用 pandas/openpyxl；PNG 用 `fig.savefig`。
- 打包：PyInstaller（阶段四后可选）。

---

## 2. 界面栅格布局与视觉规格

### 2.1 整体线框（默认 1440×900，最小 1280×800）

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ ◆ UrbanHeatOpt · 城市供热多目标优化求解平台  [v1.0.0]        ● 求解状态：就绪   │ 56px 标题栏
│ 文件(F)   数据(D)   求解(S)   报告(R)   帮助(H)                                    │ 28px 菜单栏
├──────────────┬─────────────────────────────────────────────┬───────────────────┤
│ ① 数据输入    │  工作流：①数据输入 → ②校验适配 → ③HiGHS求解 → ④结果输出           │ 72px 步骤条
│ [选择文件…]   │  ┌─────────────────────────────────────────┐ │  ┌─────────────┐ │
│  类型过滤     │  │  Pareto 经济-碳排最优前沿（可缩放/平移）   │ │  │ 校验日志     │ │
│ ② 场景参数    │  │   ● 成本端点  ● 碳端点  ● ε点  ★ 膝点    │ │  │ (等宽自动滚动)│ │
│  模式/目标/ε  │  │        Matplotlib FigureCanvas          │ │  ├─────────────┤ │
│ ③ 校验状态    │  └─────────────────────────────────────────┘ │  │ 求解日志     │ │
│  ✓/⚠/✗ Badge │  ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐     │  │ (HiGHS 输出)│ │
│ ④ HiGHS 配置 │  │最佳成本│ │最低碳排│ │膝点均衡│ │求解证据│     │  ├─────────────┤ │
│  超时/容差/线程│  └───────┘ └───────┘ └───────┘ └───────┘     │  │ QA 摘要     │ │
│ ⑤ 求解控制    │  ┌─────────────────────────────────────────┐ │  └─────────────┘ │
│ [启动] [终止] │  │ 方案明细表 QTableView（站点/模式/成本/碳/gap）│ │                  │
│ ▓▓▓▓▓▓░░ 62% │  └─────────────────────────────────────────┘ │                  │
├──────────────┴─────────────────────────────────────────────┴───────────────────┤
│ ● 就绪 │ bundle 3f2a…c9 │ run B_COST_20260907_R1 │ handoff_1.1.0 │ © 2026 项目组 │ 28px 状态栏
└─────────────────────────────────────────────────────────────────────────────────┘
     320px                 弹性拉伸（≥800px）                           280px
```

### 2.2 区域规格表（12 列概念栅格：左 3 列 / 中 6 列 / 右 3 列，QSplitter 可拖）

| 区域 | 尺寸 | 内容 | 控件 |
| --- | --- | --- | --- |
| 标题栏 Header | 56px 固定 | 系统名+版本徽章（软著截图核心要素）、右侧求解状态指示灯 | QWidget + QLabel |
| 菜单栏 MenuBar | 28px | 文件(导入/导出/退出)·数据(校验/准备)·求解(启动/终止/配置)·报告(PDF/Excel/PNG)·帮助(关于/接口版本) | QMenuBar |
| 左面板 SidePanel | 320px | 五张卡片：①数据输入 ②场景参数（模式/目标/ε/TES）③校验状态（Badge 列表+运行校验）④HiGHS 配置（超时/容差/线程/种子）⑤求解控制（启动/终止+进度条） | QScrollArea + 卡片 |
| 中央顶部 | 72px | 四步工作流步骤条：数据输入→校验适配→HiGHS求解→结果输出（完成=绿勾，进行中=蓝脉冲，失败=红叉） | 自定义 QWidget |
| 中央上部 Visual Canvas | 弹性(2/3) | Pareto 散点图：成本端点蓝、碳端点绿、ε点紫、**膝点黄星+描边**，图例+悬停数值+缩放 | FigureCanvasQTAgg |
| KPI 卡片行 | 92px | 4 张指标卡：最佳成本 / 最低碳排 / 膝点均衡 / 求解证据（incumbent·best bound·certified gap·QA） | QFrame 卡片 |
| 中央下部 | 弹性(1/3) | 方案明细表（站点/模式/目标值/gap/终止条件/选中标记） | QTableView |
| 右面板 | 280px | 日志 Tab：校验日志 / 求解日志 / QA 摘要 | QTabWidget + QPlainTextEdit |
| 状态栏 Footer | 28px | 状态指示灯+文本；bundle_id 前 12 位；run_id；接口版本 `handoff_1.1.0 · gui_contracts_1.0.0`；版权 | QStatusBar |

### 2.3 视觉规格：科学暗蓝主题（QSS Token 表）

| Token | 值 | 用途 |
| --- | --- | --- |
| `--bg-window` | `#0C1524` | 窗口底 |
| `--bg-panel` / `--bg-card` / `--bg-input` | `#101D31` / `#14233C` / `#0F1B2E` | 面板 / 卡片 / 输入控件 |
| `--border` / `--border-strong` | `#22355A` / `#2E4A7A` | 1px 描边 |
| `--primary` / `--primary-press` | `#3D7EFF` / `#2F63CC` | 主按钮、选中态 |
| `--accent` | `#2FD4E8` | 膝点/高亮强调 |
| `--text` / `--text-muted` | `#E7EEF9` / `#8DA3C4` | 正文 / 次要文本 |
| `--good` / `--warning` / `--critical` / `--info` | `#0CA30C` / `#FAB219` / `#D03B3B` / `#3D7EFF` | 固定状态色（dataviz 暗底验证通过，附图标+文字，绝不作为装饰系列色） |
| 图表系列 | 成本端点 `#199E70`、碳端点 `#D95926`、ε点 `#3987E5`、膝点=ε点色+★白描边+直接标签、网格 `#22355A`@35% | Pareto 图专用（阶段二按校验器校准：散点图全对配系列上限 3 色） |

**字体**：界面「微软雅黑 UI」12/13px，标题 16px SemiBold，指标数字 Consolas，日志 Consolas 11px；matplotlib 内中文字体同「微软雅黑」。

**组件规范**：卡片圆角 8px/内边距 12px；按钮高 32px 圆角 6px（主按钮填充、危险按钮 error 描边）；Badge 胶囊形（pass/warn/error 三色）；状态指示灯 8px 圆形（绿常亮=完成、蓝脉冲=运行、红=失败、灰=待机）；进度条 6px 圆角。

**软著截图要点**：标题栏完整系统名「UrbanHeatOpt · 城市供热多目标优化求解平台」+ 版本号 `v1.0.0`（已定稿）；四步工作流全绿状态；每个功能区带中文标题；Footer 版权行 `© 2026 UrbanHeatOpt 项目组`。上述要素在阶段二做成固定不可隐藏要素。

---

## 3. Python API 适配接口模板定义

落盘路径（阶段二实现）：`src/urbanheatopt/gui/contracts.py`。**只定义数据形状与协议，不依赖任何 UI 实现**；后端接线 = 实现三个 Protocol 或直接用内置零侵入实现（§4）。

### 3.1 Input Schema（UI → 后端）

```python
"""GUI 与求解后端之间的解耦契约（阶段一模板）
后端接线只需实现 DataImportHook / SolveBackendHook / ExportHook，
或直接使用内置的 SubprocessSolverBackend（零侵入，走 run.py solve 生产链路）。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol

CONTRACT_VERSION = "gui_contracts_1.0.0"          # 仅消费 handoff_1.1.0 交接对象


class WorkflowStage(str, Enum):
    INPUT = "input"; VALIDATE = "validate"; SOLVE = "solve"; RESULTS = "results"


class SolveState(str, Enum):
    IDLE = "idle"; QUEUED = "queued"; RUNNING = "running"
    COMPLETED = "completed"; FAILED = "failed"; CANCELLED = "cancelled"


class CheckLevel(str, Enum):
    PASS = "pass"; WARN = "warn"; ERROR = "error"


@dataclass(frozen=True, slots=True)
class SolverConfig:
    """HiGHS 配置 —— 与 SolveRequest.solver 字段一一对应（无权重字段，多目标走 ε-约束）"""
    name: str = "highs"                # highs / gurobi / auto（不代表许可证可用）
    threads: int = 8
    random_seed: int = 0
    mip_gap: float = 0.01              # [0,1]：验收合成 gap 阈值
    time_limit_s: float | None = None  # 超时阈值；None = 不限时

    def to_payload(self) -> dict[str, Any]:
        return {"name": self.name, "threads": self.threads,
                "random_seed": self.random_seed, "mip_gap": self.mip_gap,
                "time_limit_s": self.time_limit_s}


@dataclass(frozen=True, slots=True)
class ScenarioParams:
    """场景参数 —— 与 SolveRequest 顶层字段一一对应"""
    mode: str = "central"              # central / distributed / hybrid
    objective: str = "cost"            # cost / carbon
    epsilon_carbon_kg: float | None = None  # 仅 cost 目标使用的碳上限（ε-约束法）
    tes_enabled: bool = False
    allow_unserved: bool = False       # 生产固定 false，UI 禁用

    def to_payload(self) -> dict[str, Any]:
        return {"mode": self.mode, "objective": self.objective,
                "epsilon_carbon_kg": self.epsilon_carbon_kg,
                "tes_enabled": self.tes_enabled, "allow_unserved": False}


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_id: str; level: CheckLevel; message: str


@dataclass(frozen=True, slots=True)
class ImportedData:
    """文件导入 + 校验适配结果"""
    source: Path                       # 原始文件/配置
    kind: str                          # yaml / csv / excel / json / case_bundle
    checks: tuple[CheckResult, ...]
    bundle_path: Path | None = None    # prepare 产出的 case_bundle.json
    bundle_id: str | None = None
    valid: bool = False


class DataImportHook(Protocol):
    """文件导入与校验接口（真实实现：run_input_pipeline + CaseBundle.verify_artifacts + ready_report）"""
    def inspect(self, path: Path) -> ImportedData: ...
    def prepare(self, config_path: Path, *, run_id: str,
                output_root: Path | None = None) -> ImportedData: ...
```

### 3.2 HiGHS Controller Hook（求解生命周期，异步防卡顿）

```python
@dataclass(frozen=True, slots=True)
class SolveProgressEvent:
    stage: WorkflowStage
    state: SolveState
    percent: float                     # 0..100
    message: str
    site_id: str | None = None         # 候选站子任务（集中/混合式 5 站枚举）
    elapsed_seconds: float = 0.0
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SolveJob:
    bundle_path: Path                  # case_bundle.json 绝对路径
    params: ScenarioParams
    solver: SolverConfig
    run_id: str
    output_dir: Path


class SolveBackendHook(Protocol):
    """求解生命周期。UI 侧用 QTimer/QThread 调用 poll()，任何实现不得阻塞 UI 线程。"""
    def start(self, job: SolveJob) -> None: ...
    def poll(self) -> SolveProgressEvent | None: ...   # 无新事件返回 None
    def cancel(self) -> None: ...                      # 终止（子进程实现=杀进程）
    def outcome(self) -> SolveOutcome: ...             # 终态后调用
```

### 3.3 Output Schema（后端 → UI）

```python
@dataclass(frozen=True, slots=True)
class ParetoPointView:
    """由 pareto.ParetoPoint（point_to_dict）转换而来，UI 只读"""
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
    """四张 KPI 卡：最佳成本 / 最低碳排 / 膝点均衡 / 求解证据"""
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
    """求解完成后的完整结果视图（UI 只读，字段全部来自生产产物）"""
    run_id: str
    qualified: bool
    result_bundle_path: Path | None
    artifacts: Mapping[str, Path]      # role -> 文件（solver_log / independent_qa / ...）
    summary: Mapping[str, Any]         # solution_summary.json 原样透传
    kpis: KpiCardData
    pareto: tuple[ParetoPointView, ...] = ()
    table_rows: tuple[Mapping[str, Any], ...] = ()   # candidate_site_comparison.csv
    log_paths: tuple[Path, ...] = ()


class ExportHook(Protocol):
    """QA 报告 / 方案明细导出（PDF 用 QTextDocument+QPrinter；Excel 用 openpyxl）"""
    def export_excel(self, outcome: SolveOutcome, target: Path) -> Path: ...
    def export_pdf(self, outcome: SolveOutcome, target: Path) -> Path: ...
    def export_png(self, outcome: SolveOutcome, figure_name: str, target: Path) -> Path: ...
```

### 3.4 界面 ↔ 后端映射表

| 界面输入/输出 | 后端实体与字段 | 接线方式 |
| --- | --- | --- |
| 场景参数面板 | `SolveRequest`: mode / objective / epsilon_carbon_kg / tes_enabled | **经现有请求工厂生成**（不手拼 JSON，遵守任务书边界） |
| HiGHS 配置面板 | `SolveRequest.solver`: name / threads / random_seed / mip_gap / time_limit_s | `SolverConfig.to_payload()` |
| 校验状态 Badge/日志 | `run_input_pipeline(validate)` → `ready_report` → `verify_artifacts` | `DataImportHook` 实现 |
| 启动/终止/进度条 | `execute_prepared_request` / `execute_cost_endpoint_set` | 见 §4（接线方案待确认） |
| Pareto + 膝点 | `pareto.ParetoPoint`、膝点标记、`select_representative_points` | 阶段四直读 |
| KPI 卡 | `solution_summary.json` + `solver_evidence.json`（incumbent/bound/gap/QA） | `SolveOutcome.kpis` |
| 方案明细表 | `candidate_site_comparison.csv` | `SolveOutcome.table_rows` |

---

## 4. 求解接线方案（已确认：方案 A，2026-09-07）

GUI 需要：启动真实求解而不卡界面、显示进度、可终止。现有执行器是同步函数，无进度回调、无协作式取消。两种方案：

### 方案 A：子进程零侵入（推荐）

GUI 用 `QProcess` 启动 `python run.py solve --bundle ... --request ... --run-id ... --output-root runs/v2`（与 CLI 验收同一条生产链），通过**轮询执行器已写入的产物**（`candidate_tasks/*/task_status.json`、`solver.log`、`run_manifest.json`）获得进度；终止 = `QProcess.kill()`。

- 利：B 侧核心代码零修改，冻结链/验收证据原样生效；终止真实可靠；求解器崩溃不传染 GUI；环境隔离（`conda run -n urbanheatopt_env`）；进度无需新增任何回调。
- 弊：进度粒度较粗（站点级，无迭代级）；终止为硬杀（当前站点结果作废）；子进程启动有秒级开销；通信单向（命令行参数 + 磁盘文件）。

### 方案 B：进程内直调（需 B 侧核心代码配合）

GUI 在 QThread 中直接 `import` 并调用 `execute_request(...)`。需 B 侧：给执行器加 `progress_callback` 参数；暴露 HiGHS 中断钩子（Pyomo 接口下不成熟，需专门实现验证）；或把执行器重构成增量调用。

- 利：进度细（建模/迭代级）；无启动开销；对象直传。
- 弊：改动刚验收的生产链，需 B 组评审并重跑验收；进程内取消大概率仍难实现；求解器 OOM/段错误直接杀死 GUI；线程安全成本高。

### 方案 C：混合（推荐落地顺序）

先按方案 A 落地；`SolveBackendHook` 协议已把两者抽象为同一接口——将来 B 侧提供回调版本后，仅新增 `InProcessSolverBackend` 实现，**界面层零改动**。Mock 后端同样走该协议，阶段二/三与真实接线无缝衔接。

---

## 5. 已确认决策记录

| 决策项 | 结论 | 日期 |
| --- | --- | --- |
| 框架 | PySide6（LGPL） | 2026-09-07 |
| 系统名称/版本 | 「UrbanHeatOpt · 城市供热多目标优化求解平台」v1.0.0 | 2026-09-07 |
| HiGHS 面板字段 | 按 SolveRequest.solver 真实字段（无权重），ε 值归场景参数 | 2026-09-07 |
| 本设计文档 | 落盘 `docs/design/GUI_PHASE1_BLUEPRINT_CN.md` 供团队评审 | 2026-09-07 |
| 求解接线 | 方案 A：子进程零侵入（QProcess 跑 `run.py solve` + 轮询产物 + kill 终止）；协议层预留进程内实现 | 2026-09-07 |
