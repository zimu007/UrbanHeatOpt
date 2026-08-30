from __future__ import annotations

from pathlib import Path
from runpy import run_path

import pytest
import yaml
from pyomo.environ import Constraint, Objective, Var, value
from pyomo.opt import TerminationCondition

import model as model_module
from model import HeatNetworkModel


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_highspy_environment_pin_matches_runtime_gate() -> None:
    environment = yaml.safe_load(
        (PROJECT_ROOT / "environment.yml").read_text(encoding="utf-8")
    )
    pip_dependencies = next(
        row["pip"]
        for row in environment["dependencies"]
        if isinstance(row, dict) and "pip" in row
    )
    checker = run_path(str(PROJECT_ROOT / "scripts" / "check_environment.py"))
    expected = checker["EXPECTED_VERSIONS"]["highspy"]

    assert expected == "1.15.1"
    assert f"highspy=={expected}" in pip_dependencies


def _solver_config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "solver": "highs",
        "mip_gap": 0.0,
        "solver_threads": 1,
        "solver_time_limit_seconds": 60,
        "solver_random_seed": 202611,
        "solver_tee": False,
    }
    config.update(overrides)
    return config


def _linear_model(*, infeasible: bool = False) -> HeatNetworkModel:
    model = HeatNetworkModel("solver-interface-test")
    model.x = Var()
    model.objective = Objective(expr=model.x)
    model.lower_bound = Constraint(expr=model.x >= 1)
    if infeasible:
        model.upper_bound = Constraint(expr=model.x <= 0)
    return model


def test_project_config_defaults_to_deterministic_highs() -> None:
    config = yaml.safe_load((PROJECT_ROOT / "_config.yaml").read_text(encoding="utf-8"))
    assert config["solver"] == "highs"
    assert config["solver_threads"] == 1
    assert config["solver_time_limit_seconds"] == 60
    assert config["solver_random_seed"] == 202611
    assert config["solver_tee"] is False


def test_highs_solves_and_loads_only_the_optimal_solution() -> None:
    model = _linear_model()
    assert model.x.value is None

    results = model.model_run(_solver_config())

    assert results.solver.termination_condition == TerminationCondition.optimal
    assert value(model.x) == pytest.approx(1.0)


def test_missing_solver_value_falls_back_to_highs() -> None:
    model = _linear_model()

    results = model.model_run(_solver_config(solver=None))

    assert results.solver.termination_condition == TerminationCondition.optimal
    assert value(model.x) == pytest.approx(1.0)


def test_highs_receives_reproducible_limits_and_delays_solution_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_factory = model_module.SolverFactory
    captured: dict[str, object] = {}

    class CapturingSolver:
        def __init__(self, solver: object) -> None:
            self._solver = solver

        def available(self, *, exception_flag: bool = True) -> object:
            captured["exception_flag"] = exception_flag
            return self._solver.available(exception_flag=exception_flag)

        def solve(self, *args: object, **kwargs: object) -> object:
            captured["solve_kwargs"] = kwargs
            return self._solver.solve(*args, **kwargs)

    def capturing_factory(name: str) -> CapturingSolver:
        captured["factory_name"] = name
        return CapturingSolver(real_factory(name))

    monkeypatch.setattr(model_module, "SolverFactory", capturing_factory)
    model = _linear_model()

    model.model_run(_solver_config(mip_gap=0.015))

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


@pytest.mark.parametrize("solver_name", ["appsi_highs", "cbc"])
def test_unknown_solver_name_fails_before_factory_call(
    monkeypatch: pytest.MonkeyPatch,
    solver_name: str,
) -> None:
    monkeypatch.setattr(
        model_module,
        "SolverFactory",
        lambda _: pytest.fail("未知求解器不应调用 SolverFactory"),
    )
    model = _linear_model()

    with pytest.raises(ValueError, match="highs.*gurobi"):
        model.model_run(_solver_config(solver=solver_name))


def test_unavailable_gurobi_stops_before_solve(monkeypatch: pytest.MonkeyPatch) -> None:
    solve_called = False

    class UnavailableSolver:
        def available(self, *, exception_flag: bool = True) -> bool:
            assert exception_flag is False
            return False

        def solve(self, *args: object, **kwargs: object) -> object:
            nonlocal solve_called
            solve_called = True
            raise AssertionError("不可用求解器不应进入 solve")

    monkeypatch.setattr(model_module, "SolverFactory", lambda name: UnavailableSolver())
    model = _linear_model()

    with pytest.raises(RuntimeError, match="gurobi.*不可用"):
        model.model_run(_solver_config(solver="gurobi"))

    assert solve_called is False
    assert model.x.value is None


def test_infeasible_model_does_not_load_variables() -> None:
    model = _linear_model(infeasible=True)

    with pytest.raises(RuntimeError, match="optimal"):
        model.model_run(_solver_config())

    assert model.x.value is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"mip_gap": -0.1}, "mip_gap"),
        ({"mip_gap": 1.0}, "mip_gap"),
        ({"mip_gap": float("nan")}, "mip_gap"),
        ({"solver_threads": 2}, "solver_threads"),
        ({"solver_time_limit_seconds": 0}, "solver_time_limit_seconds"),
        ({"solver_random_seed": -1}, "solver_random_seed"),
        ({"solver_tee": "false"}, "solver_tee"),
    ],
)
def test_invalid_solver_settings_fail_before_solver_creation(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
    message: str,
) -> None:
    monkeypatch.setattr(
        model_module,
        "SolverFactory",
        lambda _: pytest.fail("非法配置不应创建求解器"),
    )
    model = _linear_model()

    with pytest.raises(ValueError, match=message):
        model.model_run(_solver_config(**overrides))
