from dataclasses import replace
import json

import pytest
from pyomo.environ import value

from competition.core_model import CoreModelInput, EconomicInput, TechnologySpec, ThermalStorageSpec, build_core_model
from competition.solvers import SolverSettings, solve_pyomo_model, SolverNotOptimalError
from competition.road_joint_v2.core import RoadCase, PipeDesign, build_road_model
from competition.road_joint_v2.results import export_solution, audit_export


def shared_case(mode='central', loss=.02):
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
    assert grade_indexed.nvariables()-aggregate.nvariables() == 4*edge_hours
    assert grade_indexed.nconstraints()-aggregate.nconstraints() == len(aggregate.K)*edge_hours


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
