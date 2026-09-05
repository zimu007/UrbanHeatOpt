"""UrbanHeatOpt 竞赛版扩展包。

本包承载竞赛输入契约、适配、流程编排与报告扩展，不替代或宣称上游程序已实现这些能力。
"""

from urbanheatopt.model.reference_core import (
    CoreModelInput,
    CoreModelInputError,
    CoreSolveResult,
    EconomicInput,
    SegmentSpec,
    TechnologySpec,
    build_core_model,
    solve_core_model,
    validate_core_input,
)
from urbanheatopt.data.canonical import CanonicalCaseData, CanonicalSeasonData, PipeTypeSpec, StorageSpec
from urbanheatopt.parameters.energy_units import (
    EconomicStandardizationError,
    standardize_gas_price_CNY_per_kWh_LHV,
)
from urbanheatopt.optimization.solvers import (
    SolverSettings,
    solve_pyomo_model,
    validate_solver_settings,
)
from urbanheatopt.optimization.pareto import ParetoPoint, ParetoRun, ParetoSpec, solve_case_pareto

__all__ = [
    "CoreModelInput",
    "CanonicalCaseData",
    "CanonicalSeasonData",
    "CoreModelInputError",
    "CoreSolveResult",
    "EconomicInput",
    "EconomicStandardizationError",
    "SegmentSpec",
    "PipeTypeSpec",
    "ParetoPoint",
    "ParetoRun",
    "ParetoSpec",
    "SolverSettings",
    "TechnologySpec",
    "StorageSpec",
    "build_core_model",
    "solve_core_model",
    "solve_case_pareto",
    "solve_pyomo_model",
    "standardize_gas_price_CNY_per_kWh_LHV",
    "validate_core_input",
    "validate_solver_settings",
]
