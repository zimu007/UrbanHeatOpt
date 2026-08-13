"""竞赛版三模式源—网—荷与年化经济最小核心模型。

本模块独立于旧 ``model.py``。当前只实现固定 COP/效率、一个中央站点、
多个需求节点、无向候选物理管段、未供热量和附件口径的简单年化成本；
储热、余热、管网热损失与多站点均留给后续节点。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import isfinite
from numbers import Integral, Real
from types import MappingProxyType
from typing import Any, Mapping

from pyomo.environ import (
    Binary,
    ConcreteModel,
    Constraint,
    Expression,
    NonNegativeReals,
    Objective,
    Param,
    Reals,
    Set,
    Var,
    minimize,
)

from competition.solvers import SolverSettings, solve_pyomo_model


AIR_SOURCE_HEAT_PUMP = "air_source_heat_pump"
GAS_BOILER = "gas_boiler"
CENTRAL_SCOPE = "central"
LOCAL_SCOPE = "local"
ELECTRICITY = "electricity"
GAS = "gas"
SUPPORTED_MODES = ("central", "distributed", "hybrid")


class CoreModelInputError(ValueError):
    """竞赛核心输入不满足物理或接口约束。"""


@dataclass(frozen=True, slots=True)
class TechnologySpec:
    """逐列对应 competition_input_v2_1 ``technologies.csv``。"""

    technology_id: str
    technology_type: str
    applicable_scope: str
    energy_carrier: str
    cop: float | None
    efficiency: float | None
    capacity_min_kW: float
    capacity_max_kW: float
    capex_CNY_per_kW: float
    fixed_maintenance_fraction_per_year: float
    variable_om_CNY_per_kWh_th: float
    lifetime_years: int
    source: str
    assumption_flag: str


@dataclass(frozen=True, slots=True)
class SegmentSpec:
    """一条物理无向候选管段；端点顺序只定义正流方向。"""

    segment_id: str
    node_u: str
    node_v: str
    length_m: float
    capacity_max_kW: float
    pipe_capex_CNY_per_m: float
    lifetime_years: int


@dataclass(frozen=True, slots=True)
class EconomicInput:
    """竞赛核心使用的年度成本边界。

    逐时映射的键必须与 ``CoreModelInput.hours`` 完全一致。燃气价格已经是
    ``CNY/kWh_LHV``；本核心不接收或换算来源未确认的体积燃气价格。
    """

    time_weight_h_per_year: Mapping[int, float]
    electricity_price_CNY_per_kWh_e: Mapping[int, float]
    gas_price_CNY_per_kWh_LHV: Mapping[int, float]
    expected_weight_sum_h_per_year: float
    connection_capex_CNY: Mapping[str, float]
    connection_lifetime_years: Mapping[str, int]
    hns_penalty_CNY_per_kWh: float

    def __post_init__(self) -> None:
        """复制并冻结所有映射，阻断调用方在构模前后篡改经济输入。"""

        for field in (
            "time_weight_h_per_year",
            "electricity_price_CNY_per_kWh_e",
            "gas_price_CNY_per_kWh_LHV",
            "connection_capex_CNY",
            "connection_lifetime_years",
        ):
            source = getattr(self, field)
            if isinstance(source, Mapping):
                object.__setattr__(self, field, MappingProxyType(dict(source)))


@dataclass(frozen=True, slots=True)
class CoreModelInput:
    """三模式最小核心输入。

    ``heat_demand_kW`` 使用 ``(demand_node, hour)`` 作为键。技术表必须
    恰好投影出中央空气源热泵、中央燃气锅炉和分布式空气源热泵三种角色。
    价格、年化小时权重、接入成本和未供热罚值由 ``economics`` 显式给出。
    """

    mode: str
    hours: tuple[int, ...]
    site_node: str
    demand_nodes: tuple[str, ...]
    heat_demand_kW: Mapping[tuple[str, int], float]
    technologies: tuple[TechnologySpec, ...]
    segments: tuple[SegmentSpec, ...]
    economics: EconomicInput

    def __post_init__(self) -> None:
        """复制并冻结负荷映射，阻断调用方在构模前后篡改输入。"""

        if isinstance(self.heat_demand_kW, Mapping):
            frozen_demand: Mapping[tuple[str, int], float] = MappingProxyType(
                dict(self.heat_demand_kW)
            )
            object.__setattr__(self, "heat_demand_kW", frozen_demand)


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


def _validate_plain_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CoreModelInputError(f"{field} 必须是无首尾空白的非空字符串")
    return value


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
        "fixed_maintenance_fraction_per_year",
        "variable_om_CNY_per_kWh_th",
    ):
        value = _finite_real(getattr(spec, field), f"{role}.{field}")
        if value < 0:
            raise CoreModelInputError(f"{role}.{field} 必须大于等于 0")
        if field == "fixed_maintenance_fraction_per_year" and value > 1:
            raise CoreModelInputError(
                f"{role}.{field} 必须小于等于 1"
            )
    if (
        isinstance(spec.lifetime_years, bool)
        or not isinstance(spec.lifetime_years, Integral)
        or int(spec.lifetime_years) < 1
    ):
        raise CoreModelInputError(f"{role}.lifetime_years 必须是大于等于 1 的整数")
    _validate_plain_id(spec.source, f"{role}.source")
    allowed_flags = {
        "measured",
        "manufacturer",
        "literature",
        "project_confirmed",
        "scenario_assumption",
        "synthetic_test",
    }
    if spec.assumption_flag not in allowed_flags:
        raise CoreModelInputError(f"{role}.assumption_flag 必须是 v2 契约允许值")


def _validate_ashp(spec: TechnologySpec, role: str, scope: str) -> None:
    _validate_plain_id(spec.technology_id, f"{role}.technology_id")
    _validate_capacity(spec, role)
    _validate_contract_metadata(spec, role)
    if spec.technology_type != AIR_SOURCE_HEAT_PUMP:
        raise CoreModelInputError(f"{role}.technology_type 必须为 {AIR_SOURCE_HEAT_PUMP}")
    if spec.applicable_scope != scope:
        raise CoreModelInputError(f"{role}.applicable_scope 必须为 {scope}")
    if spec.energy_carrier != ELECTRICITY:
        raise CoreModelInputError(f"{role}.energy_carrier 必须为 electricity")
    cop = _finite_real(spec.cop, f"{role}.cop")
    if cop <= 0:
        raise CoreModelInputError(f"{role}.cop 必须大于 0")
    if spec.efficiency is not None:
        raise CoreModelInputError(f"{role}.efficiency 必须为空")


def _validate_gas_boiler(spec: TechnologySpec) -> None:
    role = "central_gas_boiler"
    _validate_plain_id(spec.technology_id, f"{role}.technology_id")
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


def _resolve_technology_roles(
    technologies: tuple[TechnologySpec, ...],
) -> tuple[TechnologySpec, TechnologySpec, TechnologySpec]:
    if not isinstance(technologies, tuple):
        raise CoreModelInputError("technologies 必须是 TechnologySpec tuple")
    if any(not isinstance(spec, TechnologySpec) for spec in technologies):
        raise CoreModelInputError("technologies 必须全部是 TechnologySpec")
    if len(technologies) != 3:
        raise CoreModelInputError(
            "technologies 必须且只能包含中央空气源热泵、中央燃气锅炉和分布式空气源热泵"
        )

    central_ashp = [
        spec
        for spec in technologies
        if spec.technology_type == AIR_SOURCE_HEAT_PUMP
        and spec.applicable_scope == CENTRAL_SCOPE
    ]
    central_boiler = [
        spec
        for spec in technologies
        if spec.technology_type == GAS_BOILER
        and spec.applicable_scope == CENTRAL_SCOPE
    ]
    local_ashp = [
        spec
        for spec in technologies
        if spec.technology_type == AIR_SOURCE_HEAT_PUMP
        and spec.applicable_scope == LOCAL_SCOPE
    ]
    if len(central_ashp) != 1 or len(central_boiler) != 1 or len(local_ashp) != 1:
        raise CoreModelInputError(
            "technologies 必须由 technology_type + applicable_scope 唯一派生三个设备角色"
        )

    _validate_ashp(central_ashp[0], "central_ashp", CENTRAL_SCOPE)
    _validate_gas_boiler(central_boiler[0])
    _validate_ashp(local_ashp[0], "local_ashp", LOCAL_SCOPE)
    technology_ids = [spec.technology_id for spec in technologies]
    if len(set(technology_ids)) != len(technology_ids):
        raise CoreModelInputError("三个设备角色的 technology_id 必须互不相同")
    return central_ashp[0], central_boiler[0], local_ashp[0]


def _validate_hours(hours: object) -> tuple[int, ...]:
    if not isinstance(hours, tuple) or not hours:
        raise CoreModelInputError("hours 必须是非空 tuple")
    if any(isinstance(hour, bool) or not isinstance(hour, Integral) for hour in hours):
        raise CoreModelInputError("hours 必须全部为整数")
    normalized = tuple(int(hour) for hour in hours)
    if normalized != tuple(range(1, len(normalized) + 1)):
        raise CoreModelInputError("hours 必须严格连续为 1...N")
    return normalized


def _validate_nodes(data: CoreModelInput) -> tuple[str, ...]:
    site = _validate_plain_id(data.site_node, "site_node")
    if not isinstance(data.demand_nodes, tuple) or not data.demand_nodes:
        raise CoreModelInputError("demand_nodes 必须是非空 tuple")
    nodes = tuple(
        _validate_plain_id(node, f"demand_nodes[{index}]")
        for index, node in enumerate(data.demand_nodes)
    )
    if len(set(nodes)) != len(nodes):
        raise CoreModelInputError("demand_nodes 不得重复")
    if site in set(nodes):
        raise CoreModelInputError("site_node 不得与 demand_nodes 重复")
    return nodes


def _validate_segments(
    segments: object,
    site_node: str,
    demand_nodes: tuple[str, ...],
) -> tuple[SegmentSpec, ...]:
    if not isinstance(segments, tuple):
        raise CoreModelInputError("segments 必须是 SegmentSpec tuple")
    universe = {site_node, *demand_nodes}
    segment_ids: set[str] = set()
    unordered_pairs: set[frozenset[str]] = set()
    for index, segment in enumerate(segments):
        if not isinstance(segment, SegmentSpec):
            raise CoreModelInputError(f"segments[{index}] 必须是 SegmentSpec")
        segment_id = _validate_plain_id(segment.segment_id, f"segments[{index}].segment_id")
        node_u = _validate_plain_id(segment.node_u, f"segments[{index}].node_u")
        node_v = _validate_plain_id(segment.node_v, f"segments[{index}].node_v")
        if segment_id in segment_ids:
            raise CoreModelInputError(f"segment_id 重复：{segment_id}")
        segment_ids.add(segment_id)
        if node_u not in universe or node_v not in universe:
            raise CoreModelInputError(
                f"管段 {segment_id} 端点必须引用 site_node 或 demand_nodes"
            )
        if node_u == node_v:
            raise CoreModelInputError(f"管段 {segment_id} 不得为自环")
        pair = frozenset((node_u, node_v))
        if pair in unordered_pairs:
            raise CoreModelInputError(f"物理无向管段端点重复：{node_u}, {node_v}")
        unordered_pairs.add(pair)
        length = _finite_real(segment.length_m, f"segments[{index}].length_m")
        capacity = _finite_real(
            segment.capacity_max_kW,
            f"segments[{index}].capacity_max_kW",
        )
        pipe_capex = _finite_real(
            segment.pipe_capex_CNY_per_m,
            f"segments[{index}].pipe_capex_CNY_per_m",
        )
        if length <= 0:
            raise CoreModelInputError(f"segments[{index}].length_m 必须大于 0")
        if capacity <= 0:
            raise CoreModelInputError(
                f"segments[{index}].capacity_max_kW 必须大于 0"
            )
        if pipe_capex < 0:
            raise CoreModelInputError(
                f"segments[{index}].pipe_capex_CNY_per_m 必须大于等于 0"
            )
        if (
            isinstance(segment.lifetime_years, bool)
            or not isinstance(segment.lifetime_years, Integral)
            or int(segment.lifetime_years) < 1
        ):
            raise CoreModelInputError(
                f"segments[{index}].lifetime_years 必须是大于等于 1 的整数"
            )
    return segments


def _validate_hourly_mapping(
    values: object,
    hours: tuple[int, ...],
    field: str,
    *,
    strictly_positive: bool,
) -> dict[int, float]:
    if not isinstance(values, Mapping):
        raise CoreModelInputError(f"{field} 必须是 hour 到数值的映射")
    if any(
        isinstance(hour, bool) or not isinstance(hour, Integral)
        for hour in values.keys()
    ):
        raise CoreModelInputError(f"{field} 的 hour 键必须全部为整数")
    if set(values.keys()) != set(hours):
        raise CoreModelInputError(f"{field} 必须且只能覆盖 hours 中的全部小时")
    normalized: dict[int, float] = {}
    for hour in hours:
        value = _finite_real(values[hour], f"{field}[{hour}]")
        if strictly_positive and value <= 0:
            raise CoreModelInputError(f"{field}[{hour}] 必须大于 0")
        if not strictly_positive and value < 0:
            raise CoreModelInputError(f"{field}[{hour}] 必须大于等于 0")
        normalized[hour] = value
    return normalized


def _validate_economics(
    economics: object,
    hours: tuple[int, ...],
    demand_nodes: tuple[str, ...],
) -> None:
    if not isinstance(economics, EconomicInput):
        raise CoreModelInputError("economics 必须是 EconomicInput")

    weights = _validate_hourly_mapping(
        economics.time_weight_h_per_year,
        hours,
        "economics.time_weight_h_per_year",
        strictly_positive=True,
    )
    _validate_hourly_mapping(
        economics.electricity_price_CNY_per_kWh_e,
        hours,
        "economics.electricity_price_CNY_per_kWh_e",
        strictly_positive=False,
    )
    _validate_hourly_mapping(
        economics.gas_price_CNY_per_kWh_LHV,
        hours,
        "economics.gas_price_CNY_per_kWh_LHV",
        strictly_positive=False,
    )
    expected_weight = _finite_real(
        economics.expected_weight_sum_h_per_year,
        "economics.expected_weight_sum_h_per_year",
    )
    if expected_weight <= 0:
        raise CoreModelInputError(
            "economics.expected_weight_sum_h_per_year 必须大于 0"
        )
    if abs(sum(weights.values()) - expected_weight) > 1e-9:
        raise CoreModelInputError(
            "time_weight_h_per_year 之和必须与 expected_weight_sum_h_per_year 一致"
        )

    for field in ("connection_capex_CNY", "connection_lifetime_years"):
        values = getattr(economics, field)
        if not isinstance(values, Mapping) or set(values.keys()) != set(demand_nodes):
            raise CoreModelInputError(f"economics.{field} 必须且只能覆盖 demand_nodes")
    for node in demand_nodes:
        capex = _finite_real(
            economics.connection_capex_CNY[node],
            f"economics.connection_capex_CNY[{node!r}]",
        )
        if capex < 0:
            raise CoreModelInputError(
                f"economics.connection_capex_CNY[{node!r}] 必须大于等于 0"
            )
        lifetime = economics.connection_lifetime_years[node]
        if (
            isinstance(lifetime, bool)
            or not isinstance(lifetime, Integral)
            or int(lifetime) < 1
        ):
            raise CoreModelInputError(
                f"economics.connection_lifetime_years[{node!r}] 必须是大于等于 1 的整数"
            )

    penalty = _finite_real(
        economics.hns_penalty_CNY_per_kWh,
        "economics.hns_penalty_CNY_per_kWh",
    )
    if penalty < 0:
        raise CoreModelInputError(
            "economics.hns_penalty_CNY_per_kWh 必须大于等于 0"
        )


def _reachable_nodes(site_node: str, segments: tuple[SegmentSpec, ...]) -> set[str]:
    adjacency: dict[str, set[str]] = {}
    for segment in segments:
        adjacency.setdefault(segment.node_u, set()).add(segment.node_v)
        adjacency.setdefault(segment.node_v, set()).add(segment.node_u)
    reached = {site_node}
    pending: deque[str] = deque((site_node,))
    while pending:
        node = pending.popleft()
        for neighbor in adjacency.get(node, set()):
            if neighbor not in reached:
                reached.add(neighbor)
                pending.append(neighbor)
    return reached


def validate_core_input(data: CoreModelInput) -> None:
    """在创建 Pyomo 组件前聚合验证三模式核心输入。"""

    if not isinstance(data, CoreModelInput):
        raise TypeError("data 必须是 CoreModelInput")
    if data.mode not in SUPPORTED_MODES:
        raise CoreModelInputError("mode 只能是 'central'、'distributed' 或 'hybrid'")
    hours = _validate_hours(data.hours)
    demand_nodes = _validate_nodes(data)
    _resolve_technology_roles(data.technologies)
    segments = _validate_segments(data.segments, data.site_node, demand_nodes)
    _validate_economics(data.economics, hours, demand_nodes)

    if not isinstance(data.heat_demand_kW, Mapping):
        raise CoreModelInputError("heat_demand_kW 必须是 (demand_node, hour) 到 kW 的映射")
    expected_keys = {(node, hour) for node in demand_nodes for hour in hours}
    if any(
        not isinstance(key, tuple)
        or len(key) != 2
        or isinstance(key[1], bool)
        or not isinstance(key[1], Integral)
        for key in data.heat_demand_kW.keys()
    ):
        raise CoreModelInputError("heat_demand_kW 的 hour 键必须全部为整数")
    actual_keys = set(data.heat_demand_kW.keys())
    if actual_keys != expected_keys:
        raise CoreModelInputError(
            "heat_demand_kW 必须且只能覆盖 demand_nodes × hours 的全部组合"
        )
    for node, hour in expected_keys:
        demand = _finite_real(
            data.heat_demand_kW[(node, hour)],
            f"heat_demand_kW[{node!r}, {hour}]",
        )
        if demand < 0:
            raise CoreModelInputError(
                f"heat_demand_kW[{node!r}, {hour}] 必须大于等于 0"
            )

    if data.mode == "central":
        unreachable = set(demand_nodes) - _reachable_nodes(data.site_node, segments)
        if unreachable:
            joined = ", ".join(sorted(unreachable))
            raise CoreModelInputError(f"central 模式候选拓扑无法从站点到达：{joined}")


def build_core_model(data: CoreModelInput) -> ConcreteModel:
    """构建固定性能、无热损失和简单年化经济目标的三模式模型。"""

    validate_core_input(data)
    central_ashp, boiler, local_ashp = _resolve_technology_roles(data.technologies)
    central_specs = {
        central_ashp.technology_id: central_ashp,
        boiler.technology_id: boiler,
    }
    central_ids = tuple(central_specs)
    segment_by_id = {segment.segment_id: segment for segment in data.segments}
    segment_ids = tuple(segment_by_id)
    all_nodes = (data.site_node, *data.demand_nodes)

    incidence = {
        (node, segment_id): (
            -1
            if node == segment_by_id[segment_id].node_u
            else 1
            if node == segment_by_id[segment_id].node_v
            else 0
        )
        for node in all_nodes
        for segment_id in segment_ids
    }

    model = ConcreteModel(name=f"competition-{data.mode}-source-network-load-core")
    model.mode = data.mode
    model.site_node = data.site_node
    model.local_technology_id = local_ashp.technology_id
    model.HOURS = Set(initialize=data.hours, ordered=True)
    model.DEMAND_NODES = Set(initialize=data.demand_nodes, ordered=True)
    model.NODES = Set(initialize=all_nodes, ordered=True)
    model.CENTRAL_TECHNOLOGIES = Set(initialize=central_ids, ordered=True)
    model.CENTRAL_AIR_SOURCE_HEAT_PUMPS = Set(
        within=model.CENTRAL_TECHNOLOGIES,
        initialize=(central_ashp.technology_id,),
        ordered=True,
    )
    model.CENTRAL_GAS_BOILERS = Set(
        within=model.CENTRAL_TECHNOLOGIES,
        initialize=(boiler.technology_id,),
        ordered=True,
    )
    model.SEGMENTS = Set(initialize=segment_ids, ordered=True)

    model.heat_demand_kW = Param(
        model.DEMAND_NODES,
        model.HOURS,
        initialize={key: float(value) for key, value in data.heat_demand_kW.items()},
        within=NonNegativeReals,
    )
    model.central_capacity_min_kW = Param(
        model.CENTRAL_TECHNOLOGIES,
        initialize={key: float(spec.capacity_min_kW) for key, spec in central_specs.items()},
        within=NonNegativeReals,
    )
    model.central_capacity_max_kW = Param(
        model.CENTRAL_TECHNOLOGIES,
        initialize={key: float(spec.capacity_max_kW) for key, spec in central_specs.items()},
        within=NonNegativeReals,
    )
    model.central_ashp_cop = Param(
        model.CENTRAL_AIR_SOURCE_HEAT_PUMPS,
        initialize={central_ashp.technology_id: float(central_ashp.cop)},
        within=NonNegativeReals,
    )
    model.central_boiler_efficiency = Param(
        model.CENTRAL_GAS_BOILERS,
        initialize={boiler.technology_id: float(boiler.efficiency)},
        within=NonNegativeReals,
    )
    model.local_ashp_cop = Param(initialize=float(local_ashp.cop))
    model.local_capacity_min_kW = Param(initialize=float(local_ashp.capacity_min_kW))
    model.local_capacity_max_kW = Param(initialize=float(local_ashp.capacity_max_kW))
    model.segment_capacity_max_kW = Param(
        model.SEGMENTS,
        initialize={key: float(segment.capacity_max_kW) for key, segment in segment_by_id.items()},
        within=NonNegativeReals,
    )
    model.segment_length_m = Param(
        model.SEGMENTS,
        initialize={key: float(segment.length_m) for key, segment in segment_by_id.items()},
        within=NonNegativeReals,
    )
    model.pipe_capex_CNY_per_m = Param(
        model.SEGMENTS,
        initialize={
            key: float(segment.pipe_capex_CNY_per_m)
            for key, segment in segment_by_id.items()
        },
        within=NonNegativeReals,
    )
    model.pipe_lifetime_years = Param(
        model.SEGMENTS,
        initialize={key: int(segment.lifetime_years) for key, segment in segment_by_id.items()},
        within=NonNegativeReals,
    )
    model.time_weight_h_per_year = Param(
        model.HOURS,
        initialize={
            hour: float(data.economics.time_weight_h_per_year[hour])
            for hour in data.hours
        },
        within=NonNegativeReals,
    )
    model.electricity_price_CNY_per_kWh_e = Param(
        model.HOURS,
        initialize={
            hour: float(data.economics.electricity_price_CNY_per_kWh_e[hour])
            for hour in data.hours
        },
        within=NonNegativeReals,
    )
    model.gas_price_CNY_per_kWh_LHV = Param(
        model.HOURS,
        initialize={
            hour: float(data.economics.gas_price_CNY_per_kWh_LHV[hour])
            for hour in data.hours
        },
        within=NonNegativeReals,
    )
    model.central_capex_CNY_per_kW = Param(
        model.CENTRAL_TECHNOLOGIES,
        initialize={key: float(spec.capex_CNY_per_kW) for key, spec in central_specs.items()},
        within=NonNegativeReals,
    )
    model.central_fixed_maintenance_fraction_per_year = Param(
        model.CENTRAL_TECHNOLOGIES,
        initialize={
            key: float(spec.fixed_maintenance_fraction_per_year)
            for key, spec in central_specs.items()
        },
        within=NonNegativeReals,
    )
    model.central_variable_om_CNY_per_kWh_th = Param(
        model.CENTRAL_TECHNOLOGIES,
        initialize={
            key: float(spec.variable_om_CNY_per_kWh_th)
            for key, spec in central_specs.items()
        },
        within=NonNegativeReals,
    )
    model.central_lifetime_years = Param(
        model.CENTRAL_TECHNOLOGIES,
        initialize={key: int(spec.lifetime_years) for key, spec in central_specs.items()},
        within=NonNegativeReals,
    )
    model.local_capex_CNY_per_kW = Param(initialize=float(local_ashp.capex_CNY_per_kW))
    model.local_fixed_maintenance_fraction_per_year = Param(
        initialize=float(local_ashp.fixed_maintenance_fraction_per_year)
    )
    model.local_variable_om_CNY_per_kWh_th = Param(
        initialize=float(local_ashp.variable_om_CNY_per_kWh_th)
    )
    model.local_lifetime_years = Param(initialize=int(local_ashp.lifetime_years))
    model.connection_capex_CNY = Param(
        model.DEMAND_NODES,
        initialize={
            node: float(data.economics.connection_capex_CNY[node])
            for node in data.demand_nodes
        },
        within=NonNegativeReals,
    )
    model.connection_lifetime_years = Param(
        model.DEMAND_NODES,
        initialize={
            node: int(data.economics.connection_lifetime_years[node])
            for node in data.demand_nodes
        },
        within=NonNegativeReals,
    )
    model.hns_penalty_CNY_per_kWh = Param(
        initialize=float(data.economics.hns_penalty_CNY_per_kWh),
        within=NonNegativeReals,
    )
    model.incidence = Param(model.NODES, model.SEGMENTS, initialize=incidence)
    model.timestep_hours = Param(initialize=1.0)

    model.site_built = Var(domain=Binary)
    model.connected = Var(model.DEMAND_NODES, domain=Binary)
    model.central_installed = Var(model.CENTRAL_TECHNOLOGIES, domain=Binary)
    model.central_capacity_kW = Var(model.CENTRAL_TECHNOLOGIES, domain=NonNegativeReals)
    model.central_heat_output_kW = Var(
        model.CENTRAL_TECHNOLOGIES,
        model.HOURS,
        domain=NonNegativeReals,
    )
    model.local_installed = Var(model.DEMAND_NODES, domain=Binary)
    model.local_capacity_kW = Var(model.DEMAND_NODES, domain=NonNegativeReals)
    model.local_heat_output_kW = Var(
        model.DEMAND_NODES,
        model.HOURS,
        domain=NonNegativeReals,
    )
    model.pipe_built = Var(model.SEGMENTS, domain=Binary)
    model.pipe_capacity_kW = Var(model.SEGMENTS, domain=NonNegativeReals)
    model.heat_flow_kW = Var(model.SEGMENTS, model.HOURS, domain=Reals)
    model.network_heat_kW = Var(
        model.DEMAND_NODES,
        model.HOURS,
        domain=NonNegativeReals,
    )
    model.topology_flow = Var(model.SEGMENTS, domain=Reals)
    model.unserved_heat_kW = Var(
        model.DEMAND_NODES,
        model.HOURS,
        domain=NonNegativeReals,
    )

    model.central_capacity_minimum = Constraint(
        model.CENTRAL_TECHNOLOGIES,
        rule=lambda m, technology_id: m.central_capacity_kW[technology_id]
        >= m.central_capacity_min_kW[technology_id]
        * m.central_installed[technology_id],
    )
    model.central_capacity_maximum = Constraint(
        model.CENTRAL_TECHNOLOGIES,
        rule=lambda m, technology_id: m.central_capacity_kW[technology_id]
        <= m.central_capacity_max_kW[technology_id]
        * m.central_installed[technology_id],
    )
    model.central_dispatch_capacity = Constraint(
        model.CENTRAL_TECHNOLOGIES,
        model.HOURS,
        rule=lambda m, technology_id, hour: m.central_heat_output_kW[
            technology_id, hour
        ]
        <= m.central_capacity_kW[technology_id],
    )
    model.local_capacity_minimum = Constraint(
        model.DEMAND_NODES,
        rule=lambda m, node: m.local_capacity_kW[node]
        >= m.local_capacity_min_kW * m.local_installed[node],
    )
    model.local_capacity_maximum = Constraint(
        model.DEMAND_NODES,
        rule=lambda m, node: m.local_capacity_kW[node]
        <= m.local_capacity_max_kW * m.local_installed[node],
    )
    model.local_dispatch_capacity = Constraint(
        model.DEMAND_NODES,
        model.HOURS,
        rule=lambda m, node, hour: m.local_heat_output_kW[node, hour]
        <= m.local_capacity_kW[node],
    )
    model.service_exclusive = Constraint(
        model.DEMAND_NODES,
        rule=lambda m, node: m.local_installed[node] + m.connected[node] == 1,
    )

    model.connection_requires_site = Constraint(
        model.DEMAND_NODES,
        rule=lambda m, node: m.connected[node] <= m.site_built,
    )
    model.central_technology_requires_site = Constraint(
        model.CENTRAL_TECHNOLOGIES,
        rule=lambda m, technology_id: m.central_installed[technology_id]
        <= m.site_built,
    )
    model.site_requires_central_technology = Constraint(
        expr=model.site_built
        <= sum(model.central_installed[technology_id] for technology_id in central_ids)
    )
    model.site_requires_connection = Constraint(
        expr=model.site_built
        <= sum(model.connected[node] for node in data.demand_nodes)
    )
    model.pipe_requires_site = Constraint(
        model.SEGMENTS,
        rule=lambda m, segment_id: m.pipe_built[segment_id] <= m.site_built,
    )

    model.pipe_capacity_limit = Constraint(
        model.SEGMENTS,
        rule=lambda m, segment_id: m.pipe_capacity_kW[segment_id]
        <= m.segment_capacity_max_kW[segment_id] * m.pipe_built[segment_id],
    )
    model.heat_flow_upper = Constraint(
        model.SEGMENTS,
        model.HOURS,
        rule=lambda m, segment_id, hour: m.heat_flow_kW[segment_id, hour]
        <= m.pipe_capacity_kW[segment_id],
    )
    model.heat_flow_lower = Constraint(
        model.SEGMENTS,
        model.HOURS,
        rule=lambda m, segment_id, hour: m.heat_flow_kW[segment_id, hour]
        >= -m.pipe_capacity_kW[segment_id],
    )

    commodity_big_m = len(data.demand_nodes)
    model.topology_flow_upper = Constraint(
        model.SEGMENTS,
        rule=lambda m, segment_id: m.topology_flow[segment_id]
        <= commodity_big_m * m.pipe_built[segment_id],
    )
    model.topology_flow_lower = Constraint(
        model.SEGMENTS,
        rule=lambda m, segment_id: m.topology_flow[segment_id]
        >= -commodity_big_m * m.pipe_built[segment_id],
    )
    model.topology_demand_balance = Constraint(
        model.DEMAND_NODES,
        rule=lambda m, node: sum(
            m.incidence[node, segment_id] * m.topology_flow[segment_id]
            for segment_id in m.SEGMENTS
        )
        == m.connected[node],
    )
    model.topology_site_balance = Constraint(
        expr=sum(
            model.incidence[data.site_node, segment_id]
            * model.topology_flow[segment_id]
            for segment_id in segment_ids
        )
        == -sum(model.connected[node] for node in data.demand_nodes)
    )

    model.central_site_heat_balance = Constraint(
        model.HOURS,
        rule=lambda m, hour: sum(
            m.central_heat_output_kW[technology_id, hour]
            for technology_id in m.CENTRAL_TECHNOLOGIES
        )
        + sum(
            m.incidence[data.site_node, segment_id] * m.heat_flow_kW[segment_id, hour]
            for segment_id in m.SEGMENTS
        )
        == 0,
    )
    model.network_node_heat_balance = Constraint(
        model.DEMAND_NODES,
        model.HOURS,
        rule=lambda m, node, hour: sum(
            m.incidence[node, segment_id] * m.heat_flow_kW[segment_id, hour]
            for segment_id in m.SEGMENTS
        )
        == m.network_heat_kW[node, hour],
    )
    model.network_heat_connection_limit = Constraint(
        model.DEMAND_NODES,
        model.HOURS,
        rule=lambda m, node, hour: m.network_heat_kW[node, hour]
        <= m.heat_demand_kW[node, hour] * m.connected[node],
    )

    reachable = _reachable_nodes(data.site_node, data.segments)
    unreachable_demands = tuple(
        node for node in data.demand_nodes if node not in reachable
    )
    model.UNREACHABLE_DEMAND_NODES = Set(
        within=model.DEMAND_NODES,
        initialize=unreachable_demands,
        ordered=True,
    )
    model.unreachable_cannot_connect = Constraint(
        model.UNREACHABLE_DEMAND_NODES,
        rule=lambda m, node: m.connected[node] == 0,
    )
    model.demand_heat_balance = Constraint(
        model.DEMAND_NODES,
        model.HOURS,
        rule=lambda m, node, hour: m.network_heat_kW[node, hour]
        + m.local_heat_output_kW[node, hour]
        + m.unserved_heat_kW[node, hour]
        == m.heat_demand_kW[node, hour],
    )

    if data.mode == "central":
        model.mode_site = Constraint(expr=model.site_built == 1)
        model.mode_connections = Constraint(
            model.DEMAND_NODES,
            rule=lambda m, node: m.connected[node] == 1,
        )
    elif data.mode == "distributed":
        model.mode_site = Constraint(expr=model.site_built == 0)
        model.mode_connections = Constraint(
            model.DEMAND_NODES,
            rule=lambda m, node: m.connected[node] == 0,
        )
        model.mode_pipes = Constraint(
            model.SEGMENTS,
            rule=lambda m, segment_id: m.pipe_built[segment_id] == 0,
        )
        model.mode_central_technologies = Constraint(
            model.CENTRAL_TECHNOLOGIES,
            rule=lambda m, technology_id: m.central_installed[technology_id] == 0,
        )

    model.central_electricity_input_kW_e = Expression(
        model.CENTRAL_AIR_SOURCE_HEAT_PUMPS,
        model.HOURS,
        rule=lambda m, technology_id, hour: m.central_heat_output_kW[
            technology_id, hour
        ]
        / m.central_ashp_cop[technology_id],
    )
    model.central_electricity_input_kWh_e = Expression(
        model.CENTRAL_AIR_SOURCE_HEAT_PUMPS,
        model.HOURS,
        rule=lambda m, technology_id, hour: m.central_electricity_input_kW_e[
            technology_id, hour
        ]
        * m.timestep_hours,
    )
    model.gas_input_kW_LHV = Expression(
        model.CENTRAL_GAS_BOILERS,
        model.HOURS,
        rule=lambda m, technology_id, hour: m.central_heat_output_kW[
            technology_id, hour
        ]
        / m.central_boiler_efficiency[technology_id],
    )
    model.gas_input_kWh_LHV = Expression(
        model.CENTRAL_GAS_BOILERS,
        model.HOURS,
        rule=lambda m, technology_id, hour: m.gas_input_kW_LHV[
            technology_id, hour
        ]
        * m.timestep_hours,
    )
    model.local_electricity_input_kW_e = Expression(
        model.DEMAND_NODES,
        model.HOURS,
        rule=lambda m, node, hour: m.local_heat_output_kW[node, hour]
        / m.local_ashp_cop,
    )
    model.local_electricity_input_kWh_e = Expression(
        model.DEMAND_NODES,
        model.HOURS,
        rule=lambda m, node, hour: m.local_electricity_input_kW_e[node, hour]
        * m.timestep_hours,
    )

    # 附件口径：所有投资均采用“原始投资 / 寿命”的简单年化，不使用折现率、
    # CRF、规划期、残值或更换投资。固定运维采用附件批准口径：装机容量
    # 乘单位投资，再乘无量纲年度固定维护比例。
    model.annual_device_capex_CNY_per_year = Expression(
        expr=sum(
            model.central_capacity_kW[technology_id]
            * model.central_capex_CNY_per_kW[technology_id]
            / model.central_lifetime_years[technology_id]
            for technology_id in model.CENTRAL_TECHNOLOGIES
        )
        + sum(
            model.local_capacity_kW[node]
            * model.local_capex_CNY_per_kW
            / model.local_lifetime_years
            for node in model.DEMAND_NODES
        )
    )
    model.annual_network_capex_CNY_per_year = Expression(
        expr=sum(
            model.pipe_built[segment_id]
            * model.segment_length_m[segment_id]
            * model.pipe_capex_CNY_per_m[segment_id]
            / model.pipe_lifetime_years[segment_id]
            for segment_id in model.SEGMENTS
        )
    )
    model.annual_connection_capex_CNY_per_year = Expression(
        expr=sum(
            model.connected[node]
            * model.connection_capex_CNY[node]
            / model.connection_lifetime_years[node]
            for node in model.DEMAND_NODES
        )
    )
    model.annual_fixed_om_CNY_per_year = Expression(
        expr=sum(
            model.central_capacity_kW[technology_id]
            * model.central_capex_CNY_per_kW[technology_id]
            * model.central_fixed_maintenance_fraction_per_year[technology_id]
            for technology_id in model.CENTRAL_TECHNOLOGIES
        )
        + sum(
            model.local_capacity_kW[node]
            * model.local_capex_CNY_per_kW
            * model.local_fixed_maintenance_fraction_per_year
            for node in model.DEMAND_NODES
        )
    )
    model.annual_variable_om_CNY_per_year = Expression(
        expr=sum(
            model.central_heat_output_kW[technology_id, hour]
            * model.time_weight_h_per_year[hour]
            * model.central_variable_om_CNY_per_kWh_th[technology_id]
            for technology_id in model.CENTRAL_TECHNOLOGIES
            for hour in model.HOURS
        )
        + sum(
            model.local_heat_output_kW[node, hour]
            * model.time_weight_h_per_year[hour]
            * model.local_variable_om_CNY_per_kWh_th
            for node in model.DEMAND_NODES
            for hour in model.HOURS
        )
    )
    model.annual_electricity_cost_CNY_per_year = Expression(
        expr=sum(
            model.central_electricity_input_kW_e[technology_id, hour]
            * model.time_weight_h_per_year[hour]
            * model.electricity_price_CNY_per_kWh_e[hour]
            for technology_id in model.CENTRAL_AIR_SOURCE_HEAT_PUMPS
            for hour in model.HOURS
        )
        + sum(
            model.local_electricity_input_kW_e[node, hour]
            * model.time_weight_h_per_year[hour]
            * model.electricity_price_CNY_per_kWh_e[hour]
            for node in model.DEMAND_NODES
            for hour in model.HOURS
        )
    )
    model.annual_gas_cost_CNY_per_year = Expression(
        expr=sum(
            model.gas_input_kW_LHV[technology_id, hour]
            * model.time_weight_h_per_year[hour]
            * model.gas_price_CNY_per_kWh_LHV[hour]
            for technology_id in model.CENTRAL_GAS_BOILERS
            for hour in model.HOURS
        )
    )
    model.annual_real_cost_CNY_per_year = Expression(
        expr=model.annual_device_capex_CNY_per_year
        + model.annual_network_capex_CNY_per_year
        + model.annual_connection_capex_CNY_per_year
        + model.annual_fixed_om_CNY_per_year
        + model.annual_variable_om_CNY_per_year
        + model.annual_electricity_cost_CNY_per_year
        + model.annual_gas_cost_CNY_per_year
    )
    model.annual_hns_penalty_CNY_per_year = Expression(
        expr=sum(
            model.unserved_heat_kW[node, hour]
            * model.time_weight_h_per_year[hour]
            * model.hns_penalty_CNY_per_kWh
            for node in model.DEMAND_NODES
            for hour in model.HOURS
        )
    )
    model.optimization_objective_CNY_per_year = Expression(
        expr=model.annual_real_cost_CNY_per_year
        + model.annual_hns_penalty_CNY_per_year
    )
    model.annual_cost_objective = Objective(
        expr=model.optimization_objective_CNY_per_year,
        sense=minimize,
    )

    active_objectives = list(model.component_data_objects(Objective, active=True))
    if active_objectives != [model.annual_cost_objective]:
        raise RuntimeError("竞赛经济模型必须且只能有一个活动的正式年化成本目标")
    return model


def solve_core_model(
    data: CoreModelInput,
    settings: SolverSettings | None = None,
) -> CoreSolveResult:
    """构建并通过竞赛层安全求解接口求解三模式核心。"""

    model = build_core_model(data)
    solver_results = solve_pyomo_model(model, settings)
    return CoreSolveResult(model=model, solver_results=solver_results)
