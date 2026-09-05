from pathlib import Path
import json
import os
import shutil
from unittest.mock import patch

import pytest

from test_road_v2_core import shared_case
from urbanheatopt.data.road_builder import save_case, load_case
from urbanheatopt.optimization.reference_tasks import create_plan, run_task, assemble, refine_representatives
from urbanheatopt.optimization.solvers import SolverSettings


def test_immutable_serialization_and_old_hash_rejected(tmp_path):
    case=shared_case()
    path=tmp_path/'case.json'
    save_case(case,path)
    rebuilt=load_case(path)
    assert rebuilt.common.heat_demand_kW==case.common.heat_demand_kW
    assert rebuilt.network==case.network
    with pytest.raises(TypeError):
        rebuilt.common.heat_demand_kW['A',1]=1
    with pytest.raises(FileExistsError):
        save_case(case,path)


def test_task_guard_and_small_three_mode_epsilon_run(tmp_path):
    from dataclasses import replace
    root=tmp_path/'v2'
    original=shared_case()
    # Deliberate synthetic trade-off: gas is cheaper but emits more carbon.
    case=replace(original,common=replace(original.common,economics=replace(original.common.economics,gas_price_CNY_per_kWh_LHV={1:.1,2:.1})))
    plan=create_plan(case,root,full_scale=False,point_count=5)
    assert len(plan['tasks'])==21
    with pytest.raises(ValueError,match='六端点'):
        run_task(root,'central-epsilon-000')
    settings=SolverSettings(mip_gap=0.,threads=1)
    with patch('urbanheatopt.optimization.pareto.build_core_model',side_effect=AssertionError('旧核心不能运行')):
        for task in plan['tasks']:
            run_task(root,task['task_id'],settings=settings)
    frontier=assemble(root)
    scan_audit=json.loads((root/'scan_integrity_audit.json').read_text(encoding='utf-8'))
    assert scan_audit['status']=='passed'
    assert scan_audit['task_count']==len(plan['tasks'])
    assert all(row['verification']=='full_sha256' for row in scan_audit['tasks'])
    assert set(frontier.mode_frontiers)=={'central','distributed','hybrid'}
    assert all(frontier.mode_frontiers.values())
    assert len(frontier.mode_frontiers['central'])>=3
    assert len(frontier.combined_frontier)>=3
    epsilon_request=json.loads((root/'tasks'/'central-epsilon-000'/'attempt_0001'/'task_request.json').read_text(encoding='utf-8'))
    carbon_endpoint=json.loads((root/'tasks'/'central-carbon'/'success.json').read_text(encoding='utf-8'))['point']
    epsilon_result=json.loads((root/'tasks'/'central-epsilon-000'/'success.json').read_text(encoding='utf-8'))['point']
    assert epsilon_request['epsilon']==carbon_endpoint['annual_operating_carbon_kgCO2e_per_year']
    assert epsilon_request['epsilon_constraint_tolerance_kgCO2e_per_year']==1e-6
    assert epsilon_request['enforced_epsilon_upper_bound_kgCO2e_per_year']==pytest.approx(epsilon_request['epsilon']+1e-6)
    assert epsilon_result['epsilon_kgCO2e_per_year']==epsilon_request['epsilon']
    assert epsilon_result['annual_operating_carbon_kgCO2e_per_year']<=epsilon_request['epsilon']+1e-6
    # Carbon-only capacities may be arbitrary; the epsilon=carbon endpoint
    # solve must remove the more expensive same-carbon decision from frontier.
    minimum_carbon=min(p.annual_operating_carbon_kgCO2e_per_year for p in frontier.mode_frontiers['central'])
    carbon_ties=[p for p in frontier.mode_frontiers['central'] if abs(p.annual_operating_carbon_kgCO2e_per_year-minimum_carbon)<=1e-6]
    assert len(carbon_ties)==1
    assert carbon_ties[0].annual_real_cost_CNY_per_year<150  # this synthetic case only
    images=json.loads((root/'figures'/'render_validation.json').read_text(encoding='utf-8'))
    assert len(images)==6 and all(item['nonblank'] for item in images.values())
    policy_cap=max(p.annual_operating_carbon_kgCO2e_per_year for p in frontier.combined_frontier)
    from urbanheatopt.optimization import reference_tasks as task_module
    real_hash=task_module.file_hash
    scan_mps_rehashes=[]
    def track_scan_mps(path):
        candidate=Path(path)
        if candidate.name=='model.mps' and 'tasks' in candidate.parts:
            scan_mps_rehashes.append(candidate)
        return real_hash(candidate)
    with patch.object(task_module,'file_hash',side_effect=track_scan_mps):
        refined=refine_representatives(root,policy_carbon_cap=policy_cap,settings=settings)
    assert scan_mps_rehashes==[]
    final_audit=json.loads((root/'integrity_audit.json').read_text(encoding='utf-8'))
    assert final_audit['status']=='passed'
    assert final_audit['scan_task_count']==len(plan['tasks'])
    assert final_audit['refinement_task_count']>0
    assert all(row['certified_at_0_1_percent'] for row in refined.values())
    assert all(row['point']['reported_mip_gap']<=.001 for row in refined.values())
    assert refined['minimum_carbon']['point']['annual_real_cost_CNY_per_year']<=max(p.annual_real_cost_CNY_per_year for p in frontier.combined_frontier)+1e-4
    with pytest.raises(ValueError,match='政策碳'):
        refine_representatives(root,policy_carbon_cap=float('nan'))
    for task in plan['tasks']:
        marker=root/'tasks'/task['task_id']/'success.json'
        assert json.loads(marker.read_text(encoding='utf-8'))['qa']['passed']
    # Resuming a finished task does not re-solve; changing the model makes it invalid.
    with patch('urbanheatopt.optimization.reference_tasks.build_road_model',side_effect=AssertionError('不能重复求解')):
        run_task(root,'central-cost',settings=settings)
    # Keep the successful evidence replayable; tamper only with a separate copy.
    tampered=tmp_path/'tampered_case'
    tampered.mkdir()
    shutil.copy2(root/'case.json',tampered/'case.json')
    shutil.copy2(root/'task_plan.json',tampered/'task_plan.json')
    payload=json.loads((tampered/'case.json').read_text(encoding='utf-8'))
    payload['peak_margin']=.3
    (tampered/'case.json').write_text(json.dumps(payload),encoding='utf-8')
    with pytest.raises(ValueError,match='哈希改变'):
        run_task(tampered,'central-cost',settings=settings)


def test_mps_hash_is_reused_then_deferred_with_metadata_guard(tmp_path,monkeypatch):
    from urbanheatopt.optimization import reference_tasks as tasks
    root=tmp_path/'deferred_mps'
    create_plan(shared_case(),root,full_scale=False,point_count=5)
    real_hash=tasks.file_hash

    def forbid_redundant_mps_hash(path):
        if Path(path).name=='model.mps':
            raise AssertionError('MPS digest must be reused or deferred here')
        return real_hash(Path(path))

    with monkeypatch.context() as scoped:
        scoped.setattr(tasks,'file_hash',forbid_redundant_mps_hash)
        run_task(root,'central-cost',settings=SolverSettings(mip_gap=0.,threads=1))
        with pytest.raises(ValueError,match='六端点'):
            run_task(root,'central-epsilon-000',settings=SolverSettings(mip_gap=0.,threads=1))

    model=root/'tasks'/'central-cost'/'attempt_0001'/'model.mps'
    stat=model.stat()
    os.utime(model,ns=(stat.st_atime_ns,stat.st_mtime_ns+1_000_000_000))
    with pytest.raises(ValueError,match='MPS元数据'):
        run_task(root,'central-epsilon-000',settings=SolverSettings(mip_gap=0.,threads=1))


def test_assemble_full_hash_catches_mps_change_with_restored_metadata(tmp_path):
    root=tmp_path/'full_audit'
    plan=create_plan(shared_case(),root,full_scale=False,point_count=5)
    settings=SolverSettings(mip_gap=0.,threads=1)
    for task in plan['tasks']:
        run_task(root,task['task_id'],settings=settings)
    model=root/'tasks'/'central-cost'/'attempt_0001'/'model.mps'
    stat=model.stat()
    with model.open('r+b') as stream:
        original=stream.read(1)
        stream.seek(0)
        stream.write(b'X' if original!=b'X' else b'Y')
    os.utime(model,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    assert model.stat().st_size==stat.st_size
    assert model.stat().st_mtime_ns==stat.st_mtime_ns
    with pytest.raises(ValueError,match='已完成结果被修改'):
        assemble(root)


def test_task_records_aggregate_flow_formulation(tmp_path):
    root=tmp_path/'flow_evidence'
    create_plan(shared_case(),root,full_scale=False,point_count=5)
    run_task(root,'central-cost',settings=SolverSettings(mip_gap=0.,threads=1))
    build=json.loads((root/'tasks'/'central-cost'/'attempt_0001'/'model_build_completed.json').read_text(encoding='utf-8'))
    assert build['flow_formulation']=='aggregate_uniform_pumping'
    assert build['service_leaf_flow_elimination'] is True
    assert build['eliminated_service_leaf_edge_count'] == 2
    assert build['deterministic_demand_dispatch'] is True
    assert build['distributed_fastpath'] is False


def test_first_full_season_task_and_worker_reservation_guards(tmp_path,monkeypatch):
    from urbanheatopt.optimization import reference_tasks as tasks
    plan=create_plan(shared_case(),tmp_path/'case',point_count=5,full_scale=False)
    monkeypatch.setattr(tasks,'verify_plan',lambda root:{**plan,'full_scale':True})
    with pytest.raises(ValueError,match='首次全季'):
        run_task(tmp_path/'case','distributed-cost')
    occupied=tmp_path/'case'/'tasks'/'central-cost'
    occupied.mkdir(parents=True)
    (occupied/'worker_reservation.json').write_text(json.dumps({'pid':1,'attempt':'attempt_0001'}),encoding='utf-8')
    with pytest.raises(ValueError,match='已有工作进程'):
        run_task(tmp_path/'case','central-cost')


def test_failed_worker_reservation_is_archived_before_retry(tmp_path,monkeypatch):
    root=tmp_path/'retry_failed_worker'
    create_plan(shared_case(),root,point_count=5,full_scale=False)
    task_root=root/'tasks'/'central-cost'
    failed_attempt=task_root/'attempt_0001'
    failed_attempt.mkdir(parents=True)
    (failed_attempt/'failure.json').write_text(json.dumps({'error':'synthetic failure'}),encoding='utf-8')
    reservation={'pid':1,'attempt':'attempt_0001','created_at':'synthetic'}
    (task_root/'worker_reservation.json').write_text(json.dumps(reservation),encoding='utf-8')
    monkeypatch.setattr(
        'urbanheatopt.optimization.reference_tasks.build_road_model',
        lambda *_args,**_kwargs: (_ for _ in ()).throw(RuntimeError('stop after reservation rollover')),
    )
    with pytest.raises(RuntimeError,match='reservation rollover'):
        run_task(root,'central-cost',settings=SolverSettings(mip_gap=0.,threads=1))
    archived=json.loads((failed_attempt/'worker_reservation.json').read_text(encoding='utf-8'))
    active=json.loads((task_root/'worker_reservation.json').read_text(encoding='utf-8'))
    assert archived==reservation
    assert active['attempt']=='attempt_0002'
    assert (task_root/'attempt_0002'/'failure.json').exists()


def test_real_pipeline_stops_before_solver_when_geometry_fails(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import pandas as pd
    from urbanheatopt.data import reference_road_pipeline as pipeline
    from urbanheatopt.spatial.atomic_network import RoadNetworkError
    data=SimpleNamespace(building_count=62,hour_count=2160,load_row_count=133920,
        loads=pd.DataFrame({'building_id':['B'],'hour':[1],'heating_kW':[100.]}),buildings='fake')
    gate=SimpleNamespace(adaptation=SimpleNamespace(canonical_data=data,source_report=SimpleNamespace(valid=True),canonical_report=SimpleNamespace(valid=True)))
    monkeypatch.setattr(pipeline,'run_guanggu_v03_input_validation',lambda *a,**kw:gate)
    monkeypatch.setattr(pipeline,'resolve_guanggu_v03_source_roots',lambda *a:SimpleNamespace(delivery_root=tmp_path))
    monkeypatch.setattr(pipeline,'read_package',lambda *a:{})
    data.external_timeseries='fake'
    data.adaptation_metadata={'natural_gas_lhv_MJ_per_Nm3':38.931}
    monkeypatch.setattr(pipeline,'describe_energy_input',lambda *a:{})
    monkeypatch.setattr(pipeline,'effective_parameters',lambda *a,**kw:{})
    monkeypatch.setattr(pipeline,'write_gap_report',lambda *a:None)
    def failure(*a):
        raise RoadNetworkError('需接入线',[{'building_id':'B'}])
    monkeypatch.setattr(pipeline,'atomize_snapshot',failure)
    def forbid(*a,**kw):
        raise AssertionError('失败后禁止构模和求解')
    monkeypatch.setattr(pipeline,'build_season_case',forbid)
    monkeypatch.setattr(pipeline,'create_plan',forbid)
    source=tmp_path/'osm.json'
    source.write_text('{}',encoding='utf-8')
    with pytest.raises(RoadNetworkError):
        pipeline.prepare_delivery(tmp_path,source,tmp_path,'attempt')
    state=json.loads((tmp_path/'attempt'/'preparation_status.json').read_text(encoding='utf-8'))
    assert state['canonical_validation_passed']
    assert not state['model_ready'] and not state['solver_executed']


def test_full_shape_builder_uses_all_synthetic_62_by_2160_rows(tmp_path):
    """Schema-scale test only: all load values and coordinates here are synthetic."""
    from types import SimpleNamespace
    import pandas as pd
    from urbanheatopt.data.road_builder import build_season_case
    from urbanheatopt.parameters.legacy_economics import TEST_VALUES
    ids=[f'synthetic_{i:02d}' for i in range(62)]
    timestamps=pd.date_range('2026-12-01',periods=2160,freq='h',tz='Asia/Shanghai')
    loads=pd.DataFrame([(b,h,1.) for b in ids for h in range(1,2161)],columns=['building_id','hour','heating_kW'])
    external=pd.DataFrame({'hour':range(1,2161),'timestamp':timestamps,'outdoor_temperature_C':0.,
        'time_weight_h_per_year':1.,'electricity_price_CNY_per_kWh_e':1.,'gas_price_CNY_per_kWh_LHV':.35,
        'electricity_carbon_kgCO2e_per_kWh_e':.4,'gas_carbon_kgCO2e_per_kWh_LHV':.2})
    values={k:v[0] for k,v in TEST_VALUES.items()}
    values.update(central_hp_capex=2400.,local_hp_capex=2400.,boiler_capex=270.,hp_fixed_om=.01,
        hp_life=20,tes_eta_charge=.95,tes_eta_discharge=.92,tes_life=20,pipe_capacity_kW_th=[44.64,74.4,111.6])
    snapshot={'values':values,'full_park_peak_kW':62.,'snapshot_sha256':'synthetic_test'}
    curve=tmp_path/'curve.csv'
    pd.DataFrame({'technology_id':['ASHP_BASE_01']*2,'Tout':[-20,15],'Tsupply':[45,45],
                  'PLR':[1,1],'COP':[2.,4.],'capacity_ratio':[.5,1.]}).to_csv(curve,index=False)
    data=SimpleNamespace(building_count=62,hour_count=2160,load_row_count=133920,loads=loads,
        external_timeseries=external,buildings=pd.DataFrame({'building_id':ids}),
        equipment_performance=pd.DataFrame({'technology_type':['gas_boiler'],'energy_basis':['LHV'],'efficiency':[.94]}))
    adaptation=SimpleNamespace(canonical_data=data,source_report=SimpleNamespace(valid=True),
        canonical_report=SimpleNamespace(valid=True),equipment_performance_path=curve)
    network={'crs':'EPSG:32650','nodes':[dict(node_id='R',node_type='road',x_m=0.,y_m=0.)]
        +[dict(node_id=b,node_type='building',x_m=i+1.,y_m=10.) for i,b in enumerate(ids)],
        'edges':[dict(edge_id=f'e{i}',node_u='R',node_v=b,coordinates=[[0.,0.],[i+1.,10.]],
                      length_m=((i+1)**2+100)**.5,edge_type='building_service') for i,b in enumerate(ids)],
        'sites':[dict(site_id='S1',attachment_node_id='R')]}
    case=build_season_case(adaptation,network,snapshot)
    assert len(case.common.heat_demand_kW)==133920
    assert len(case.common.hours)==2160
    assert case.common.economics.gas_price_CNY_per_kWh_LHV[2160]==.35
    assert case.common.heat_pump_cop_by_hour['central_hp',1]==pytest.approx(22/7)
    assert case.common.storage.discharge_efficiency==.92
    data.equipment_performance.loc[0,'energy_basis']='HHV'
    with pytest.raises(ValueError,match='LHV'):
        build_season_case(adaptation,network,snapshot)
