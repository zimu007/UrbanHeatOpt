"""Decision exports and independent QA (QA never reads a Pyomo expression)."""
from __future__ import annotations

from dataclasses import asdict
import json
from math import fsum
from pathlib import Path

import networkx as nx
import pandas as pd
import numpy as np
from pyomo.environ import value
from shapely.geometry import LineString, mapping

from competition.road_joint_v2.core import RoadCase


def _json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def export_solution(case: RoadCase, model, root: str | Path):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    m, d, net = model, case.common, case.network
    specs = {x.technology_id: x for x in d.technologies}
    local = next(x for x in d.technologies if x.applicable_scope == 'local')
    timestamps = dict(zip(d.hours, case.timestamps))
    levels = {x.pipe_type_id:x for x in case.pipe_designs}
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
    pd.DataFrame([dict(**site,built=value(m.station_built[site['site_id']])) for site in net['sites']]).to_csv(root/'station_decisions.csv',index=False)
    storage = [dict(site_id=s,built=value(m.tes_built[s]),energy_capacity_kWh=value(m.tes_energy[s]),
        charge_capacity_kW=value(m.tes_charge_capacity[s]),discharge_capacity_kW=value(m.tes_discharge_capacity[s]),
        power_cost_capacity_kW=value(m.tes_power_cost_capacity[s])) for s in m.S]
    pd.DataFrame(storage).to_csv(root/'storage_decisions.csv',index=False)
    pd.DataFrame([dict(site_id=s,hour=h,timestamp=timestamps[h],charge_kW=value(m.charge[s,h]),
        discharge_kW=value(m.discharge[s,h]),soc_kWh=value(m.soc[s,h]),charging=value(m.charging[s,h]))
        for s in m.S for h in d.hours]).to_parquet(root/'storage_hourly.parquet',index=False)
    node_rows = [dict(building_id=b,hour=h,timestamp=timestamps[h],demand_kW=d.heat_demand_kW[b,h],
        network_kW=value(m.network_heat[b,h]),local_kW=value(m.local_heat[b,h]),unserved_kW=value(m.unserved_heat_kW[b,h]))
        for b in d.demand_nodes for h in d.hours]
    pd.DataFrame(node_rows).to_parquet(root/'building_hourly.parquet',index=False)
    edge_rows, edge_hourly, features = [], [], []
    for edge in net['edges']:
        e = edge['edge_id']
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
            road_constrained=True,construction_feasibility_verified=False)
        flow_peak, peak_hour = 0.0, d.hours[0]
        for h in d.hours:
            pos,neg = (value(sum(getattr(m,attr)[e,k,h] for k in m.K)) for attr in ('forward','reverse'))
            signed, loss = pos-neg, value(m.edge_loss[e])
            port_u, port_v = signed+loss/2, -signed+loss/2  # Positive = withdrawal from node into edge.
            port_peak = max(abs(port_u),abs(port_v))
            if port_peak > flow_peak:
                flow_peak, peak_hour = port_peak, h
            edge_hourly.append(dict(edge_id=e,hour=h,timestamp=timestamps[h],signed_flow_kW_th=signed,
                forward_kW_th=pos,reverse_kW_th=neg,direction=value(m.direction[e,h]),
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
    pd.DataFrame(edge_hourly).to_parquet(root/'network_hourly.parquet',index=False)
    _json(root/'network_decisions.geojson',dict(type='FeatureCollection',crs={'type':'name','properties':{'name':net['crs']}},features=features))
    names = ['device_investment','fixed_om','pipe_investment','station_investment','connection_investment',
             'storage_investment','electricity_cost','gas_cost','variable_om']
    pd.DataFrame([dict(component=name,annual_CNY=value(getattr(m,name))) for name in names]).to_csv(root/'cost_breakdown.csv',index=False)
    summary = dict(mode=d.mode,model_version='road_joint_v2',building_count=len(d.demand_nodes),hour_count=len(d.hours),
        real_cost_CNY=value(m.annual_real_cost_CNY_per_year),hns_penalty_CNY=value(m.annual_hns_penalty_CNY_per_year),
        carbon_kgCO2e=value(m.annual_operating_physical_carbon_kgCO2e_per_year),
        carbon_tCO2e=value(m.annual_operating_physical_carbon_kgCO2e_per_year)/1000,
        policy_carbon_cost_CNY=value(m.policy_carbon_cost),formal_engineering_result=False)
    _json(root/'solution_summary.json',summary)
    qa = audit_export(case,root)
    _json(root/'qa_summary.json',qa)
    if not qa['passed']:
        raise ValueError(f'V2独立QA未通过: {qa}')
    return qa


def audit_export(case: RoadCase, root: str | Path):
    """Recompute solely from exported decisions, dispatch and canonical inputs."""
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
    specs={t.technology_id:t for t in d.technologies}
    levels={x.pipe_type_id:x for x in case.pipe_designs}
    cap_index=caps.set_index(['location_id','technology_id'])
    errors={key:0. for key in ('heat_balance_kW','capacity_kW','energy_conversion_kW','pipe_kW','direction_kW',
         'loss_kW','pump_kW','soc_kWh','tes_power_kW','margin_kW','mode_violation','cost_CNY','carbon_kgCO2e')}
    violations=[]
    for label, frame in [('capacity',caps),('dispatch',dispatch),('connections',conn),('stations',sites),('storage',storage),('soc',soc),('demand',demand),('network_hourly',flows)]:
        if not np.isfinite(frame.select_dtypes(include='number').to_numpy()).all():
            raise ValueError(f'导出{label}含NaN/Inf，不能通过QA')
    def record(key,val):
        errors[key]=max(errors[key],float(val))
    def annual_factor(years):
        r=econ.discount_rate
        return 1/years if r==0 else r*(1+r)**years/((1+r)**years-1)
    def expected_rows(frame,keys,expected,label):
        actual=set(map(tuple,frame.reset_index()[keys].itertuples(index=False,name=None)))
        if len(frame)!=len(expected) or actual!=set(expected):
            violations.append(label+'_coverage')
    expected_rows(demand,['building_id','hour'],d.heat_demand_kW,'demand')
    expected_rows(soc,['site_id','hour'],[(s,h) for s in sites.index for h in d.hours],'storage')
    expected_rows(flows,['edge_id','hour'],[(e['edge_id'],h) for e in net['edges'] for h in d.hours],'network')
    expected_dispatch=[(s,t.technology_id,h) for s in sites.index for t in d.technologies if t.applicable_scope=='central' for h in d.hours]
    expected_dispatch += [(b,t.technology_id,h) for b in d.demand_nodes for t in d.technologies if t.applicable_scope=='local' for h in d.hours]
    expected_rows(dispatch,['location_id','technology_id','hour'],expected_dispatch,'dispatch')
    for frame in (dispatch, flows):
        for row in frame[['hour','timestamp']].drop_duplicates().itertuples(index=False):
            if row.timestamp != case.timestamps[row.hour-1]:
                violations.append('timestamp_mismatch')
    for row in caps.itertuples(index=False):
        spec=specs[row.technology_id]
        record('capacity_kW',max(0.,row.capacity_kW_th-spec.capacity_max_kW*row.installed,
              spec.capacity_min_kW*row.installed-row.capacity_kW_th,-row.capacity_kW_th))
        record('mode_violation',abs(row.installed-round(row.installed)))
        if row.scope=='central':
            record('mode_violation',max(0.,row.installed-sites.loc[row.location_id,'built']))
        else:
            record('mode_violation',abs(row.installed-conn.loc[row.location_id,'local_installed']))
    electricity={h:[] for h in d.hours}; gas={h:[] for h in d.hours}
    node_balance={(n['node_id'],h):0. for n in net['nodes'] for h in d.hours}
    hourly_site_heat={(s,h):0. for s in sites.index for h in d.hours}
    variable_cost=[]
    for row in dispatch.itertuples(index=False):
        spec=specs[row.technology_id]
        h=row.hour
        ratio=d.heat_pump_capacity_ratio_by_hour.get((row.technology_id,h),1.)
        performance=d.heat_pump_cop_by_hour.get((row.technology_id,h),spec.cop) if spec.energy_carrier=='electricity' else spec.efficiency
        record('energy_conversion_kW',abs(row.energy_input_kW-row.heat_kW_th/performance))
        capacity=cap_index.loc[(row.location_id,row.technology_id),'capacity_kW_th']
        record('capacity_kW',max(0.,row.heat_kW_th-capacity*ratio,-row.heat_kW_th))
        (electricity if spec.energy_carrier=='electricity' else gas)[h].append(row.energy_input_kW)
        variable_cost.append(row.heat_kW_th*spec.variable_om_CNY_per_kWh_th*econ.time_weight_h_per_year[h])
        if row.scope=='central':
            hourly_site_heat[row.location_id,h]+=row.heat_kW_th
        else:
            record('heat_balance_kW',abs(row.heat_kW_th-demand.loc[row.location_id,h].local_kW))
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
        elif abs(row.capacity_kW_th)>1e-6:
            violations.append('unbuilt_capacity')
        record('mode_violation',abs(row.built-round(row.built)))
        if edge['edge_type']=='building_service':
            building=next(n for n in (edge['node_u'],edge['node_v']) if n in d.demand_nodes)
            record('mode_violation',abs(row.built-conn.loc[building,'connected']))
    edge_lookup={e['edge_id']:e for e in net['edges']}
    for row in flows.itertuples(index=False):
        edge=edge_lookup[row.edge_id]; choice=pipes.loc[row.edge_id]
        level=levels[choice.pipe_type_id] if choice.built>.5 else None
        loss=edge['length_m']*level.pair_loss_kW_per_route_m if level else 0.
        pump=edge['length_m']*abs(row.signed_flow_kW_th)*level.pumping_kWh_e_per_kWh_th_m if level else 0.
        record('loss_kW',abs(loss-row.loss_kW_th))
        record('pump_kW',abs(pump-row.pump_kW_e))
        record('direction_kW',min(abs(row.forward_kW_th),abs(row.reverse_kW_th)))
        record('direction_kW',max(0.,row.forward_kW_th*(1-row.direction),row.reverse_kW_th*row.direction))
        record('direction_kW',abs(row.signed_flow_kW_th-row.forward_kW_th+row.reverse_kW_th))
        u,v=row.signed_flow_kW_th+loss/2,-row.signed_flow_kW_th+loss/2
        record('heat_balance_kW',max(abs(u-row.port_u_withdrawal_kW_th),abs(v-row.port_v_withdrawal_kW_th)))
        record('pipe_kW',max(0.,abs(u)-choice.capacity_kW_th,abs(v)-choice.capacity_kW_th))
        node_balance[edge['node_u'],row.hour]-=u
        node_balance[edge['node_v'],row.hour]-=v
        electricity[row.hour].append(row.pump_kW_e)
    local=next(t for t in d.technologies if t.applicable_scope=='local')
    for b in d.demand_nodes:
        record('mode_violation',abs(conn.loc[b,'connected']+conn.loc[b,'local_installed']-1))
        if d.mode!='hybrid':
            record('mode_violation',abs(conn.loc[b,'connected']-int(d.mode=='central')))
        for h in d.hours:
            row=demand.loc[b,h]
            node_balance[b,h]-=row.network_kW
            record('heat_balance_kW',abs(row.network_kW+row.local_kW+row.unserved_kW-d.heat_demand_kW[b,h]))
            record('capacity_kW',max(0.,row.network_kW-d.heat_demand_kW[b,h]*conn.loc[b,'connected'],-row.network_kW,-row.local_kW,-row.unserved_kW))
            record('margin_kW',max(0.,(1+d.peak_capacity_margin_fraction)*d.heat_demand_kW[b,h]*(1-conn.loc[b,'connected'])
                -cap_index.loc[(b,local.technology_id),'capacity_kW_th']*d.heat_pump_capacity_ratio_by_hour.get((local.technology_id,h),1.)))
    for s in sites.index:
        attachment=sites.loc[s,'attachment_node_id']
        record('mode_violation',abs(sites.loc[s,'built']-round(sites.loc[s,'built'])))
        record('mode_violation',max(0.,storage.loc[s,'built']-sites.loc[s,'built']))
        if d.storage:
            st=d.storage
            record('soc_kWh',max(0.,storage.loc[s,'energy_capacity_kWh']-st.energy_capacity_max_kWh_th*storage.loc[s,'built']))
            record('tes_power_kW',max(0.,storage.loc[s,'charge_capacity_kW']-st.charge_capacity_max_kW_th*storage.loc[s,'built'],storage.loc[s,'discharge_capacity_kW']-st.discharge_capacity_max_kW_th*storage.loc[s,'built'],storage.loc[s,'charge_capacity_kW']-storage.loc[s,'power_cost_capacity_kW'],storage.loc[s,'discharge_capacity_kW']-storage.loc[s,'power_cost_capacity_kW']))
        for i,h in enumerate(d.hours):
            row=soc.loc[s,h]
            node_balance[attachment,h]+=hourly_site_heat[s,h]+row.discharge_kW-row.charge_kW
            if d.storage:
                st=d.storage
                previous=soc.loc[s,d.hours[i-1]].soc_kWh
                record('soc_kWh',abs(row.soc_kWh-previous*(1-st.standing_loss_fraction_per_hour)-row.charge_kW*st.charge_efficiency+row.discharge_kW/st.discharge_efficiency))
                record('soc_kWh',max(0.,row.soc_kWh-storage.loc[s,'energy_capacity_kWh'],-row.soc_kWh))
            record('tes_power_kW',max(0.,row.charge_kW-storage.loc[s,'charge_capacity_kW'],row.discharge_kW-storage.loc[s,'discharge_capacity_kW'],min(row.charge_kW,row.discharge_kW)))
    for h in d.hours:
        available=fsum(row.capacity_kW_th*d.heat_pump_capacity_ratio_by_hour.get((row.technology_id,h),1.) for row in caps.itertuples(index=False) if row.scope=='central')
        required=(1+d.peak_capacity_margin_fraction)*(fsum(d.heat_demand_kW[b,h]*conn.loc[b,'connected'] for b in d.demand_nodes)+loss_total)
        record('margin_kW',max(0.,required-available))
    record('heat_balance_kW',max(abs(x) for x in node_balance.values()))
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
    costs['electricity_cost']=fsum(fsum(electricity[h])*econ.electricity_price_CNY_per_kWh_e[h]*econ.time_weight_h_per_year[h] for h in d.hours)
    costs['gas_cost']=fsum(fsum(gas[h])*econ.gas_price_CNY_per_kWh_LHV[h]*econ.time_weight_h_per_year[h] for h in d.hours)
    costs['variable_om']=fsum(variable_cost)
    reported=pd.read_csv(root/'cost_breakdown.csv').set_index('component')['annual_CNY']
    record('cost_CNY',max(abs(costs[k]-reported[k]) for k in costs))
    record('cost_CNY',abs(fsum(costs.values())-summary['real_cost_CNY']))
    carbon_e=fsum(fsum(electricity[h])*econ.electricity_carbon_kgCO2e_per_kWh_e[h]*econ.time_weight_h_per_year[h] for h in d.hours)
    carbon_g=fsum(fsum(gas[h])*econ.gas_carbon_kgCO2e_per_kWh_LHV[h]*econ.time_weight_h_per_year[h] for h in d.hours)
    record('carbon_kgCO2e',abs(carbon_e+carbon_g-summary['carbon_kgCO2e']))
    unserved=fsum(demand.loc[b,h].unserved_kW*econ.time_weight_h_per_year[h] for b in d.demand_nodes for h in d.hours)
    record('cost_CNY',abs(unserved*econ.hns_penalty_CNY_per_kWh-summary['hns_penalty_CNY']))
    heat=fsum(d.heat_demand_kW.values())
    if unserved>max(1e-6,heat*1e-6):
        violations.append('unserved_heat')
    pd.DataFrame([dict(node_id=n,hour=h,residual_kW=residual) for (n,h),residual in node_balance.items()]).to_parquet(root/'node_balance_check.parquet',index=False)
    _json(root/'independent_recalculation.json',dict(cost=costs,carbon_electricity_kgCO2e=carbon_e,carbon_gas_kgCO2e=carbon_g,
         carbon_tCO2e=(carbon_e+carbon_g)/1000,unserved_kWh=unserved))
    return dict(passed=not violations and all(x<=1e-6 for x in errors.values()),max_errors=errors,
                violations=violations,unserved_kWh=unserved,relative_unserved=unserved/heat if heat else 0,
                qa_basis='exported_decisions_and_canonical_input_not_model_expressions')
