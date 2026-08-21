"""Fresh-model epsilon-constraint orchestration for cost and physical carbon."""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import hypot, isfinite
from types import MappingProxyType
from typing import Any, Mapping

from pyomo.environ import Constraint, Objective, minimize, value

from competition.canonical import CanonicalCaseData
from competition.core_model import CoreModelInput, CoreSolveResult, build_core_model
from competition.solvers import SolverSettings, solve_pyomo_model


@dataclass(frozen=True, slots=True)
class ParetoSpec:
    point_count: int
    unserved_tolerance_kWh: float = 1e-6
    cost_tolerance_CNY_per_year: float = 1e-6
    carbon_tolerance_kgCO2e_per_year: float = 1e-6


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


@dataclass(frozen=True, slots=True)
class ParetoRun:
    mode_frontiers: Mapping[str, tuple[ParetoPoint, ...]]
    combined_frontier: tuple[ParetoPoint, ...]
    solutions: Mapping[str, CoreSolveResult]

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode_frontiers", MappingProxyType(dict(self.mode_frontiers)))
        object.__setattr__(self, "solutions", MappingProxyType(dict(self.solutions)))


def _validate_spec(spec: ParetoSpec) -> None:
    if isinstance(spec.point_count, bool) or not isinstance(spec.point_count, int) or spec.point_count < 3:
        raise ValueError("Pareto point_count 必须是大于等于 3 的整数")
    for field in (
        "unserved_tolerance_kWh",
        "cost_tolerance_CNY_per_year",
        "carbon_tolerance_kgCO2e_per_year",
    ):
        number = getattr(spec, field)
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
) -> tuple[ParetoPoint, CoreSolveResult]:
    model = build_core_model(data)
    model.pareto_unserved_limit = Constraint(
        expr=sum(
            model.unserved_heat_kW[node, hour] * model.time_weight_h_per_year[hour]
            for node in model.DEMAND_NODES
            for hour in model.HOURS
        )
        <= spec.unserved_tolerance_kWh
    )
    if epsilon is not None:
        model.pareto_carbon_limit = Constraint(
            expr=model.annual_operating_physical_carbon_kgCO2e_per_year
            <= float(epsilon) + spec.carbon_tolerance_kgCO2e_per_year
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
    solver_results = solve_pyomo_model(model, settings)
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


def solve_mode_pareto(
    data: CoreModelInput,
    settings: SolverSettings,
    spec: ParetoSpec,
) -> tuple[tuple[ParetoPoint, ...], dict[str, CoreSolveResult]]:
    _validate_spec(spec)
    solutions: dict[str, CoreSolveResult] = {}
    points: list[ParetoPoint] = []
    cost_point, cost_solution = _solve_point(
        data, settings, spec, point_id=f"{data.mode}-cost", labels=("cost_endpoint",), objective="cost"
    )
    carbon_point, carbon_solution = _solve_point(
        data, settings, spec, point_id=f"{data.mode}-carbon", labels=("carbon_endpoint",), objective="carbon"
    )
    points.extend((cost_point, carbon_point))
    solutions[cost_point.point_id] = cost_solution
    solutions[carbon_point.point_id] = carbon_solution
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
        solutions[point.point_id] = solution
    return _mark_knee(_frontier(points, spec)), solutions


def solve_case_pareto(case: CanonicalCaseData, spec: ParetoSpec) -> ParetoRun:
    mode_frontiers: dict[str, tuple[ParetoPoint, ...]] = {}
    solutions: dict[str, CoreSolveResult] = {}
    for mode in case.modes:
        frontier, mode_solutions = solve_mode_pareto(case.to_core_input(mode), case.solver, spec)
        mode_frontiers[mode] = frontier
        solutions.update(mode_solutions)
    combined = _mark_knee(_frontier([point for points in mode_frontiers.values() for point in points], spec))
    return ParetoRun(mode_frontiers=mode_frontiers, combined_frontier=combined, solutions=solutions)


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
    }
