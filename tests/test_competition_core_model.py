"""竞赛层三模式源—网—荷核心模型测试。"""

from __future__ import annotations

from dataclasses import replace

import pytest
from pyomo.environ import ConcreteModel, Constraint, Objective, Var, value
from pyomo.core.base.objective import Objective as ObjectiveComponent
from pyomo.opt import TerminationCondition

import competition.solvers as solver_module
from competition.core_model import (
    CoreModelInput,
    CoreModelInputError,
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


def _technology(role: str, **overrides: object) -> TechnologySpec:
    rows: dict[str, dict[str, object]] = {
        "central_ashp": {
            "technology_id": "central_ashp",
            "technology_type": "air_source_heat_pump",
            "applicable_scope": "central",
            "energy_carrier": "electricity",
            "cop": 4.0,
            "efficiency": None,
            "capacity_min_kW": 0.0,
            "capacity_max_kW": 100.0,
        },
        "central_gas_boiler": {
            "technology_id": "central_gas_boiler",
            "technology_type": "gas_boiler",
            "applicable_scope": "central",
            "energy_carrier": "gas",
            "cop": None,
            "efficiency": 0.9,
            "capacity_min_kW": 0.0,
            "capacity_max_kW": 100.0,
        },
        "local_ashp": {
            "technology_id": "local_ashp",
            "technology_type": "air_source_heat_pump",
            "applicable_scope": "local",
            "energy_carrier": "electricity",
            "cop": 3.5,
            "efficiency": None,
            "capacity_min_kW": 0.0,
            "capacity_max_kW": 100.0,
        },
    }
    values = rows[role]
    values.update(
        {
            "capex_CNY_per_kW": 0.0,
            "fixed_om_CNY_per_kW_year": 0.0,
            "variable_om_CNY_per_kWh_heat": 0.0,
            "lifetime_years": 20,
            "source": "synthetic_test",
            "assumption_flag": "synthetic_test",
        }
    )
    values.update(overrides)
    return TechnologySpec(**values)


def _technologies(**role_overrides: dict[str, object]) -> tuple[TechnologySpec, ...]:
    return tuple(
        _technology(role, **role_overrides.get(role, {}))
        for role in ("central_ashp", "central_gas_boiler", "local_ashp")
    )


def _segment(
    segment_id: str,
    node_u: str,
    node_v: str,
    *,
    capacity_max_kW: float = 100.0,
) -> SegmentSpec:
    return SegmentSpec(
        segment_id=segment_id,
        node_u=node_u,
        node_v=node_v,
        length_m=10.0,
        capacity_max_kW=capacity_max_kW,
    )


def _core_input(**overrides: object) -> CoreModelInput:
    values: dict[str, object] = {
        "mode": "central",
        "hours": (1,),
        "site_node": "site_1",
        "demand_nodes": ("demand_1",),
        "heat_demand_kW": {("demand_1", 1): 100.0},
        "technologies": _technologies(
            central_ashp={"capacity_max_kW": 60.0},
            central_gas_boiler={"capacity_max_kW": 40.0},
        ),
        "segments": (_segment("segment_1", "site_1", "demand_1"),),
    }
    values.update(overrides)
    return CoreModelInput(**values)


def test_central_mode_solves_independent_devices_and_hand_energy_inputs() -> None:
    result = solve_core_model(_core_input(), SolverSettings(mip_gap=0.0))
    model = result.model

    assert result.solver_results.solver.termination_condition == TerminationCondition.optimal
    assert value(model.connected["demand_1"]) == pytest.approx(1)
    assert value(model.local_installed["demand_1"]) == pytest.approx(0)
    assert value(model.central_heat_output_kW["central_ashp", 1]) == pytest.approx(60)
    assert value(model.central_heat_output_kW["central_gas_boiler", 1]) == pytest.approx(40)
    assert value(model.central_electricity_input_kWh_e["central_ashp", 1]) == pytest.approx(15)
    assert value(model.gas_input_kWh_LHV["central_gas_boiler", 1]) == pytest.approx(40 / 0.9)
    assert value(model.unserved_heat_kW["demand_1", 1]) == pytest.approx(0)


def test_core_input_copies_and_freezes_heat_demand_mapping() -> None:
    source = {("demand_1", 1): 100.0}
    data = _core_input(heat_demand_kW=source)

    source[("demand_1", 1)] = 999.0

    assert data.heat_demand_kW[("demand_1", 1)] == pytest.approx(100.0)
    with pytest.raises(TypeError):
        data.heat_demand_kW[("demand_1", 1)] = 50.0


def test_only_active_objective_is_total_unserved_heat_proxy() -> None:
    model = build_core_model(_core_input())
    for node in model.DEMAND_NODES:
        for hour in model.HOURS:
            model.unserved_heat_kW[node, hour].set_value(7.5)

    active_objectives = list(
        model.component_data_objects(ObjectiveComponent, active=True)
    )

    assert active_objectives == [model.temporary_non_economic_proxy]
    assert value(model.temporary_non_economic_proxy) == pytest.approx(
        sum(
            value(model.unserved_heat_kW[node, hour])
            for node in model.DEMAND_NODES
            for hour in model.HOURS
        )
    )


def test_nonzero_economic_fields_are_not_consumed_before_economic_node() -> None:
    technologies = _technologies(
        central_ashp={
            "capex_CNY_per_kW": 111.0,
            "fixed_om_CNY_per_kW_year": 22.0,
            "variable_om_CNY_per_kWh_heat": 3.0,
        },
        central_gas_boiler={
            "capex_CNY_per_kW": 444.0,
            "fixed_om_CNY_per_kW_year": 55.0,
            "variable_om_CNY_per_kWh_heat": 6.0,
        },
        local_ashp={
            "capex_CNY_per_kW": 777.0,
            "fixed_om_CNY_per_kW_year": 88.0,
            "variable_om_CNY_per_kWh_heat": 9.0,
        },
    )
    model = build_core_model(_core_input(technologies=technologies))
    component_names = set(model.component_map())

    assert not any(
        token in component_name.lower()
        for component_name in component_names
        for token in ("capex", "fixed_om", "variable_om", "cost", "price")
    )
    assert list(model.component_data_objects(ObjectiveComponent, active=True)) == [
        model.temporary_non_economic_proxy
    ]


def test_two_hours_convert_power_to_energy_once_without_annual_weight() -> None:
    data = _core_input(
        mode="distributed",
        hours=(1, 2),
        heat_demand_kW={
            ("demand_1", 1): 35.0,
            ("demand_1", 2): 70.0,
        },
        technologies=_technologies(local_ashp={"capacity_max_kW": 70.0}),
        segments=(),
    )
    model = solve_core_model(data).model

    assert value(model.timestep_hours) == pytest.approx(1.0)
    assert value(model.local_electricity_input_kW_e["demand_1", 1]) == pytest.approx(10.0)
    assert value(model.local_electricity_input_kWh_e["demand_1", 1]) == pytest.approx(10.0)
    assert value(model.local_electricity_input_kW_e["demand_1", 2]) == pytest.approx(20.0)
    assert value(model.local_electricity_input_kWh_e["demand_1", 2]) == pytest.approx(20.0)
    assert not any("weight" in name.lower() for name in model.component_map())


def test_distributed_mode_has_no_site_network_or_central_supply() -> None:
    data = _core_input(
        mode="distributed",
        demand_nodes=("demand_1", "demand_2"),
        heat_demand_kW={("demand_1", 1): 20.0, ("demand_2", 1): 30.0},
        segments=(),
        technologies=_technologies(local_ashp={"capacity_max_kW": 30.0}),
    )
    model = solve_core_model(data).model

    assert value(model.site_built) == pytest.approx(0)
    assert all(value(model.connected[node]) == pytest.approx(0) for node in model.DEMAND_NODES)
    assert all(value(model.local_installed[node]) == pytest.approx(1) for node in model.DEMAND_NODES)
    assert all(value(model.unserved_heat_kW[node, 1]) == pytest.approx(0) for node in model.DEMAND_NODES)
    assert sum(value(model.central_heat_output_kW[t, 1]) for t in model.CENTRAL_TECHNOLOGIES) == pytest.approx(0)


def test_hybrid_mode_uses_network_for_reachable_node_and_local_for_unreachable_node() -> None:
    data = _core_input(
        mode="hybrid",
        demand_nodes=("demand_connected", "demand_local"),
        heat_demand_kW={
            ("demand_connected", 1): 40.0,
            ("demand_local", 1): 30.0,
        },
        segments=(
            _segment("segment_1", "site_1", "demand_connected", capacity_max_kW=40),
        ),
        technologies=_technologies(
            central_ashp={"capacity_min_kW": 40.0, "capacity_max_kW": 40.0},
            central_gas_boiler={"capacity_max_kW": 40.0},
            local_ashp={"capacity_min_kW": 30.0, "capacity_max_kW": 30.0},
        ),
    )
    model = solve_core_model(data).model

    assert value(model.connected["demand_connected"]) == pytest.approx(1)
    assert value(model.local_installed["demand_connected"]) == pytest.approx(0)
    assert value(model.connected["demand_local"]) == pytest.approx(0)
    assert value(model.local_installed["demand_local"]) == pytest.approx(1)
    assert value(model.network_heat_kW["demand_connected", 1]) == pytest.approx(40)
    assert value(model.local_heat_output_kW["demand_local", 1]) == pytest.approx(30)


def test_each_physical_segment_has_one_build_capacity_and_signed_flow_variable() -> None:
    model = build_core_model(_core_input())

    assert len(model.SEGMENTS) == 1
    assert len(model.pipe_built) == 1
    assert len(model.pipe_capacity_kW) == 1
    assert len(model.heat_flow_kW) == 1


@pytest.mark.parametrize(
    ("node_u", "node_v", "expected_flow"),
    [("site_1", "demand_1", 100.0), ("demand_1", "site_1", -100.0)],
)
def test_reversing_segment_endpoints_only_reverses_signed_flow(
    node_u: str,
    node_v: str,
    expected_flow: float,
) -> None:
    data = _core_input(segments=(_segment("segment_1", node_u, node_v),))
    model = solve_core_model(data).model

    assert value(model.heat_flow_kW["segment_1", 1]) == pytest.approx(expected_flow)
    assert value(model.pipe_built["segment_1"]) == pytest.approx(1)
    assert value(model.pipe_capacity_kW["segment_1"]) == pytest.approx(100)


def test_multihop_signed_flows_follow_each_segment_endpoint_direction() -> None:
    data = _core_input(
        demand_nodes=("demand_1", "demand_2"),
        heat_demand_kW={("demand_1", 1): 20.0, ("demand_2", 1): 30.0},
        technologies=_technologies(
            central_ashp={"capacity_max_kW": 50.0},
            central_gas_boiler={"capacity_max_kW": 50.0},
        ),
        segments=(
            _segment("site_to_1", "site_1", "demand_1", capacity_max_kW=50),
            _segment("two_to_one", "demand_2", "demand_1", capacity_max_kW=30),
        ),
    )
    model = solve_core_model(data).model

    assert value(model.heat_flow_kW["site_to_1", 1]) == pytest.approx(50.0)
    assert value(model.heat_flow_kW["two_to_one", 1]) == pytest.approx(-30.0)
    assert value(model.network_heat_kW["demand_1", 1]) == pytest.approx(20.0)
    assert value(model.network_heat_kW["demand_2", 1]) == pytest.approx(30.0)


def test_node_hour_heat_balances_are_exact() -> None:
    model = solve_core_model(_core_input()).model

    assert value(model.central_site_heat_balance[1].body) == pytest.approx(0, abs=1e-9)
    assert value(model.network_node_heat_balance["demand_1", 1].body) == pytest.approx(0, abs=1e-9)
    assert value(model.demand_heat_balance["demand_1", 1].body) == pytest.approx(100)
    assert value(model.demand_heat_balance["demand_1", 1].lower) == pytest.approx(100)


def test_forced_capacity_shortage_appears_as_nonzero_unserved_heat() -> None:
    data = _core_input(
        technologies=_technologies(
            central_ashp={"capacity_max_kW": 20.0},
            central_gas_boiler={"capacity_max_kW": 20.0},
        ),
    )
    model = solve_core_model(data).model

    assert value(model.unserved_heat_kW["demand_1", 1]) == pytest.approx(60)


@pytest.mark.parametrize("mode", ["centralized", "distributed_only", "S0", ""])
def test_unknown_mode_is_rejected(mode: str) -> None:
    with pytest.raises(CoreModelInputError, match="mode"):
        validate_core_input(_core_input(mode=mode))


def test_central_mode_rejects_unreachable_demand_topology() -> None:
    data = _core_input(
        demand_nodes=("demand_1", "demand_2"),
        heat_demand_kW={("demand_1", 1): 50.0, ("demand_2", 1): 50.0},
        segments=(_segment("segment_1", "site_1", "demand_1"),),
    )

    with pytest.raises(CoreModelInputError, match="无法从站点到达.*demand_2"):
        validate_core_input(data)


@pytest.mark.parametrize(
    ("segments", "message"),
    [
        (
            (
                _segment("s1", "site_1", "demand_1"),
                _segment("s2", "demand_1", "site_1"),
            ),
            "物理无向管段端点重复",
        ),
        ((_segment("s1", "site_1", "unknown"),), "端点必须引用"),
        ((_segment("s1", "site_1", "site_1"),), "自环"),
        (
            (
                _segment("s1", "site_1", "demand_1"),
                _segment("s1", "site_1", "demand_1"),
            ),
            "segment_id 重复",
        ),
    ],
)
def test_invalid_segment_topology_is_rejected(
    segments: tuple[SegmentSpec, ...],
    message: str,
) -> None:
    with pytest.raises(CoreModelInputError, match=message):
        validate_core_input(_core_input(segments=segments))


def test_invalid_local_ashp_role_is_rejected() -> None:
    data = _core_input(
        technologies=_technologies(local_ashp={"energy_carrier": "gas"})
    )

    with pytest.raises(CoreModelInputError, match="local_ashp.energy_carrier"):
        validate_core_input(data)


@pytest.mark.parametrize(
    ("role", "field", "invalid_value", "message"),
    [
        ("central_ashp", "cop", None, "central_ashp.cop"),
        ("central_ashp", "cop", 0.0, "central_ashp.cop"),
        ("central_ashp", "cop", float("inf"), "central_ashp.cop"),
        ("central_ashp", "efficiency", 0.9, "central_ashp.efficiency"),
        ("central_ashp", "energy_carrier", "gas", "central_ashp.energy_carrier"),
        ("central_gas_boiler", "efficiency", None, "central_gas_boiler.efficiency"),
        ("central_gas_boiler", "efficiency", 0.0, "central_gas_boiler.efficiency"),
        ("central_gas_boiler", "efficiency", 1.01, "central_gas_boiler.efficiency"),
        ("central_gas_boiler", "efficiency", float("nan"), "central_gas_boiler.efficiency"),
        ("central_gas_boiler", "cop", 3.0, "central_gas_boiler.cop"),
        ("central_gas_boiler", "energy_carrier", "electricity", "central_gas_boiler.energy_carrier"),
        ("local_ashp", "cop", 0.0, "local_ashp.cop"),
        ("local_ashp", "efficiency", 0.9, "local_ashp.efficiency"),
    ],
)
def test_invalid_device_performance_or_carrier_is_rejected(
    role: str,
    field: str,
    invalid_value: object,
    message: str,
) -> None:
    data = _core_input(
        technologies=_technologies(**{role: {field: invalid_value}})
    )

    with pytest.raises(CoreModelInputError, match=message):
        validate_core_input(data)


def test_duplicate_technology_ids_are_rejected() -> None:
    data = _core_input(
        technologies=_technologies(
            central_gas_boiler={"technology_id": "central_ashp"}
        )
    )

    with pytest.raises(CoreModelInputError, match="technology_id.*互不相同"):
        validate_core_input(data)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"hours": (0,)}, "1...N"),
        ({"demand_nodes": ("demand_1", "demand_1")}, "不得重复"),
        ({"site_node": "demand_1"}, "不得与"),
        ({"heat_demand_kW": {}}, "全部组合"),
        ({"heat_demand_kW": {("demand_1", 1): -1.0}}, "大于等于 0"),
        ({"heat_demand_kW": {("demand_1", 1): float("nan")}}, "有限数值"),
    ],
)
def test_invalid_hours_nodes_or_demand_are_rejected(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(CoreModelInputError, match=message):
        validate_core_input(_core_input(**overrides))


def test_competition_solver_keeps_infeasible_variables_unloaded() -> None:
    model = ConcreteModel()
    model.x = Var()
    model.objective = Objective(expr=model.x)
    model.lower = Constraint(expr=model.x >= 1)
    model.upper = Constraint(expr=model.x <= 0)

    with pytest.raises(RuntimeError, match="optimal"):
        solve_pyomo_model(model, SolverSettings())
    assert model.x.value is None


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (SolverSettings(name="cbc"), "name"),
        (SolverSettings(mip_gap=-0.1), "mip_gap"),
        (SolverSettings(mip_gap=1.0), "mip_gap"),
        (SolverSettings(threads=2), "threads"),
        (SolverSettings(time_limit_seconds=0), "time_limit_seconds"),
        (SolverSettings(random_seed=-1), "random_seed"),
        (SolverSettings(tee="false"), "tee"),
    ],
)
def test_competition_solver_rejects_invalid_settings_before_factory(
    monkeypatch: pytest.MonkeyPatch,
    settings: SolverSettings,
    message: str,
) -> None:
    monkeypatch.setattr(
        solver_module,
        "SolverFactory",
        lambda _: pytest.fail("非法设置不应创建求解器"),
    )
    with pytest.raises(ValueError, match=message):
        validate_solver_settings(settings)
    with pytest.raises(ValueError, match=message):
        solve_pyomo_model(ConcreteModel(), settings)


def test_competition_solver_stops_when_requested_solver_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solve_called = False

    class UnavailableSolver:
        def available(self, *, exception_flag: bool = True) -> bool:
            assert exception_flag is False
            return False

        def solve(self, *args: object, **kwargs: object) -> object:
            nonlocal solve_called
            solve_called = True
            raise AssertionError("不可用求解器不应进入 solve")

    monkeypatch.setattr(solver_module, "SolverFactory", lambda _: UnavailableSolver())
    model = ConcreteModel()
    model.x = Var()
    with pytest.raises(RuntimeError, match="gurobi.*不可用"):
        solve_pyomo_model(model, SolverSettings(name="gurobi"))
    assert solve_called is False
    assert model.x.value is None


def test_competition_highs_uses_reproducible_options_and_delays_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_factory = solver_module.SolverFactory
    captured: dict[str, object] = {}

    class CapturingSolver:
        def __init__(self, solver: object) -> None:
            self.solver = solver

        def available(self, *, exception_flag: bool = True) -> object:
            captured["exception_flag"] = exception_flag
            return self.solver.available(exception_flag=exception_flag)

        def solve(self, *args: object, **kwargs: object) -> object:
            captured["solve_kwargs"] = kwargs
            return self.solver.solve(*args, **kwargs)

    def capturing_factory(name: str) -> CapturingSolver:
        captured["factory_name"] = name
        return CapturingSolver(real_factory(name))

    monkeypatch.setattr(solver_module, "SolverFactory", capturing_factory)
    model = ConcreteModel()
    model.x = Var()
    model.objective = Objective(expr=model.x)
    model.lower = Constraint(expr=model.x >= 1)
    solve_pyomo_model(model, SolverSettings(mip_gap=0.015))

    assert captured["factory_name"] == "appsi_highs"
    assert captured["exception_flag"] is False
    solve_kwargs = captured["solve_kwargs"]
    assert solve_kwargs["tee"] is False
    assert solve_kwargs["load_solutions"] is False
    assert solve_kwargs["options"] == {
        "mip_rel_gap": 0.015,
        "threads": 1,
        "time_limit": 60.0,
        "random_seed": 202611,
    }
    assert value(model.x) == pytest.approx(1.0)
