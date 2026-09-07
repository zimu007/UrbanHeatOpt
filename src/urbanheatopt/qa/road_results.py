"""Decision exports and independent QA (QA never reads a Pyomo expression)."""
from __future__ import annotations

from dataclasses import asdict
import json
from math import fsum
from pathlib import Path
from time import perf_counter

import networkx as nx
import pandas as pd
import numpy as np
from pyomo.environ import value
from shapely.geometry import LineString, mapping

from urbanheatopt.model.road_core import RoadCase, classify_direction_solution
from urbanheatopt.optimization.mode_diagnostics import classify_realized_mode
from urbanheatopt.spatial.atomic_network import access_options


def _json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def export_solution(case: RoadCase, model, root: str | Path):
    export_started=perf_counter()
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    m, d, net = model, case.common, case.network
    uniform_pumping = bool(value(m.uniform_pumping_flow))
    inferred_directions = {}
    if bool(value(getattr(m, 'direction_relaxation', False))):
        inferred_directions = classify_direction_solution(m)['inferred_directions']
    specs = {x.technology_id: x for x in d.technologies}
    local = next(x for x in d.technologies if x.applicable_scope == 'local')
    timestamps = dict(zip(d.hours, case.timestamps))
    levels = {x.pipe_type_id:x for x in case.pipe_designs}
    compact_midpoint = getattr(m, '_compact_midpoint_matrix', None)
    compact_edge_index = getattr(m, '_compact_edge_index', None)
    compact_fast_export = (
        isinstance(compact_midpoint, np.ndarray)
        and isinstance(compact_edge_index, dict)
        and bool(getattr(m, '_compact_export_views_materialized', False))
    )
    if compact_fast_export and tuple(m.HOURS) != tuple(d.hours):
        raise ValueError('compact export cache hour order does not match the case')
    capacity, dispatch = [], []
    for scope, locs, techs in [('central',tuple(m.S),tuple(m.T)), ('local',d.demand_nodes,(local.technology_id,))]:
        for loc in locs:
            for tid in techs:
                spec = specs[tid]
                capacity.append(dict(location_id=loc,technology_id=tid,scope=scope,
                    capacity_kW_th=value(m.capacity[loc,tid] if scope=='central' else m.local_capacity[loc]),
                    installed=value(m.installed[loc,tid] if scope=='central' else m.local_installed[loc]),
                    capex_CNY_per_kW_th=spec.capex_CNY_per_kW,lifetime_years=spec.lifetime_years,
                    fixed_om_fraction=spec.fixed_maintenance_fraction_per_year))
                for h in d.hours:
                    q=value(m.heat[loc,tid,h] if scope=='central' else m.local_heat[loc,h])
                    performance = d.heat_pump_cop_by_hour.get((tid,h), spec.cop) if spec.energy_carrier=='electricity' else spec.efficiency
                    dispatch.append(dict(location_id=loc,technology_id=tid,scope=scope,hour=h,timestamp=timestamps[h],
                        heat_kW_th=q,energy_input_kW=q/performance,energy_carrier=spec.energy_carrier))
    pd.DataFrame(capacity).to_csv(root/'capacity_decisions.csv',index=False)
    pd.DataFrame(dispatch).to_parquet(root/'dispatch_hourly.parquet',index=False)
    buildings = [dict(building_id=b,connected=value(m.connected[b]),local_installed=value(m.local_installed[b])) for b in d.demand_nodes]
    pd.DataFrame(buildings).to_csv(root/'building_connection.csv',index=False)
    accesses = [dict(**option, selected=value(m.access_selected[option['option_id']])) for option in access_options(net)]
    pd.DataFrame([{k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in row.items()}
                  for row in accesses]).to_csv(root/'access_decisions.csv', index=False)
    _json(root/'access_decisions.geojson', dict(type='FeatureCollection', crs={'type':'name','properties':{'name':net['crs']}},
        features=[dict(type='Feature', properties={k:v for k,v in row.items() if k != 'coordinates'},
                       geometry=mapping(LineString(row['coordinates']))) for row in accesses]))
    pd.DataFrame([dict(**site,built=value(m.station_built[site['site_id']])) for site in net['sites']]).to_csv(root/'station_decisions.csv',index=False)
    storage = []
    for s in m.S:
        spec = d.storage
        energy = value(m.tes_energy[s])
        charge_capacity = value(m.tes_charge_capacity[s])
        discharge_capacity = value(m.tes_discharge_capacity[s])
        peak_charge = max((value(m.charge[s, h]) for h in d.hours), default=0.0)
        peak_discharge = max((value(m.discharge[s, h]) for h in d.hours), default=0.0)
        energy_upper = spec.energy_capacity_max_kWh_th if spec is not None else 0.0
        charge_upper = spec.charge_capacity_max_kW_th if spec is not None else 0.0
        discharge_upper = spec.discharge_capacity_max_kW_th if spec is not None else 0.0
        tolerance = 1e-6
        storage.append(dict(
            site_id=s,
            built=value(m.tes_built[s]),
            energy_capacity_kWh=energy,
            charge_capacity_kW=charge_capacity,
            discharge_capacity_kW=discharge_capacity,
            power_cost_capacity_kW=value(m.tes_power_cost_capacity[s]),
            energy_capacity_upper_kWh_th=energy_upper,
            charge_capacity_upper_kW_th=charge_upper,
            discharge_capacity_upper_kW_th=discharge_upper,
            actual_peak_charge_kW_th=peak_charge,
            actual_peak_discharge_kW_th=peak_discharge,
            energy_upper_bound_binding=bool(
                spec is not None and abs(energy - energy_upper) <= tolerance * max(1.0, energy_upper)
            ),
            charge_upper_bound_binding=bool(
                spec is not None and abs(charge_capacity - charge_upper) <= tolerance * max(1.0, charge_upper)
            ),
            discharge_upper_bound_binding=bool(
                spec is not None and abs(discharge_capacity - discharge_upper) <= tolerance * max(1.0, discharge_upper)
            ),
            capacity_margin_offset_allowed=False,
        ))
    pd.DataFrame(storage).to_csv(root/'storage_decisions.csv',index=False)
    pd.DataFrame([dict(site_id=s,hour=h,timestamp=timestamps[h],charge_kW=value(m.charge[s,h]),
        discharge_kW=value(m.discharge[s,h]),soc_kWh=value(m.soc[s,h]),charging=value(m.charging[s,h]))
        for s in m.S for h in d.hours]).to_parquet(root/'storage_hourly.parquet',index=False)
    if compact_fast_export:
        compact_demand=np.asarray(m._compact_demand_matrix,dtype=float)
        compact_connection=np.asarray(m._compact_connection_vector,dtype=float)
        local_installed=np.asarray(
            [value(m.local_installed[b]) for b in d.demand_nodes],dtype=float)
        building_count,hour_count=compact_demand.shape
        if (building_count,hour_count)!=(len(d.demand_nodes),len(d.hours)):
            raise ValueError('compact building export cache shape does not match the case')
        pd.DataFrame(dict(
            building_id=np.repeat(np.asarray(d.demand_nodes,dtype=object),hour_count),
            hour=np.tile(np.asarray(d.hours),building_count),
            timestamp=np.tile(np.asarray(case.timestamps,dtype=object),building_count),
            demand_kW=compact_demand.reshape(-1),
            network_kW=(compact_connection[:,None]*compact_demand).reshape(-1),
            local_kW=(local_installed[:,None]*compact_demand).reshape(-1),
            unserved_kW=np.zeros(building_count*hour_count,dtype=float),
        )).to_parquet(root/'building_hourly.parquet',index=False)
    else:
        node_rows = [dict(building_id=b,hour=h,timestamp=timestamps[h],demand_kW=d.heat_demand_kW[b,h],
            network_kW=value(m.network_heat[b,h]),local_kW=value(m.local_heat[b,h]),unserved_kW=value(m.unserved_heat_kW[b,h]))
            for b in d.demand_nodes for h in d.hours]
        pd.DataFrame(node_rows).to_parquet(root/'building_hourly.parquet',index=False)
    edge_rows, features = [], []
    edge_hourly = [] if not compact_fast_export else None
    edge_capacity_values=[]
    if compact_fast_export:
        edge_ids=tuple(edge['edge_id'] for edge in net['edges'])
        cache_rows=np.asarray([compact_edge_index[edge] for edge in edge_ids],dtype=int)
        midpoint=np.asarray(compact_midpoint,dtype=float)[cache_rows,:]
        orientation=np.asarray(m._compact_orientation_vector,dtype=float)[cache_rows]
        cached_direction=np.asarray(m._compact_direction_vector,dtype=float)[cache_rows]
        cached_loss=np.asarray(m._compact_edge_loss_vector,dtype=float)[cache_rows]
        pump_rate=np.asarray(m._compact_pump_rate_vector,dtype=float)[cache_rows]
        flat_midpoint=midpoint.reshape(-1)
        flat_orientation=np.repeat(orientation,len(d.hours))
        flat_signed=flat_orientation*flat_midpoint
        flat_loss=np.repeat(cached_loss,len(d.hours))
        flat_port_u=flat_signed+flat_loss/2
        flat_port_v=-flat_signed+flat_loss/2
        flat_port_peak=np.maximum(np.abs(flat_port_u),np.abs(flat_port_v))
        port_peak_matrix=flat_port_peak.reshape(len(edge_ids),len(d.hours))
        peak_indices=np.argmax(port_peak_matrix,axis=1)
        peak_values=port_peak_matrix[np.arange(len(edge_ids)),peak_indices]
    for edge in net['edges']:
        e = edge['edge_id']
        edge_position=len(edge_rows)
        selected = [k for k in m.K if value(m.grade[e,k]) > .5]
        pipe = levels[selected[0]] if len(selected) == 1 else None
        cost = pipe.capex_CNY_per_route_m if pipe else 0.0
        row = {k:v for k,v in edge.items() if k != 'coordinates'}
        row.update(built=value(m.built[e]),selected_grade_count=sum(value(m.grade[e,k]) for k in m.K),
            pipe_type_id=pipe.pipe_type_id if pipe else '',dn_mm=pipe.dn_mm if pipe else None,
            capacity_kW_th=value(m.edge_capacity[e]),unit_cost_CNY_per_pair_route_m=cost,
            initial_investment_CNY=cost*edge['length_m'],lifetime_years=pipe.lifetime_years if pipe else None,
            pair_loss_kW_per_route_m=pipe.pair_loss_kW_per_route_m if pipe else 0.,
            pump_kWh_e_per_kWh_th_m=pipe.pumping_kWh_e_per_kWh_th_m if pipe else 0.,
            parameter_version=case.parameter_version,pipe_design_status='synthetic_capacity_only',
            road_constrained=True,construction_feasibility_verified=False,
            network_policy_version=net.get('metadata', {}).get('network_policy_version', 'legacy_single_access'),
            installation_concept=net.get('metadata', {}).get('installation_concept', 'historical_test'))
        edge_capacity_values.append(row['capacity_kW_th'])
        if compact_fast_export:
            flow_peak=float(peak_values[edge_position])
            peak_hour=d.hours[int(peak_indices[edge_position])]
        else:
            flow_peak, peak_hour = 0.0, d.hours[0]
            loss = value(m.edge_loss[e])
            for h in d.hours:
                if uniform_pumping:
                    pos,neg = value(m.forward[e,h]),value(m.reverse[e,h])
                else:
                    pos,neg = (value(sum(getattr(m,attr)[e,k,h] for k in m.K))
                               for attr in ('forward','reverse'))
                signed = pos-neg
                port_u, port_v = signed+loss/2, -signed+loss/2  # Positive = withdrawal from node into edge.
                port_peak = max(abs(port_u),abs(port_v))
                if port_peak > flow_peak:
                    flow_peak, peak_hour = port_peak, h
                direction = (inferred_directions[e,h]
                    if (e,h) in inferred_directions and (e,h) not in m.DIRECTION_PAIRS
                    else value(m.direction[e,h]))
                edge_hourly.append(dict(edge_id=e,hour=h,timestamp=timestamps[h],signed_flow_kW_th=signed,
                    forward_kW_th=pos,reverse_kW_th=neg,direction=direction,
                    port_u_withdrawal_kW_th=port_u,port_v_withdrawal_kW_th=port_v,
                    loss_kW_th=loss,pump_kW_e=value(m.pump[e,h]),
                    utilization=port_peak/row['capacity_kW_th'] if row['capacity_kW_th']>0 else 0.,
                    capacity_excess_kW=max(0.,port_peak-row['capacity_kW_th'])))
        row.update(max_port_flow_kW=flow_peak,max_port_flow_hour=peak_hour,
                   max_utilization=flow_peak/row['capacity_kW_th'] if row['capacity_kW_th'] else 0.)
        if pipe:
            rate, years=d.economics.discount_rate,pipe.lifetime_years
            factor=1/years if rate==0 else rate*(1+rate)**years/((1+rate)**years-1)
        else:
            factor=0.
        row.update(crf=factor,annualized_investment_CNY=row['initial_investment_CNY']*factor)
        edge_rows.append(row)
        features.append(dict(type='Feature',properties=row,geometry=mapping(LineString(edge['coordinates']))))
    pd.DataFrame(edge_rows).to_csv(root/'network_decisions.csv',index=False)
    if compact_fast_export:
        flat_capacity=np.repeat(np.asarray(edge_capacity_values,dtype=float),len(d.hours))
        flat_utilization=np.divide(
            flat_port_peak,flat_capacity,
            out=np.zeros_like(flat_port_peak),where=flat_capacity>0)
        pd.DataFrame(dict(
            edge_id=np.repeat(np.asarray(edge_ids,dtype=object),len(d.hours)),
            hour=np.tile(np.asarray(d.hours),len(edge_ids)),
            timestamp=np.tile(np.asarray(case.timestamps,dtype=object),len(edge_ids)),
            signed_flow_kW_th=flat_signed,
            forward_kW_th=np.where(flat_orientation>0,flat_midpoint,0.),
            reverse_kW_th=np.where(flat_orientation<0,flat_midpoint,0.),
            direction=np.repeat(cached_direction,len(d.hours)),
            port_u_withdrawal_kW_th=flat_port_u,
            port_v_withdrawal_kW_th=flat_port_v,
            loss_kW_th=flat_loss,
            pump_kW_e=np.repeat(pump_rate,len(d.hours))*flat_midpoint,
            utilization=flat_utilization,
            capacity_excess_kW=np.maximum(0.,flat_port_peak-flat_capacity),
        )).to_parquet(root/'network_hourly.parquet',index=False)
    else:
        pd.DataFrame(edge_hourly).to_parquet(root/'network_hourly.parquet',index=False)
    _json(root/'network_decisions.geojson',dict(type='FeatureCollection',crs={'type':'name','properties':{'name':net['crs']}},features=features))
    names = ['device_investment','fixed_om','pipe_investment','station_investment','connection_investment',
             'storage_investment','electricity_cost','gas_cost','variable_om',
             'annual_monthly_demand_charge_CNY_per_year']
    reported_costs = {name: value(getattr(m,name)) for name in names}
    pd.DataFrame([dict(component=name,annual_CNY=reported_costs[name]) for name in names]).to_csv(root/'cost_breakdown.csv',index=False)
    carbon = value(m.annual_operating_physical_carbon_kgCO2e_per_year)
    hns_penalty = value(m.annual_hns_penalty_CNY_per_year)
    connected_values = {row['building_id']: row['connected'] for row in buildings}
    connected_count = sum(float(selected) > .5 for selected in connected_values.values())
    selected_sites = [site['site_id'] for site in net['sites'] if value(m.station_built[site['site_id']]) > .5]
    summary = dict(mode=d.mode,requested_mode=d.mode,
        realized_mode=classify_realized_mode(connected_values),
        connected_building_count=connected_count,
        local_building_count=len(d.demand_nodes)-connected_count,
        selected_site=selected_sites[0] if len(selected_sites)==1 else None,
        model_version='road_joint_v2',building_count=len(d.demand_nodes),hour_count=len(d.hours),
        real_cost_CNY=fsum(reported_costs.values()),hns_penalty_CNY=hns_penalty,
        carbon_kgCO2e=carbon,
        carbon_tCO2e=carbon/1000,
        policy_carbon_cost_CNY=carbon/1000*case.common.economics.policy_carbon_price_CNY_per_tCO2e,formal_engineering_result=False,
        execution_purpose='source_load_matching_and_optimization_validation',
        network_policy_version=net.get('metadata', {}).get('network_policy_version', 'legacy_single_access'))
    _json(root/'solution_summary.json',summary)
    export_before_qa_seconds=perf_counter()-export_started
    qa = audit_export(case,root)
    qa['pipeline_timing_seconds']={
        'export_before_independent_qa':export_before_qa_seconds,
        'independent_qa':qa.get('timing_seconds',{}).get('total'),
        'export_and_qa_total':perf_counter()-export_started,
    }
    _json(root/'qa_summary.json',qa)
    if not qa['passed']:
        raise ValueError(f'V2独立QA未通过: {qa}')
    return qa


def audit_export(case: RoadCase, root: str | Path):
    """Recompute solely from exported decisions, dispatch and canonical inputs."""
    audit_started=perf_counter()
    phase_started=audit_started
    timing_seconds={}

    def finish_phase(name):
        nonlocal phase_started
        now=perf_counter()
        timing_seconds[name]=now-phase_started
        phase_started=now

    root=Path(root)
    d, net, econ=case.common,case.network,case.common.economics
    caps=pd.read_csv(root/'capacity_decisions.csv')
    dispatch=pd.read_parquet(root/'dispatch_hourly.parquet')
    conn=pd.read_csv(root/'building_connection.csv').set_index('building_id')
    sites=pd.read_csv(root/'station_decisions.csv').set_index('site_id')
    storage=pd.read_csv(root/'storage_decisions.csv').set_index('site_id')
    soc=pd.read_parquet(root/'storage_hourly.parquet').set_index(['site_id','hour'])
    demand=pd.read_parquet(root/'building_hourly.parquet').set_index(['building_id','hour'])
    pipes=pd.read_csv(root/'network_decisions.csv').set_index('edge_id')
    flows=pd.read_parquet(root/'network_hourly.parquet')
    summary=json.loads((root/'solution_summary.json').read_text(encoding='utf-8'))
    access_table=pd.read_csv(root/'access_decisions.csv').set_index('option_id')
    reported_cost_table=pd.read_csv(root/'cost_breakdown.csv')
    finish_phase('read_exports')
    specs={t.technology_id:t for t in d.technologies}
    levels={x.pipe_type_id:x for x in case.pipe_designs}
    cap_index=caps.set_index(['location_id','technology_id'])
    hours=tuple(d.hours)
    hour_index=pd.Index(hours)
    errors={key:0. for key in ('heat_balance_kW','capacity_kW','energy_conversion_kW','pipe_kW','direction_kW',
         'loss_kW','pump_kW','soc_kWh','tes_power_kW','margin_kW','mode_violation','cost_CNY','carbon_kgCO2e')}
    violations=[]
    # DN is deliberately nullable in capacity-only tests; an unbuilt edge also
    # has no selected lifetime. Do NOT exempt the entire static pipe table.
    finite_pipes=pipes[['length_m','built','selected_grade_count','capacity_kW_th',
        'unit_cost_CNY_per_pair_route_m','initial_investment_CNY','pair_loss_kW_per_route_m',
        'pump_kWh_e_per_kWh_th_m','max_port_flow_kW','max_port_flow_hour','max_utilization',
        'crf','annualized_investment_CNY']].apply(pd.to_numeric,errors='raise')
    for label, frame in [('access',access_table),('capacity',caps),('dispatch',dispatch),('connections',conn),('stations',sites),('storage',storage),('soc',soc),('demand',demand),('network_hourly',flows),('network_static',finite_pipes),('cost',reported_cost_table)]:
        if not np.isfinite(frame.select_dtypes(include='number').to_numpy()).all():
            raise ValueError(f'导出{label}含NaN/Inf，不能通过QA')
    for field in ('real_cost_CNY','hns_penalty_CNY','carbon_kgCO2e','carbon_tCO2e','policy_carbon_cost_CNY'):
        if not isinstance(summary[field],(int,float)) or not np.isfinite(summary[field]):
            raise ValueError(f'导出summary.{field}含NaN/Inf或非数值')
    def record(key,val):
        if not np.isfinite(val):
            raise ValueError(f'独立复算残差{key}为NaN/Inf')
        errors[key]=max(errors[key],float(val))
    def annual_factor(years):
        r=econ.discount_rate
        return 1/years if r==0 else r*(1+r)**years/((1+r)**years-1)
    def coverage_violation(label):
        violation=label+'_coverage'
        if violation not in violations:
            violations.append(violation)

    def expected_rows(frame,keys,expected,label):
        # This helper is retained for the small, non-Cartesian access table.
        # Large dense tables use integer product codes below instead of
        # materializing millions of Python tuples and two large sets.
        actual=set(map(tuple,frame.reset_index()[keys].itertuples(index=False,name=None)))
        expected=set(expected)
        if len(frame)!=len(expected) or actual!=expected:
            coverage_violation(label)

    def expected_product_rows(frame,keys,domains,label):
        table=frame.reset_index()[keys]
        expected_count=int(np.prod([len(domain) for domain in domains],dtype=np.int64))
        if len(table)!=expected_count:
            coverage_violation(label)
        codes=np.zeros(len(table),dtype=np.int64)
        valid=True
        for key,domain in zip(keys,domains):
            positions=pd.Index(domain).get_indexer(table[key])
            if (positions<0).any():
                valid=False
                break
            codes=codes*len(domain)+positions
        if not valid or len(table)!=expected_count or np.unique(codes).size!=expected_count:
            coverage_violation(label)

    expected_product_rows(
        demand,['building_id','hour'],(tuple(d.demand_nodes),hours),'demand')
    expected_options=access_options(net)
    expected_rows(access_table,['option_id'],[(o['option_id'],) for o in expected_options],'access')
    edge_users={e['edge_id']:[] for e in net['edges']}
    selected_by_building={b:[] for b in d.demand_nodes}
    for option in expected_options:
        row=access_table.loc[option['option_id']]
        record('mode_violation',max(abs(row.selected-round(row.selected)), -row.selected, row.selected-1))
        if row.building_id!=option['building_id'] or row.attachment_node_id!=option['attachment_node_id'] or json.loads(row.edge_ids)!=option['edge_ids']:
            violations.append('access_path_mismatch')
        record('cost_CNY',abs(row.length_m-option['length_m']))
        selected_by_building[option['building_id']].append(row.selected)
        for eid in option['edge_ids']:
            edge_users[eid].append(row.selected)
            record('mode_violation',max(0.,row.selected-pipes.loc[eid,'built']))
    for b, selected in selected_by_building.items():
        record('mode_violation',abs(fsum(selected)-conn.loc[b,'connected']))
        leaf_degree=fsum(pipes.loc[e['edge_id'],'built'] for e in net['edges'] if b in (e['node_u'],e['node_v']))
        record('mode_violation',abs(leaf_degree-conn.loc[b,'connected']))
    expected_product_rows(
        soc,['site_id','hour'],(tuple(sites.index),hours),'storage')
    expected_product_rows(
        flows,['edge_id','hour'],
        (tuple(e['edge_id'] for e in net['edges']),hours),'network')
    central_technology_ids=tuple(
        technology.technology_id for technology in d.technologies
        if technology.applicable_scope=='central')
    local_technology_ids=tuple(
        technology.technology_id for technology in d.technologies
        if technology.applicable_scope=='local')
    central_dispatch_rows=dispatch.loc[dispatch['scope'].eq('central')]
    local_dispatch_rows=dispatch.loc[dispatch['scope'].eq('local')]
    if len(central_dispatch_rows)+len(local_dispatch_rows)!=len(dispatch):
        coverage_violation('dispatch')
    expected_product_rows(
        central_dispatch_rows,['location_id','technology_id','hour'],
        (tuple(sites.index),central_technology_ids,hours),'dispatch')
    expected_product_rows(
        local_dispatch_rows,['location_id','technology_id','hour'],
        (tuple(d.demand_nodes),local_technology_ids,hours),'dispatch')
    for frame in (dispatch, flows):
        for row in frame[['hour','timestamp']].drop_duplicates().itertuples(index=False):
            if row.timestamp != case.timestamps[row.hour-1]:
                violations.append('timestamp_mismatch')
    finish_phase('structure_and_coverage')
    for row in caps.itertuples(index=False):
        spec=specs[row.technology_id]
        record('capacity_kW',max(0.,row.capacity_kW_th-spec.capacity_max_kW*row.installed,
              spec.capacity_min_kW*row.installed-row.capacity_kW_th,-row.capacity_kW_th))
        record('mode_violation',abs(row.installed-round(row.installed)))
        if row.scope=='central':
            record('mode_violation',max(0.,row.installed-sites.loc[row.location_id,'built']))
        else:
            record('mode_violation',abs(row.installed-conn.loc[row.location_id,'local_installed']))
    node_ids=tuple(n['node_id'] for n in net['nodes'])
    node_index=pd.Index(node_ids)
    node_balance=np.zeros((len(node_ids),len(hours)),dtype=float)
    dispatch_hour_index=hour_index.get_indexer(dispatch['hour'])
    if (dispatch_hour_index<0).any():
        raise KeyError('dispatch references an unknown canonical hour')
    dispatch_technology=dispatch['technology_id'].to_numpy()
    dispatch_specs=np.asarray([specs[technology] for technology in dispatch_technology],dtype=object)
    dispatch_heat=dispatch['heat_kW_th'].to_numpy(dtype=float)
    dispatch_input=dispatch['energy_input_kW'].to_numpy(dtype=float)
    dispatch_performance=np.asarray([
        d.heat_pump_cop_by_hour.get((technology,hour),spec.cop)
        if spec.energy_carrier=='electricity' else spec.efficiency
        for technology,hour,spec in zip(dispatch_technology,dispatch['hour'],dispatch_specs)
    ],dtype=float)
    dispatch_ratio=np.asarray([
        d.heat_pump_capacity_ratio_by_hour.get((technology,hour),1.)
        for technology,hour in zip(dispatch_technology,dispatch['hour'])
    ],dtype=float)
    dispatch_capacity_index=pd.MultiIndex.from_arrays(
        (dispatch['location_id'],dispatch['technology_id']),
        names=cap_index.index.names)
    dispatch_capacity=cap_index['capacity_kW_th'].reindex(dispatch_capacity_index).to_numpy(dtype=float)
    if not np.isfinite(dispatch_capacity).all():
        raise KeyError('dispatch references a missing capacity decision')
    record('energy_conversion_kW',np.max(np.abs(
        dispatch_input-dispatch_heat/dispatch_performance)))
    record('capacity_kW',np.max(np.maximum.reduce((
        np.zeros(len(dispatch),dtype=float),
        dispatch_heat-dispatch_capacity*dispatch_ratio,
        -dispatch_heat))))
    electricity_mask=np.fromiter(
        (spec.energy_carrier=='electricity' for spec in dispatch_specs),
        dtype=bool,count=len(dispatch))
    electricity_dispatch_by_hour=np.bincount(
        dispatch_hour_index[electricity_mask],
        weights=dispatch_input[electricity_mask],minlength=len(hours))
    gas_by_hour=np.bincount(
        dispatch_hour_index[~electricity_mask],
        weights=dispatch_input[~electricity_mask],minlength=len(hours))
    variable_om=np.fromiter(
        (spec.variable_om_CNY_per_kWh_th for spec in dispatch_specs),
        dtype=float,count=len(dispatch))
    dispatch_time_weight=np.asarray([
        econ.time_weight_h_per_year[hour] for hour in dispatch['hour']
    ],dtype=float)
    variable_cost=dispatch_heat*variable_om*dispatch_time_weight
    local_dispatch_mask=dispatch['scope'].ne('central').to_numpy()
    if local_dispatch_mask.any():
        local_dispatch_index=pd.MultiIndex.from_arrays((
            dispatch.loc[local_dispatch_mask,'location_id'],
            dispatch.loc[local_dispatch_mask,'hour'],
        ),names=demand.index.names)
        exported_local_heat=demand['local_kW'].reindex(local_dispatch_index).to_numpy(dtype=float)
        if not np.isfinite(exported_local_heat).all():
            raise KeyError('local dispatch references a missing building-hour export')
        record('heat_balance_kW',np.max(np.abs(
            dispatch_heat[local_dispatch_mask]-exported_local_heat)))
    finish_phase('capacity_and_dispatch')
    graph=nx.Graph()
    loss_total=0.
    for edge in net['edges']:
        row=pipes.loc[edge['edge_id']]
        if abs(row.selected_grade_count-row.built)>1e-6:
            violations.append('pipe_grade_choice')
        if row.built>.5:
            graph.add_edge(edge['node_u'],edge['node_v'])
            loss_total+=edge['length_m']*levels[row.pipe_type_id].pair_loss_kW_per_route_m
            record('pipe_kW',abs(row.capacity_kW_th-levels[row.pipe_type_id].capacity_kW_th))
            selected=levels[row.pipe_type_id]
            record('cost_CNY',abs(row.unit_cost_CNY_per_pair_route_m-selected.capex_CNY_per_route_m))
            record('cost_CNY',abs(row.initial_investment_CNY-edge['length_m']*selected.capex_CNY_per_route_m))
            record('cost_CNY',abs(row.lifetime_years-selected.lifetime_years))
            record('cost_CNY',abs(row.crf-annual_factor(selected.lifetime_years)))
            record('cost_CNY',abs(row.annualized_investment_CNY-edge['length_m']*selected.capex_CNY_per_route_m*annual_factor(selected.lifetime_years)))
        elif abs(row.capacity_kW_th)>1e-6:
            violations.append('unbuilt_capacity')
        else:
            record('cost_CNY',max(abs(row.initial_investment_CNY),abs(row.annualized_investment_CNY)))
        record('mode_violation',abs(row.built-round(row.built)))
        if edge['edge_type']!='road':
            record('mode_violation',max(0.,row.built-fsum(edge_users[edge['edge_id']])))
    # The hourly network table is the largest QA input (578 edges x 2160
    # hours in the full case).  Resolve its canonical/static data once per
    # edge and validate all hourly rows in NumPy.  This remains independent
    # from the optimization model: only exported tables and canonical case
    # inputs are used here.
    edge_ids=tuple(e['edge_id'] for e in net['edges'])
    edge_index=pd.Index(edge_ids)
    ordered_pipes=pipes.reindex(edge_ids)
    edge_lengths=np.asarray([e['length_m'] for e in net['edges']],dtype=float)
    edge_u_index=node_index.get_indexer([e['node_u'] for e in net['edges']])
    edge_v_index=node_index.get_indexer([e['node_v'] for e in net['edges']])
    if (edge_u_index<0).any() or (edge_v_index<0).any():
        raise KeyError('network edge references an unknown canonical node')
    built_by_edge=ordered_pipes['built'].to_numpy(dtype=float)
    capacity_by_edge=ordered_pipes['capacity_kW_th'].to_numpy(dtype=float)
    loss_by_edge=np.zeros(len(edge_ids),dtype=float)
    pump_factor_by_edge=np.zeros(len(edge_ids),dtype=float)
    for i,(built,pipe_type_id) in enumerate(zip(built_by_edge,ordered_pipes['pipe_type_id'])):
        if built>.5:
            level=levels[pipe_type_id]
            loss_by_edge[i]=edge_lengths[i]*level.pair_loss_kW_per_route_m
            pump_factor_by_edge[i]=edge_lengths[i]*level.pumping_kWh_e_per_kWh_th_m

    flow_edge_index=edge_index.get_indexer(flows['edge_id'])
    flow_hour_index=hour_index.get_indexer(flows['hour'])
    if (flow_edge_index<0).any() or (flow_hour_index<0).any():
        raise KeyError('network_hourly references an unknown edge or hour')
    signed=flows['signed_flow_kW_th'].to_numpy(dtype=float)
    forward=flows['forward_kW_th'].to_numpy(dtype=float)
    reverse=flows['reverse_kW_th'].to_numpy(dtype=float)
    direction=flows['direction'].to_numpy(dtype=float)
    reported_loss=flows['loss_kW_th'].to_numpy(dtype=float)
    reported_pump=flows['pump_kW_e'].to_numpy(dtype=float)
    expected_loss=loss_by_edge[flow_edge_index]
    expected_pump=pump_factor_by_edge[flow_edge_index]*np.abs(signed)
    zero=np.zeros(len(flows),dtype=float)

    record('loss_kW',np.max(np.abs(expected_loss-reported_loss)))
    record('pump_kW',np.max(np.abs(expected_pump-reported_pump)))
    record('direction_kW',np.max(np.maximum.reduce((zero,-forward,-reverse))))
    record('direction_kW',np.max(np.minimum(np.abs(forward),np.abs(reverse))))
    record('direction_kW',np.max(np.maximum.reduce((zero,np.abs(direction-np.rint(direction)),-direction,direction-1))))
    record('direction_kW',np.max(np.maximum.reduce((zero,forward*(1-direction),reverse*direction))))
    record('direction_kW',np.max(np.abs(signed-forward+reverse)))
    record('direction_kW',np.max(np.maximum(zero,expected_loss/2-np.maximum(forward,reverse))))

    port_u=signed+expected_loss/2
    port_v=-signed+expected_loss/2
    exported_port_u=flows['port_u_withdrawal_kW_th'].to_numpy(dtype=float)
    exported_port_v=flows['port_v_withdrawal_kW_th'].to_numpy(dtype=float)
    record('heat_balance_kW',np.max(np.maximum(np.abs(port_u-exported_port_u),np.abs(port_v-exported_port_v))))
    row_capacity=capacity_by_edge[flow_edge_index]
    record('pipe_kW',np.max(np.maximum.reduce((zero,np.abs(port_u)-row_capacity,np.abs(port_v)-row_capacity))))

    flat_balance=node_balance.reshape(-1)
    np.add.at(flat_balance,edge_u_index[flow_edge_index]*len(hours)+flow_hour_index,-port_u)
    np.add.at(flat_balance,edge_v_index[flow_edge_index]*len(hours)+flow_hour_index,-port_v)
    pumping_by_hour=np.bincount(flow_hour_index,weights=reported_pump,minlength=len(hours))
    finish_phase('network_hourly')
    local=next(t for t in d.technologies if t.applicable_scope=='local')

    building_ids=tuple(d.demand_nodes)
    building_index=pd.Index(building_ids)
    connection_by_building=conn.reindex(building_ids)['connected'].to_numpy(dtype=float)
    for b in d.demand_nodes:
        record('mode_violation',abs(conn.loc[b,'connected']+conn.loc[b,'local_installed']-1))
        if d.mode!='hybrid':
            record('mode_violation',abs(conn.loc[b,'connected']-int(d.mode=='central')))

    # Reindex once to the canonical building-major/hour-minor order.  All
    # demand-side checks and node withdrawals then operate on contiguous
    # arrays instead of 62 x 2160 MultiIndex scalar lookups.
    building_hour_index=pd.MultiIndex.from_product(
        (building_ids,hours),names=('building_id','hour'))
    ordered_demand=demand.reindex(building_hour_index)
    canonical_demand=(pd.Series(d.heat_demand_kW,dtype=float)
        .unstack().reindex(index=building_ids,columns=hours).to_numpy(dtype=float))
    demand_network=ordered_demand['network_kW'].to_numpy(dtype=float).reshape(len(building_ids),len(hours))
    demand_local=ordered_demand['local_kW'].to_numpy(dtype=float).reshape(len(building_ids),len(hours))
    demand_unserved=ordered_demand['unserved_kW'].to_numpy(dtype=float).reshape(len(building_ids),len(hours))
    zero_demand=np.zeros_like(canonical_demand)
    record('heat_balance_kW',np.max(np.abs(demand_network+demand_local+demand_unserved-canonical_demand)))
    record('capacity_kW',np.max(np.maximum.reduce((
        zero_demand,
        demand_network-canonical_demand*connection_by_building[:,None],
        -demand_network,-demand_local,-demand_unserved))))

    local_capacity=np.asarray([
        cap_index.loc[(b,local.technology_id),'capacity_kW_th'] for b in building_ids
    ],dtype=float)
    local_capacity_ratio=np.asarray([
        d.heat_pump_capacity_ratio_by_hour.get((local.technology_id,h),1.) for h in hours
    ],dtype=float)
    record('margin_kW',np.max(np.maximum(zero_demand,
        (1+d.peak_capacity_margin_fraction)*canonical_demand*(1-connection_by_building[:,None])
        -local_capacity[:,None]*local_capacity_ratio[None,:])))
    building_node_index=node_index.get_indexer(building_ids)
    if (building_node_index<0).any():
        raise KeyError('demand node is absent from the canonical network nodes')
    np.add.at(flat_balance,
        (building_node_index[:,None]*len(hours)+np.arange(len(hours))[None,:]).reshape(-1),
        -demand_network.reshape(-1))
    finish_phase('building_hourly')

    site_ids=tuple(sites.index)
    site_hour_index=pd.MultiIndex.from_product((site_ids,hours),names=('site_id','hour'))
    ordered_soc=soc.reindex(site_hour_index)
    charge=ordered_soc['charge_kW'].to_numpy(dtype=float).reshape(len(site_ids),len(hours))
    discharge=ordered_soc['discharge_kW'].to_numpy(dtype=float).reshape(len(site_ids),len(hours))
    soc_energy=ordered_soc['soc_kWh'].to_numpy(dtype=float).reshape(len(site_ids),len(hours))
    central_dispatch=dispatch.loc[dispatch['scope'].eq('central'),
        ['location_id','hour','heat_kW_th']]
    central_heat=(central_dispatch.groupby(['location_id','hour'],sort=False)['heat_kW_th'].sum()
        .reindex(site_hour_index,fill_value=0.).to_numpy(dtype=float)
        .reshape(len(site_ids),len(hours)))
    ordered_storage=storage.reindex(site_ids)
    for s in sites.index:
        record('mode_violation',abs(sites.loc[s,'built']-round(sites.loc[s,'built'])))
        record('mode_violation',max(0.,storage.loc[s,'built']-sites.loc[s,'built']))
        if d.storage:
            st=d.storage
            record('soc_kWh',max(0.,storage.loc[s,'energy_capacity_kWh']-st.energy_capacity_max_kWh_th*storage.loc[s,'built']))
            record('tes_power_kW',max(0.,storage.loc[s,'charge_capacity_kW']-st.charge_capacity_max_kW_th*storage.loc[s,'built'],storage.loc[s,'discharge_capacity_kW']-st.discharge_capacity_max_kW_th*storage.loc[s,'built'],storage.loc[s,'charge_capacity_kW']-storage.loc[s,'power_cost_capacity_kW'],storage.loc[s,'discharge_capacity_kW']-storage.loc[s,'power_cost_capacity_kW']))

    charge_capacity=ordered_storage['charge_capacity_kW'].to_numpy(dtype=float)
    discharge_capacity=ordered_storage['discharge_capacity_kW'].to_numpy(dtype=float)
    record('tes_power_kW',np.max(np.maximum.reduce((
        np.zeros_like(charge),
        charge-charge_capacity[:,None],
        discharge-discharge_capacity[:,None],
        np.minimum(charge,discharge)))))
    if d.storage:
        st=d.storage
        energy_capacity=ordered_storage['energy_capacity_kWh'].to_numpy(dtype=float)
        previous_soc=np.roll(soc_energy,1,axis=1)
        record('soc_kWh',np.max(np.abs(soc_energy
            -previous_soc*(1-st.standing_loss_fraction_per_hour)
            -charge*st.charge_efficiency+discharge/st.discharge_efficiency)))
        record('soc_kWh',np.max(np.maximum.reduce((
            np.zeros_like(soc_energy),soc_energy-energy_capacity[:,None],-soc_energy))))

    attachment_node_index=node_index.get_indexer(
        sites.reindex(site_ids)['attachment_node_id'])
    if (attachment_node_index<0).any():
        raise KeyError('station references an unknown canonical attachment node')
    np.add.at(flat_balance,
        (attachment_node_index[:,None]*len(hours)+np.arange(len(hours))[None,:]).reshape(-1),
        (central_heat+discharge-charge).reshape(-1))

    available=np.zeros(len(hours),dtype=float)
    for row in caps.itertuples(index=False):
        if row.scope=='central':
            available+=row.capacity_kW_th*np.asarray([
                d.heat_pump_capacity_ratio_by_hour.get((row.technology_id,h),1.) for h in hours
            ],dtype=float)
    required=(1+d.peak_capacity_margin_fraction)*(
        np.sum(canonical_demand*connection_by_building[:,None],axis=0)+loss_total)
    record('margin_kW',np.max(np.maximum(0.,required-available)))
    record('heat_balance_kW',np.max(np.abs(node_balance)))
    built_sites=[s for s in sites.index if sites.loc[s,'built']>.5]
    if len(built_sites)>1:
        violations.append('multiple_built_stations')
    if d.mode=='central' and len(built_sites)!=1:
        violations.append('central_no_station')
    if d.mode=='distributed' and (built_sites or pipes.built.sum()>1e-6):
        violations.append('distributed_built_station_or_network')
    for b in d.demand_nodes:
        if conn.loc[b,'connected']>.5 and not any(nx.has_path(graph,sites.loc[s,'attachment_node_id'],b) for s in built_sites if sites.loc[s,'attachment_node_id'] in graph and b in graph):
            violations.append('disconnected_'+b)
    finish_phase('storage_node_and_connectivity')
    costs={}
    costs['device_investment']=fsum(row.capacity_kW_th*specs[row.technology_id].capex_CNY_per_kW*annual_factor(specs[row.technology_id].lifetime_years) for row in caps.itertuples(index=False))
    costs['fixed_om']=fsum(row.capacity_kW_th*specs[row.technology_id].capex_CNY_per_kW*specs[row.technology_id].fixed_maintenance_fraction_per_year for row in caps.itertuples(index=False))
    costs['pipe_investment']=fsum(edge['length_m']*levels[pipes.loc[edge['edge_id'],'pipe_type_id']].capex_CNY_per_route_m*annual_factor(levels[pipes.loc[edge['edge_id'],'pipe_type_id']].lifetime_years) for edge in net['edges'] if pipes.loc[edge['edge_id'],'built']>.5)
    costs['station_investment']=fsum(sites.loc[s,'built']*econ.station_fixed_capex_CNY*annual_factor(econ.station_lifetime_years) for s in sites.index)
    costs['connection_investment']=fsum(conn.loc[b,'connected']*econ.connection_capex_CNY[b]*annual_factor(econ.connection_lifetime_years[b]) for b in d.demand_nodes)
    costs['storage_investment']=0.
    if d.storage:
        st=d.storage
        costs['storage_investment']=fsum((row.energy_capacity_kWh*st.capex_CNY_per_kWh_th+row.power_cost_capacity_kW*st.power_capex_CNY_per_kW_th+row.built*st.fixed_capex_CNY)*annual_factor(st.lifetime_years) for row in storage.itertuples())
    electricity_by_hour=electricity_dispatch_by_hour+pumping_by_hour
    time_weight=np.asarray([econ.time_weight_h_per_year[h] for h in hours],dtype=float)
    electricity_price=np.asarray([econ.electricity_price_CNY_per_kWh_e[h] for h in hours],dtype=float)
    gas_price=np.asarray([econ.gas_price_CNY_per_kWh_LHV[h] for h in hours],dtype=float)
    costs['electricity_cost']=fsum(electricity_by_hour*electricity_price*time_weight)
    costs['gas_cost']=fsum(gas_by_hour*gas_price*time_weight)
    costs['variable_om']=fsum(variable_cost)
    month_peaks={}
    billing=(case.monthly_demand_charge.billing_month_by_hour
             if case.monthly_demand_charge is not None else {h:'not_applied' for h in hours})
    for index,h in enumerate(hours):
        month_peaks[billing[h]]=max(month_peaks.get(billing[h],0.),float(electricity_by_hour[index]))
    demand_rate=(case.monthly_demand_charge.rate_CNY_per_kW_month
                 if case.monthly_demand_charge is not None else 0.)
    costs['annual_monthly_demand_charge_CNY_per_year']=fsum(month_peaks.values())*demand_rate
    reported=reported_cost_table.set_index('component')['annual_CNY']
    record('cost_CNY',max(abs(costs[k]-reported[k]) for k in costs))
    record('cost_CNY',abs(fsum(costs.values())-summary['real_cost_CNY']))
    electricity_carbon=np.asarray([econ.electricity_carbon_kgCO2e_per_kWh_e[h] for h in hours],dtype=float)
    gas_carbon=np.asarray([econ.gas_carbon_kgCO2e_per_kWh_LHV[h] for h in hours],dtype=float)
    carbon_e=fsum(electricity_by_hour*electricity_carbon*time_weight)
    carbon_g=fsum(gas_by_hour*gas_carbon*time_weight)
    record('carbon_kgCO2e',abs(carbon_e+carbon_g-summary['carbon_kgCO2e']))
    unserved=fsum((demand_unserved*time_weight[None,:]).reshape(-1))
    record('cost_CNY',abs(unserved*econ.hns_penalty_CNY_per_kWh-summary['hns_penalty_CNY']))
    heat=fsum(d.heat_demand_kW.values())
    if unserved>max(1e-6,heat*1e-6):
        violations.append('unserved_heat')
    finish_phase('economics_and_carbon')
    pd.DataFrame(dict(
        node_id=np.repeat(np.asarray(node_ids,dtype=object),len(hours)),
        hour=np.tile(np.asarray(hours),len(node_ids)),
        residual_kW=node_balance.reshape(-1),
    )).to_parquet(root/'node_balance_check.parquet',index=False)
    _json(root/'independent_recalculation.json',dict(cost=costs,carbon_electricity_kgCO2e=carbon_e,carbon_gas_kgCO2e=carbon_g,
         carbon_tCO2e=(carbon_e+carbon_g)/1000,unserved_kWh=unserved))
    finish_phase('write_audit_artifacts')
    timing_seconds['total']=perf_counter()-audit_started
    return dict(passed=not violations and all(x<=1e-6 for x in errors.values()),max_errors=errors,
                violations=violations,unserved_kWh=unserved,relative_unserved=unserved/heat if heat else 0,
                qa_basis='exported_decisions_and_canonical_input_not_model_expressions',
                timing_seconds=timing_seconds)
