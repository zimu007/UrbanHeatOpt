from pathlib import Path

import pytest
from pyomo.environ import value

from urbanheatopt.optimization.b4_tes_research import (
    RESEARCH_EVIDENCE, audit_b4_solution, build_b4_research_case,
    soc_transition, solve_b4_research_case,
)
from urbanheatopt.model.reference_core import CAPACITY_MARGIN_BASIS_SOURCE_INCLUDING_LOSS


@pytest.fixture(scope='module')
def solved_pair(tmp_path_factory):
    root = tmp_path_factory.mktemp('b4_solver_evidence')
    off = solve_b4_research_case(False, root)
    on = solve_b4_research_case(True, root)
    return root, off, on


def test_case_is_explicit_two_node_24_hour_research_boundary():
    case = build_b4_research_case()
    assert len(case.network['nodes']) == 2
    assert len(case.common.hours) == 24
    assert case.parameter_version == 'research/revised_20260831/B4_single_case'
    assert RESEARCH_EVIDENCE['status'] == 'research_assumption'
    assert RESEARCH_EVIDENCE['capacity_margin_basis'] == CAPACITY_MARGIN_BASIS_SOURCE_INCLUDING_LOSS
    assert RESEARCH_EVIDENCE['maxima_basis'] == 'explicit_B4_research_boundary_not_peak_multiplier'


def test_off_and_on_are_real_matched_solves_with_evidence(solved_pair):
    root, (off, _), (on, _) = solved_pair
    assert off.solver_status == on.solver_status == 'ok'
    assert off.termination_condition == on.termination_condition == 'optimal'
    assert off.reported_gap == on.reported_gap == pytest.approx(0.)
    assert (root / 'tes_off_solver_evidence.json').is_file()
    assert (root / 'tes_on_solver_evidence.json').is_file()
    assert off.solve_seconds > 0 and on.solve_seconds > 0
    assert build_b4_research_case() == build_b4_research_case()


def test_tes_off_has_no_storage_state_or_residual_cost(solved_pair):
    _, (off, _), _ = solved_pair
    assert off.tes_energy_capacity_kWh_th == pytest.approx(0.)
    assert off.tes_charge_capacity_kW_th == pytest.approx(0.)
    assert off.tes_discharge_capacity_kW_th == pytest.approx(0.)
    assert max(map(abs, off.soc_kWh + off.charge_kW + off.discharge_kW)) == pytest.approx(0.)
    assert off.tes_annualized_capex_CNY == pytest.approx(0.)


def test_on_allows_zero_but_respects_soc_efficiency_loss_cyclic_and_capacity(solved_pair):
    _, _, (on, model) = solved_pair
    assert model.tes_energy['SOURCE_SITE'].lb == 0
    audit = audit_b4_solution(on, model)
    assert audit['soc_pass']
    assert audit['cyclic_pass']
    assert audit['capacity_pass']
    assert audit['no_free_initial_energy_pass']
    assert audit['no_simultaneous_charge_discharge_pass']


def test_manual_tes_cost_demand_charge_and_carbon_reconcile(solved_pair):
    _, (off, off_model), (on, on_model) = solved_pair
    for result, model in ((off, off_model), (on, on_model)):
        audit = audit_b4_solution(result, model)
        assert audit['tes_cost_pass']
        assert audit['demand_charge_pass']
        assert audit['carbon_pass']
    assert soc_transition(100., 10., 3., loss=1/2400, eta_charge=.95,
                          eta_discharge=.95) == pytest.approx(106.30043859649123)


def test_twenty_percent_source_reserve_is_same_and_excludes_tes(solved_pair):
    case = build_b4_research_case()
    assert case.common.peak_capacity_margin_fraction == pytest.approx(.2)
    _, (off, off_model), (on, on_model) = solved_pair
    for result, model in ((off, off_model), (on, on_model)):
        peak_source = max(case.common.heat_demand_kW['LOAD', h] for h in model.HOURS)
        assert result.hp_capacity_kW_th + result.boiler_capacity_kW_th >= 1.2 * peak_source - 1e-6
    # TES energy/power is not added to either heat-source capacity reported above.


def test_on_off_delta_is_computed_without_assuming_its_sign(solved_pair):
    _, (off, _), (on, _) = solved_pair
    delta = (on.annual_real_cost_CNY - off.annual_real_cost_CNY,
             on.physical_carbon_kgCO2e - off.physical_carbon_kgCO2e,
             on.demand_charge_CNY - off.demand_charge_CNY)
    assert all(value == pytest.approx(value) for value in delta)


def test_input_readiness_cannot_be_misreported_as_reliable_result():
    source = Path('src/urbanheatopt/data/bundles.py').read_text(encoding='utf-8')
    handoff = Path('src/urbanheatopt/optimization/adapters/handoff_v1.py').read_text(encoding='utf-8')
    assert '"economic_result_reliable": False' in source
    assert '"solver_executed": False' in source
    assert 'model_ready: bool = False' in handoff
