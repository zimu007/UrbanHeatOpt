"""Fresh-model epsilon-constraint orchestration for cost and physical carbon."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import gc
from math import hypot, isfinite
from types import MappingProxyType
from time import perf_counter
from typing import Any, Callable, Mapping

from pyomo.environ import Constraint, Objective, minimize, value

from competition.canonical import CanonicalCaseData
from competition.core_model import CoreModelInput, CoreSolveResult, build_core_model
from competition.solvers import SolverSettings, get_solver_evidence, solve_pyomo_model


@dataclass(frozen=True, slots=True)
class ParetoSpec:
    point_count: int
    unserved_tolerance_kWh: float = 1e-6
    cost_tolerance_CNY_per_year: float = 1e-6
    carbon_tolerance_kgCO2e_per_year: float = 1e-6
    epsilon_constraint_tolerance_kgCO2e_per_year: float | None = None


@dataclass(frozen=True, slots=True)
class ParetoPoint:
    point_id: str
    mode: str
    labels: tuple[str, ...]
    epsilon_kgCO2e_per_year: float | None
    annual_real_cost_CNY_per_year: float
    annual_operating_carbon_kgCO2e_per_year: float
    annual_hns_penalty_CNY_per_year: float
    unserved_heat_kWh: float
    is_knee: bool = False
    solve_elapsed_seconds: float = 0.0
    solve_started_at_utc: str | None = None
    solve_finished_at_utc: str | None = None
    solver_status: str = "unknown"
    termination_condition: str = "unknown"
    reported_mip_gap: float | None = None
    incumbent_objective: float | None = None
    best_objective_bound: float | None = None
    reported_wallclock_seconds: float | None = None
    model_sha256: str | None = None
    solver_log_file: str | None = None
    solver_evidence_file: str | None = None


@dataclass(frozen=True, slots=True)
class ParetoRun:
    mode_frontiers: Mapping[str, tuple[ParetoPoint, ...]]
    combined_frontier: tuple[ParetoPoint, ...]
    solutions: Mapping[str, CoreSolveResult]
    mode_all_points: Mapping[str, tuple[ParetoPoint, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode_frontiers", MappingProxyType(dict(self.mode_frontiers)))
        object.__setattr__(self, "solutions", MappingProxyType(dict(self.solutions)))
        object.__setattr__(
            self,
            "mode_all_points",
            MappingProxyType(dict(self.mode_all_points or self.mode_frontiers)),
        )


def _validate_spec(spec: ParetoSpec) -> None:
    if (
        isinstance(spec.point_count, bool)
        or not isinstance(spec.point_count, int)
        or (spec.point_count != 0 and spec.point_count < 3)
    ):
        raise ValueError("Pareto point_count 必须为 0（仅端点）或大于等于 3 的整数")
    for field in (
        "unserved_tolerance_kWh",
        "cost_tolerance_CNY_per_year",
        "carbon_tolerance_kgCO2e_per_year",
        "epsilon_constraint_tolerance_kgCO2e_per_year",
    ):
        number = getattr(spec, field)
        if number is None and field == "epsilon_constraint_tolerance_kgCO2e_per_year":
            continue
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not isfinite(float(number)) or number < 0:
            raise ValueError(f"Pareto {field} 必须是有限非负数")


def _solve_point(
    data: CoreModelInput,
    settings: SolverSettings,
    spec: ParetoSpec,
    *,
    point_id: str,
    labels: tuple[str, ...],
    objective: str,
    epsilon: float | None = None,
    model_builder: Callable[[CoreModelInput], Any] | None = None,
) -> tuple[ParetoPoint, CoreSolveResult]:
    model = (model_builder or build_core_model)(data)
    numerical_reserve = max(1e-12, spec.unserved_tolerance_kWh * 1e-3)
    enforced_unserved_limit = max(
        0.0, spec.unserved_tolerance_kWh - numerical_reserve
    )
    model.pareto_unserved_limit = Constraint(
        expr=sum(
            model.unserved_heat_kW[node, hour] * model.time_weight_h_per_year[hour]
            for node in model.DEMAND_NODES
            for hour in model.HOURS
        )
        <= enforced_unserved_limit
    )
    if epsilon is not None:
        epsilon_tolerance = (
            spec.carbon_tolerance_kgCO2e_per_year
            if spec.epsilon_constraint_tolerance_kgCO2e_per_year is None
            else spec.epsilon_constraint_tolerance_kgCO2e_per_year
        )
        model.pareto_carbon_limit = Constraint(
            expr=model.annual_operating_physical_carbon_kgCO2e_per_year
            <= float(epsilon) + epsilon_tolerance
        )
    if objective == "carbon":
        model.annual_cost_objective.deactivate()
        model.annual_carbon_objective = Objective(
            expr=model.annual_operating_physical_carbon_kgCO2e_per_year,
            sense=minimize,
        )
    elif objective != "cost":
        raise ValueError(f"未知 Pareto objective：{objective}")
    active = list(model.component_data_objects(Objective, active=True))
    if len(active) != 1:
        raise RuntimeError("每个 Pareto 模型必须且只能有一个活动目标")
    solve_started = datetime.now(timezone.utc)
    solve_clock = perf_counter()
    solver_results = solve_pyomo_model(model, settings)
    solve_elapsed = perf_counter() - solve_clock
    solve_finished = datetime.now(timezone.utc)
    solver = solver_results.solver
    evidence = get_solver_evidence(solver_results)
    unserved = float(
        sum(
            value(model.unserved_heat_kW[node, hour])
            * value(model.time_weight_h_per_year[hour])
            for node in model.DEMAND_NODES
            for hour in model.HOURS
        )
    )
    point = ParetoPoint(
        point_id=point_id,
        mode=data.mode,
        labels=labels,
        epsilon_kgCO2e_per_year=epsilon,
        annual_real_cost_CNY_per_year=float(value(model.annual_real_cost_CNY_per_year)),
        annual_operating_carbon_kgCO2e_per_year=float(
            value(model.annual_operating_physical_carbon_kgCO2e_per_year)
        ),
        annual_hns_penalty_CNY_per_year=float(value(model.annual_hns_penalty_CNY_per_year)),
        unserved_heat_kWh=unserved,
        solve_elapsed_seconds=solve_elapsed,
        solve_started_at_utc=solve_started.isoformat(),
        solve_finished_at_utc=solve_finished.isoformat(),
        solver_status=str(getattr(solver, "status", "unknown")),
        termination_condition=str(solver.termination_condition),
        reported_mip_gap=evidence.get("relative_mip_gap"),
        incumbent_objective=evidence.get("incumbent_objective"),
        best_objective_bound=evidence.get("best_objective_bound"),
        reported_wallclock_seconds=evidence.get("elapsed_seconds"),
        model_sha256=evidence.get("model_sha256"),
        solver_log_file=evidence.get("solver_log_file"),
        solver_evidence_file=evidence.get("solver_evidence_file"),
    )
    return point, CoreSolveResult(model=model, solver_results=solver_results)


def _dominates(a: ParetoPoint, b: ParetoPoint, spec: ParetoSpec) -> bool:
    cost_le = a.annual_real_cost_CNY_per_year <= b.annual_real_cost_CNY_per_year + spec.cost_tolerance_CNY_per_year
    carbon_le = a.annual_operating_carbon_kgCO2e_per_year <= b.annual_operating_carbon_kgCO2e_per_year + spec.carbon_tolerance_kgCO2e_per_year
    strict = (
        a.annual_real_cost_CNY_per_year < b.annual_real_cost_CNY_per_year - spec.cost_tolerance_CNY_per_year
        or a.annual_operating_carbon_kgCO2e_per_year < b.annual_operating_carbon_kgCO2e_per_year - spec.carbon_tolerance_kgCO2e_per_year
    )
    return cost_le and carbon_le and strict


def _frontier(points: list[ParetoPoint], spec: ParetoSpec) -> tuple[ParetoPoint, ...]:
    nondominated = [point for point in points if not any(_dominates(other, point, spec) for other in points if other is not point)]
    ordered = sorted(nondominated, key=lambda item: (item.annual_operating_carbon_kgCO2e_per_year, item.annual_real_cost_CNY_per_year, item.point_id))
    unique: list[ParetoPoint] = []
    for point in ordered:
        duplicate = next(
            (
                existing
                for existing in unique
                if abs(existing.annual_real_cost_CNY_per_year - point.annual_real_cost_CNY_per_year) <= spec.cost_tolerance_CNY_per_year
                and abs(existing.annual_operating_carbon_kgCO2e_per_year - point.annual_operating_carbon_kgCO2e_per_year) <= spec.carbon_tolerance_kgCO2e_per_year
            ),
            None,
        )
        if duplicate is None:
            unique.append(point)
        else:
            index = unique.index(duplicate)
            unique[index] = replace(duplicate, labels=tuple(sorted(set(duplicate.labels + point.labels))))
    return tuple(unique)


def _mark_knee(points: tuple[ParetoPoint, ...]) -> tuple[ParetoPoint, ...]:
    if len(points) < 3:
        return points
    costs = [point.annual_real_cost_CNY_per_year for point in points]
    carbons = [point.annual_operating_carbon_kgCO2e_per_year for point in points]
    cost_span = max(costs) - min(costs)
    carbon_span = max(carbons) - min(carbons)
    if cost_span <= 0 or carbon_span <= 0:
        return points
    normalized = [
        ((point.annual_operating_carbon_kgCO2e_per_year - min(carbons)) / carbon_span,
         (point.annual_real_cost_CNY_per_year - min(costs)) / cost_span)
        for point in points
    ]
    x1, y1 = normalized[0]
    x2, y2 = normalized[-1]
    denominator = hypot(y2 - y1, x2 - x1)
    distances = [
        abs((y2 - y1) * x - (x2 - x1) * y + x2 * y1 - y2 * x1) / denominator
        for x, y in normalized
    ]
    knee_index = max(range(1, len(points) - 1), key=lambda index: distances[index])
    return tuple(replace(point, is_knee=index == knee_index) for index, point in enumerate(points))


def _solve_mode_pareto_full(
    data: CoreModelInput,
    settings: SolverSettings,
    spec: ParetoSpec,
    *,
    solution_callback: Callable[[ParetoPoint, CoreSolveResult], None] | None = None,
    retain_solutions: bool = True,
) -> tuple[tuple[ParetoPoint, ...], tuple[ParetoPoint, ...], dict[str, CoreSolveResult]]:
    _validate_spec(spec)
    solutions: dict[str, CoreSolveResult] = {}
    points: list[ParetoPoint] = []
    cost_point, cost_solution = _solve_point(
        data, settings, spec, point_id=f"{data.mode}-cost", labels=("cost_endpoint",), objective="cost"
    )
    points.append(cost_point)
    if solution_callback is not None:
        solution_callback(cost_point, cost_solution)
    if retain_solutions:
        solutions[cost_point.point_id] = cost_solution
    else:
        del cost_solution
        gc.collect()

    carbon_point, carbon_solution = _solve_point(
        data, settings, spec, point_id=f"{data.mode}-carbon", labels=("carbon_endpoint",), objective="carbon"
    )
    points.append(carbon_point)
    if solution_callback is not None:
        solution_callback(carbon_point, carbon_solution)
    if retain_solutions:
        solutions[carbon_point.point_id] = carbon_solution
    else:
        del carbon_solution
        gc.collect()
    low = carbon_point.annual_operating_carbon_kgCO2e_per_year
    high = cost_point.annual_operating_carbon_kgCO2e_per_year
    if high < low - spec.carbon_tolerance_kgCO2e_per_year:
        raise RuntimeError("成本端点碳排低于碳端点，目标切换结果不一致")
    for index in range(spec.point_count):
        epsilon = low if spec.point_count == 1 else low + (high - low) * index / (spec.point_count - 1)
        point, solution = _solve_point(
            data, settings, spec,
            point_id=f"{data.mode}-epsilon-{index:03d}",
            labels=("epsilon",), objective="cost", epsilon=epsilon,
        )
        points.append(point)
        if solution_callback is not None:
            solution_callback(point, solution)
        if retain_solutions:
            solutions[point.point_id] = solution
        else:
            del solution
            gc.collect()
    frontier = _mark_knee(_frontier(points, spec))
    return frontier, tuple(points), solutions


def solve_mode_pareto(
    data: CoreModelInput,
    settings: SolverSettings,
    spec: ParetoSpec,
) -> tuple[tuple[ParetoPoint, ...], dict[str, CoreSolveResult]]:
    """Backward-compatible public mode solver returning its frontier and solutions."""

    frontier, _, solutions = _solve_mode_pareto_full(data, settings, spec)
    return frontier, solutions


def solve_pareto_task(
    data: CoreModelInput,
    settings: SolverSettings,
    spec: ParetoSpec,
    *,
    point_id: str,
    objective: str,
    epsilon_kgCO2e_per_year: float | None = None,
    labels: tuple[str, ...] = (),
    model_builder: Callable[[CoreModelInput], Any] | None = None,
) -> tuple[ParetoPoint, CoreSolveResult]:
    """Solve one independently schedulable point using unchanged core physics."""

    _validate_spec(spec)
    resolved_labels = labels or (
        ("cost_endpoint",)
        if objective == "cost" and epsilon_kgCO2e_per_year is None
        else ("carbon_endpoint",)
        if objective == "carbon"
        else ("epsilon",)
    )
    return _solve_point(
        data,
        settings,
        spec,
        point_id=point_id,
        labels=resolved_labels,
        objective=objective,
        epsilon=epsilon_kgCO2e_per_year,
        model_builder=model_builder,
    )


def assemble_pareto_run(
    points: tuple[ParetoPoint, ...],
    spec: ParetoSpec,
    *,
    modes: tuple[str, ...] = ("central", "distributed", "hybrid"),
) -> ParetoRun:
    """Assemble independently solved points without re-solving any model."""

    _validate_spec(spec)
    grouped: dict[str, tuple[ParetoPoint, ...]] = {}
    frontiers: dict[str, tuple[ParetoPoint, ...]] = {}
    for mode in modes:
        mode_points = tuple(point for point in points if point.mode == mode)
        if not mode_points:
            raise ValueError(f"缺少模式 {mode} 的独立求解点")
        grouped[mode] = mode_points
        frontiers[mode] = _mark_knee(_frontier(list(mode_points), spec))
    combined = _mark_knee(
        _frontier(
            [point for frontier in frontiers.values() for point in frontier],
            spec,
        )
    )
    return ParetoRun(
        mode_frontiers=frontiers,
        combined_frontier=combined,
        solutions={},
        mode_all_points=grouped,
    )


def solve_case_pareto(
    case: CanonicalCaseData,
    spec: ParetoSpec,
    *,
    solution_callback: Callable[[ParetoPoint, CoreSolveResult], None] | None = None,
    retain_solutions: bool = True,
) -> ParetoRun:
    mode_frontiers: dict[str, tuple[ParetoPoint, ...]] = {}
    mode_all_points: dict[str, tuple[ParetoPoint, ...]] = {}
    solutions: dict[str, CoreSolveResult] = {}
    for mode in case.modes:
        frontier, all_points, mode_solutions = _solve_mode_pareto_full(
            case.to_core_input(mode),
            case.solver,
            spec,
            solution_callback=solution_callback,
            retain_solutions=retain_solutions,
        )
        mode_frontiers[mode] = frontier
        mode_all_points[mode] = all_points
        solutions.update(mode_solutions)
    combined = _mark_knee(_frontier([point for points in mode_frontiers.values() for point in points], spec))
    return ParetoRun(
        mode_frontiers=mode_frontiers,
        combined_frontier=combined,
        solutions=solutions,
        mode_all_points=mode_all_points,
    )


def select_representative_points(
    points: tuple[ParetoPoint, ...],
    *,
    policy_carbon_constraint_kgCO2e_per_year: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Select auditable representatives without manufacturing distinct solutions."""

    if not points:
        return {
            name: {"point_id": None, "status": "unavailable_empty_frontier"}
            for name in ("minimum_cost", "minimum_carbon", "normalized_knee", "policy_constraint")
        }
    minimum_cost = min(
        points,
        key=lambda item: (
            item.annual_real_cost_CNY_per_year,
            item.annual_operating_carbon_kgCO2e_per_year,
            item.point_id,
        ),
    )
    minimum_carbon = min(
        points,
        key=lambda item: (
            item.annual_operating_carbon_kgCO2e_per_year,
            item.annual_real_cost_CNY_per_year,
            item.point_id,
        ),
    )
    knee = next((point for point in points if point.is_knee), None)
    policy = None
    if policy_carbon_constraint_kgCO2e_per_year is not None:
        feasible = [
            point
            for point in points
            if point.annual_operating_carbon_kgCO2e_per_year
            <= policy_carbon_constraint_kgCO2e_per_year
        ]
        if feasible:
            policy = min(
                feasible,
                key=lambda item: (
                    item.annual_real_cost_CNY_per_year,
                    item.annual_operating_carbon_kgCO2e_per_year,
                    item.point_id,
                ),
            )

    def record(point: ParetoPoint | None, missing_status: str) -> dict[str, Any]:
        if point is None:
            return {"point_id": None, "status": missing_status}
        return {
            "point_id": point.point_id,
            "mode": point.mode,
            "status": "selected",
            "annual_real_cost_CNY_per_year": point.annual_real_cost_CNY_per_year,
            "annual_operating_carbon_kgCO2e_per_year": (
                point.annual_operating_carbon_kgCO2e_per_year
            ),
        }

    result = {
        "minimum_cost": record(minimum_cost, "unavailable"),
        "minimum_carbon": record(minimum_carbon, "unavailable"),
        "normalized_knee": record(knee, "unavailable_fewer_than_three_distinct_points"),
        "policy_constraint": record(
            policy,
            (
                "not_selected_missing_policy_carbon_constraint"
                if policy_carbon_constraint_kgCO2e_per_year is None
                else "no_frontier_point_satisfies_policy_constraint"
            ),
        ),
    }
    selected_ids = [item["point_id"] for item in result.values() if item["point_id"]]
    duplicates = sorted({point_id for point_id in selected_ids if selected_ids.count(point_id) > 1})
    for item in result.values():
        item["overlaps_other_representative"] = item.get("point_id") in duplicates
    return result


def point_to_dict(point: ParetoPoint) -> dict[str, Any]:
    return {
        "point_id": point.point_id,
        "mode": point.mode,
        "labels": list(point.labels),
        "epsilon_kgCO2e_per_year": point.epsilon_kgCO2e_per_year,
        "annual_real_cost_CNY_per_year": point.annual_real_cost_CNY_per_year,
        "annual_operating_carbon_kgCO2e_per_year": point.annual_operating_carbon_kgCO2e_per_year,
        "annual_hns_penalty_CNY_per_year": point.annual_hns_penalty_CNY_per_year,
        "unserved_heat_kWh": point.unserved_heat_kWh,
        "is_knee": point.is_knee,
        "solver_status": point.solver_status,
        "termination_condition": point.termination_condition,
        "reported_mip_gap": point.reported_mip_gap,
        "incumbent_objective": point.incumbent_objective,
        "best_objective_bound": point.best_objective_bound,
        "reported_wallclock_seconds": point.reported_wallclock_seconds,
        "model_sha256": point.model_sha256,
        "solver_log_file": point.solver_log_file,
        "solver_evidence_file": point.solver_evidence_file,
    }
