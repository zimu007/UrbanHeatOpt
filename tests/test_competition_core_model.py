"""中央空气源热泵与燃气锅炉独立核心模型测试。"""

from __future__ import annotations

from dataclasses import replace

import pytest
from pyomo.environ import ConcreteModel, Constraint, Objective, Var, value
from pyomo.opt import TerminationCondition

import competition.solvers as solver_module
from competition.core_model import (
    CoreModelInput,
    CoreModelInputError,
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


def _ashp(**overrides: object) -> TechnologySpec:
    values: dict[str, object] = {
        "technology_id": "central_ashp",
        "technology_type": "air_source_heat_pump",
        "applicable_scope": "central",
        "energy_carrier": "electricity",
        "capacity_min_kW": 0.0,
        "capacity_max_kW": 60.0,
        "cop": 4.0,
        "efficiency": None,
        "capex_CNY_per_kW": 0.0,
        "fixed_om_CNY_per_kW_year": 0.0,
        "variable_om_CNY_per_kWh_heat": 0.0,
        "lifetime_years": 20,
        "source": "synthetic_test",
        "assumption_flag": "synthetic_test",
    }
    values.update(overrides)
    return TechnologySpec(**values)


def _boiler(**overrides: object) -> TechnologySpec:
    values: dict[str, object] = {
        "technology_id": "central_gas_boiler",
        "technology_type": "gas_boiler",
        "applicable_scope": "central",
        "energy_carrier": "gas",
        "capacity_min_kW": 0.0,
        "capacity_max_kW": 40.0,
        "cop": None,
        "efficiency": 0.9,
        "capex_CNY_per_kW": 0.0,
        "fixed_om_CNY_per_kW_year": 0.0,
        "variable_om_CNY_per_kWh_heat": 0.0,
        "lifetime_years": 20,
        "source": "synthetic_test",
        "assumption_flag": "synthetic_test",
    }
    values.update(overrides)
    return TechnologySpec(**values)


def _core_input(**overrides: object) -> CoreModelInput:
    values: dict[str, object] = {
        "hours": (1,),
        "heat_demand_kW": {1: 100.0},
        "technologies": (_ashp(), _boiler()),
    }
    values.update(overrides)
    return CoreModelInput(**values)


def test_two_central_technologies_have_independent_sets_capacity_and_dispatch() -> None:
    model = build_core_model(_core_input())

    assert list(model.TECHNOLOGIES) == ["central_ashp", "central_gas_boiler"]
    assert list(model.AIR_SOURCE_HEAT_PUMPS) == ["central_ashp"]
    assert list(model.GAS_BOILERS) == ["central_gas_boiler"]
    assert len(model.installed_capacity_kW) == 2
    assert len(model.heat_output_kW) == 2


def test_highs_solves_forced_split_and_energy_inputs_match_hand_calculation() -> None:
    result = solve_core_model(
        _core_input(),
        SolverSettings(name="highs", mip_gap=0.0),
    )
    model = result.model

    assert result.solver_results.solver.termination_condition == (
        TerminationCondition.optimal
    )
    assert value(model.installed_capacity_kW["central_ashp"]) == pytest.approx(60.0)
    assert value(model.installed_capacity_kW["central_gas_boiler"]) == pytest.approx(
        40.0
    )
    assert value(model.heat_output_kW["central_ashp", 1]) == pytest.approx(60.0)
    assert value(model.heat_output_kW["central_gas_boiler", 1]) == pytest.approx(40.0)
    assert value(model.electricity_input_kW_e["central_ashp", 1]) == pytest.approx(
        15.0
    )
    assert value(model.electricity_input_kWh_e["central_ashp", 1]) == pytest.approx(
        15.0
    )
    assert value(model.gas_input_kW_LHV["central_gas_boiler", 1]) == pytest.approx(
        40.0 / 0.9
    )
    assert value(model.gas_input_kWh_LHV["central_gas_boiler", 1]) == pytest.approx(
        40.0 / 0.9
    )
    assert value(model.central_heat_balance[1].body) == pytest.approx(100.0)


@pytest.mark.parametrize(
    ("field", "invalid_value", "message"),
    [
        ("cop", None, "cop"),
        ("cop", 0.0, "cop"),
        ("cop", float("inf"), "cop"),
        ("efficiency", 0.9, "efficiency"),
        ("energy_carrier", "gas", "energy_carrier"),
        ("applicable_scope", "local", "applicable_scope"),
        ("technology_type", "gas_boiler", "technology_type"),
        ("capacity_min_kW", -1.0, "capacity_min_kW"),
        ("capacity_max_kW", 0.0, "capacity_max_kW"),
    ],
)
def test_invalid_central_ashp_is_rejected_before_model_build(
    field: str,
    invalid_value: object,
    message: str,
) -> None:
    data = replace(
        _core_input(),
        technologies=(replace(_ashp(), **{field: invalid_value}), _boiler()),
    )

    with pytest.raises(CoreModelInputError, match=message):
        validate_core_input(data)


@pytest.mark.parametrize(
    ("field", "invalid_value", "message"),
    [
        ("efficiency", None, "efficiency"),
        ("efficiency", 0.0, "efficiency"),
        ("efficiency", 1.01, "efficiency"),
        ("efficiency", float("nan"), "efficiency"),
        ("cop", 3.0, "cop"),
        ("energy_carrier", "electricity", "energy_carrier"),
        ("applicable_scope", "local", "applicable_scope"),
        ("technology_type", "air_source_heat_pump", "technology_type"),
        ("capacity_max_kW", 10.0, "capacity_max_kW"),
    ],
)
def test_invalid_central_gas_boiler_is_rejected_before_model_build(
    field: str,
    invalid_value: object,
    message: str,
) -> None:
    boiler = replace(_boiler(), **{field: invalid_value})
    if field == "capacity_max_kW":
        boiler = replace(boiler, capacity_min_kW=20.0)
    data = replace(_core_input(), technologies=(_ashp(), boiler))

    with pytest.raises(CoreModelInputError, match=message):
        validate_core_input(data)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"hours": (0,)}, "1...N"),
        ({"hours": (1, 3), "heat_demand_kW": {1: 1.0, 3: 1.0}}, "1...N"),
        ({"heat_demand_kW": {}}, "全部小时"),
        ({"heat_demand_kW": {1: -1.0}}, "大于等于 0"),
        ({"heat_demand_kW": {1: float("nan")}}, "有限数值"),
        ({"heat_demand_kW": {1: 101.0}}, "容量上限"),
    ],
)
def test_invalid_hour_or_demand_is_rejected(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(CoreModelInputError, match=message):
        validate_core_input(_core_input(**overrides))


def test_duplicate_technology_id_is_rejected() -> None:
    data = replace(
        _core_input(),
        technologies=(
            _ashp(),
            replace(_boiler(), technology_id="central_ashp"),
        ),
    )

    with pytest.raises(CoreModelInputError, match="technology_id.*不同"):
        validate_core_input(data)


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

    monkeypatch.setattr(
        solver_module,
        "SolverFactory",
        lambda _: UnavailableSolver(),
    )
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

    solve_pyomo_model(
        model,
        SolverSettings(mip_gap=0.015, time_limit_seconds=60, random_seed=202611),
    )

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
