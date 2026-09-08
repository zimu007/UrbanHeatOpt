"""B4 two-node, 24-hour TES research case; never a formal V2 input."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from time import perf_counter

from pyomo.environ import value

from urbanheatopt.model.costing.annualized import capital_recovery_factor
from urbanheatopt.model.reference_core import (
    CAPACITY_MARGIN_BASIS_SOURCE_INCLUDING_LOSS,
    CoreModelInput, EconomicInput, TechnologySpec, ThermalStorageSpec,
)
from urbanheatopt.model.road_core import MonthlyDemandChargeInput, PipeDesign, RoadCase, build_road_model
from urbanheatopt.optimization.solvers import SolverSettings, solve_pyomo_model


RESEARCH_SCENARIO_ID = 'B4_single_case'
RESEARCH_EVIDENCE = {
    'status': 'research_assumption',
    'formal_engineering_result': False,
    'capacity_margin_basis': CAPACITY_MARGIN_BASIS_SOURCE_INCLUDING_LOSS,
    'tes_energy_capacity_max_kWh_th': 500.0,
    'tes_charge_capacity_max_kW_th': 150.0,
    'tes_discharge_capacity_max_kW_th': 150.0,
    'maxima_basis': 'explicit_B4_research_boundary_not_peak_multiplier',
}


@dataclass(frozen=True, slots=True)
class B4Result:
    enable_tes: bool
    solver_status: str
    termination_condition: str
    reported_gap: float | None
    solve_seconds: float
    annual_real_cost_CNY: float
    physical_carbon_kgCO2e: float
    electricity_cost_CNY: float
    gas_cost_CNY: float
    demand_charge_CNY: float
    hp_annualized_capex_CNY: float
    boiler_annualized_capex_CNY: float
    tes_annualized_capex_CNY: float
    peak_electricity_kW: float
    hp_capacity_kW_th: float
    boiler_capacity_kW_th: float
    tes_energy_capacity_kWh_th: float
    tes_charge_capacity_kW_th: float
    tes_discharge_capacity_kW_th: float
    unmet_heat_kWh: float
    soc_kWh: tuple[float, ...]
    charge_kW: tuple[float, ...]
    discharge_kW: tuple[float, ...]


def soc_transition(previous_soc_kWh: float, charge_kW: float, discharge_kW: float,
                   *, loss: float, eta_charge: float, eta_discharge: float) -> float:
    return (previous_soc_kWh * (1 - loss) + eta_charge * charge_kW
            - discharge_kW / eta_discharge)


def build_b4_research_case() -> RoadCase:
    hours = tuple(range(1, 25))
    # Fixed before solving: one deliberately ordinary daily profile and one
    # low/high tariff split. These are scenario data, not calibrated to force TES.
    load = (90., 88., 86., 85., 85., 90., 105., 120., 125., 120., 110., 105.,
            100., 98., 100., 110., 130., 150., 160., 155., 140., 120., 105., 95.)
    price = {hour: (0.38 if hour <= 7 else 1.05 if 17 <= hour <= 21 else 0.68)
             for hour in hours}
    techs = (
        TechnologySpec('central_hp', 'air_source_heat_pump', 'central', 'electricity',
                       3.5, None, 0., 300., 3000., .01, 0., 20,
                       'revised_20260831_B1_projection', 'scenario_assumption'),
        TechnologySpec('central_boiler', 'gas_boiler', 'central', 'gas',
                       None, .94, 0., 300., 782., .04, 0., 15,
                       'revised_20260831_B1_projection_LHV', 'scenario_assumption'),
        TechnologySpec('local_hp', 'air_source_heat_pump', 'local', 'electricity',
                       3.2, None, 0., 300., 3000., .01, 0., 20,
                       'revised_20260831_B1_projection', 'scenario_assumption'),
    )
    econ = EconomicInput(
        {hour: 1. for hour in hours}, price, {hour: .36 for hour in hours},
        24., {'LOAD': 0.}, {'LOAD': 20}, 0., discount_rate=.05,
        station_fixed_capex_CNY=0., station_lifetime_years=20,
        electricity_carbon_kgCO2e_per_kWh_e={hour: .55 for hour in hours},
        gas_carbon_kgCO2e_per_kWh_LHV={hour: .202 for hour in hours},
    )
    storage = ThermalStorageSpec(
        'central_tes', RESEARCH_EVIDENCE['tes_energy_capacity_max_kWh_th'],
        RESEARCH_EVIDENCE['tes_charge_capacity_max_kW_th'],
        RESEARCH_EVIDENCE['tes_discharge_capacity_max_kW_th'],
        .95, .95, .0004166666666667, 1135.212613473483, 450., 0., 20,
    )
    common = CoreModelInput(
        'central', hours, None, ('LOAD',),
        {('LOAD', hour): load[hour - 1] for hour in hours}, techs, (), econ,
        storage=storage,
        heat_pump_cop_by_hour={
            **{('central_hp', hour): 3.5 for hour in hours},
            **{('local_hp', hour): 3.2 for hour in hours}},
        heat_pump_capacity_ratio_by_hour={
            **{('central_hp', hour): 1. for hour in hours},
            **{('local_hp', hour): 1. for hour in hours}},
        allow_unserved=False, peak_capacity_margin_fraction=.2,
        capacity_margin_basis=CAPACITY_MARGIN_BASIS_SOURCE_INCLUDING_LOSS,
        candidate_station_nodes=('SOURCE_SITE',),
    )
    network = {
        'crs': 'EPSG:32650',
        'metadata': {'scenario_id': RESEARCH_SCENARIO_ID,
                     'parameter_status': 'research_assumption'},
        'nodes': [
            {'node_id': 'SOURCE', 'node_type': 'road', 'x_m': 0., 'y_m': 0.},
            {'node_id': 'LOAD', 'node_type': 'building', 'x_m': 10., 'y_m': 0.},
        ],
        'edges': [{'edge_id': 'FIXED_ROUTE', 'node_u': 'SOURCE', 'node_v': 'LOAD',
                   'coordinates': [[0., 0.], [10., 0.]], 'length_m': 10.,
                   'edge_type': 'building_service', 'route_basis': 'supply_return_pair_route_m',
                   'highway': 'research', 'osm_way_ids': ['research']}],
        'sites': [{'site_id': 'SOURCE_SITE', 'attachment_node_id': 'SOURCE'}],
    }
    pipes = tuple(PipeDesign(f'RESEARCH_PIPE_{index}', capacity, 0., 30, 0., 0.)
                  for index, capacity in enumerate((200., 250., 300.), 1))
    timestamps = tuple(f'2026-01-15T{hour - 1:02d}:00:00+08:00' for hour in hours)
    return RoadCase(common, json.dumps(network, sort_keys=True), pipes, timestamps,
                    'research/revised_20260831/B4_single_case',
                    MonthlyDemandChargeInput(42., {hour: '2026-01' for hour in hours}))


def solve_b4_research_case(enable_tes: bool, evidence_dir: str | Path) -> tuple[B4Result, object]:
    case = build_b4_research_case()
    if not enable_tes:
        from dataclasses import replace
        case = replace(case, common=replace(case.common, storage=None))
    model = build_road_model(case)
    evidence_dir = Path(evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    label = 'tes_on' if enable_tes else 'tes_off'
    started = perf_counter()
    solved = solve_pyomo_model(model, SolverSettings(
        threads=1, mip_gap=0., time_limit_seconds=60.,
        log_file=str(evidence_dir / f'{label}_solver.log'),
        evidence_file=str(evidence_dir / f'{label}_solver_evidence.json')))
    elapsed = perf_counter() - started
    evidence = solved.urbanheatopt_evidence
    site, hp, boiler = 'SOURCE_SITE', 'central_hp', 'central_boiler'
    rate = case.common.economics.discount_rate
    hp_spec = next(row for row in case.common.technologies if row.technology_id == hp)
    boiler_spec = next(row for row in case.common.technologies if row.technology_id == boiler)
    hours = case.common.hours
    result = B4Result(
        enable_tes, evidence['solver_status'], evidence['termination_condition'],
        evidence.get('relative_mip_gap'), elapsed,
        value(model.annual_real_cost_CNY_per_year),
        value(model.annual_operating_physical_carbon_kgCO2e_per_year),
        value(model.electricity_cost), value(model.gas_cost),
        value(model.annual_monthly_demand_charge_CNY_per_year),
        value(model.capacity[site, hp]) * hp_spec.capex_CNY_per_kW
        * capital_recovery_factor(rate, hp_spec.lifetime_years),
        value(model.capacity[site, boiler]) * boiler_spec.capex_CNY_per_kW
        * capital_recovery_factor(rate, boiler_spec.lifetime_years),
        value(model.storage_investment), max(value(model.electricity[hour]) for hour in hours),
        value(model.capacity[site, hp]), value(model.capacity[site, boiler]),
        value(model.tes_energy[site]), value(model.tes_charge_capacity[site]),
        value(model.tes_discharge_capacity[site]), 0.,
        tuple(value(model.soc[site, hour]) for hour in hours),
        tuple(value(model.charge[site, hour]) for hour in hours),
        tuple(value(model.discharge[site, hour]) for hour in hours),
    )
    return result, model


def audit_b4_solution(result: B4Result, model) -> dict:
    case = build_b4_research_case()
    storage, hours, site = case.common.storage, case.common.hours, 'SOURCE_SITE'
    residuals = []
    for index, hour in enumerate(hours):
        previous = index - 1
        expected = soc_transition(
            result.soc_kWh[previous], result.charge_kW[index], result.discharge_kW[index],
            loss=storage.standing_loss_fraction_per_hour,
            eta_charge=storage.charge_efficiency,
            eta_discharge=storage.discharge_efficiency)
        residuals.append(result.soc_kWh[index] - expected)
    crf = capital_recovery_factor(case.common.economics.discount_rate, storage.lifetime_years)
    manual_tes_cost = (result.tes_energy_capacity_kWh_th * storage.capex_CNY_per_kWh_th
                       + max(result.tes_charge_capacity_kW_th,
                             result.tes_discharge_capacity_kW_th)
                       * storage.power_capex_CNY_per_kW_th
                       + (storage.fixed_capex_CNY if result.tes_energy_capacity_kWh_th > 1e-7 else 0.)) * crf
    manual_demand = result.peak_electricity_kW * 42.
    manual_carbon = sum(
        value(model.electricity[hour]) * case.common.economics.electricity_carbon_kgCO2e_per_kWh_e[hour]
        + value(model.gas[hour]) * case.common.economics.gas_carbon_kgCO2e_per_kWh_LHV[hour]
        for hour in hours)
    tol = 1e-6
    return {
        'soc_pass': max(map(abs, residuals)) <= tol,
        'cyclic_pass': abs(residuals[0]) <= tol,
        'capacity_pass': all(-tol <= soc <= result.tes_energy_capacity_kWh_th + tol
                             for soc in result.soc_kWh)
                         and max(result.charge_kW) <= result.tes_charge_capacity_kW_th + tol
                         and max(result.discharge_kW) <= result.tes_discharge_capacity_kW_th + tol,
        'no_free_initial_energy_pass': abs(residuals[0]) <= tol,
        'no_simultaneous_charge_discharge_pass': max(
            (min(c, d) for c, d in zip(result.charge_kW, result.discharge_kW)), default=0.) <= tol,
        'tes_cost_pass': abs(manual_tes_cost - result.tes_annualized_capex_CNY) <= tol,
        'demand_charge_pass': abs(manual_demand - result.demand_charge_CNY) <= tol,
        'carbon_pass': abs(manual_carbon - result.physical_carbon_kgCO2e) <= tol,
        'max_soc_residual_kWh': max(map(abs, residuals)),
        'manual_tes_cost_CNY': manual_tes_cost,
        'manual_demand_charge_CNY': manual_demand,
        'manual_carbon_kgCO2e': manual_carbon,
        'nonzero_transition_witness_kWh': soc_transition(
            100., 10., 3., loss=storage.standing_loss_fraction_per_hour,
            eta_charge=storage.charge_efficiency,
            eta_discharge=storage.discharge_efficiency),
    }
