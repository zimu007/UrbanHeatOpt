"""竞赛版中央双设备最小核心模型。

本节点只建立一个汇总中央热负荷，以及独立的中央空气源热泵和中央
燃气锅炉。三种供热模式、建筑接入、管网、分布式设备和正式年化成本
均不属于本模块当前版本。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from numbers import Integral, Real
from typing import Any, Mapping

from pyomo.environ import (
    ConcreteModel,
    Constraint,
    Expression,
    NonNegativeReals,
    Objective,
    Param,
    Set,
    Var,
    minimize,
)

from competition.solvers import SolverSettings, solve_pyomo_model


AIR_SOURCE_HEAT_PUMP = "air_source_heat_pump"
GAS_BOILER = "gas_boiler"
CENTRAL_SCOPE = "central"
ELECTRICITY = "electricity"
GAS = "gas"


class CoreModelInputError(ValueError):
    """中央双设备核心输入不满足物理或接口约束。"""


@dataclass(frozen=True, slots=True)
class TechnologySpec:
    """逐列对应 competition_input_v2 ``technologies.csv``。"""

    technology_id: str
    technology_type: str
    applicable_scope: str
    energy_carrier: str
    cop: float | None
    efficiency: float | None
    capacity_min_kW: float
    capacity_max_kW: float
    capex_CNY_per_kW: float
    fixed_om_CNY_per_kW_year: float
    variable_om_CNY_per_kWh_heat: float
    lifetime_years: int
    source: str
    assumption_flag: str


@dataclass(frozen=True, slots=True)
class CoreModelInput:
    """一个中央负荷和两类独立中央设备的最小输入。"""

    hours: tuple[int, ...]
    heat_demand_kW: Mapping[int, float]
    technologies: tuple[TechnologySpec, ...]


@dataclass(frozen=True, slots=True)
class CoreSolveResult:
    """已加载最优变量值的核心模型及原始求解器结果。"""

    model: ConcreteModel
    solver_results: Any


def _finite_real(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise CoreModelInputError(f"{field} 必须是有限数值")
    normalized = float(value)
    if not isfinite(normalized):
        raise CoreModelInputError(f"{field} 必须是有限数值")
    return normalized


def _validate_capacity(spec: TechnologySpec, role: str) -> None:
    minimum = _finite_real(spec.capacity_min_kW, f"{role}.capacity_min_kW")
    maximum = _finite_real(spec.capacity_max_kW, f"{role}.capacity_max_kW")
    if minimum < 0:
        raise CoreModelInputError(f"{role}.capacity_min_kW 必须大于等于 0")
    if maximum <= 0:
        raise CoreModelInputError(f"{role}.capacity_max_kW 必须大于 0")
    if maximum < minimum:
        raise CoreModelInputError(
            f"{role}.capacity_max_kW 必须大于等于 capacity_min_kW"
        )


def _validate_contract_metadata(spec: TechnologySpec, role: str) -> None:
    for field in (
        "capex_CNY_per_kW",
        "fixed_om_CNY_per_kW_year",
        "variable_om_CNY_per_kWh_heat",
    ):
        value = _finite_real(getattr(spec, field), f"{role}.{field}")
        if value < 0:
            raise CoreModelInputError(f"{role}.{field} 必须大于等于 0")
    if (
        isinstance(spec.lifetime_years, bool)
        or not isinstance(spec.lifetime_years, Integral)
        or int(spec.lifetime_years) < 1
    ):
        raise CoreModelInputError(f"{role}.lifetime_years 必须是大于等于 1 的整数")
    if not isinstance(spec.source, str) or not spec.source.strip():
        raise CoreModelInputError(f"{role}.source 必须是非空字符串")
    if spec.source != spec.source.strip():
        raise CoreModelInputError(f"{role}.source 不得含首尾空白")
    allowed_flags = {
        "measured",
        "manufacturer",
        "literature",
        "project_confirmed",
        "scenario_assumption",
        "synthetic_test",
    }
    if spec.assumption_flag not in allowed_flags:
        raise CoreModelInputError(
            f"{role}.assumption_flag 必须是 v2 契约允许值"
        )


def _validate_id(technology_id: object, role: str) -> None:
    if (
        not isinstance(technology_id, str)
        or not technology_id
        or technology_id != technology_id.strip()
    ):
        raise CoreModelInputError(f"{role}.technology_id 必须是无首尾空白的非空字符串")


def _validate_ashp(spec: TechnologySpec) -> None:
    role = "central_ashp"
    _validate_id(spec.technology_id, role)
    _validate_capacity(spec, role)
    _validate_contract_metadata(spec, role)
    if spec.technology_type != AIR_SOURCE_HEAT_PUMP:
        raise CoreModelInputError(f"{role}.technology_type 必须为 {AIR_SOURCE_HEAT_PUMP}")
    if spec.applicable_scope != CENTRAL_SCOPE:
        raise CoreModelInputError(f"{role}.applicable_scope 必须为 central")
    if spec.energy_carrier != ELECTRICITY:
        raise CoreModelInputError(f"{role}.energy_carrier 必须为 electricity")
    cop = _finite_real(spec.cop, f"{role}.cop")
    if cop <= 0:
        raise CoreModelInputError(f"{role}.cop 必须大于 0")
    if spec.efficiency is not None:
        raise CoreModelInputError(f"{role}.efficiency 必须为空")


def _validate_gas_boiler(spec: TechnologySpec) -> None:
    role = "central_gas_boiler"
    _validate_id(spec.technology_id, role)
    _validate_capacity(spec, role)
    _validate_contract_metadata(spec, role)
    if spec.technology_type != GAS_BOILER:
        raise CoreModelInputError(f"{role}.technology_type 必须为 {GAS_BOILER}")
    if spec.applicable_scope != CENTRAL_SCOPE:
        raise CoreModelInputError(f"{role}.applicable_scope 必须为 central")
    if spec.energy_carrier != GAS:
        raise CoreModelInputError(f"{role}.energy_carrier 必须为 gas")
    efficiency = _finite_real(spec.efficiency, f"{role}.efficiency")
    if not 0 < efficiency <= 1:
        raise CoreModelInputError(f"{role}.efficiency 必须在 (0, 1] 内")
    if spec.cop is not None:
        raise CoreModelInputError(f"{role}.cop 必须为空")


def _resolve_central_technologies(
    technologies: tuple[TechnologySpec, ...],
) -> tuple[TechnologySpec, TechnologySpec]:
    if not isinstance(technologies, tuple):
        raise CoreModelInputError("technologies 必须是 TechnologySpec tuple")
    if any(not isinstance(spec, TechnologySpec) for spec in technologies):
        raise CoreModelInputError("technologies 必须全部是 TechnologySpec")
    if len(technologies) != 2:
        raise CoreModelInputError(
            "节点 2 technologies 必须且只能包含一个中央空气源热泵和一个中央燃气锅炉"
        )

    # 先按设备类型识别候选行，再由各角色验证器给出精确到字段的错误；
    # type 本身缺失或重复时才返回角色集合错误。
    ashp_by_type = [
        spec for spec in technologies if spec.technology_type == AIR_SOURCE_HEAT_PUMP
    ]
    boiler_by_type = [
        spec for spec in technologies if spec.technology_type == GAS_BOILER
    ]
    if len(ashp_by_type) == 1 and len(boiler_by_type) == 1:
        _validate_ashp(ashp_by_type[0])
        _validate_gas_boiler(boiler_by_type[0])
        return ashp_by_type[0], boiler_by_type[0]

    raise CoreModelInputError(
        "technologies 必须由 type/scope/carrier 唯一派生出中央空气源热泵和中央燃气锅炉；"
        "technology_type 必须各有一个 air_source_heat_pump 和 gas_boiler"
    )


def validate_core_input(data: CoreModelInput) -> None:
    """验证中央双设备输入；任何错误均在构建 Pyomo 模型前失败。"""

    if not isinstance(data, CoreModelInput):
        raise TypeError("data 必须是 CoreModelInput")
    if not isinstance(data.hours, tuple) or not data.hours:
        raise CoreModelInputError("hours 必须是非空 tuple")
    if any(isinstance(hour, bool) or not isinstance(hour, Integral) for hour in data.hours):
        raise CoreModelInputError("hours 必须全部为整数")
    normalized_hours = tuple(int(hour) for hour in data.hours)
    if normalized_hours != tuple(range(1, len(normalized_hours) + 1)):
        raise CoreModelInputError("hours 必须严格连续为 1...N")

    central_ashp, central_gas_boiler = _resolve_central_technologies(
        data.technologies
    )
    if central_ashp.technology_id == central_gas_boiler.technology_id:
        raise CoreModelInputError("中央空气源热泵和燃气锅炉的 technology_id 必须不同")

    if not isinstance(data.heat_demand_kW, Mapping):
        raise CoreModelInputError("heat_demand_kW 必须是 hour 到 kW 的映射")
    demand_keys = tuple(data.heat_demand_kW.keys())
    if any(isinstance(hour, bool) or not isinstance(hour, Integral) for hour in demand_keys):
        raise CoreModelInputError("heat_demand_kW 的键必须为整数小时")
    if set(int(hour) for hour in demand_keys) != set(normalized_hours):
        raise CoreModelInputError("heat_demand_kW 必须且只能覆盖 hours 中的全部小时")

    demand_values: list[float] = []
    for hour in normalized_hours:
        demand = _finite_real(data.heat_demand_kW[hour], f"heat_demand_kW[{hour}]")
        if demand < 0:
            raise CoreModelInputError(f"heat_demand_kW[{hour}] 必须大于等于 0")
        demand_values.append(demand)

    total_maximum = (
        float(central_ashp.capacity_max_kW)
        + float(central_gas_boiler.capacity_max_kW)
    )
    if max(demand_values) > total_maximum:
        raise CoreModelInputError("峰值热负荷超过两类中央设备容量上限之和")


def build_core_model(data: CoreModelInput) -> ConcreteModel:
    """构建无管网、无成本的中央双设备容量与调度模型。"""

    validate_core_input(data)
    ashp, boiler = _resolve_central_technologies(data.technologies)
    technology_ids = (ashp.technology_id, boiler.technology_id)
    specs = {ashp.technology_id: ashp, boiler.technology_id: boiler}

    model = ConcreteModel(name="competition-central-dual-technology-core")
    model.HOURS = Set(initialize=data.hours, ordered=True)
    model.TECHNOLOGIES = Set(initialize=technology_ids, ordered=True)
    model.AIR_SOURCE_HEAT_PUMPS = Set(
        within=model.TECHNOLOGIES,
        initialize=(ashp.technology_id,),
        ordered=True,
    )
    model.GAS_BOILERS = Set(
        within=model.TECHNOLOGIES,
        initialize=(boiler.technology_id,),
        ordered=True,
    )

    model.heat_demand_kW = Param(
        model.HOURS,
        initialize={hour: float(data.heat_demand_kW[hour]) for hour in data.hours},
        within=NonNegativeReals,
    )
    model.capacity_min_kW = Param(
        model.TECHNOLOGIES,
        initialize={key: float(spec.capacity_min_kW) for key, spec in specs.items()},
        within=NonNegativeReals,
    )
    model.capacity_max_kW = Param(
        model.TECHNOLOGIES,
        initialize={key: float(spec.capacity_max_kW) for key, spec in specs.items()},
        within=NonNegativeReals,
    )
    model.cop = Param(
        model.AIR_SOURCE_HEAT_PUMPS,
        initialize={ashp.technology_id: float(ashp.cop)},
        within=NonNegativeReals,
    )
    model.efficiency = Param(
        model.GAS_BOILERS,
        initialize={boiler.technology_id: float(boiler.efficiency)},
        within=NonNegativeReals,
    )
    model.timestep_hours = Param(initialize=1.0)

    model.installed_capacity_kW = Var(model.TECHNOLOGIES, domain=NonNegativeReals)
    model.heat_output_kW = Var(model.TECHNOLOGIES, model.HOURS, domain=NonNegativeReals)

    def capacity_minimum_rule(pyomo_model: ConcreteModel, technology_id: str):
        return (
            pyomo_model.installed_capacity_kW[technology_id]
            >= pyomo_model.capacity_min_kW[technology_id]
        )

    model.capacity_minimum = Constraint(
        model.TECHNOLOGIES,
        rule=capacity_minimum_rule,
    )

    def capacity_maximum_rule(pyomo_model: ConcreteModel, technology_id: str):
        return (
            pyomo_model.installed_capacity_kW[technology_id]
            <= pyomo_model.capacity_max_kW[technology_id]
        )

    model.capacity_maximum = Constraint(
        model.TECHNOLOGIES,
        rule=capacity_maximum_rule,
    )

    def dispatch_capacity_rule(
        pyomo_model: ConcreteModel,
        technology_id: str,
        hour: int,
    ):
        return (
            pyomo_model.heat_output_kW[technology_id, hour]
            <= pyomo_model.installed_capacity_kW[technology_id]
        )

    model.dispatch_capacity = Constraint(
        model.TECHNOLOGIES,
        model.HOURS,
        rule=dispatch_capacity_rule,
    )

    def central_heat_balance_rule(pyomo_model: ConcreteModel, hour: int):
        return sum(
            pyomo_model.heat_output_kW[technology_id, hour]
            for technology_id in pyomo_model.TECHNOLOGIES
        ) == pyomo_model.heat_demand_kW[hour]

    model.central_heat_balance = Constraint(
        model.HOURS,
        rule=central_heat_balance_rule,
    )

    def electricity_input_power_rule(
        pyomo_model: ConcreteModel,
        technology_id: str,
        hour: int,
    ):
        return (
            pyomo_model.heat_output_kW[technology_id, hour]
            / pyomo_model.cop[technology_id]
        )

    model.electricity_input_kW_e = Expression(
        model.AIR_SOURCE_HEAT_PUMPS,
        model.HOURS,
        rule=electricity_input_power_rule,
    )
    model.electricity_input_kWh_e = Expression(
        model.AIR_SOURCE_HEAT_PUMPS,
        model.HOURS,
        rule=lambda pyomo_model, technology_id, hour: (
            pyomo_model.electricity_input_kW_e[technology_id, hour]
            * pyomo_model.timestep_hours
        ),
    )

    def gas_input_power_rule(
        pyomo_model: ConcreteModel,
        technology_id: str,
        hour: int,
    ):
        return (
            pyomo_model.heat_output_kW[technology_id, hour]
            / pyomo_model.efficiency[technology_id]
        )

    model.gas_input_kW_LHV = Expression(
        model.GAS_BOILERS,
        model.HOURS,
        rule=gas_input_power_rule,
    )
    model.gas_input_kWh_LHV = Expression(
        model.GAS_BOILERS,
        model.HOURS,
        rule=lambda pyomo_model, technology_id, hour: (
            pyomo_model.gas_input_kW_LHV[technology_id, hour]
            * pyomo_model.timestep_hours
        ),
    )

    # 这是节点 2 的非经济代理目标，只选择满足峰值所需的最小总容量。
    # 正式年化总成本将在后续经济节点替换它。
    model.temporary_capacity_proxy_objective = Objective(
        expr=sum(
            model.installed_capacity_kW[technology_id]
            for technology_id in model.TECHNOLOGIES
        ),
        sense=minimize,
    )
    return model


def solve_core_model(
    data: CoreModelInput,
    settings: SolverSettings | None = None,
) -> CoreSolveResult:
    """构建并通过竞赛层安全求解接口求解中央双设备模型。"""

    model = build_core_model(data)
    solver_results = solve_pyomo_model(model, settings)
    return CoreSolveResult(model=model, solver_results=solver_results)
