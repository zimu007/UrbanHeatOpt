"""UrbanHeatOpt 竞赛版扩展包。

本包承载竞赛输入契约、适配、流程编排与报告扩展，不替代或宣称上游程序已实现这些能力。

顶层符号采用惰性导出（PEP 562）：仅当真正访问时才加载对应子模块，
避免无关入口（如 GUI 前端）被迫加载 pyomo 求解链。
"""
from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    # model.reference_core
    "CoreModelInput": ("urbanheatopt.model.reference_core", "CoreModelInput"),
    "CoreModelInputError": ("urbanheatopt.model.reference_core", "CoreModelInputError"),
    "CoreSolveResult": ("urbanheatopt.model.reference_core", "CoreSolveResult"),
    "EconomicInput": ("urbanheatopt.model.reference_core", "EconomicInput"),
    "SegmentSpec": ("urbanheatopt.model.reference_core", "SegmentSpec"),
    "TechnologySpec": ("urbanheatopt.model.reference_core", "TechnologySpec"),
    "build_core_model": ("urbanheatopt.model.reference_core", "build_core_model"),
    "solve_core_model": ("urbanheatopt.model.reference_core", "solve_core_model"),
    "validate_core_input": ("urbanheatopt.model.reference_core", "validate_core_input"),
    # data.canonical
    "CanonicalCaseData": ("urbanheatopt.data.canonical", "CanonicalCaseData"),
    "CanonicalSeasonData": ("urbanheatopt.data.canonical", "CanonicalSeasonData"),
    "PipeTypeSpec": ("urbanheatopt.data.canonical", "PipeTypeSpec"),
    "StorageSpec": ("urbanheatopt.data.canonical", "StorageSpec"),
    # parameters.energy_units
    "EconomicStandardizationError": (
        "urbanheatopt.parameters.energy_units",
        "EconomicStandardizationError",
    ),
    "standardize_gas_price_CNY_per_kWh_LHV": (
        "urbanheatopt.parameters.energy_units",
        "standardize_gas_price_CNY_per_kWh_LHV",
    ),
    # optimization.solvers
    "SolverSettings": ("urbanheatopt.optimization.solvers", "SolverSettings"),
    "solve_pyomo_model": ("urbanheatopt.optimization.solvers", "solve_pyomo_model"),
    "validate_solver_settings": ("urbanheatopt.optimization.solvers", "validate_solver_settings"),
    # optimization.pareto
    "ParetoPoint": ("urbanheatopt.optimization.pareto", "ParetoPoint"),
    "ParetoRun": ("urbanheatopt.optimization.pareto", "ParetoRun"),
    "ParetoSpec": ("urbanheatopt.optimization.pareto", "ParetoSpec"),
    "solve_case_pareto": ("urbanheatopt.optimization.pareto", "solve_case_pareto"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        module_name, attr = _LAZY_EXPORTS[name]
        value = getattr(importlib.import_module(module_name), attr)
        globals()[name] = value  # 首次访问后缓存
        return value
    # 兜底：兼容「import urbanheatopt; urbanheatopt.model.xxx」式的子模块访问
    try:
        module = importlib.import_module(f"{__name__}.{name}")
    except ModuleNotFoundError as exc:
        raise AttributeError(f"module 'urbanheatopt' has no attribute {name!r}") from exc
    globals()[name] = module
    return module


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
