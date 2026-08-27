"""竞赛层共用的安全 Pyomo 求解接口。"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from numbers import Integral, Real
from typing import Any

from pyomo.opt import SolverFactory, TerminationCondition


@dataclass(frozen=True, slots=True)
class SolverSettings:
    """外部可配置且可复现的求解器参数。"""

    name: str = "highs"
    mip_gap: float = 0.0
    threads: int = 1
    time_limit_seconds: float = 60.0
    random_seed: int = 202611
    tee: bool = False


class SolverNotOptimalError(RuntimeError):
    """Structured non-optimal termination; no model values have been loaded."""

    def __init__(self, solver_name: str, results: Any, settings: SolverSettings) -> None:
        solver = results.solver
        self.solver_name = solver_name
        self.solver_status = str(getattr(solver, "status", "unknown"))
        self.termination_condition = str(solver.termination_condition)
        self.reported_mip_gap = _optional_finite(getattr(solver, "gap", None))
        self.time_limit_seconds = float(settings.time_limit_seconds)
        self.mip_gap_target = float(settings.mip_gap)
        self.threads = int(settings.threads)
        self.random_seed = int(settings.random_seed)
        super().__init__(
            "求解未达到 optimal，变量未加载且不会导出结果："
            f"solver={solver_name}, termination_condition={self.termination_condition}, "
            f"reported_mip_gap={self.reported_mip_gap}"
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


def validate_solver_settings(settings: SolverSettings) -> None:
    """在创建求解器前验证全部设置。"""

    if not isinstance(settings, SolverSettings):
        raise TypeError("settings 必须是 SolverSettings")
    if settings.name not in {"highs", "gurobi"}:
        raise ValueError("name 只能是 'highs' 或 'gurobi'")

    mip_gap = _finite_real(settings.mip_gap, "mip_gap")
    if not 0 <= mip_gap < 1:
        raise ValueError("mip_gap 必须在 [0, 1) 内")

    if (
        isinstance(settings.threads, bool)
        or not isinstance(settings.threads, Integral)
        or int(settings.threads) != 1
    ):
        raise ValueError("threads 必须为 1，以保持可复现的单线程求解")

    time_limit = _finite_real(settings.time_limit_seconds, "time_limit_seconds")
    if time_limit <= 0:
        raise ValueError("time_limit_seconds 必须大于 0")

    if (
        isinstance(settings.random_seed, bool)
        or not isinstance(settings.random_seed, Integral)
        or not 0 <= int(settings.random_seed) <= 2_147_483_647
    ):
        raise ValueError("random_seed 必须是 0 到 2147483647 的整数")
    if not isinstance(settings.tee, bool):
        raise ValueError("tee 必须是布尔值")


def solve_pyomo_model(model: Any, settings: SolverSettings | None = None) -> Any:
    """安全求解 Pyomo 模型，只在严格 optimal 后加载变量值。"""

    resolved = settings if settings is not None else SolverSettings()
    validate_solver_settings(resolved)

    if resolved.name == "highs":
        factory_name = "appsi_highs"
        options = {
            "mip_rel_gap": float(resolved.mip_gap),
            "threads": int(resolved.threads),
            "time_limit": float(resolved.time_limit_seconds),
            "random_seed": int(resolved.random_seed),
            "primal_feasibility_tolerance": 1e-9,
            "dual_feasibility_tolerance": 1e-9,
            "mip_feasibility_tolerance": 1e-9,
        }
    else:
        factory_name = "gurobi"
        options = {
            "MIPGap": float(resolved.mip_gap),
            "Threads": int(resolved.threads),
            "TimeLimit": float(resolved.time_limit_seconds),
            "Seed": int(resolved.random_seed),
        }

    solver = SolverFactory(factory_name)
    if not solver.available(exception_flag=False):
        raise RuntimeError(
            f"请求的求解器 {resolved.name!r} 在当前环境中不可用；"
            "不会自动切换到其他求解器"
        )

    results = solver.solve(
        model,
        tee=resolved.tee,
        load_solutions=False,
        options=options,
    )
    termination = results.solver.termination_condition
    if termination != TerminationCondition.optimal:
        raise SolverNotOptimalError(resolved.name, results, resolved)

    model.solutions.load_from(results)
    return results
