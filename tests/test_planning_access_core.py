from dataclasses import replace
import json
from math import hypot

import pandas as pd
import pytest
from pyomo.environ import value

from test_road_v2_core import shared_case, solve
from urbanheatopt.model.road_core import build_road_model
from urbanheatopt.spatial.atomic_network import validate_network, access_options
from urbanheatopt.qa.road_results import export_solution, audit_export
from urbanheatopt.optimization.solvers import solve_pyomo_model, SolverNotOptimalError


def alternative_case(mode='central'):
    case = shared_case(mode, loss=0.)
    net = case.network
    for node in net['nodes']:
        if node['node_id'] == 'B':
            node['x_m'] = 0
    for edge in net['edges']:
        if edge['edge_id'] == 'branchB':
            edge.update(node_u='R', coordinates=[[0, 0], [0, -10]])
    far = dict(edge_id='farA', node_u='R', node_v='A', coordinates=[[0, 0], [300, 10]],
               length_m=hypot(300, 10), edge_type='building_service', highway='service_branch')
    net['edges'].append(far)
    net['access_options'] = access_options(net)
    # Explicit legacy normalization yields both A choices; rank is only display.
    for i, option in enumerate(net['access_options']):
        option['candidate_rank'] = i+1
    validate_network(net)
    return replace(case, network_json=json.dumps(net))


def test_optimizer_chooses_longer_access_for_lower_whole_network_cost(tmp_path):
    case = alternative_case()
    model = solve(case, 'S1')
    assert value(model.built['farA']) == 1
    assert value(model.built['branchA']) == 0
    assert value(model.built['trunk']) == 0
    factor = .05*1.05**30/(1.05**30-1)
    assert value(model.pipe_investment) == pytest.approx((hypot(300,10)+20)*factor)
    qa = export_solution(case, model, tmp_path/'solution')
    assert qa['passed']
    options = pd.read_csv(tmp_path/'solution'/'access_decisions.csv')
    assert options.groupby('building_id').selected.sum().to_dict() == {'A': 1., 'B': 1.}


@pytest.mark.parametrize('mode', ['central', 'distributed', 'hybrid'])
def test_multiple_candidates_are_exclusive_and_outputs_independently_checked(mode, tmp_path):
    case = alternative_case(mode)
    model = solve(case, 'S1' if mode == 'central' else None)
    root = tmp_path/'solution'
    assert export_solution(case, model, root)['passed']
    table = pd.read_csv(root/'access_decisions.csv')
    for b in model.DEMAND_NODES:
        assert table.loc[table.building_id.eq(b), 'selected'].sum() == value(model.connected[b])
    table.loc[0, 'selected'] = 1-table.loc[0, 'selected']
    table.to_csv(root/'access_decisions.csv', index=False)
    assert not audit_export(case, root)['passed']


def test_cannot_build_two_accesses_to_turn_a_building_into_a_junction():
    model = build_road_model(alternative_case())
    model.built['branchA'].fix(1)
    model.built['farA'].fix(1)
    with pytest.raises(SolverNotOptimalError):
        solve_pyomo_model(model)


@pytest.mark.parametrize('pure', ['central','distributed'])
def test_multi_access_hybrid_contains_pure_mode(pure):
    original=solve(alternative_case(pure))
    hybrid=build_road_model(alternative_case('hybrid'))
    for b in hybrid.DEMAND_NODES:
        hybrid.connected[b].fix(int(pure=='central'))
    solve_pyomo_model(hybrid)
    assert value(hybrid.annual_real_cost_CNY_per_year)==pytest.approx(value(original.annual_real_cost_CNY_per_year),abs=1e-6)


def test_candidate_connectivity_must_not_use_a_building_as_a_road_bridge():
    case=alternative_case()
    net=case.network
    net['edges']=[e for e in net['edges'] if e['edge_id']!='trunk']
    from urbanheatopt.spatial.atomic_network import RoadNetworkError
    with pytest.raises(RoadNetworkError,match='不能到达'):
        validate_network(net)


def test_shared_access_tail_costs_once_and_selected_path_is_complete(tmp_path):
    case = alternative_case()
    net = case.network
    net['nodes'].append(dict(node_id='G', node_type='service_junction', x_m=300, y_m=5))
    for edge in net['edges']:
        if edge['edge_id'] in ('branchA', 'farA'):
            edge['node_v'] = 'G'
            edge['coordinates'][-1] = [300, 5]
            edge['length_m'] = 5 if edge['edge_id'] == 'branchA' else hypot(300, 5)
    net['edges'].append(dict(edge_id='tail', node_u='G', node_v='A', coordinates=[[300,5],[300,10]],
                             length_m=5, edge_type='building_service', highway='service_branch'))
    for option in net['access_options']:
        if option['building_id'] == 'A':
            option['edge_ids'].append('tail')
            option['length_m'] = sum(e['length_m'] for e in net['edges'] if e['edge_id'] in option['edge_ids'])
    case = replace(case, network_json=json.dumps(net))
    model = solve(case, 'S1')
    assert value(model.built['tail']) == 1
    assert value(model.built['farA'])+value(model.built['branchA']) == pytest.approx(1)
    assert export_solution(case, model, tmp_path/'solution')['passed']


def test_generated_multichoice_network_runs_three_mode_pareto(tmp_path):
    from test_planning_network import inputs
    from urbanheatopt.spatial.atomic_network import atomize_snapshot
    from urbanheatopt.optimization.reference_tasks import create_plan, run_task, assemble
    from urbanheatopt.optimization.solvers import SolverSettings
    from unittest.mock import patch
    source, buildings=inputs()
    net=atomize_snapshot(source,buildings,{'A':1.},candidate_count=1)
    base=shared_case(loss=.02)
    econ=replace(base.common.economics,connection_capex_CNY={'A':10.},connection_lifetime_years={'A':20},
                 gas_price_CNY_per_kWh_LHV={1:.1,2:.1})
    common=replace(base.common,demand_nodes=('A',),heat_demand_kW={('A',1):40.,('A',2):30.},
                   candidate_station_nodes=tuple(s['site_id'] for s in net['sites']),economics=econ)
    case=replace(base,common=common,network_json=json.dumps(net))
    root=tmp_path/'multichoice'
    plan=create_plan(case,root,full_scale=False,point_count=5)
    with patch('urbanheatopt.optimization.pareto.build_core_model',side_effect=AssertionError('禁止旧模型')):
        for task in plan['tasks']:
            run_task(root,task['task_id'],settings=SolverSettings(threads=1,mip_gap=0.))
    result=assemble(root)
    assert set(result.mode_frontiers)=={'central','distributed','hybrid'}
    assert all(result.mode_frontiers.values())
    for task in plan['tasks']:
        assert json.loads((root/'tasks'/task['task_id']/'success.json').read_text(encoding='utf-8'))['qa']['passed']
