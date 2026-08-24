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
    EconomicInput,
    PipeLevelSpec,
    SegmentSpec,
    TechnologySpec,
    ThermalStorageSpec,
    build_core_model,
    solve_core_model,
    validate_core_input,
)
from competition.economics import (
    EconomicStandardizationError,
    standardize_gas_price_CNY_per_kWh_LHV,
)
from competition.costing.annualized import capital_recovery_factor
from competition.solvers import (
    SolverSettings,
    solve_pyomo_model,
    validate_solver_settings,
)


COST_ABS_TOL_CNY_PER_YEAR = 1e-6


def _cost_approx(expected: float) -> object:
    """年度成本验收只使用绝对误差，不随金额大小放宽。"""

    return pytest.approx(expected, rel=0, abs=COST_ABS_TOL_CNY_PER_YEAR)


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
            "fixed_maintenance_fraction_per_year": 0.0,
            "variable_om_CNY_per_kWh_th": 0.0,
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
    length_m: float = 10.0,
    pipe_capex_CNY_per_m: float = 0.0,
    lifetime_years: int = 25,
) -> SegmentSpec:
    return SegmentSpec(
        segment_id=segment_id,
        node_u=node_u,
        node_v=node_v,
        length_m=length_m,
        capacity_max_kW=capacity_max_kW,
        pipe_capex_CNY_per_m=pipe_capex_CNY_per_m,
        lifetime_years=lifetime_years,
    )


def _economics(
    *,
    hours: tuple[int, ...] = (1,),
    demand_nodes: tuple[str, ...] = ("demand_1",),
    weights: dict[int, float] | None = None,
    electricity_prices: dict[int, float] | None = None,
    gas_prices: dict[int, float] | None = None,
    connection_capex: dict[str, float] | None = None,
    connection_lifetimes: dict[str, int] | None = None,
    hns_penalty: float = 1_000_000.0,
    expected_weight_sum: float | None = None,
) -> EconomicInput:
    weight_values = weights or {hour: 1.0 for hour in hours}
    return EconomicInput(
        time_weight_h_per_year=weight_values,
        electricity_price_CNY_per_kWh_e=(
            electricity_prices or {hour: 0.0 for hour in hours}
        ),
        gas_price_CNY_per_kWh_LHV=(
            gas_prices or {hour: 0.0 for hour in hours}
        ),
        expected_weight_sum_h_per_year=(
            sum(weight_values.values())
            if expected_weight_sum is None
            else expected_weight_sum
        ),
        connection_capex_CNY=(
            connection_capex or {node: 0.0 for node in demand_nodes}
        ),
        connection_lifetime_years=(
            connection_lifetimes or {node: 20 for node in demand_nodes}
        ),
        hns_penalty_CNY_per_kWh=hns_penalty,
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
    values.setdefault(
        "economics",
        _economics(
            hours=values["hours"],
            demand_nodes=values["demand_nodes"],
        ),
    )
    return CoreModelInput(**values)


def test_peak_capacity_margin_is_configurable_and_excludes_storage() -> None:
    data = _core_input(
        peak_capacity_margin_fraction=0.2,
        technologies=_technologies(
            central_ashp={"capacity_max_kW": 120.0, "capex_CNY_per_kW": 1.0},
            central_gas_boiler={"capacity_max_kW": 120.0, "capex_CNY_per_kW": 10.0},
        ),
        storage=ThermalStorageSpec(
            technology_id="storage",
            energy_capacity_max_kWh_th=1000.0,
            charge_capacity_max_kW_th=1000.0,
            discharge_capacity_max_kW_th=1000.0,
            charge_efficiency=1.0,
            discharge_efficiency=1.0,
            standing_loss_fraction_per_hour=0.0,
            capex_CNY_per_kWh_th=0.0,
            power_capex_CNY_per_kW_th=0.0,
            fixed_capex_CNY=0.0,
            lifetime_years=20,
        ),
    )
    solved = solve_core_model(data, SolverSettings(name="highs", mip_gap=0.0))
    model = solved.model
    installed_capacity = sum(
        value(model.central_capacity_kW[technology_id])
        for technology_id in model.CENTRAL_TECHNOLOGIES
    )
    assert installed_capacity == pytest.approx(120.0, rel=0, abs=1e-6)
    assert value(model.storage_discharge_capacity_kW) <= 1000.0
    assert value(model.peak_capacity_margin_fraction) == pytest.approx(0.2)


def test_distributed_peak_margin_applies_per_demand_node() -> None:
    data = _core_input(
        mode="distributed",
        peak_capacity_margin_fraction=0.2,
        technologies=_technologies(
            local_ashp={"capacity_max_kW": 150.0, "capex_CNY_per_kW": 1.0},
        ),
    )
    solved = solve_core_model(data, SolverSettings(name="highs", mip_gap=0.0))
    assert value(solved.model.local_capacity_kW["demand_1"]) == pytest.approx(
        120.0, rel=0, abs=1e-6
    )


@pytest.mark.parametrize("margin", [-0.01, 1.01, float("nan"), True])
def test_peak_capacity_margin_rejects_invalid_values(margin) -> None:
    with pytest.raises(CoreModelInputError, match="peak_capacity_margin_fraction"):
        validate_core_input(_core_input(peak_capacity_margin_fraction=margin))


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


def test_crf_annualizes_device_and_station_capex() -> None:
    economics = replace(
        _economics(),
        discount_rate=0.05,
        station_fixed_capex_CNY=20_000.0,
        station_lifetime_years=30,
    )
    technologies = _technologies(
        central_ashp={"capacity_max_kW": 100.0, "capex_CNY_per_kW": 1_000.0},
        central_gas_boiler={"capacity_max_kW": 100.0, "capex_CNY_per_kW": 1_000_000.0},
    )
    model = solve_core_model(
        _core_input(technologies=technologies, economics=economics)
    ).model
    expected_device = 100.0 * 1_000.0 * capital_recovery_factor(0.05, 20)
    expected_station = 20_000.0 * capital_recovery_factor(0.05, 30)
    assert value(model.annual_device_capex_CNY_per_year) == _cost_approx(expected_device)
    assert value(model.annual_station_capex_CNY_per_year) == _cost_approx(expected_station)


def test_operating_carbon_and_policy_cost_are_separate_from_real_cost() -> None:
    economics = replace(
        _economics(),
        electricity_carbon_kgCO2e_per_kWh_e={1: 0.4},
        gas_carbon_kgCO2e_per_kWh_LHV={1: 0.2},
        policy_carbon_price_CNY_per_tCO2e=100.0,
    )
    model = solve_core_model(_core_input(economics=economics)).model
    expected_electric = 60.0 / 4.0 * 0.4
    expected_gas = 40.0 / 0.9 * 0.2
    expected_carbon = expected_electric + expected_gas
    assert value(model.annual_electricity_carbon_kgCO2e_per_year) == pytest.approx(expected_electric)
    assert value(model.annual_gas_carbon_kgCO2e_per_year) == pytest.approx(expected_gas)
    assert value(model.annual_operating_physical_carbon_kgCO2e_per_year) == pytest.approx(expected_carbon)
    assert value(model.annual_policy_carbon_cost_CNY_per_year) == _cost_approx(expected_carbon / 1000 * 100)
    assert value(model.annual_policy_adjusted_cost_CNY_per_year) == _cost_approx(
        value(model.annual_real_cost_CNY_per_year) + expected_carbon / 1000 * 100
    )
    assert value(model.optimization_objective_CNY_per_year) == _cost_approx(
        value(model.annual_real_cost_CNY_per_year)
        + value(model.annual_hns_penalty_CNY_per_year)
    )


def test_carbon_factor_hour_keys_and_discount_rate_fail_before_build() -> None:
    with pytest.raises(CoreModelInputError, match="必须且只能覆盖"):
        validate_core_input(
            _core_input(
                economics=replace(
                    _economics(),
                    electricity_carbon_kgCO2e_per_kWh_e={2: 0.4},
                )
            )
        )


def test_three_pipe_levels_select_smallest_feasible_physical_capacity() -> None:
    levels = (
        PipeLevelSpec("dn1", 1, 50, 1, 30),
        PipeLevelSpec("dn2", 2, 100, 2, 30),
        PipeLevelSpec("dn3", 3, 150, 3, 30),
    )
    model = solve_core_model(_core_input(pipe_levels=levels)).model
    assert value(model.pipe_level_built["segment_1", "dn1"]) == pytest.approx(0)
    assert value(model.pipe_level_built["segment_1", "dn2"]) == pytest.approx(1)
    assert value(model.pipe_level_built["segment_1", "dn3"]) == pytest.approx(0)
    assert value(model.pipe_capacity_kW["segment_1"]) == pytest.approx(100)


def test_precomputed_cop_and_capacity_derating_are_used_hour_by_hour() -> None:
    hours = (1, 2)
    economics = _economics(
        hours=hours,
        electricity_prices={1: 1, 2: 1},
        gas_prices={1: 100, 2: 100},
    )
    model = solve_core_model(
        _core_input(
            hours=hours,
            heat_demand_kW={("demand_1", 1): 50, ("demand_1", 2): 50},
            technologies=_technologies(
                central_ashp={"capacity_max_kW": 100},
                central_gas_boiler={"capacity_max_kW": 100},
            ),
            economics=economics,
            heat_pump_cop_by_hour={
                ("central_ashp", 1): 4,
                ("central_ashp", 2): 2,
                ("local_ashp", 1): 3,
                ("local_ashp", 2): 3,
            },
            heat_pump_capacity_ratio_by_hour={
                ("central_ashp", 1): 0.5,
                ("central_ashp", 2): 0.25,
                ("local_ashp", 1): 1,
                ("local_ashp", 2): 1,
            },
        )
    ).model
    assert value(model.central_capacity_kW["central_ashp"]) == pytest.approx(100)
    assert value(model.central_heat_output_kW["central_ashp", 1]) == pytest.approx(50)
    assert value(model.central_heat_output_kW["central_ashp", 2]) == pytest.approx(25)
    assert value(model.central_heat_output_kW["central_gas_boiler", 2]) == pytest.approx(25)
    assert value(model.central_electricity_input_kW_e["central_ashp", 1]) == pytest.approx(12.5)
    assert value(model.central_electricity_input_kW_e["central_ashp", 2]) == pytest.approx(12.5)


def test_linear_pipe_loss_and_pumping_enter_balance_cost_and_carbon() -> None:
    levels = (
        PipeLevelSpec("dn1", 1, 50, 1, 30, 0.1, 0.02),
        PipeLevelSpec("dn2", 2, 100, 2, 30, 0.1, 0.02),
        PipeLevelSpec("dn3", 3, 150, 3, 30, 0.1, 0.02),
    )
    economics = replace(
        _economics(electricity_prices={1: 1}, gas_prices={1: 100}),
        electricity_carbon_kgCO2e_per_kWh_e={1: 0.4},
        gas_carbon_kgCO2e_per_kWh_LHV={1: 0.2},
    )
    model = solve_core_model(
        _core_input(
            heat_demand_kW={("demand_1", 1): 50},
            pipe_levels=levels,
            economics=economics,
        )
    ).model
    assert value(model.pipe_level_built["segment_1", "dn1"]) == pytest.approx(1)
    assert value(model.heat_flow_kW["segment_1", 1]) == pytest.approx(50)
    assert value(model.central_heat_output_kW["central_ashp", 1]) == pytest.approx(51)
    assert value(model.pumping_electricity_input_kW_e[1]) == pytest.approx(1)
    expected_electricity = 51 / 4 + 1
    assert value(model.annual_electricity_cost_CNY_per_year) == _cost_approx(expected_electricity)
    assert value(model.annual_electricity_carbon_kgCO2e_per_year) == pytest.approx(
        expected_electricity * 0.4
    )


def test_formal_policy_can_forbid_unserved_heat_as_a_hard_constraint() -> None:
    model = build_core_model(
        _core_input(
            allow_unserved=False,
            technologies=_technologies(
                central_ashp={"capacity_max_kW": 20},
                central_gas_boiler={"capacity_max_kW": 20},
            ),
        )
    )
    with pytest.raises(RuntimeError, match="optimal"):
        solve_pyomo_model(model)
    assert all(
        value(model.unserved_heat_kW[node, hour], exception=False) is None
        for node in model.DEMAND_NODES
        for hour in model.HOURS
    )


def test_storage_cyclic_soc_moves_heat_between_two_hours() -> None:
    storage = ThermalStorageSpec(
        technology_id="tes", energy_capacity_max_kWh_th=100,
        charge_capacity_max_kW_th=100, discharge_capacity_max_kW_th=100,
        charge_efficiency=1, discharge_efficiency=1,
        standing_loss_fraction_per_hour=0,
        capex_CNY_per_kWh_th=0, power_capex_CNY_per_kW_th=0,
        fixed_capex_CNY=0, lifetime_years=15,
    )
    economics = _economics(
        hours=(1, 2), weights={1: 1, 2: 1},
        electricity_prices={1: 0, 2: 10}, gas_prices={1: 100, 2: 100},
    )
    technologies = _technologies(
        central_ashp={"capacity_max_kW": 100},
        central_gas_boiler={"capacity_max_kW": 100},
    )
    model = solve_core_model(
        _core_input(
            hours=(1, 2),
            heat_demand_kW={("demand_1", 1): 0, ("demand_1", 2): 100},
            economics=economics, technologies=technologies, storage=storage,
        )
    ).model
    assert value(model.storage_installed) == pytest.approx(1)
    assert value(model.storage_charge_kW[1]) == pytest.approx(100)
    assert value(model.storage_discharge_kW[2]) == pytest.approx(100)
    assert value(model.storage_charge_kW[2]) == pytest.approx(0)
    assert value(model.storage_discharge_kW[1]) == pytest.approx(0)
    for hour in model.HOURS:
        assert value(model.storage_charge_kW[hour]) * value(model.storage_discharge_kW[hour]) == pytest.approx(0)


def test_storage_ratio_loss_and_cyclic_boundary_hand_case() -> None:
    storage = ThermalStorageSpec(
        technology_id="tes", energy_capacity_max_kWh_th=500,
        charge_capacity_max_kW_th=500, discharge_capacity_max_kW_th=500,
        charge_efficiency=0.95, discharge_efficiency=0.95,
        standing_loss_fraction_per_hour=0.0060774,
        capex_CNY_per_kWh_th=0, power_capex_CNY_per_kW_th=0,
        fixed_capex_CNY=0, lifetime_years=15,
        max_charge_ratio_per_hour=0.25,
        max_discharge_ratio_per_hour=0.25,
    )
    economics = _economics(
        hours=(1, 2), weights={1: 1, 2: 1},
        electricity_prices={1: 0, 2: 10}, gas_prices={1: 100, 2: 100},
    )
    model = solve_core_model(
        _core_input(
            hours=(1, 2),
            heat_demand_kW={("demand_1", 1): 0, ("demand_1", 2): 50},
            economics=economics,
            technologies=_technologies(
                central_ashp={"capacity_max_kW": 500},
                central_gas_boiler={"capacity_max_kW": 500},
            ),
            storage=storage,
        )
    ).model
    assert value(model.storage_installed) == pytest.approx(1)
    assert value(model.storage_charge_kW[1]) > 0
    assert value(model.storage_discharge_kW[2]) > 0
    assert value(model.storage_charge_capacity_kW) <= (
        0.25 * value(model.storage_energy_capacity_kWh) + 1e-7
    )
    assert value(model.storage_discharge_capacity_kW) <= (
        0.25 * value(model.storage_energy_capacity_kWh) + 1e-7
    )
    for constraint in model.storage_soc_balance.values():
        assert value(constraint.body) == pytest.approx(0, abs=1e-7)
    with pytest.raises(CoreModelInputError, match="discount_rate"):
        validate_core_input(
            _core_input(economics=replace(_economics(), discount_rate=1.0))
        )


def test_core_input_copies_and_freezes_heat_demand_mapping() -> None:
    source = {("demand_1", 1): 100.0}
    data = _core_input(heat_demand_kW=source)

    source[("demand_1", 1)] = 999.0

    assert data.heat_demand_kW[("demand_1", 1)] == pytest.approx(100.0)
    with pytest.raises(TypeError):
        data.heat_demand_kW[("demand_1", 1)] = 50.0


def test_only_active_objective_is_formal_annual_cost_objective() -> None:
    model = solve_core_model(_core_input()).model

    active_objectives = list(
        model.component_data_objects(ObjectiveComponent, active=True)
    )

    assert active_objectives == [model.annual_cost_objective]
    assert value(model.annual_cost_objective) == _cost_approx(
        value(model.annual_real_cost_CNY_per_year)
        + value(model.annual_hns_penalty_CNY_per_year)
    )
    assert value(model.optimization_objective_CNY_per_year) == _cost_approx(
        value(model.annual_cost_objective)
    )


def test_nonzero_economic_fields_are_consumed_by_named_cost_components() -> None:
    technologies = _technologies(
        central_ashp={
            "capex_CNY_per_kW": 111.0,
            "fixed_maintenance_fraction_per_year": 0.22,
            "variable_om_CNY_per_kWh_th": 3.0,
        },
        central_gas_boiler={
            "capex_CNY_per_kW": 444.0,
            "fixed_maintenance_fraction_per_year": 0.55,
            "variable_om_CNY_per_kWh_th": 6.0,
        },
        local_ashp={
            "capex_CNY_per_kW": 777.0,
            "fixed_maintenance_fraction_per_year": 0.88,
            "variable_om_CNY_per_kWh_th": 9.0,
        },
    )
    model = build_core_model(_core_input(technologies=technologies))
    component_names = set(model.component_map())

    assert "annual_device_capex_CNY_per_year" in component_names
    assert "annual_fixed_om_CNY_per_year" in component_names
    assert "annual_variable_om_CNY_per_year" in component_names
    assert "annual_real_cost_CNY_per_year" in component_names
    assert "optimization_objective_CNY_per_year" in component_names
    assert list(model.component_data_objects(ObjectiveComponent, active=True)) == [
        model.annual_cost_objective
    ]


def test_two_hours_keep_interval_energy_separate_from_annual_weight() -> None:
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
    assert value(model.time_weight_h_per_year[1]) == pytest.approx(1.0)
    assert value(model.time_weight_h_per_year[2]) == pytest.approx(1.0)


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


def test_annual_cost_breakdown_matches_two_hour_hand_calculation() -> None:
    """覆盖简单年化、固定/可变维护、峰谷电价和 LHV 燃气价。"""

    technologies = _technologies(
        central_ashp={
            "capacity_min_kW": 50.0,
            "capacity_max_kW": 50.0,
            "cop": 4.0,
            "capex_CNY_per_kW": 2000.0,
            "fixed_maintenance_fraction_per_year": 0.015,
            "variable_om_CNY_per_kWh_th": 0.02,
            "lifetime_years": 20,
        },
        central_gas_boiler={
            "capacity_min_kW": 30.0,
            "capacity_max_kW": 30.0,
            "efficiency": 0.9,
            "capex_CNY_per_kW": 800.0,
            "fixed_maintenance_fraction_per_year": 0.01875,
            "variable_om_CNY_per_kWh_th": 0.03,
            "lifetime_years": 15,
        },
    )
    economics = _economics(
        hours=(1, 2),
        weights={1: 4000.0, 2: 4760.0},
        electricity_prices={1: 0.48, 2: 2.0},
        gas_prices={1: 0.354, 2: 0.354},
        connection_capex={"demand_1": 20_000.0},
        connection_lifetimes={"demand_1": 20},
        hns_penalty=100.0,
    )
    data = _core_input(
        hours=(1, 2),
        heat_demand_kW={
            ("demand_1", 1): 58.0,
            ("demand_1", 2): 29.0,
        },
        technologies=technologies,
        segments=(
            _segment(
                "segment_1",
                "site_1",
                "demand_1",
                capacity_max_kW=58.0,
                length_m=100.0,
                pipe_capex_CNY_per_m=500.0,
                lifetime_years=25,
            ),
        ),
        economics=economics,
    )
    model = build_core_model(data)
    model.central_installed["central_ashp"].fix(1)
    model.central_installed["central_gas_boiler"].fix(1)
    model.central_capacity_kW["central_ashp"].fix(50)
    model.central_capacity_kW["central_gas_boiler"].fix(30)
    model.central_heat_output_kW["central_ashp", 1].fix(40)
    model.central_heat_output_kW["central_ashp", 2].fix(20)
    model.central_heat_output_kW["central_gas_boiler", 1].fix(18)
    model.central_heat_output_kW["central_gas_boiler", 2].fix(9)
    solve_pyomo_model(model, SolverSettings(mip_gap=0.0))

    # 在该组价格下，HP 两时段分别出 40/20，GB 分别出 18/9。
    assert value(model.central_heat_output_kW["central_ashp", 1]) == pytest.approx(40)
    assert value(model.central_heat_output_kW["central_ashp", 2]) == pytest.approx(20)
    assert value(model.central_heat_output_kW["central_gas_boiler", 1]) == pytest.approx(18)
    assert value(model.central_heat_output_kW["central_gas_boiler", 2]) == pytest.approx(9)
    components = (
        (model.annual_device_capex_CNY_per_year, 6600.0),
        (model.annual_network_capex_CNY_per_year, 2000.0),
        (model.annual_connection_capex_CNY_per_year, 1000.0),
        (model.annual_fixed_om_CNY_per_year, 1950.0),
        (model.annual_variable_om_CNY_per_year, 8549.2),
        (model.annual_electricity_cost_CNY_per_year, 66800.0),
        (model.annual_gas_cost_CNY_per_year, 45170.4),
    )
    for expression, expected in components:
        assert value(expression) == _cost_approx(expected)

    recomputed_real_cost = sum(value(expression) for expression, _ in components)
    assert recomputed_real_cost == _cost_approx(132069.6)
    assert value(model.annual_real_cost_CNY_per_year) == _cost_approx(
        recomputed_real_cost
    )
    assert value(model.annual_hns_penalty_CNY_per_year) == _cost_approx(0.0)
    recomputed_objective = (
        recomputed_real_cost + value(model.annual_hns_penalty_CNY_per_year)
    )
    assert value(model.optimization_objective_CNY_per_year) == _cost_approx(
        recomputed_objective
    )
    assert value(model.annual_cost_objective) == _cost_approx(recomputed_objective)


def test_hns_penalty_is_separate_from_real_cost() -> None:
    data = _core_input(
        technologies=_technologies(
            central_ashp={"capacity_max_kW": 20.0},
            central_gas_boiler={"capacity_max_kW": 20.0},
        ),
        economics=_economics(
            weights={1: 4760.0},
            hns_penalty=100.0,
        ),
    )
    model = solve_core_model(data).model

    assert value(model.unserved_heat_kW["demand_1", 1]) == pytest.approx(60.0)
    assert value(model.annual_hns_penalty_CNY_per_year) == _cost_approx(28_560_000.0)
    assert value(model.annual_cost_objective) == _cost_approx(
        value(model.annual_real_cost_CNY_per_year)
        + value(model.annual_hns_penalty_CNY_per_year)
    )


def test_energy_prices_switch_central_dispatch() -> None:
    common = dict(
        heat_demand_kW={("demand_1", 1): 20.0},
        technologies=_technologies(
            central_ashp={"capacity_max_kW": 20.0, "cop": 4.0},
            central_gas_boiler={"capacity_max_kW": 20.0, "efficiency": 1.0},
        ),
    )
    electric_model = solve_core_model(
        _core_input(
            **common,
            economics=_economics(
                electricity_prices={1: 0.48},
                gas_prices={1: 2.0},
            ),
        )
    ).model
    gas_model = solve_core_model(
        _core_input(
            **common,
            economics=_economics(
                electricity_prices={1: 2.0},
                gas_prices={1: 0.1},
            ),
        )
    ).model

    assert value(electric_model.central_heat_output_kW["central_ashp", 1]) == pytest.approx(20)
    assert value(gas_model.central_heat_output_kW["central_gas_boiler", 1]) == pytest.approx(20)


def test_user_peak_valley_example_uses_absolute_prices_and_one_public_weight() -> None:
    """0.6/1.0 电价与 0.8/1.0 气价不得伪装成技术专属权重。"""

    data = _core_input(
        hours=(1, 2),
        heat_demand_kW={
            ("demand_1", 1): 20.0,
            ("demand_1", 2): 20.0,
        },
        technologies=_technologies(
            central_ashp={"cop": 2.0, "capacity_max_kW": 10.0},
            central_gas_boiler={"efficiency": 1.0, "capacity_max_kW": 10.0},
        ),
        economics=_economics(
            hours=(1, 2),
            weights={1: 1.0, 2: 1.0},
            electricity_prices={1: 0.6, 2: 1.0},
            gas_prices={1: 0.8, 2: 1.0},
        ),
    )
    model = solve_core_model(data, SolverSettings(mip_gap=0.0)).model

    assert [value(model.time_weight_h_per_year[h]) for h in model.HOURS] == [1.0, 1.0]
    assert [value(model.electricity_price_CNY_per_kWh_e[h]) for h in model.HOURS] == [0.6, 1.0]
    assert [value(model.gas_price_CNY_per_kWh_LHV[h]) for h in model.HOURS] == [0.8, 1.0]
    assert value(model.annual_electricity_cost_CNY_per_year) == _cost_approx(8.0)
    assert value(model.annual_gas_cost_CNY_per_year) == _cost_approx(18.0)


def test_volume_gas_price_is_standardized_once_at_input_boundary() -> None:
    standardized = standardize_gas_price_CNY_per_kWh_LHV(3.54, 35.588)

    assert standardized == pytest.approx(
        0.3580982353602338,
        rel=0,
        abs=1e-12,
    )
    economics = _economics(gas_prices={1: standardized})
    assert economics.gas_price_CNY_per_kWh_LHV[1] == standardized


@pytest.mark.parametrize(
    ("price", "lhv", "message"),
    [
        (-1.0, 35.588, "gas_price"),
        (float("nan"), 35.588, "gas_price"),
        (True, 35.588, "gas_price"),
        (3.54, 0.0, "gas_lhv"),
        (3.54, float("inf"), "gas_lhv"),
        (3.54, True, "gas_lhv"),
    ],
)
def test_invalid_volume_gas_price_standardization_is_rejected(
    price: object,
    lhv: object,
    message: str,
) -> None:
    with pytest.raises(EconomicStandardizationError, match=message):
        standardize_gas_price_CNY_per_kWh_LHV(price, lhv)


def test_distributed_mode_annual_cost_has_only_local_device_and_operation() -> None:
    data = _core_input(
        mode="distributed",
        heat_demand_kW={("demand_1", 1): 20.0},
        segments=(),
        technologies=_technologies(
            local_ashp={
                "cop": 4.0,
                "capacity_max_kW": 20.0,
                "capex_CNY_per_kW": 1000.0,
                "fixed_maintenance_fraction_per_year": 0.1,
                "variable_om_CNY_per_kWh_th": 0.02,
                "lifetime_years": 10,
            },
        ),
        economics=_economics(
            weights={1: 100.0},
            electricity_prices={1: 0.5},
        ),
    )
    model = solve_core_model(data).model

    assert value(model.local_heat_output_kW["demand_1", 1]) == pytest.approx(20.0)
    assert value(model.annual_device_capex_CNY_per_year) == _cost_approx(2000.0)
    assert value(model.annual_fixed_om_CNY_per_year) == _cost_approx(2000.0)
    assert value(model.annual_variable_om_CNY_per_year) == _cost_approx(40.0)
    assert value(model.annual_electricity_cost_CNY_per_year) == _cost_approx(250.0)
    assert value(model.annual_network_capex_CNY_per_year) == _cost_approx(0.0)
    assert value(model.annual_connection_capex_CNY_per_year) == _cost_approx(0.0)
    assert value(model.annual_real_cost_CNY_per_year) == _cost_approx(4290.0)


def test_hybrid_mode_costs_connected_and_local_supply_without_double_counting() -> None:
    data = _core_input(
        mode="hybrid",
        demand_nodes=("demand_1", "demand_2"),
        heat_demand_kW={
            ("demand_1", 1): 40.0,
            ("demand_2", 1): 30.0,
        },
        segments=(
            _segment(
                "segment_1",
                "site_1",
                "demand_1",
                length_m=10.0,
                pipe_capex_CNY_per_m=100.0,
                lifetime_years=20,
            ),
        ),
        technologies=_technologies(
            central_ashp={
                "capacity_max_kW": 40.0,
                "capex_CNY_per_kW": 1000.0,
                "lifetime_years": 10,
            },
            central_gas_boiler={
                "capacity_max_kW": 1.0,
                "capex_CNY_per_kW": 100_000.0,
                "lifetime_years": 1,
            },
            local_ashp={
                "capacity_max_kW": 30.0,
                "capex_CNY_per_kW": 500.0,
                "lifetime_years": 10,
            },
        ),
        economics=_economics(
            demand_nodes=("demand_1", "demand_2"),
            connection_capex={"demand_1": 1000.0, "demand_2": 0.0},
            connection_lifetimes={"demand_1": 10, "demand_2": 10},
        ),
    )
    model = solve_core_model(data).model

    assert value(model.connected["demand_1"]) == pytest.approx(1.0)
    assert value(model.local_installed["demand_2"]) == pytest.approx(1.0)
    assert value(model.annual_device_capex_CNY_per_year) == _cost_approx(5500.0)
    assert value(model.annual_network_capex_CNY_per_year) == _cost_approx(50.0)
    assert value(model.annual_connection_capex_CNY_per_year) == _cost_approx(100.0)
    assert value(model.annual_real_cost_CNY_per_year) == _cost_approx(5650.0)


def test_physical_segment_capex_is_counted_once_when_endpoint_order_reverses() -> None:
    costs = []
    for node_u, node_v in (("site_1", "demand_1"), ("demand_1", "site_1")):
        model = solve_core_model(
            _core_input(
                segments=(
                    _segment(
                        "segment_1",
                        node_u,
                        node_v,
                        length_m=100.0,
                        pipe_capex_CNY_per_m=500.0,
                        lifetime_years=25,
                    ),
                )
            )
        ).model
        costs.append(value(model.annual_network_capex_CNY_per_year))
    assert costs == pytest.approx(
        [2000.0, 2000.0],
        rel=0,
        abs=COST_ABS_TOL_CNY_PER_YEAR,
    )


@pytest.mark.parametrize(
    ("economic_override", "message"),
    [
        ({"weights": {1: 0.0}}, "time_weight.*大于 0"),
        ({"weights": {1: float("nan")}}, "time_weight.*有限"),
        ({"weights": {2: 1.0}}, "time_weight.*全部小时"),
        ({"electricity_prices": {1: -0.1}}, "electricity_price.*大于等于 0"),
        ({"electricity_prices": {1: float("nan")}}, "electricity_price.*有限"),
        ({"electricity_prices": {2: 0.1}}, "electricity_price.*全部小时"),
        ({"gas_prices": {1: -0.1}}, "gas_price.*大于等于 0"),
        ({"gas_prices": {1: float("nan")}}, "gas_price.*有限"),
        ({"gas_prices": {2: 0.1}}, "gas_price.*全部小时"),
        ({"expected_weight_sum": 0.0}, "expected_weight_sum.*大于 0"),
        ({"expected_weight_sum": float("nan")}, "expected_weight_sum.*有限"),
        ({"expected_weight_sum": 2.0}, "之和.*expected_weight"),
        ({"expected_weight_sum": 1.0 + 2e-9}, "之和.*expected_weight"),
        ({"hns_penalty": -1.0}, "hns_penalty.*大于等于 0"),
        ({"hns_penalty": float("nan")}, "hns_penalty.*有限"),
        ({"connection_capex": {"demand_1": -1.0}}, "connection_capex.*大于等于 0"),
        ({"connection_capex": {"demand_1": float("nan")}}, "connection_capex.*有限"),
        ({"connection_capex": {"other": 0.0}}, "connection_capex.*demand_nodes"),
        ({"connection_lifetimes": {"demand_1": 0}}, "connection_lifetime.*整数"),
        ({"connection_lifetimes": {"demand_1": True}}, "connection_lifetime.*整数"),
        ({"connection_lifetimes": {"other": 20}}, "connection_lifetime.*demand_nodes"),
    ],
)
def test_invalid_economic_input_is_rejected_before_model_build(
    economic_override: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(CoreModelInputError, match=message):
        build_core_model(_core_input(economics=_economics(**economic_override)))


@pytest.mark.parametrize(
    ("field", "mapping"),
    [
        ("weights", {True: 1.0}),
        ("weights", {1.0: 1.0}),
        ("electricity_prices", {True: 0.5}),
        ("electricity_prices", {1.0: 0.5}),
        ("gas_prices", {True: 0.5}),
        ("gas_prices", {1.0: 0.5}),
    ],
)
def test_economic_hour_keys_must_be_real_integers(
    field: str,
    mapping: dict[object, float],
) -> None:
    with pytest.raises(CoreModelInputError, match="hour 键必须全部为整数"):
        build_core_model(_core_input(economics=_economics(**{field: mapping})))


@pytest.mark.parametrize("invalid_hour", [True, 1.0])
def test_heat_demand_hour_keys_must_be_real_integers(invalid_hour: object) -> None:
    with pytest.raises(CoreModelInputError, match="hour 键必须全部为整数"):
        build_core_model(
            _core_input(
                heat_demand_kW={("demand_1", invalid_hour): 100.0},
            )
        )


@pytest.mark.parametrize(
    ("segment_override", "message"),
    [
        ({"pipe_capex_CNY_per_m": -1.0}, "pipe_capex.*大于等于 0"),
        ({"pipe_capex_CNY_per_m": float("nan")}, "pipe_capex.*有限"),
        ({"lifetime_years": 0}, "lifetime_years.*整数"),
        ({"lifetime_years": True}, "lifetime_years.*整数"),
    ],
)
def test_invalid_network_economics_is_rejected(
    segment_override: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(CoreModelInputError, match=message):
        build_core_model(
            _core_input(
                segments=(
                    _segment(
                        "segment_1",
                        "site_1",
                        "demand_1",
                        **segment_override,
                    ),
                )
            )
        )


def test_economic_input_mappings_are_copied_and_frozen() -> None:
    weights = {1: 1.0}
    economics = _economics(weights=weights)
    weights[1] = 99.0

    assert economics.time_weight_h_per_year[1] == pytest.approx(1.0)
    with pytest.raises(TypeError):
        economics.time_weight_h_per_year[1] = 2.0


def test_weight_sum_tolerance_accepts_difference_not_exceeding_1e_minus_9() -> None:
    data = _core_input(
        economics=_economics(expected_weight_sum=1.0 + 0.5e-9),
    )

    validate_core_input(data)


@pytest.mark.parametrize(
    ("field", "invalid_value", "message"),
    [
        ("capex_CNY_per_kW", -1.0, "capex_CNY_per_kW.*大于等于 0"),
        (
            "fixed_maintenance_fraction_per_year",
            -0.01,
            "fixed_maintenance_fraction_per_year.*大于等于 0",
        ),
        (
            "fixed_maintenance_fraction_per_year",
            float("nan"),
            "fixed_maintenance_fraction_per_year.*有限",
        ),
        (
            "fixed_maintenance_fraction_per_year",
            1.01,
            "fixed_maintenance_fraction_per_year.*小于等于 1",
        ),
        (
            "variable_om_CNY_per_kWh_th",
            -0.01,
            "variable_om_CNY_per_kWh_th.*大于等于 0",
        ),
        ("lifetime_years", 0, "lifetime_years.*整数"),
        ("lifetime_years", True, "lifetime_years.*整数"),
    ],
)
def test_invalid_technology_economics_is_rejected(
    field: str,
    invalid_value: object,
    message: str,
) -> None:
    data = _core_input(
        technologies=_technologies(
            central_ashp={field: invalid_value},
        )
    )

    with pytest.raises(CoreModelInputError, match=message):
        validate_core_input(data)


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
