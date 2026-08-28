"""Independent full-season task planning without changing the Pyomo core."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
import platform
import socket
import sys
from typing import Any

import geopandas as gpd
import yaml

from competition.pareto import (
    ParetoPoint,
    ParetoSpec,
    assemble_pareto_run,
    point_to_dict,
    select_representative_points,
    solve_pareto_task,
)
from competition.results import export_v3_results, export_v3_solution
from competition.solvers import SolverNotOptimalError
from competition.validation.v3_inputs import load_v3_case


MODES = ("central", "distributed", "hybrid")
CORE_FREEZE = Path(__file__).resolve().parent / "configs" / "core_model_freeze.yaml"
SCHEMA = "urbanheatopt_full_season_tasks_v1"


@dataclass(frozen=True, slots=True)
class FullSeasonTask:
    task_id: str
    phase: str
    mode: str
    point_id: str
    objective: str
    epsilon_kgCO2e_per_year: float | None
    threads: int
    time_limit_seconds: float
    mip_gap: float
    export_solution: bool
    ready: bool


def _json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _package_version(distribution: str) -> str | None:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def host_environment_record(physical_core_count: int | None = None) -> dict[str, Any]:
    if physical_core_count is not None and physical_core_count <= 0:
        raise ValueError("物理核心数必须大于0")
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "processor": platform.processor() or None,
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "pyomo_version": _package_version("pyomo"),
        "highspy_version": _package_version("highspy"),
        "logical_cpu_count": os.cpu_count(),
        "physical_core_count": physical_core_count,
        "physical_core_count_source": (
            "explicit_server_inventory" if physical_core_count is not None else "not_recorded"
        ),
        "available_memory_bytes_at_plan_creation": _available_memory_bytes(),
    }


def frozen_core_record() -> dict[str, Any]:
    record = yaml.safe_load(CORE_FREEZE.read_text(encoding="utf-8"))
    target = Path(__file__).resolve().parents[1] / str(record["core_model_path"])
    actual = _hash(target)
    expected = str(record["sha256"]).lower()
    if actual != expected:
        raise RuntimeError(
            "核心模型哈希与冻结记录不一致，禁止全季服务器任务："
            f"expected={expected}, actual={actual}"
        )
    return {**record, "actual_sha256": actual}


def _endpoint_tasks() -> list[FullSeasonTask]:
    rows: list[FullSeasonTask] = []
    for mode in MODES:
        rows.extend(
            (
                FullSeasonTask(
                    task_id=f"{mode}-cost",
                    phase="endpoint",
                    mode=mode,
                    point_id=f"{mode}-cost",
                    objective="cost",
                    epsilon_kgCO2e_per_year=None,
                    threads=8,
                    time_limit_seconds=21600.0,
                    mip_gap=0.01,
                    export_solution=True,
                    ready=True,
                ),
                FullSeasonTask(
                    task_id=f"{mode}-carbon",
                    phase="endpoint",
                    mode=mode,
                    point_id=f"{mode}-carbon",
                    objective="carbon",
                    epsilon_kgCO2e_per_year=None,
                    threads=8,
                    time_limit_seconds=21600.0,
                    mip_gap=0.01,
                    export_solution=True,
                    ready=True,
                ),
            )
        )
    return rows


def _epsilon_tasks(point_count: int) -> list[FullSeasonTask]:
    return [
        FullSeasonTask(
            task_id=f"{mode}-epsilon-{index:03d}",
            phase="epsilon",
            mode=mode,
            point_id=f"{mode}-epsilon-{index:03d}",
            objective="cost",
            epsilon_kgCO2e_per_year=None,
            threads=8,
            time_limit_seconds=21600.0,
            mip_gap=0.01,
            export_solution=True,
            ready=False,
        )
        for mode in MODES
        for index in range(point_count)
    ]


def _benchmark_tasks() -> list[FullSeasonTask]:
    return [
        FullSeasonTask(
            task_id=f"benchmark-central-cost-t{threads}",
            phase="benchmark",
            mode="central",
            point_id=f"benchmark-central-cost-t{threads}",
            objective="cost",
            epsilon_kgCO2e_per_year=None,
            threads=threads,
            time_limit_seconds=1800.0,
            mip_gap=0.0,
            export_solution=False,
            ready=True,
        )
        for threads in (1, 4, 8)
    ]


def create_task_plan(
    run_root: str | Path,
    case_dir: str | Path,
    *,
    point_count: int = 11,
    physical_core_count: int | None = None,
) -> Path:
    root = Path(run_root).resolve()
    plan_path = root / "task_plan.json"
    if plan_path.exists():
        raise FileExistsError(f"任务计划已存在，拒绝覆盖：{plan_path}")
    if point_count != 11:
        raise ValueError("原规模全季任务固定为每种模式11个 epsilon 点")
    case_path = Path(case_dir).resolve()
    case = load_v3_case(case_path, profile="v0-full-season")
    if len(case.demand_nodes) != 62 or len(case.hours) != 2160:
        raise ValueError("服务器全季任务必须严格为62栋×2160小时")
    core = frozen_core_record()
    tasks = _benchmark_tasks() + _endpoint_tasks() + _epsilon_tasks(point_count)
    root.mkdir(parents=True, exist_ok=True)
    (root / "tasks").mkdir()
    payload = {
        "schema_version": SCHEMA,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_dir": str(case_path),
        "core_freeze": core,
        "host_environment": host_environment_record(physical_core_count),
        "case_input_sha256": dict(case.input_sha256),
        "required_building_count": 62,
        "required_hour_count": 2160,
        "mode_order": list(MODES),
        "epsilon_point_count_per_mode": point_count,
        "main_task_count": 6 + len(MODES) * point_count,
        "benchmark_task_count": 3,
        "tasks": [asdict(task) for task in tasks],
    }
    _json(plan_path, payload)
    return plan_path


def _load_plan(run_root: str | Path) -> tuple[Path, dict[str, Any]]:
    root = Path(run_root).resolve()
    plan_path = root / "task_plan.json"
    if not plan_path.is_file():
        raise FileNotFoundError(f"缺少服务器任务计划：{plan_path}")
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA:
        raise ValueError("服务器任务计划版本不受支持")
    frozen_core_record()
    return root, payload


def _task(payload: dict[str, Any], task_id: str) -> FullSeasonTask:
    matches = [row for row in payload["tasks"] if row["task_id"] == task_id]
    if len(matches) != 1:
        raise KeyError(f"任务ID不存在或不唯一：{task_id}")
    return FullSeasonTask(**matches[0])


def _success_path(root: Path, task_id: str) -> Path:
    return root / "tasks" / task_id / "task_success.json"


def _point_path(root: Path, task_id: str) -> Path:
    return root / "tasks" / task_id / "point.json"


def resolve_epsilon_tasks(run_root: str | Path) -> Path:
    root, payload = _load_plan(run_root)
    updated: list[dict[str, Any]] = []
    endpoints: dict[str, tuple[float, float]] = {}
    for mode in MODES:
        cost_path = _point_path(root, f"{mode}-cost")
        carbon_path = _point_path(root, f"{mode}-carbon")
        if not cost_path.is_file() or not carbon_path.is_file():
            raise RuntimeError(f"模式 {mode} 的两个端点尚未完成，不能生成 epsilon")
        cost = json.loads(cost_path.read_text(encoding="utf-8"))
        carbon = json.loads(carbon_path.read_text(encoding="utf-8"))
        low = float(carbon["annual_operating_carbon_kgCO2e_per_year"])
        high = float(cost["annual_operating_carbon_kgCO2e_per_year"])
        if high < low - 1e-6:
            raise RuntimeError(f"模式 {mode} 成本端点碳排低于碳端点")
        endpoints[mode] = (low, high)
    count = int(payload["epsilon_point_count_per_mode"])
    for row in payload["tasks"]:
        task = FullSeasonTask(**row)
        if task.phase != "epsilon":
            updated.append(row)
            continue
        index = int(task.task_id.rsplit("-", 1)[1])
        low, high = endpoints[task.mode]
        epsilon = low + (high - low) * index / (count - 1)
        updated.append(
            asdict(replace(task, epsilon_kgCO2e_per_year=epsilon, ready=True))
        )
    payload["tasks"] = updated
    payload["epsilon_resolved_at_utc"] = datetime.now(timezone.utc).isoformat()
    _json(root / "task_plan.json", payload)
    return root / "task_plan.json"


def _main_points(root: Path, payload: dict[str, Any]) -> tuple[ParetoPoint, ...]:
    main_rows = [
        row for row in payload["tasks"] if row["phase"] in {"endpoint", "epsilon"}
    ]
    missing = [
        row["task_id"]
        for row in main_rows
        if not _point_path(root, row["task_id"]).is_file()
    ]
    if missing:
        raise RuntimeError(f"39个主任务尚未全部完成：{missing}")
    points: list[ParetoPoint] = []
    for row in main_rows:
        point_payload = json.loads(
            _point_path(root, row["task_id"]).read_text(encoding="utf-8")
        )
        point_payload["labels"] = tuple(point_payload.get("labels", ()))
        points.append(ParetoPoint(**point_payload))
    return tuple(points)


def _pareto_spec(case: Any) -> ParetoSpec:
    return ParetoSpec(
        point_count=11,
        unserved_tolerance_kWh=float(
            case.raw_config["qa"]["unserved_tolerance_kWh"]
        ),
        cost_tolerance_CNY_per_year=float(
            case.raw_config["qa"]["cost_tolerance_CNY_per_year"]
        ),
        carbon_tolerance_kgCO2e_per_year=float(
            case.raw_config["qa"]["carbon_tolerance_kgCO2e_per_year"]
        ),
    )


def resolve_representative_tasks(
    run_root: str | Path,
    *,
    policy_carbon_constraint_kgCO2e_per_year: float,
) -> Path:
    """Select four rules and create unique 0.1%-gap certification tasks."""

    if not math.isfinite(policy_carbon_constraint_kgCO2e_per_year):
        raise ValueError("政策碳约束必须是有限数值")
    if policy_carbon_constraint_kgCO2e_per_year < 0:
        raise ValueError("政策碳约束必须大于等于0")
    root, payload = _load_plan(run_root)
    if any(row["phase"] == "representative" for row in payload["tasks"]):
        raise FileExistsError("代表解认证任务已经生成，拒绝重复修改任务计划")
    case = load_v3_case(payload["case_dir"], profile="v0-full-season")
    pareto = assemble_pareto_run(_main_points(root, payload), _pareto_spec(case))
    selected = select_representative_points(
        pareto.combined_frontier,
        policy_carbon_constraint_kgCO2e_per_year=(
            policy_carbon_constraint_kgCO2e_per_year
        ),
    )
    unavailable = {
        name: row["status"]
        for name, row in selected.items()
        if row.get("point_id") is None
    }
    if unavailable:
        raise RuntimeError(f"四类代表解尚不能完整选择：{unavailable}")
    source_tasks = {row["point_id"]: FullSeasonTask(**row) for row in payload["tasks"]}
    representative_tasks: list[FullSeasonTask] = []
    source_to_task: dict[str, str] = {}
    for source_point_id in dict.fromkeys(
        str(row["point_id"]) for row in selected.values()
    ):
        source = source_tasks[source_point_id]
        task_id = f"representative-{source_point_id}"
        source_to_task[source_point_id] = task_id
        representative_tasks.append(
            FullSeasonTask(
                task_id=task_id,
                phase="representative",
                mode=source.mode,
                point_id=f"certified-{source_point_id}",
                objective=source.objective,
                epsilon_kgCO2e_per_year=source.epsilon_kgCO2e_per_year,
                threads=8,
                time_limit_seconds=43200.0,
                mip_gap=0.001,
                export_solution=True,
                ready=True,
            )
        )
    mapping = {
        name: {
            **row,
            "source_point_id": row["point_id"],
            "certification_task_id": source_to_task[str(row["point_id"])],
        }
        for name, row in selected.items()
    }
    payload["tasks"].extend(asdict(task) for task in representative_tasks)
    payload["representative_task_count"] = len(representative_tasks)
    payload["representative_rules_count"] = 4
    payload["representative_selection"] = mapping
    payload["policy_carbon_constraint_kgCO2e_per_year"] = (
        policy_carbon_constraint_kgCO2e_per_year
    )
    payload["representatives_resolved_at_utc"] = datetime.now(
        timezone.utc
    ).isoformat()
    _json(root / "task_plan.json", payload)
    return root / "task_plan.json"


def _next_attempt(task_dir: Path) -> Path:
    existing = [
        int(path.name.split("_", 1)[1])
        for path in task_dir.glob("attempt_*")
        if path.is_dir() and path.name.split("_", 1)[1].isdigit()
    ]
    attempt = task_dir / f"attempt_{max(existing, default=0) + 1:03d}"
    attempt.mkdir(parents=True, exist_ok=False)
    return attempt


def run_server_task(run_root: str | Path, task_id: str) -> dict[str, Any]:
    root, payload = _load_plan(run_root)
    task = _task(payload, task_id)
    if not task.ready:
        raise RuntimeError(f"任务尚未就绪：{task_id}")
    success = _success_path(root, task_id)
    if success.is_file():
        return {"task_id": task_id, "status": "skipped_already_successful"}
    task_dir = root / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    lock = task_dir / "task.lock"
    try:
        try:
            with lock.open("x", encoding="utf-8") as stream:
                json.dump(
                    {
                        "pid": os.getpid(),
                        "host": socket.gethostname(),
                        "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    },
                    stream,
                    ensure_ascii=False,
                )
        except FileExistsError as exc:
            raise RuntimeError(f"任务已被其他进程锁定：{task_id}") from exc
        attempt = _next_attempt(task_dir)
        case = load_v3_case(payload["case_dir"], profile="v0-full-season")
        if len(case.demand_nodes) != 62 or len(case.hours) != 2160:
            raise RuntimeError("任务执行前案例不再是62栋×2160小时")
        settings = replace(
            case.solver,
            threads=task.threads,
            time_limit_seconds=task.time_limit_seconds,
            mip_gap=task.mip_gap,
            log_file=str(attempt / "solver.log"),
            model_file=str(attempt / "model.mps"),
            evidence_file=str(attempt / "solver_evidence.json"),
            tee=False,
        )
        spec = ParetoSpec(
            point_count=11,
            unserved_tolerance_kWh=float(
                case.raw_config["qa"]["unserved_tolerance_kWh"]
            ),
            cost_tolerance_CNY_per_year=float(
                case.raw_config["qa"]["cost_tolerance_CNY_per_year"]
            ),
            carbon_tolerance_kgCO2e_per_year=float(
                case.raw_config["qa"]["carbon_tolerance_kgCO2e_per_year"]
            ),
        )
        point, solution = solve_pareto_task(
            case.to_core_input(task.mode),
            settings,
            spec,
            point_id=task.point_id,
            objective=task.objective,
            epsilon_kgCO2e_per_year=task.epsilon_kgCO2e_per_year,
        )
        point_payload = point_to_dict(point)
        _json(attempt / "point.json", point_payload)
        if task.export_solution:
            network = gpd.read_file(
                Path(payload["case_dir"])
                / case.raw_config["files"]["candidate_network"]
            )
            sites = gpd.read_file(
                Path(payload["case_dir"])
                / case.raw_config["files"]["candidate_sites"]
            )
            export_v3_solution(case, point, solution, root, network, sites)
            _json(_point_path(root, task_id), point_payload)
        result = {
            "task_id": task_id,
            "status": "success",
            "attempt": attempt.name,
            "point": point_payload,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _json(success, result)
        return result
    except SolverNotOptimalError as exc:
        failure = {
            "task_id": task_id,
            "status": "benchmark_complete_unaccepted"
            if task.phase == "benchmark"
            else "failed_unaccepted",
            "termination_condition": exc.termination_condition,
            "incumbent_objective": exc.incumbent_objective,
            "best_objective_bound": exc.best_objective_bound,
            "reported_mip_gap": exc.reported_mip_gap,
            "has_feasible_solution": exc.has_feasible_solution,
            "model_sha256": exc.model_sha256,
            "solver_log_file": exc.solver_log_file,
            "solver_evidence_file": exc.solver_evidence_file,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _json(task_dir / f"failure_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json", failure)
        if task.phase == "benchmark":
            _json(success, failure)
            return failure
        raise
    finally:
        if lock.exists():
            lock.unlink()


def _available_memory_bytes() -> int | None:
    if platform.system().lower() == "windows":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullAvailPhys)
        return None
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    return None


def recommended_worker_count(
    *,
    threads_per_task: int = 8,
    maximum_workers: int = 6,
    memory_per_task_gib: float = 12.0,
    physical_core_count: int | None = None,
) -> int:
    core_count = physical_core_count or (os.cpu_count() or 1)
    cpu_limit = max(1, core_count // threads_per_task)
    available = _available_memory_bytes()
    memory_limit = (
        max(1, math.floor(available * 0.70 / (memory_per_task_gib * 1024**3)))
        if available is not None
        else 1
    )
    return max(1, min(maximum_workers, cpu_limit, memory_limit))


def run_ready_tasks(
    run_root: str | Path,
    *,
    phase: str,
    maximum_workers: int | None = None,
    memory_per_task_gib: float = 12.0,
    physical_core_count: int | None = None,
) -> list[dict[str, Any]]:
    root, payload = _load_plan(run_root)
    ready = [
        FullSeasonTask(**row)
        for row in payload["tasks"]
        if row["phase"] == phase
        and row["ready"]
        and not _success_path(root, row["task_id"]).is_file()
    ]
    if not ready:
        return []
    threads = max(task.threads for task in ready)
    workers = maximum_workers or recommended_worker_count(
        threads_per_task=threads,
        memory_per_task_gib=memory_per_task_gib,
        physical_core_count=physical_core_count,
    )
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(run_server_task, root, task.task_id): task.task_id
            for task in ready
        }
        for future in as_completed(futures):
            results.append(future.result())
    return results


def summarize_benchmarks(run_root: str | Path) -> Path:
    """Verify identical MPS and compare incumbent/bound/gap improvement evidence."""

    root, payload = _load_plan(run_root)
    rows: list[dict[str, Any]] = []
    for task in (
        FullSeasonTask(**row)
        for row in payload["tasks"]
        if row["phase"] == "benchmark"
    ):
        success = _success_path(root, task.task_id)
        if not success.is_file():
            raise RuntimeError(f"线程基准尚未完成：{task.task_id}")
        result = json.loads(success.read_text(encoding="utf-8"))
        point = result.get("point", {})
        rows.append(
            {
                "task_id": task.task_id,
                "threads": task.threads,
                "status": result["status"],
                "model_sha256": result.get("model_sha256")
                or point.get("model_sha256"),
                "incumbent_objective": result.get("incumbent_objective")
                or point.get("incumbent_objective"),
                "best_objective_bound": result.get("best_objective_bound")
                or point.get("best_objective_bound"),
                "reported_mip_gap": result.get("reported_mip_gap")
                if "reported_mip_gap" in result
                else point.get("reported_mip_gap"),
            }
        )
    hashes = {row["model_sha256"] for row in rows}
    if None in hashes or len(hashes) != 1:
        raise RuntimeError(f"1/4/8线程基准的MPS哈希不一致或缺失：{hashes}")
    report = root / "benchmark_comparison.json"
    _json(
        report,
        {
            "status": "same_mathematical_model_verified",
            "model_sha256": next(iter(hashes)),
            "comparison_rule": (
                "按相同1800秒内incumbent、best_bound和gap改善选择正式线程数，"
                "不得只比较退出码"
            ),
            "benchmarks": sorted(rows, key=lambda row: row["threads"]),
        },
    )
    return report


def task_status(run_root: str | Path) -> dict[str, Any]:
    root, payload = _load_plan(run_root)
    rows = []
    for row in payload["tasks"]:
        task = FullSeasonTask(**row)
        state = (
            "success"
            if _success_path(root, task.task_id).is_file()
            else "running"
            if (root / "tasks" / task.task_id / "task.lock").is_file()
            else "pending"
            if task.ready
            else "blocked_waiting_endpoints"
        )
        rows.append({"task_id": task.task_id, "phase": task.phase, "state": state})
    return {
        "run_root": str(root),
        "core_sha256": frozen_core_record()["actual_sha256"],
        "counts": {
            state: sum(row["state"] == state for row in rows)
            for state in sorted({row["state"] for row in rows})
        },
        "tasks": rows,
    }


def assemble_full_season_results(run_root: str | Path) -> Path:
    root, payload = _load_plan(run_root)
    points = _main_points(root, payload)
    case = load_v3_case(payload["case_dir"], profile="v0-full-season")
    pareto = assemble_pareto_run(points, _pareto_spec(case))
    export_v3_results(case, pareto, root, payload["case_dir"])
    marker = root / "full_season_39_tasks_complete.json"
    _json(
        marker,
        {
            "status": "complete",
            "main_task_count": 39,
            "building_count": len(case.demand_nodes),
            "hour_count": len(case.hours),
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "core_sha256": frozen_core_record()["actual_sha256"],
        },
    )
    return marker


def finalize_representative_results(run_root: str | Path) -> Path:
    """Certify the four labels, retaining aliases when rules select one point."""

    root, payload = _load_plan(run_root)
    mapping = payload.get("representative_selection")
    if not isinstance(mapping, dict) or set(mapping) != {
        "minimum_cost",
        "minimum_carbon",
        "normalized_knee",
        "policy_constraint",
    }:
        raise RuntimeError("尚未生成四类代表解认证任务")
    tasks = {
        row["task_id"]: FullSeasonTask(**row)
        for row in payload["tasks"]
        if row["phase"] == "representative"
    }
    certified_points: dict[str, dict[str, Any]] = {}
    for task_id in sorted(tasks):
        success = _success_path(root, task_id)
        point_path = _point_path(root, task_id)
        if not success.is_file() or not point_path.is_file():
            raise RuntimeError(f"代表解认证任务尚未完成：{task_id}")
        point = json.loads(point_path.read_text(encoding="utf-8"))
        gap = point.get("reported_mip_gap")
        if gap is None or float(gap) > 0.001 + 1e-12:
            raise RuntimeError(f"代表解未达到0.1% gap：{task_id}, gap={gap}")
        certified_points[task_id] = point
    labels: dict[str, Any] = {}
    for name, selection in mapping.items():
        task_id = selection["certification_task_id"]
        labels[name] = {
            **selection,
            "certified_point": certified_points[task_id],
            "certified_gap_threshold": 0.001,
        }
    report = root / "certified_representative_solutions.json"
    _json(
        report,
        {
            "status": "certified",
            "representative_rules_count": 4,
            "unique_certification_task_count": len(tasks),
            "policy_carbon_constraint_kgCO2e_per_year": payload[
                "policy_carbon_constraint_kgCO2e_per_year"
            ],
            "representatives": labels,
            "core_sha256": frozen_core_record()["actual_sha256"],
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    return report
