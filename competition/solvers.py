"""竞赛层共用的安全 Pyomo 求解接口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from numbers import Integral, Real
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
from time import perf_counter
from typing import Any
from uuid import uuid4

from pyomo.opt import ProblemFormat, SolverFactory, TerminationCondition


@dataclass(frozen=True, slots=True)
class SolverSettings:
    """外部可配置且可复现的求解器参数。"""

    name: str = "highs"
    mip_gap: float = 0.0
    threads: int = 1
    time_limit_seconds: float = 60.0
    random_seed: int = 202611
    tee: bool = False
    log_file: str | None = None
    model_file: str | None = None
    evidence_file: str | None = None
    presolve: str = "choose"
    feasibility_tolerance: float = 1e-9


class SolverNotOptimalError(RuntimeError):
    """Structured non-optimal termination; no model values have been loaded."""

    def __init__(
        self,
        solver_name: str,
        results: Any,
        settings: SolverSettings,
        evidence: dict[str, Any],
    ) -> None:
        solver = results.solver
        self.solver_name = solver_name
        self.solver_status = str(getattr(solver, "status", "unknown"))
        self.termination_condition = str(solver.termination_condition)
        self.incumbent_objective = evidence.get("incumbent_objective")
        self.best_objective_bound = evidence.get("best_objective_bound")
        self.reported_mip_gap = evidence.get("relative_mip_gap")
        self.has_feasible_solution = bool(evidence.get("has_feasible_solution"))
        self.model_sha256 = evidence.get("model_sha256")
        self.solver_log_file = evidence.get("solver_log_file")
        self.solver_evidence_file = evidence.get("solver_evidence_file")
        self.time_limit_seconds = float(settings.time_limit_seconds)
        self.mip_gap_target = float(settings.mip_gap)
        self.threads = int(settings.threads)
        self.random_seed = int(settings.random_seed)
        super().__init__(
            "求解未达到 optimal，变量未加载且不会导出结果："
            f"solver={solver_name}, termination_condition={self.termination_condition}, "
            f"reported_mip_gap={self.reported_mip_gap}, "
            f"has_feasible_solution={self.has_feasible_solution}"
        )


def _optional_finite(value: object) -> float | None:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return normalized if isfinite(normalized) else None


def _finite_real(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field} 必须是有限数值")
    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError(f"{field} 必须是有限数值")
    return normalized


def _validated_optional_path(value: object, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空路径字符串或 None")


def _peak_working_set_bytes() -> int | None:
    """Return process peak RSS without adding a runtime dependency."""

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        )
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        process = kernel32.GetCurrentProcess()
        if psapi.GetProcessMemoryInfo(
            process, ctypes.byref(counters), counters.cb
        ):
            return int(counters.PeakWorkingSetSize)
        return None
    try:
        import resource

        maximum = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return maximum if sys.platform == "darwin" else maximum * 1024
    except (ImportError, OSError, ValueError):
        return None


def _streaming_sha256(path: Path) -> str:
    """Hash a potentially multi-gigabyte artifact without loading it into RAM."""

    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_model_snapshot(
    model: Any, filename: str | None
) -> tuple[str | None, str | None, int | None, int | None]:
    if filename is None:
        return None, None, None, None
    path = Path(filename).expanduser().resolve()
    if path.exists():
        raise FileExistsError(f"MPS 证据文件已存在，拒绝覆盖：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    model.write(
        str(path),
        format=ProblemFormat.mps,
        # Numeric labels preserve the exact coefficients and ordering while
        # avoiding repeated long Pyomo component names in very large MPS files.
        io_options={"symbolic_solver_labels": False},
    )
    digest = _streaming_sha256(path)
    stat = path.stat()
    return str(path), digest, int(stat.st_size), int(stat.st_mtime_ns)


def _highs_log_proxy(path: Path) -> Path | None:
    """Return an ASCII-only Windows proxy for a non-ASCII HiGHS log path.

    HighsPy 1.15.1 can solve models whose Python-side paths contain Unicode,
    but its native log writer may silently skip such a path on Windows.  The
    requested log remains the public evidence path; only the native write is
    redirected and copied back after ``solve`` returns.  ASCII run roots keep
    their direct path so long-running logs remain observable in real time.
    """

    if os.name != "nt" or str(path).isascii():
        return None
    candidates: list[Path] = []
    for name in ("TEMP", "TMP", "TMPDIR"):
        value = os.environ.get(name)
        if value:
            candidates.append(Path(value))
    try:
        candidates.append(Path(tempfile.gettempdir()))
    except (OSError, RuntimeError):
        pass
    candidates.append(Path.cwd())
    for directory in candidates:
        try:
            directory = directory.expanduser().resolve()
        except OSError:
            continue
        if not str(directory).isascii() or not directory.is_dir():
            continue
        for _ in range(10):
            proxy = directory / (
                f"urbanheatopt_highs_{os.getpid()}_{uuid4().hex}.log"
            )
            if not proxy.exists():
                return proxy
    raise RuntimeError(
        "HighsPy on Windows requires an ASCII TEMP/TMP/TMPDIR or working "
        f"directory to preserve the requested Unicode solver log: {path}"
    )


def _result_metrics(results: Any) -> dict[str, Any]:
    problem = results.problem
    lower = _optional_finite(getattr(problem, "lower_bound", None))
    upper = _optional_finite(getattr(problem, "upper_bound", None))
    sense = str(getattr(problem, "sense", "minimize")).lower()
    has_solution = bool(len(results.solution))
    if "max" in sense:
        incumbent = lower if has_solution else None
        bound = upper
    else:
        incumbent = upper if has_solution else None
        bound = lower
    gap = None
    if incumbent is not None and bound is not None:
        gap = abs(incumbent - bound) / max(1.0, abs(incumbent))
    return {
        "objective_sense": "maximize" if "max" in sense else "minimize",
        "has_feasible_solution": has_solution,
        "incumbent_objective": incumbent,
        "best_objective_bound": bound,
        "relative_mip_gap": gap,
        "raw_lower_bound": lower,
        "raw_upper_bound": upper,
    }


def get_solver_evidence(results: Any) -> dict[str, Any]:
    """Return normalized evidence attached by :func:`solve_pyomo_model`."""

    evidence = getattr(results, "urbanheatopt_evidence", None)
    return dict(evidence) if isinstance(evidence, dict) else _result_metrics(results)


def _write_evidence(path_value: str | None, payload: dict[str, Any]) -> str | None:
    if path_value is None:
        return None
    path = Path(path_value).expanduser().resolve()
    if path.exists():
        raise FileExistsError(f"求解证据文件已存在，拒绝覆盖：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["solver_evidence_file"] = str(path)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return str(path)


def validate_solver_settings(settings: SolverSettings) -> None:
    """在创建求解器前验证全部设置。"""

    if not isinstance(settings, SolverSettings):
        raise TypeError("settings 必须是 SolverSettings")
    if settings.name not in {"highs", "gurobi"}:
        raise ValueError("name 只能是 'highs' 或 'gurobi'")
    if not isinstance(settings.presolve, str) or settings.presolve not in {
        "choose",
        "on",
        "off",
    }:
        raise ValueError("presolve 只能是 'choose'、'on' 或 'off'")

    mip_gap = _finite_real(settings.mip_gap, "mip_gap")
    if not 0 <= mip_gap < 1:
        raise ValueError("mip_gap 必须在 [0, 1) 内")

    if (
        isinstance(settings.threads, bool)
        or not isinstance(settings.threads, Integral)
        or int(settings.threads) not in {1, 4, 8}
    ):
        raise ValueError("threads 只能是 1、4 或 8")

    time_limit = _finite_real(settings.time_limit_seconds, "time_limit_seconds")
    if time_limit <= 0:
        raise ValueError("time_limit_seconds 必须大于 0")

    feasibility_tolerance = _finite_real(
        settings.feasibility_tolerance,
        "feasibility_tolerance",
    )
    if not 1e-10 <= feasibility_tolerance <= 1e-4:
        raise ValueError("feasibility_tolerance must be between 1e-10 and 1e-4")

    if (
        isinstance(settings.random_seed, bool)
        or not isinstance(settings.random_seed, Integral)
        or not 0 <= int(settings.random_seed) <= 2_147_483_647
    ):
        raise ValueError("random_seed 必须是 0 到 2147483647 的整数")
    if not isinstance(settings.tee, bool):
        raise ValueError("tee 必须是布尔值")
    _validated_optional_path(settings.log_file, "log_file")
    _validated_optional_path(settings.model_file, "model_file")
    _validated_optional_path(settings.evidence_file, "evidence_file")


def solve_pyomo_model(model: Any, settings: SolverSettings | None = None) -> Any:
    """Solve without changing model physics and load only gap-certified values."""

    resolved = settings if settings is not None else SolverSettings()
    validate_solver_settings(resolved)
    (
        model_path,
        model_sha256,
        model_size_bytes,
        model_mtime_ns,
    ) = _write_model_snapshot(model, resolved.model_file)
    log_path = (
        str(Path(resolved.log_file).expanduser().resolve())
        if resolved.log_file is not None
        else None
    )
    if log_path is not None:
        log_file = Path(log_path)
        if log_file.exists():
            raise FileExistsError(f"求解日志已存在，拒绝覆盖：{log_file}")
        log_file.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc)
    started_clock = perf_counter()
    peak_before = _peak_working_set_bytes()

    highs_log_proxy = None
    if resolved.name == "highs":
        factory_name = "appsi_highs"
        options = {
            "presolve": resolved.presolve,
            "mip_rel_gap": float(resolved.mip_gap),
            "threads": int(resolved.threads),
            "time_limit": float(resolved.time_limit_seconds),
            "random_seed": int(resolved.random_seed),
            "primal_feasibility_tolerance": float(resolved.feasibility_tolerance),
            "dual_feasibility_tolerance": float(resolved.feasibility_tolerance),
            "mip_feasibility_tolerance": float(resolved.feasibility_tolerance),
        }
        if resolved.threads > 1:
            options["parallel"] = "on"
        if log_path is not None:
            highs_log_proxy = _highs_log_proxy(Path(log_path))
            options["log_file"] = str(highs_log_proxy or log_path)
    else:
        factory_name = "gurobi"
        options = {
            "MIPGap": float(resolved.mip_gap),
            "Threads": int(resolved.threads),
            "TimeLimit": float(resolved.time_limit_seconds),
            "Seed": int(resolved.random_seed),
        }
        if log_path is not None:
            options["LogFile"] = log_path

    solver = SolverFactory(factory_name)
    if not solver.available(exception_flag=False):
        raise RuntimeError(
            f"请求的求解器 {resolved.name!r} 在当前环境中不可用；"
            "不会自动切换到其他求解器"
        )

    try:
        results = solver.solve(
            model,
            tee=resolved.tee,
            load_solutions=False,
            options=options,
        )
    finally:
        if highs_log_proxy is not None:
            # HiGHS keeps the log handle open after run() on Windows.  Resetting
            # this one option flushes and closes it without clearing the solved
            # model or result object needed below.
            native_highs = getattr(solver, "_solver_model", None)
            if native_highs is not None:
                native_highs.setOptionValue("log_file", "")
            if highs_log_proxy.is_file():
                shutil.copyfile(highs_log_proxy, Path(log_path))
                highs_log_proxy.unlink()
    finished_at = datetime.now(timezone.utc)
    metrics = _result_metrics(results)
    termination = results.solver.termination_condition
    gap = metrics["relative_mip_gap"]
    certified = bool(
        metrics["has_feasible_solution"]
        and gap is not None
        and gap <= float(resolved.mip_gap) + 1e-12
    )
    accepted = termination == TerminationCondition.optimal or (
        termination == TerminationCondition.maxTimeLimit and certified
    )
    evidence: dict[str, Any] = {
        "schema_version": "urbanheatopt_solver_evidence_v1",
        "solver_name": resolved.name,
        "solver_factory": factory_name,
        "solver_status": str(getattr(results.solver, "status", "unknown")),
        "termination_condition": str(termination),
        "accepted": accepted,
        "acceptance_reason": (
            "solver_optimal_with_configured_gap"
            if termination == TerminationCondition.optimal
            else "time_limit_but_gap_certified"
            if accepted
            else "not_gap_certified"
        ),
        "configured_mip_gap": float(resolved.mip_gap),
        "configured_feasibility_tolerance": float(resolved.feasibility_tolerance),
        "threads": int(resolved.threads),
        "time_limit_seconds": float(resolved.time_limit_seconds),
        "random_seed": int(resolved.random_seed),
        "parallel_enabled": resolved.threads > 1,
        "started_at_utc": started_at.isoformat(),
        "finished_at_utc": finished_at.isoformat(),
        "elapsed_seconds": perf_counter() - started_clock,
        "peak_working_set_before_bytes": peak_before,
        "peak_working_set_after_bytes": _peak_working_set_bytes(),
        "model_file": model_path,
        "model_sha256": model_sha256,
        "model_size_bytes": model_size_bytes,
        "model_mtime_ns": model_mtime_ns,
        "model_label_style": "numeric" if model_path is not None else None,
        "solver_log_file": log_path,
        "solver_log_transport": (
            "ascii_proxy" if highs_log_proxy is not None else "direct"
        ) if log_path is not None else None,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "logical_cpu_count": os.cpu_count(),
        **metrics,
    }
    if resolved.name == "highs":
        evidence["presolve"] = resolved.presolve
    evidence_path = _write_evidence(resolved.evidence_file, evidence)
    if evidence_path is not None:
        evidence["solver_evidence_file"] = evidence_path
    results.urbanheatopt_evidence = evidence
    if not accepted:
        raise SolverNotOptimalError(resolved.name, results, resolved, evidence)

    model.solutions.load_from(results)
    return results
