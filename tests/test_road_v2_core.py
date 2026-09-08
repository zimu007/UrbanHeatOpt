from dataclasses import replace
import json

import pytest
from pyomo.environ import value

from urbanheatopt.model.reference_core import (
    CAPACITY_MARGIN_BASIS_BUILDING_USEFUL,
    CAPACITY_MARGIN_BASIS_SOURCE_INCLUDING_LOSS,
    CoreModelInput, EconomicInput, TechnologySpec, ThermalStorageSpec, build_core_model,
)
from urbanheatopt.optimization.solvers import SolverSettings, solve_pyomo_model, SolverNotOptimalError
from urbanheatopt.model.road_core import (
    RoadCase,
    PipeDesign,
    build_road_model,
    classify_direction_pair,
    classify_direction_solution,
)
from urbanheatopt.qa.road_results import export_solution, audit_export


def shared_case(
    mode='central',
    loss=.02,
    capacity_margin_basis=CAPACITY_MARGIN_BASIS_SOURCE_INCLUDING_LOSS,
):
    nodes = [dict(node_id=n, node_type=kind, x_m=x, y_m=y)
             for n,kind,x,y in [('R','road',0,0),('J','road',300,0),('A','building',300,10),('B','building',300,-10)]]
    edges = [dict(edge_id=e, node_u=u, node_v=v, coordinates=coords, length_m=length,
                  edge_type=kind, route_basis='supply_return_pair_route_m', highway='secondary', osm_way_ids=['test'])
             for e,u,v,coords,length,kind in [
                 ('trunk','R','J',[(0,0),(300,0)],300,'road'),
                 ('branchA','J','A',[(300,0),(300,10)],10,'building_service'),
                 ('branchB','J','B',[(300,0),(300,-10)],10,'building_service')]]
    network = dict(crs='EPSG:32650', nodes=nodes, edges=edges,
                   sites=[dict(site_id='S1',attachment_node_id='R'),dict(site_id='S2',attachment_node_id='J')])
    techs = tuple(TechnologySpec(t,kind,scope,carrier,cop,eta,0,1000,cost,.01,0,20,'synthetic_test','synthetic_test')
                  for t,kind,scope,carrier,cop,eta,cost in [
                      ('hp','air_source_heat_pump','central','electricity',4,None,2),
                      ('gas','gas_boiler','central','gas',None,.94,2),
                      ('local','air_source_heat_pump','local','electricity',3,None,3)])
    econ = EconomicInput({1:1.,2:1.},{1:1.,2:2.},{1:.5,2:.5},2.,
        {'A':10.,'B':10.},{'A':20,'B':20},1e6,discount_rate=.05,station_fixed_capex_CNY=10.,
        electricity_carbon_kgCO2e_per_kWh_e={1:.4,2:.6},gas_carbon_kgCO2e_per_kWh_LHV={1:.2,2:.2})
    common = CoreModelInput(mode,(1,2),None,('A','B'),{('A',1):40.,('A',2):30.,('B',1):60.,('B',2):50.},
                           techs,(),econ,allow_unserved=False,peak_capacity_margin_fraction=.2,
                           capacity_margin_basis=capacity_margin_basis,
                           candidate_station_nodes=('S1','S2'))
    levels = tuple(PipeDesign(f'test_{i}',cap,cost,30,loss,1e-5) for i,cap,cost in [(1,50,1),(2,100,2),(3,200,3)])
    return RoadCase(common,json.dumps(network),levels,('2026-12-01T00:00:00+08:00','2026-12-01T01:00:00+08:00'))


def solve(case, fixed_site=None):
    m = build_road_model(case)
    if fixed_site:
        m.station_built[fixed_site].fix(1)
    solve_pyomo_model(m, SolverSettings())
    return m


def with_pumping_rates(case, rates):
    return replace(case, pipe_designs=tuple(
        replace(level, pumping_kWh_e_per_kWh_th_m=rate)
        for level, rate in zip(case.pipe_designs, rates, strict=True)
    ))


def test_uniform_pumping_uses_aggregate_flow_and_nonuniform_falls_back():
    case = shared_case()
    aggregate = build_road_model(case)
    grade_indexed = build_road_model(with_pumping_rates(case, (1e-5, 2e-5, 3e-5)))
    edge_hours = len(aggregate.E)*len(aggregate.HOURS)

    assert value(aggregate.uniform_pumping_flow) is True
    assert value(grade_indexed.uniform_pumping_flow) is False
    assert len(aggregate.forward) == edge_hours
    assert len(aggregate.reverse) == edge_hours
    assert len(grade_indexed.forward) == edge_hours*len(grade_indexed.K)
    assert len(grade_indexed.reverse) == edge_hours*len(grade_indexed.K)
    optimized_edge_hours = len(aggregate.FLOW_E)*len(aggregate.HOURS)
    assert grade_indexed.nvariables()-aggregate.nvariables() == 4*optimized_edge_hours
    assert grade_indexed.nconstraints()-aggregate.nconstraints() == len(aggregate.K)*optimized_edge_hours


def test_direction_relaxation_reduces_binary_and_constraint_counts():
    exact = build_road_model(shared_case())
    relaxed = build_road_model(shared_case(), direction_relaxation=True)
    pair_count = len(exact.FLOW_E)*len(exact.HOURS)

    assert tuple(exact.EXACT_DIRECTION_PAIRS) == (('trunk', 1), ('trunk', 2))
    assert tuple(relaxed.EXACT_DIRECTION_PAIRS) == ()
    assert tuple(relaxed.RELAXED_DIRECTION_PAIRS) == (('trunk', 1), ('trunk', 2))
    assert len(exact._direction_var)-len(relaxed._direction_var) == pair_count
    assert exact.nvariables()-relaxed.nvariables() == pair_count
    assert exact.nconstraints()-relaxed.nconstraints() == 3*pair_count

    distributed = build_road_model(shared_case('distributed'), direction_relaxation=True)
    assert tuple(distributed.EXACT_DIRECTION_PAIRS) == ()
    assert tuple(distributed.RELAXED_DIRECTION_PAIRS) == ()
    assert len(distributed._direction_var) == 0


def test_direction_relaxation_adds_convex_hull_loss_lower_bound():
    model = build_road_model(shared_case(), direction_relaxation=True)
    row = model.direction_relaxation_lower['trunk', 1]
    for grade in model.K:
        model.grade['trunk', grade].set_value(int(grade == 'test_1'))
    half_loss = value(model.edge_loss['trunk'])/2
    model._forward_var['trunk', 1].set_value(half_loss-.1)
    model._reverse_var['trunk', 1].set_value(0)
    assert value(row.body) > value(row.upper)
    model._forward_var['trunk', 1].set_value(half_loss)
    assert value(row.body) == pytest.approx(value(row.upper))


def test_direction_solution_classification_is_fully_liftable_and_infers_output():
    model = build_road_model(shared_case(), direction_relaxation=True)
    model.station_built['S1'].fix(1)
    solve_pyomo_model(model)

    audit = classify_direction_solution(model)
    assert audit['liftable']
    assert audit['violation_pairs'] == ()
    assert audit['metrics']['pair_count'] == 2
    assert audit['metrics']['relaxed_pair_count'] == 2
    assert audit['inferred_directions'] == {('trunk', 1): 1, ('trunk', 2): 1}


def test_direction_solution_classification_detects_counterflow_and_loss_shortfall():
    model = build_road_model(shared_case(), direction_relaxation=True)
    model.station_built['S1'].fix(1)
    solve_pyomo_model(model)
    half_loss = value(model.edge_loss['trunk'])/2

    model._forward_var['trunk', 1].set_value(half_loss)
    model._reverse_var['trunk', 1].set_value(half_loss)
    counterflow = classify_direction_solution(model)
    assert counterflow['violation_pairs'] == (('trunk', 1),)
    assert counterflow['violations'][0]['counterflow_kW'] == pytest.approx(half_loss)
    assert counterflow['violations'][0]['loss_shortfall_kW'] == 0

    model._forward_var['trunk', 1].set_value(0)
    model._reverse_var['trunk', 1].set_value(0)
    shortfall = classify_direction_solution(model)
    assert shortfall['violation_pairs'] == (('trunk', 1),)
    assert shortfall['violations'][0]['counterflow_kW'] == 0
    assert shortfall['violations'][0]['loss_shortfall_kW'] == pytest.approx(half_loss)
    assert classify_direction_pair(half_loss, 0, 2*half_loss)['direction'] == 1
    assert classify_direction_pair(0, half_loss, 2*half_loss)['direction'] == 0


def test_direction_relaxation_restores_selected_exact_pair():
    relaxed = build_road_model(shared_case(), direction_relaxation=True)
    restored = build_road_model(
        shared_case(),
        direction_relaxation=True,
        exact_direction_pairs=(('trunk', 1),),
    )

    assert tuple(restored.EXACT_DIRECTION_PAIRS) == (('trunk', 1),)
    assert tuple(restored.RELAXED_DIRECTION_PAIRS) == (('trunk', 2),)
    assert ('trunk', 1) in restored._direction_var
    assert ('trunk', 2) not in restored._direction_var
    assert restored.nvariables()-relaxed.nvariables() == 1
    assert restored.nconstraints()-relaxed.nconstraints() == 3
    restored.station_built['S1'].fix(1)
    solve_pyomo_model(restored)
    assert classify_direction_solution(restored)['liftable']


def test_service_leaf_flows_are_exact_expressions_and_keep_public_interface():
    case = shared_case()
    model = solve(case, 'S1')

    assert tuple(model.SERVICE_LEAF_E) == ('branchA', 'branchB')
    assert tuple(model.FLOW_E) == ('trunk',)
    assert len(model.forward) == len(model.E)*len(model.HOURS)
    assert len(model._forward_var) == len(model.FLOW_E)*len(model.HOURS)
    for edge, building in (('branchA', 'A'), ('branchB', 'B')):
        for hour in model.HOURS:
            expected = case.common.heat_demand_kW[building,hour] + value(model.edge_loss[edge])/2
            assert value(model.forward[edge,hour]) == pytest.approx(expected)
            assert value(model.reverse[edge,hour]) == 0
            assert value(model.direction[edge,hour]) == 1


def test_service_leaf_node_u_uses_reverse_expression_and_exports_qa(tmp_path):
    case = shared_case()
    network = case.network
    branch = next(edge for edge in network['edges'] if edge['edge_id'] == 'branchA')
    branch['node_u'], branch['node_v'] = branch['node_v'], branch['node_u']
    branch['coordinates'] = branch['coordinates'][::-1]
    case = replace(case, network_json=json.dumps(network))
    model = solve(case, 'S1')

    for hour in model.HOURS:
        expected = case.common.heat_demand_kW['A',hour] + value(model.edge_loss['branchA'])/2
        assert value(model.forward['branchA',hour]) == 0
        assert value(model.reverse['branchA',hour]) == pytest.approx(expected)
        assert value(model.direction['branchA',hour]) == 0
    assert export_solution(case, model, tmp_path/'solution')['passed']


def test_exact_reductions_match_same_case_general_flow_fallback():
    optimized_case = shared_case('central')
    optimized = solve(optimized_case, 'S1')
    fallback_case = replace(optimized_case,
        common=replace(optimized_case.common, allow_unserved=True))
    fallback = build_road_model(fallback_case)
    fallback.station_built['S1'].fix(1)
    for building in fallback.DEMAND_NODES:
        for hour in fallback.HOURS:
            fallback.unserved_heat_kW[building,hour].fix(0)
    solve_pyomo_model(fallback)

    assert not value(fallback.service_leaf_flow_elimination)
    assert value(optimized.annual_real_cost_CNY_per_year) == pytest.approx(
        value(fallback.annual_real_cost_CNY_per_year), abs=1e-8)
    assert value(optimized.annual_operating_physical_carbon_kgCO2e_per_year) == pytest.approx(
        value(fallback.annual_operating_physical_carbon_kgCO2e_per_year), abs=1e-8)
    for edge in optimized.E:
        for hour in optimized.HOURS:
            assert value(optimized.flow[edge,hour]) == pytest.approx(
                value(fallback.flow[edge,hour]), abs=1e-8)


def test_service_leaf_elimination_falls_back_when_dispatch_is_not_determined():
    case = shared_case()
    case = replace(case, common=replace(case.common, allow_unserved=True))
    model = build_road_model(case)

    assert not value(model.service_leaf_flow_elimination)
    assert len(model.SERVICE_LEAF_E) == 0
    assert len(model.FLOW_E) == len(model.E)
    assert len(model._forward_var) == len(model.E)*len(model.HOURS)


def test_reverse_upper_bound_implies_direction_not_built_without_redundant_row():
    case = shared_case('hybrid')
    case = replace(case, common=replace(case.common, allow_unserved=True))
    model = build_road_model(case)
    for building in model.DEMAND_NODES:
        model.connected[building].fix(0)
    model.built['trunk'].fix(0)
    model._direction_var['trunk', 1].fix(1)
    with pytest.raises(SolverNotOptimalError):
        solve_pyomo_model(model)


def test_fixed_modes_and_demand_identity_remove_only_determined_variables():
    central = build_road_model(shared_case('central'))
    distributed = solve(shared_case('distributed'))
    hybrid = build_road_model(shared_case('hybrid'))

    assert value(central.deterministic_demand_dispatch)
    assert central.nvariables() == 70
    assert distributed.nvariables() == len(distributed.DEMAND_NODES) + 1
    assert distributed.nconstraints() == 3*len(distributed.DEMAND_NODES) + len(distributed.HOURS)
    assert hybrid.nvariables() == 74
    for building in distributed.DEMAND_NODES:
        assert value(distributed.local_installed[building]) == 1
        for hour in distributed.HOURS:
            assert value(distributed.local_heat[building,hour]) == shared_case().common.heat_demand_kW[building,hour]
            assert value(distributed.network_heat[building,hour]) == 0
            assert value(distributed.unserved_heat_kW[building,hour]) == 0


def test_capacity_margin_applies_to_building_demand_not_network_loss(tmp_path):
    case = shared_case(
        "central",
        loss=0.1,
        capacity_margin_basis=CAPACITY_MARGIN_BASIS_BUILDING_USEFUL,
    )
    model = solve(case, "S1")

    installed = sum(value(model.capacity["S1", tech]) for tech in model.T)
    peak_building_demand = max(
        sum(case.common.heat_demand_kW[building, hour] for building in case.common.demand_nodes)
        for hour in case.common.hours
    )
    total_loss = sum(value(model.edge_loss[edge]) for edge in model.E)
    assert value(model.connected_building_demand[1]) == pytest.approx(peak_building_demand)
    assert installed == pytest.approx(peak_building_demand + total_loss, abs=1e-6)
    assert installed >= 1.2 * peak_building_demand - 1e-6
    assert installed < 1.2 * (peak_building_demand + total_loss)

    qa = export_solution(case, model, tmp_path / "capacity_margin_scope")
    assert qa["passed"]
    assert qa["capacity_margin_basis"] == CAPACITY_MARGIN_BASIS_BUILDING_USEFUL
    assert qa["network_heat_loss_in_capacity_margin"] is False
    assert qa["storage_counted_in_capacity_margin"] is False


def test_nonuniform_pumping_fallback_exports_grade_weighted_flow(tmp_path):
    case = with_pumping_rates(shared_case(), (1e-5, 2e-5, 3e-5))
    model = solve(case, 'S1')
    assert value(model.uniform_pumping_flow) is False
    assert export_solution(case, model, tmp_path/'solution')['passed']


def test_shared_300m_cost_capacity_loss_and_junction_balance():
    case = shared_case()
    m = solve(case, 'S1')
    assert value(m.station_built['S2']) == 0
    assert value(m.grade['trunk','test_3']) == 1
    assert value(m.flow['trunk',1]) == pytest.approx(103.4)
    assert value(sum(m.heat['S1',t,1] for t in m.T)) == pytest.approx(106.4)
    assert value(m.edge_capacity['trunk']) >= value(m.abs_flow['trunk',1]+m.edge_loss['trunk']/2)
    # Pair-route length is charged once: trunk300*3, A10*1, B10*2.
    r,n = .05,30
    crf = r*(1+r)**n/((1+r)**n-1)
    assert value(m.pipe_investment) == pytest.approx(930*crf)
    assert value(m.pump['trunk',1]) == pytest.approx(300*103.4*1e-5)


@pytest.mark.parametrize('mode',['central','distributed','hybrid'])
def test_modes_capacity_and_real_cost(mode):
    m = solve(shared_case(mode))
    assert sum(value(m.unserved_heat_kW[b,h]) for b in m.DEMAND_NODES for h in m.HOURS) == 0
    for b in m.DEMAND_NODES:
        assert value(m.connected[b]+m.local_installed[b]) == 1
    if mode == 'central':
        assert all(value(m.connected[b]) == 1 for b in m.DEMAND_NODES)
    if mode == 'distributed':
        assert all(value(m.built[e]) == 0 for e in m.E)
        assert all(value(m.station_built[s]) == 0 for s in m.S)
    assert value(m.annual_cost_objective) == value(m.annual_real_cost_CNY_per_year)


def test_unbuilt_station_does_not_disconnect_road():
    m = solve(shared_case(), 'S1')
    assert value(m.station_built['S2']) == 0
    assert value(m.built['trunk']) == 1
    assert value(m.commodity['trunk']) == pytest.approx(2)


def test_forbidden_trunk_cannot_be_masked_by_unserved():
    case = shared_case()
    case = replace(case, common=replace(case.common, allow_unserved=True))
    m = build_road_model(case)
    m.station_built['S1'].fix(1)
    m.built['trunk'].fix(0)
    with pytest.raises(SolverNotOptimalError):
        solve_pyomo_model(m)


def test_hybrid_feasible_domain_contains_pure_modes():
    for pure in ('central','distributed'):
        original = solve(shared_case(pure))
        hybrid = build_road_model(shared_case('hybrid'))
        for b in hybrid.DEMAND_NODES:
            hybrid.connected[b].fix(int(pure == 'central'))
        solve_pyomo_model(hybrid)
        assert value(hybrid.annual_real_cost_CNY_per_year) == pytest.approx(value(original.annual_real_cost_CNY_per_year), abs=1e-6)


def test_distributed_same_physics_economics_as_frozen_core():
    case = shared_case('distributed')
    new = solve(case)
    old = build_core_model(case.common)
    solve_pyomo_model(old)
    assert value(new.annual_real_cost_CNY_per_year) == pytest.approx(value(old.annual_real_cost_CNY_per_year), abs=1e-6)
    assert value(new.annual_operating_physical_carbon_kgCO2e_per_year) == pytest.approx(value(old.annual_operating_physical_carbon_kgCO2e_per_year), abs=1e-6)


def test_tes_cyclic_balance_and_exclusive_dispatch():
    case = shared_case()
    tes = ThermalStorageSpec('tes',1000,1000,1000,.95,.92,.0005,.0001,.0001,.0001,20)
    case = replace(case,common=replace(case.common,storage=tes))
    m = solve(case,'S1')
    assert sum(value(m.charge[s,h]) for s in m.S for h in m.HOURS) > 0
    for s in m.S:
        for i,h in enumerate(case.common.hours):
            previous = case.common.hours[i-1]
            assert value(m.soc[s,h]) == pytest.approx(value(m.soc[s,previous])*.9995+value(m.charge[s,h])*.95-value(m.discharge[s,h])/.92, abs=1e-7)
            assert value(m.charge[s,h])*value(m.discharge[s,h]) == 0


@pytest.mark.parametrize('mode',['central','distributed','hybrid'])
def test_export_independent_qa_and_detect_modified_cost(mode,tmp_path):
    import pandas as pd
    case=shared_case(mode)
    m=solve(case)
    root=tmp_path/'solution'
    result=export_solution(case,m,root)
    assert result['passed']
    costs=pd.read_csv(root/'cost_breakdown.csv')
    costs.loc[0,'annual_CNY']+=1
    costs.to_csv(root/'cost_breakdown.csv',index=False)
    assert not audit_export(case,root)['passed']


def test_export_detects_thermal_dispatch_tamper(tmp_path):
    import pandas as pd
    case=shared_case()
    root=tmp_path/'solution'
    export_solution(case,solve(case,'S1'),root)
    flow=pd.read_parquet(root/'network_hourly.parquet')
    flow.loc[0,'signed_flow_kW_th']+=10
    flow.to_parquet(root/'network_hourly.parquet',index=False)
    assert not audit_export(case,root)['passed']


def test_export_audit_is_invariant_to_network_hourly_row_order(tmp_path):
    import pandas as pd
    case=shared_case()
    root=tmp_path/'solution'
    baseline=export_solution(case,solve(case,'S1'),root)
    flow=pd.read_parquet(root/'network_hourly.parquet')
    shuffled=flow.iloc[::-1].reset_index(drop=True)
    assert not shuffled[['edge_id','hour']].equals(flow[['edge_id','hour']])
    shuffled.to_parquet(root/'network_hourly.parquet',index=False)

    verdict=audit_export(case,root)

    assert verdict['passed']
    assert verdict['max_errors'].keys() == baseline['max_errors'].keys()
    for key, expected in baseline['max_errors'].items():
        assert verdict['max_errors'][key] == pytest.approx(expected,abs=1e-9)
    assert 'total' in verdict['timing_seconds']
    assert verdict['timing_seconds']['total'] >= 0


@pytest.mark.parametrize('mutation',['duplicate','missing'])
def test_export_audit_detects_network_hourly_coverage_errors(tmp_path,mutation):
    import pandas as pd
    case=shared_case()
    root=tmp_path/'solution'
    export_solution(case,solve(case,'S1'),root)
    flow=pd.read_parquet(root/'network_hourly.parquet')
    if mutation=='duplicate':
        flow.iloc[0]=flow.iloc[1]
    else:
        flow=flow.iloc[:-1]
    flow.to_parquet(root/'network_hourly.parquet',index=False)

    verdict=audit_export(case,root)

    assert not verdict['passed']
    assert 'network_coverage' in verdict['violations']


@pytest.mark.parametrize('target',['pipe_nan','summary_nan','pipe_cost','cost_nan'])
def test_export_rejects_nonfinite_or_inconsistent_static_records(tmp_path,target):
    import pandas as pd
    case=shared_case()
    root=tmp_path/'solution'
    export_solution(case,solve(case,'S1'),root)
    if target=='summary_nan':
        payload=json.loads((root/'solution_summary.json').read_text(encoding='utf-8'))
        payload['real_cost_CNY']=float('nan')
        (root/'solution_summary.json').write_text(json.dumps(payload),encoding='utf-8')
    else:
        name='cost_breakdown.csv' if target=='cost_nan' else 'network_decisions.csv'
        table=pd.read_csv(root/name)
        key={'cost_nan':'annual_CNY','pipe_nan':'capacity_kW_th','pipe_cost':'initial_investment_CNY'}[target]
        table.loc[0,key]=float('nan') if target.endswith('_nan') else 1e9
        table.to_csv(root/name,index=False)
    try:
        verdict=audit_export(case,root)
    except ValueError:
        return
    assert not verdict['passed']
