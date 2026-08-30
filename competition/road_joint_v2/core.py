"""Road-atomic MILP V2. No calls to either legacy model builder.

Signed flow is midpoint thermal power. Half of the constant pair-route loss
is assigned to each port. Nonnegative receiving power plus a binary direction
forbids artificial counterflow; capacity limits include the sending port.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from math import isfinite

import pyomo.environ as p
import pandas as pd

from competition.core_model import CoreModelInput, validate_core_input, ThermalStorageSpec
from competition.costing.annualized import capital_recovery_factor as crf
from competition.road_joint_v2.network import validate_network, access_options


@dataclass(frozen=True)
class PipeDesign:
    pipe_type_id: str
    capacity_kW_th: float
    capex_CNY_per_route_m: float
    lifetime_years: int
    pair_loss_kW_per_route_m: float
    pumping_kWh_e_per_kWh_th_m: float
    dn_mm: float | None = None


@dataclass(frozen=True)
class RoadCase:
    common: CoreModelInput
    network_json: str
    pipe_designs: tuple[PipeDesign, ...]
    timestamps: tuple[str, ...]
    parameter_version: str = 'synthetic_test'

    @property
    def network(self):
        return json.loads(self.network_json)

    def with_mode(self, mode):
        return replace(self, common=replace(self.common, mode=mode))


def validate_case(case: RoadCase):
    d, net = case.common, case.network
    # Reuse only input/technology validation, not old topology or mathematics.
    validate_core_input(replace(d, mode='distributed', segments=(), pipe_levels=()))
    if d.mode not in ('central', 'distributed', 'hybrid'):
        raise ValueError('非法供热模式')
    validate_network(net)
    if set(d.demand_nodes) != {n['node_id'] for n in net['nodes'] if n['node_type'] == 'building'}:
        raise ValueError('路网与负荷建筑ID不一致')
    if set(d.candidate_station_nodes or (d.site_node,)) != {s['site_id'] for s in net['sites']}:
        raise ValueError('路网与设备候选站ID不一致')
    if len(case.timestamps) != len(d.hours) or len(set(case.timestamps)) != len(d.hours):
        raise ValueError('timestamp必须唯一且逐小时覆盖')
    index = pd.DatetimeIndex(case.timestamps)
    if index.tz is None or index.hasnans or any((b-a).total_seconds()!=3600 for a,b in zip(index,index[1:])):
        raise ValueError('timestamp必须带时区、严格连续1小时')
    if any(d.economics.time_weight_h_per_year[h] != 1 for h in d.hours):
        raise ValueError('道路V2只接受1h连续时步/权重')
    if len(case.pipe_designs) != 3 or len({x.pipe_type_id for x in case.pipe_designs}) != 3:
        raise ValueError('必须提供3档唯一管型')
    for level in case.pipe_designs:
        for value in (level.capacity_kW_th, level.capex_CNY_per_route_m, level.pair_loss_kW_per_route_m, level.pumping_kWh_e_per_kWh_th_m):
            if isinstance(value, bool) or not isfinite(value) or value < 0:
                raise ValueError('管型参数必须有限非负')
        if level.capacity_kW_th <= 0 or not isinstance(level.lifetime_years, int) or level.lifetime_years <= 0:
            raise ValueError('管型容量/寿命必须为正')
    if not d.economics.electricity_carbon_kgCO2e_per_kWh_e or not d.economics.gas_carbon_kgCO2e_per_kWh_LHV:
        raise ValueError('V2必须提供电气碳因子')


def build_road_model(case: RoadCase):
    validate_case(case)
    d, net, econ = case.common, case.network, case.common.economics
    edges = {e['edge_id']: e for e in net['edges']}
    options = {o['option_id']: o for o in access_options(net)}
    building_options = {b: [o for o, row in options.items() if row['building_id'] == b] for b in d.demand_nodes}
    edge_options = {e: [o for o, row in options.items() if e in row['edge_ids']] for e in edges}
    stations = {s['site_id']: s['attachment_node_id'] for s in net['sites']}
    levels = {x.pipe_type_id: x for x in case.pipe_designs}
    central = {t.technology_id: t for t in d.technologies if t.applicable_scope == 'central'}
    local = next(t for t in d.technologies if t.applicable_scope == 'local')
    nodes = [n['node_id'] for n in net['nodes']]
    incident = {n: [] for n in nodes}
    for eid, edge in edges.items():
        incident[edge['node_u']].append((eid, -1))
        incident[edge['node_v']].append((eid, 1))
    sources = {n: [s for s, attach in stations.items() if attach == n] for n in nodes}
    st = d.storage or ThermalStorageSpec('disabled', 1, 1, 1, 1, 1, 0, 0, 0, 0, 1)
    def ratio(t, h):
        return d.heat_pump_capacity_ratio_by_hour.get((t, h), 1.0)
    def cop(t, h):
        spec = central.get(t, local)
        return d.heat_pump_cop_by_hour.get((t, h), spec.cop)
    m = p.ConcreteModel(name='road_joint_v2')
    m.HOURS = p.Set(initialize=d.hours, ordered=True)
    m.DEMAND_NODES = p.Set(initialize=d.demand_nodes, ordered=True)
    m.N = p.Set(initialize=nodes, ordered=True)
    m.S = p.Set(initialize=tuple(stations), ordered=True)
    m.T = p.Set(initialize=tuple(central), ordered=True)
    m.E = p.Set(initialize=tuple(edges), ordered=True)
    m.K = p.Set(initialize=tuple(levels), ordered=True)
    m.ACCESS = p.Set(initialize=tuple(options), ordered=True)
    m.access_selected = p.Var(m.ACCESS, domain=p.Binary)
    m.time_weight_h_per_year = p.Param(m.HOURS, initialize=dict(econ.time_weight_h_per_year))
    m.station_built = p.Var(m.S, domain=p.Binary)
    m.connected = p.Var(m.DEMAND_NODES, domain=p.Binary)
    m.installed = p.Var(m.S, m.T, domain=p.Binary)
    m.capacity = p.Var(m.S, m.T, domain=p.NonNegativeReals)
    m.heat = p.Var(m.S, m.T, m.HOURS, domain=p.NonNegativeReals)
    m.local_installed = p.Var(m.DEMAND_NODES, domain=p.Binary)
    m.local_capacity = p.Var(m.DEMAND_NODES, domain=p.NonNegativeReals)
    m.local_heat = p.Var(m.DEMAND_NODES, m.HOURS, domain=p.NonNegativeReals)
    m.network_heat = p.Var(m.DEMAND_NODES, m.HOURS, domain=p.NonNegativeReals)
    m.unserved_heat_kW = p.Var(m.DEMAND_NODES, m.HOURS, domain=p.NonNegativeReals)
    m.built = p.Var(m.E, domain=p.Binary)
    m.grade = p.Var(m.E, m.K, domain=p.Binary)
    m.forward = p.Var(m.E, m.K, m.HOURS, domain=p.NonNegativeReals)
    m.reverse = p.Var(m.E, m.K, m.HOURS, domain=p.NonNegativeReals)
    m.direction = p.Var(m.E, m.HOURS, domain=p.Binary)
    m.commodity = p.Var(m.E, domain=p.Reals)
    m.commodity_supply = p.Var(m.S, domain=p.NonNegativeReals)
    m.tes_built = p.Var(m.S, domain=p.Binary)
    m.tes_energy = p.Var(m.S, domain=p.NonNegativeReals)
    m.tes_charge_capacity = p.Var(m.S, domain=p.NonNegativeReals)
    m.tes_discharge_capacity = p.Var(m.S, domain=p.NonNegativeReals)
    m.tes_power_cost_capacity = p.Var(m.S, domain=p.NonNegativeReals)
    m.charge = p.Var(m.S, m.HOURS, domain=p.NonNegativeReals)
    m.discharge = p.Var(m.S, m.HOURS, domain=p.NonNegativeReals)
    m.soc = p.Var(m.S, m.HOURS, domain=p.NonNegativeReals)
    m.charging = p.Var(m.S, m.HOURS, domain=p.Binary)
    m.constraints = p.ConstraintList()
    c = m.constraints.add
    c(sum(m.station_built[s] for s in stations) <= 1)
    c(sum(m.station_built[s] for s in stations) <= sum(m.connected[b] for b in d.demand_nodes))
    for s in stations:
        c(m.station_built[s] <= sum(m.installed[s,t] for t in central))
        c(m.commodity_supply[s] <= len(d.demand_nodes)*m.station_built[s])
        for t, spec in central.items():
            c(m.installed[s,t] <= m.station_built[s])
            c(m.capacity[s,t] <= spec.capacity_max_kW*m.installed[s,t])
            c(m.capacity[s,t] >= spec.capacity_min_kW*m.installed[s,t])
            for h in d.hours:
                c(m.heat[s,t,h] <= m.capacity[s,t]*ratio(t,h))
        c(m.tes_built[s] <= m.station_built[s])
        if d.storage is None:
            m.tes_built[s].fix(0)
        c(m.tes_energy[s] <= st.energy_capacity_max_kWh_th*m.tes_built[s])
        c(m.tes_charge_capacity[s] <= st.charge_capacity_max_kW_th*m.tes_built[s])
        c(m.tes_discharge_capacity[s] <= st.discharge_capacity_max_kW_th*m.tes_built[s])
        c(m.tes_power_cost_capacity[s] >= m.tes_charge_capacity[s])
        c(m.tes_power_cost_capacity[s] >= m.tes_discharge_capacity[s])
        c(m.tes_power_cost_capacity[s] <= max(st.charge_capacity_max_kW_th,st.discharge_capacity_max_kW_th)*m.tes_built[s])
        if st.max_charge_ratio_per_hour is not None:
            c(m.tes_charge_capacity[s] <= st.max_charge_ratio_per_hour*m.tes_energy[s])
        if st.max_discharge_ratio_per_hour is not None:
            c(m.tes_discharge_capacity[s] <= st.max_discharge_ratio_per_hour*m.tes_energy[s])
        for i,h in enumerate(d.hours):
            c(m.soc[s,h] <= m.tes_energy[s])
            c(m.charge[s,h] <= m.tes_charge_capacity[s])
            c(m.discharge[s,h] <= m.tes_discharge_capacity[s])
            c(m.charging[s,h] <= m.tes_built[s])
            c(m.charge[s,h] <= st.charge_capacity_max_kW_th*m.charging[s,h])
            c(m.discharge[s,h] <= st.discharge_capacity_max_kW_th*(m.tes_built[s]-m.charging[s,h]))
            c(m.soc[s,h] == m.soc[s,d.hours[i-1]]*(1-st.standing_loss_fraction_per_hour)
              + st.charge_efficiency*m.charge[s,h] - m.discharge[s,h]/st.discharge_efficiency)
    for b in d.demand_nodes:
        c(sum(m.access_selected[o] for o in building_options[b]) == m.connected[b])
        c(sum(m.built[e] for e, _ in incident[b]) == m.connected[b])
        c(m.connected[b]+m.local_installed[b] == 1)
        c(m.connected[b] <= sum(m.station_built[s] for s in stations))
        c(m.local_capacity[b] <= local.capacity_max_kW*m.local_installed[b])
        c(m.local_capacity[b] >= local.capacity_min_kW*m.local_installed[b])
        if d.mode != 'hybrid':
            m.connected[b].fix(int(d.mode == 'central'))
        for h in d.hours:
            demand = d.heat_demand_kW[b,h]
            c(m.local_heat[b,h] <= m.local_capacity[b]*ratio(local.technology_id,h))
            c(m.local_capacity[b]*ratio(local.technology_id,h) >= (1+d.peak_capacity_margin_fraction)*demand*(1-m.connected[b]))
            c(m.network_heat[b,h] <= demand*m.connected[b])
            c(m.network_heat[b,h]+m.local_heat[b,h]+m.unserved_heat_kW[b,h] == demand)
            if not d.allow_unserved:
                m.unserved_heat_kW[b,h].fix(0)
    if d.mode == 'central':
        c(sum(m.station_built[s] for s in stations) == 1)
    elif d.mode == 'distributed':
        for s in stations:
            m.station_built[s].fix(0)
    m.edge_capacity = p.Expression(m.E, rule=lambda _,e: sum(levels[k].capacity_kW_th*m.grade[e,k] for k in levels))
    m.edge_loss = p.Expression(m.E, rule=lambda _,e: edges[e]['length_m']*sum(levels[k].pair_loss_kW_per_route_m*m.grade[e,k] for k in levels))
    m.flow = p.Expression(m.E,m.HOURS, rule=lambda _,e,h: sum(m.forward[e,k,h]-m.reverse[e,k,h] for k in levels))
    m.abs_flow = p.Expression(m.E,m.HOURS, rule=lambda _,e,h: sum(m.forward[e,k,h]+m.reverse[e,k,h] for k in levels))
    m.pump = p.Expression(m.E,m.HOURS, rule=lambda _,e,h: edges[e]['length_m']*sum(levels[k].pumping_kWh_e_per_kWh_th_m*(m.forward[e,k,h]+m.reverse[e,k,h]) for k in levels))
    big_m = max(x.capacity_kW_th for x in levels.values())
    for e, edge in edges.items():
        c(sum(m.grade[e,k] for k in levels) == m.built[e])
        c(m.built[e] <= sum(m.station_built[s] for s in stations))
        for o in edge_options[e]:
            c(m.built[e] >= m.access_selected[o])
        if edge['edge_type'] != 'road':
            c(m.built[e] <= sum(m.access_selected[o] for o in edge_options[e]))
        c(m.commodity[e] <= len(d.demand_nodes)*m.built[e])
        c(m.commodity[e] >= -len(d.demand_nodes)*m.built[e])
        for h in d.hours:
            c(m.direction[e,h] <= m.built[e])
            c(sum(m.forward[e,k,h] for k in levels) <= big_m*m.direction[e,h])
            c(sum(m.reverse[e,k,h] for k in levels) <= big_m*(m.built[e]-m.direction[e,h]))
            c(m.abs_flow[e,h] + m.edge_loss[e]/2 <= m.edge_capacity[e])
            c(sum(m.forward[e,k,h] for k in levels) >= m.edge_loss[e]/2 - big_m*(1-m.direction[e,h]))
            c(sum(m.reverse[e,k,h] for k in levels) >= m.edge_loss[e]/2 - big_m*m.direction[e,h])
            for k, level in levels.items():
                c(m.forward[e,k,h]+m.reverse[e,k,h] <= level.capacity_kW_th*m.grade[e,k])
    for n in nodes:
        balance = sum(sign*m.commodity[e] for e,sign in incident[n])+sum(m.commodity_supply[s] for s in sources[n])
        c(balance == (m.connected[n] if n in d.demand_nodes else 0))
        for h in d.hours:
            thermal = sum(sign*m.flow[e,h]-m.edge_loss[e]/2 for e,sign in incident[n])
            thermal += sum(sum(m.heat[s,t,h] for t in central)+m.discharge[s,h]-m.charge[s,h] for s in sources[n])
            c(thermal == (m.network_heat[n,h] if n in d.demand_nodes else 0))
    for h in d.hours:
        c(sum(m.capacity[s,t]*ratio(t,h) for s in stations for t in central)
          >= (1+d.peak_capacity_margin_fraction)*(sum(d.heat_demand_kW[b,h]*m.connected[b] for b in d.demand_nodes)+sum(m.edge_loss[e] for e in edges)))
    m.electricity = p.Expression(m.HOURS, rule=lambda _,h:
        sum(m.heat[s,t,h]/cop(t,h) for s in stations for t in central if central[t].energy_carrier == 'electricity')
        +sum(m.local_heat[b,h]/cop(local.technology_id,h) for b in d.demand_nodes)+sum(m.pump[e,h] for e in edges))
    m.gas = p.Expression(m.HOURS, rule=lambda _,h:
        sum(m.heat[s,t,h]/central[t].efficiency for s in stations for t in central if central[t].energy_carrier == 'gas'))
    r = econ.discount_rate
    m.device_investment = p.Expression(expr=sum(m.capacity[s,t]*central[t].capex_CNY_per_kW*crf(r,central[t].lifetime_years) for s in stations for t in central)
        +sum(m.local_capacity[b]*local.capex_CNY_per_kW*crf(r,local.lifetime_years) for b in d.demand_nodes))
    m.fixed_om = p.Expression(expr=sum(m.capacity[s,t]*central[t].capex_CNY_per_kW*central[t].fixed_maintenance_fraction_per_year for s in stations for t in central)
        +sum(m.local_capacity[b]*local.capex_CNY_per_kW*local.fixed_maintenance_fraction_per_year for b in d.demand_nodes))
    m.pipe_investment = p.Expression(expr=sum(edges[e]['length_m']*levels[k].capex_CNY_per_route_m*crf(r,levels[k].lifetime_years)*m.grade[e,k] for e in edges for k in levels))
    m.station_investment = p.Expression(expr=sum(m.station_built[s] for s in stations)*econ.station_fixed_capex_CNY*crf(r,econ.station_lifetime_years))
    m.connection_investment = p.Expression(expr=sum(m.connected[b]*econ.connection_capex_CNY[b]*crf(r,econ.connection_lifetime_years[b]) for b in d.demand_nodes))
    m.storage_investment = p.Expression(expr=sum(m.tes_energy[s]*st.capex_CNY_per_kWh_th+m.tes_power_cost_capacity[s]*st.power_capex_CNY_per_kW_th+m.tes_built[s]*st.fixed_capex_CNY for s in stations)*crf(r,st.lifetime_years))
    m.electricity_cost = p.Expression(expr=sum(m.electricity[h]*econ.electricity_price_CNY_per_kWh_e[h]*econ.time_weight_h_per_year[h] for h in d.hours))
    m.gas_cost = p.Expression(expr=sum(m.gas[h]*econ.gas_price_CNY_per_kWh_LHV[h]*econ.time_weight_h_per_year[h] for h in d.hours))
    m.variable_om = p.Expression(expr=sum((sum(m.heat[s,t,h]*central[t].variable_om_CNY_per_kWh_th for s in stations for t in central)+sum(m.local_heat[b,h]*local.variable_om_CNY_per_kWh_th for b in d.demand_nodes))*econ.time_weight_h_per_year[h] for h in d.hours))
    m.annual_real_cost_CNY_per_year = p.Expression(expr=m.device_investment+m.fixed_om+m.pipe_investment+m.station_investment+m.connection_investment+m.storage_investment+m.electricity_cost+m.gas_cost+m.variable_om)
    m.annual_hns_penalty_CNY_per_year = p.Expression(expr=sum(m.unserved_heat_kW[b,h]*econ.time_weight_h_per_year[h]*econ.hns_penalty_CNY_per_kWh for b in d.demand_nodes for h in d.hours))
    m.annual_operating_physical_carbon_kgCO2e_per_year = p.Expression(expr=sum((m.electricity[h]*econ.electricity_carbon_kgCO2e_per_kWh_e[h]+m.gas[h]*econ.gas_carbon_kgCO2e_per_kWh_LHV[h])*econ.time_weight_h_per_year[h] for h in d.hours))
    m.policy_carbon_cost = p.Expression(expr=m.annual_operating_physical_carbon_kgCO2e_per_year/1000*econ.policy_carbon_price_CNY_per_tCO2e)
    m.annual_cost_objective = p.Objective(expr=m.annual_real_cost_CNY_per_year+m.annual_hns_penalty_CNY_per_year)
    return m
