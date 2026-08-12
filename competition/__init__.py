"""UrbanHeatOpt 竞赛版扩展包。

本包承载竞赛输入契约、适配、流程编排与报告扩展，不替代或宣称上游程序已实现这些能力。
"""

from competition.core_model import (
    CoreModelInput,
    CoreModelInputError,
    CoreSolveResult,
    SegmentSpec,
    TechnologySpec,
    build_core_model,
    solve_core_model,
    validate_core_input,
)
from competition.solvers import (
    SolverSettings,
    solve_pyomo_model,
    validate_solver_settings,
)

__all__ = [
    "CoreModelInput",
    "CoreModelInputError",
    "CoreSolveResult",
    "SegmentSpec",
    "SolverSettings",
    "TechnologySpec",
    "build_core_model",
    "solve_core_model",
    "solve_pyomo_model",
    "validate_core_input",
    "validate_solver_settings",
]
