"""Cost-carbon epsilon-constraint tests."""

from __future__ import annotations

from pyomo.environ import Objective
import pytest

from competition.core_model import CoreModelInput, EconomicInput, SegmentSpec, TechnologySpec
from competition.pareto import ParetoSpec, solve_mode_pareto
from competition.solvers import SolverSettings


def _technology(technology_id: str, kind: str, scope: str, carrier: str, cop: float | None, efficiency: float | None) -> TechnologySpec:
    return TechnologySpec(
        technology_id, kind, scope, carrier, cop, efficiency,
        0, 100, 0, 0, 0, 20, "synthetic_test", "synthetic_test",
    )


def _data() -> CoreModelInput:
    return CoreModelInput(
        mode="central", hours=(1,), site_node="site_1", demand_nodes=("demand_1",),
        heat_demand_kW={("demand_1", 1): 100.0},
        technologies=(
            _technology("central_hp", "air_source_heat_pump", "central", "electricity", 4.0, None),
            _technology("central_boiler", "gas_boiler", "central", "gas", None, 0.9),
            _technology("local_hp", "air_source_heat_pump", "local", "electricity", 4.0, None),
        ),
        segments=(SegmentSpec("segment_1", "site_1", "demand_1", 10, 100, 0, 30),),
        economics=EconomicInput(
            time_weight_h_per_year={1: 1},
            electricity_price_CNY_per_kWh_e={1: 1},
            gas_price_CNY_per_kWh_LHV={1: 0},
            expected_weight_sum_h_per_year=1,
            connection_capex_CNY={"demand_1": 0},
            connection_lifetime_years={"demand_1": 30},
            hns_penalty_CNY_per_kWh=1_000_000,
            electricity_carbon_kgCO2e_per_kWh_e={1: 0},
            gas_carbon_kgCO2e_per_kWh_LHV={1: 1},
        ),
    )


def test_epsilon_frontier_contains_endpoints_and_no_unserved_heat() -> None:
    spec = ParetoSpec(point_count=5)
    frontier, _ = solve_mode_pareto(_data(), SolverSettings(mip_gap=0), spec)
    assert len(frontier) == 5
    assert "carbon_endpoint" in frontier[0].labels
    assert "cost_endpoint" in frontier[-1].labels
    assert all(point.unserved_heat_kWh <= spec.unserved_tolerance_kWh for point in frontier)
    assert [point.annual_operating_carbon_kgCO2e_per_year for point in frontier] == sorted(
        point.annual_operating_carbon_kgCO2e_per_year for point in frontier
    )
    assert sum(point.is_knee for point in frontier) == 1


def test_cost_and_carbon_endpoints_switch_the_same_model_physics() -> None:
    frontier, solutions = solve_mode_pareto(_data(), SolverSettings(mip_gap=0), ParetoSpec(point_count=5))
    low_carbon = frontier[0]
    low_cost = frontier[-1]
    assert low_carbon.annual_operating_carbon_kgCO2e_per_year == pytest.approx(0)
    assert low_carbon.annual_real_cost_CNY_per_year == pytest.approx(25)
    assert low_cost.annual_real_cost_CNY_per_year == pytest.approx(0)
    assert low_cost.annual_operating_carbon_kgCO2e_per_year == pytest.approx(100 / 0.9)
    assert len({id(solution.model) for solution in solutions.values()}) == len(solutions)
    carbon_model = solutions["central-carbon"].model
    active = list(carbon_model.component_data_objects(Objective, active=True))
    assert [objective.name for objective in active] == ["annual_carbon_objective"]


def test_pareto_spec_rejects_invalid_point_count() -> None:
    with pytest.raises(ValueError, match="point_count"):
        solve_mode_pareto(_data(), SolverSettings(), ParetoSpec(point_count=2))
