import pytest

from urbanheatopt.optimization.mode_diagnostics import (
    REAL_COST_COMPONENTS, classify_realized_mode, compare_mode_diagnostics,
    make_mode_diagnostic,
)


def _connections(connected):
    return {f'B{i:02d}': float(i < connected) for i in range(62)}


def _diag(connected, *, requested='hybrid', total=45., hash_='case', site='S1',
          network='network', tree='tree', objective='cost', cap=100., demand=5.):
    parts = {key: 5. for key in REAL_COST_COMPONENTS}
    parts['monthly_demand_charge'] = demand
    total = sum(parts.values()) if total is None else total
    return make_mode_diagnostic(
        requested_mode=requested, connected=_connections(connected),
        annual_cost_breakdown=parts, annual_real_cost_CNY_per_year=total,
        annual_carbon_breakdown={'electricity': 20., 'gas': 30.},
        annual_physical_carbon_kgCO2e_per_year=50., selected_site=site,
        tree_summary={}, selected_pipe_summary={},
        monthly_demand_charge_CNY_per_year=demand, solver_status='ok',
        termination_condition='optimal', solver_gap=0., case_sha256=hash_,
        parameter_version='p', snapshot_id='p', network_hash=network,
        tree_hash=tree, objective=objective, carbon_cap_kgCO2e_per_year=cap)


def test_requested_hybrid_can_realize_both_pure_modes_or_true_hybrid():
    assert _diag(62).realized_mode == 'PURE_CENTRAL'
    assert _diag(0).realized_mode == 'PURE_DISTRIBUTED'
    diagnostic = _diag(61)
    assert diagnostic.realized_mode == 'TRUE_HYBRID'
    assert (diagnostic.connected_building_count, diagnostic.local_building_count) == (61, 1)


def test_nonbinary_or_missing_connection_decisions_fail_closed():
    with pytest.raises(ValueError):
        classify_realized_mode({})
    with pytest.raises(ValueError, match='binary'):
        classify_realized_mode({'B': .8})


def test_hash_mismatch_prevents_matched_comparison():
    result = compare_mode_diagnostics(_diag(62, hash_='a'), _diag(61, hash_='b'))
    assert not result['matched_comparison']
    assert 'case_bundle' in result['different_fields']


@pytest.mark.parametrize('field,value', [
    ('site', 'S2'), ('network', 'n2'), ('tree', 't2'),
    ('objective', 'carbon'), ('cap', 99.),
])
def test_site_network_tree_objective_or_cap_difference_is_reported(field, value):
    kwargs = {field: value}
    result = compare_mode_diagnostics(_diag(62), _diag(61, **kwargs))
    assert not result['matched_comparison']
    expected = {'network': 'network', 'tree': 'tree', 'cap': 'carbon_cap'}.get(field, field)
    assert expected in result['different_fields']


def test_cost_components_reconcile_and_monthly_demand_is_separate():
    baseline = _diag(62, total=None, demand=5.)
    alternative = _diag(61, total=None, demand=8.)
    result = compare_mode_diagnostics(baseline, alternative)
    assert result['matched_comparison']
    assert result['cost_decomposition_complete']
    assert sum(result['cost_component_delta_CNY_per_year'].values()) == pytest.approx(
        result['delta_cost_CNY_per_year'])
    assert result['monthly_demand_charge_delta_CNY_per_year'] == pytest.approx(3.)
