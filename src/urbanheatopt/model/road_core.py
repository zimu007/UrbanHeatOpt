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

from urbanheatopt.model.reference_core import CoreModelInput, validate_core_input, ThermalStorageSpec
from urbanheatopt.model.costing.annualized import capital_recovery_factor as crf
from urbanheatopt.spatial.atomic_network import validate_network, access_options


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


def classify_direction_pair(
    forward_kW: float,
    reverse_kW: float,
    pair_loss_kW: float,
    *,
    tolerance_kW: float = 1e-6,
):
    """Classify one relaxed pair without changing model state."""
    values = (forward_kW, reverse_kW, pair_loss_kW, tolerance_kW)
    if any(isinstance(value, bool) or not isfinite(value) for value in values):
        raise ValueError('direction audit values must be finite numbers')
    if pair_loss_kW < 0 or tolerance_kW < 0:
        raise ValueError('pair_loss_kW and tolerance_kW must be nonnegative')
    half_loss_kW = pair_loss_kW/2
    counterflow_kW = max(0.0, min(forward_kW, reverse_kW))
    loss_shortfall_kW = max(0.0, half_loss_kW-max(forward_kW, reverse_kW))
    liftable = (
        min(forward_kW, reverse_kW) <= tolerance_kW
        and max(forward_kW, reverse_kW) >= half_loss_kW-tolerance_kW
    )
    return {
        'liftable': liftable,
        'direction': int(forward_kW > reverse_kW),
        'counterflow_kW': counterflow_kW,
        'loss_shortfall_kW': loss_shortfall_kW,
    }


def classify_direction_solution(model, *, tolerance_kW: float = 1e-6):
    """Audit non-leaf flow pairs and identify pairs requiring exactification."""
    if isinstance(tolerance_kW, bool) or not isfinite(tolerance_kW) or tolerance_kW < 0:
        raise ValueError('tolerance_kW must be a finite nonnegative number')
    exact_pairs = frozenset(tuple(pair) for pair in model.EXACT_DIRECTION_PAIRS)
    violation_pairs = []
    violations = []
    inferred_directions = {}
    max_counterflow_kW = 0.0
    max_loss_shortfall_kW = 0.0
    exact_violation_count = 0
    uniform_pumping = bool(p.value(model.uniform_pumping_flow))
    for edge in model.FLOW_E:
        for hour in model.HOURS:
            if uniform_pumping:
                forward_kW = p.value(model.forward[edge, hour], exception=False)
                reverse_kW = p.value(model.reverse[edge, hour], exception=False)
            else:
                forward_kW = p.value(sum(model.forward[edge, grade, hour] for grade in model.K), exception=False)
                reverse_kW = p.value(sum(model.reverse[edge, grade, hour] for grade in model.K), exception=False)
            pair_loss_kW = p.value(model.edge_loss[edge], exception=False)
            if any(value is None or not isfinite(value) for value in (forward_kW, reverse_kW, pair_loss_kW)):
                raise ValueError(f'direction pair {(edge, hour)!r} has no finite solution value')
            result = classify_direction_pair(
                float(forward_kW), float(reverse_kW), float(pair_loss_kW),
                tolerance_kW=tolerance_kW,
            )
            pair = (edge, hour)
            inferred_directions[pair] = result['direction']
            max_counterflow_kW = max(max_counterflow_kW, result['counterflow_kW'])
            max_loss_shortfall_kW = max(max_loss_shortfall_kW, result['loss_shortfall_kW'])
            if not result['liftable']:
                violation_pairs.append(pair)
                exact = pair in exact_pairs
                exact_violation_count += int(exact)
                violations.append({
                    'edge_id': edge,
                    'hour': hour,
                    'forward_kW': float(forward_kW),
                    'reverse_kW': float(reverse_kW),
                    'pair_loss_kW': float(pair_loss_kW),
                    'exact': exact,
                    **result,
                })
    pair_count = len(model.FLOW_E)*len(model.HOURS)
    return {
        'liftable': not violation_pairs,
        'violation_pairs': tuple(violation_pairs),
        'violations': tuple(violations),
        'inferred_directions': inferred_directions,
        'metrics': {
            'pair_count': pair_count,
            'exact_pair_count': len(exact_pairs),
            'relaxed_pair_count': pair_count-len(exact_pairs),
            'violation_count': len(violation_pairs),
            'exact_violation_count': exact_violation_count,
            'max_counterflow_kW': max_counterflow_kW,
            'max_loss_shortfall_kW': max_loss_shortfall_kW,
            'tolerance_kW': tolerance_kW,
        },
    }


def build_road_model(
    case: RoadCase,
    *,
    direction_relaxation: bool = False,
    exact_direction_pairs=(),
):
    validate_case(case)
    if not isinstance(direction_relaxation, bool):
        raise ValueError('direction_relaxation must be boolean')
    d, net, econ = case.common, case.network, case.common.economics
    edges = {e['edge_id']: e for e in net['edges']}
    options = {o['option_id']: o for o in access_options(net)}
    building_options = {b: [o for o, row in options.items() if row['building_id'] == b] for b in d.demand_nodes}
    edge_options = {e: [o for o, row in options.items() if e in row['edge_ids']] for e in edges}
    stations = {s['site_id']: s['attachment_node_id'] for s in net['sites']}
    levels = {x.pipe_type_id: x for x in case.pipe_designs}
    pumping_rates = {x.pumping_kWh_e_per_kWh_th_m for x in levels.values()}
    # When all grades have exactly the same pumping coefficient, grade-indexed
    # directional flows carry no information.  The selected grade already
    # enters edge_capacity/edge_loss, and exactly one grade can be active.
    # Use exact equality here: an approximate match would change the objective.
    uniform_pumping = len(pumping_rates) == 1
    uniform_pumping_rate = next(iter(pumping_rates)) if uniform_pumping else None
    central = {t.technology_id: t for t in d.technologies if t.applicable_scope == 'central'}
    local = next(t for t in d.technologies if t.applicable_scope == 'local')
    nodes = [n['node_id'] for n in net['nodes']]
    incident = {n: [] for n in nodes}
    for eid, edge in edges.items():
        incident[edge['node_u']].append((eid, -1))
        incident[edge['node_v']].append((eid, 1))
    sources = {n: [s for s, attach in stations.items() if attach == n] for n in nodes}
    deterministic_dispatch = not d.allow_unserved
    distributed_fastpath = d.mode == 'distributed'
    # With no unserved heat, the local/network split is exactly determined by
    # connected: local=demand*(1-connected), network=demand*connected.  A
    # building leaf then has a unique lift for every selected service edge:
    # its flow is demand plus half the pair loss, directed toward the building.
    # Apply this only when *all* edges incident to that building have the
    # validated one-building service-edge shape; otherwise retain the general
    # formulation for the whole building.
    candidate_leaf_edges = {}
    for eid, edge in edges.items():
        demand_ends = [n for n in (edge['node_u'], edge['node_v']) if n in d.demand_nodes]
        if edge['edge_type'] == 'building_service' and len(demand_ends) == 1:
            candidate_leaf_edges[eid] = demand_ends[0]
    leaf_buildings = {
        b for b in d.demand_nodes
        if incident[b] and all(candidate_leaf_edges.get(e) == b for e, _ in incident[b])
    } if deterministic_dispatch and not distributed_fastpath else set()
    # Preserve the canonical network edge order in every generated MPS.
    service_leaf_edge_ids = tuple(
        e for e in edges if candidate_leaf_edges.get(e) in leaf_buildings
    )
    service_leaf_edges = frozenset(service_leaf_edge_ids)
    service_leaf_building = {e: candidate_leaf_edges[e] for e in service_leaf_edges}
    flow_edges = tuple(e for e in edges if e not in service_leaf_edges)
    flow_direction_pairs = tuple(
        (e, h) for e in (() if distributed_fastpath else flow_edges) for h in d.hours
    )
    try:
        requested_exact_pairs = frozenset(tuple(pair) for pair in exact_direction_pairs)
    except (TypeError, ValueError) as exc:
        raise ValueError('exact_direction_pairs must contain (edge_id, hour) pairs') from exc
    if any(len(pair) != 2 for pair in requested_exact_pairs):
        raise ValueError('exact_direction_pairs must contain (edge_id, hour) pairs')
    valid_direction_pairs = frozenset(flow_direction_pairs)
    unknown_exact_pairs = requested_exact_pairs - valid_direction_pairs
    if unknown_exact_pairs:
        raise ValueError(f'exact_direction_pairs contains invalid pairs: {sorted(unknown_exact_pairs, key=repr)!r}')
    if not direction_relaxation and requested_exact_pairs:
        raise ValueError('exact_direction_pairs is only valid when direction_relaxation=True')
    exact_direction_pair_ids = (
        tuple(pair for pair in flow_direction_pairs if pair in requested_exact_pairs)
        if direction_relaxation else flow_direction_pairs
    )
    exact_direction_pair_set = frozenset(exact_direction_pair_ids)
    relaxed_direction_pair_ids = tuple(
        pair for pair in flow_direction_pairs if pair not in exact_direction_pair_set
    )
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
    m.FLOW_E = p.Set(initialize=() if distributed_fastpath else flow_edges, ordered=True)
    m.SERVICE_LEAF_E = p.Set(initialize=() if distributed_fastpath else service_leaf_edge_ids, ordered=True)
    m.EXACT_DIRECTION_PAIRS = p.Set(dimen=2, initialize=exact_direction_pair_ids, ordered=True)
    m.RELAXED_DIRECTION_PAIRS = p.Set(dimen=2, initialize=relaxed_direction_pair_ids, ordered=True)
    m.deterministic_demand_dispatch = p.Param(initialize=deterministic_dispatch, within=p.Boolean)
    m.distributed_fastpath = p.Param(initialize=distributed_fastpath, within=p.Boolean)
    m.service_leaf_flow_elimination = p.Param(initialize=bool(service_leaf_edges), within=p.Boolean)
    m.direction_relaxation = p.Param(initialize=direction_relaxation, within=p.Boolean)
    if distributed_fastpath:
        m.access_selected = p.Param(m.ACCESS, initialize=0.0)
    else:
        m.access_selected = p.Var(m.ACCESS, domain=p.Binary)
    m.time_weight_h_per_year = p.Param(m.HOURS, initialize=dict(econ.time_weight_h_per_year))
    if distributed_fastpath:
        m.station_built = p.Param(m.S, initialize=0.0)
        m.installed = p.Expression(m.S, m.T, rule=lambda *_: 0.0)
        m.capacity = p.Expression(m.S, m.T, rule=lambda *_: 0.0)
        m.heat = p.Expression(m.S, m.T, m.HOURS, rule=lambda *_: 0.0)
    else:
        m.station_built = p.Var(m.S, domain=p.Binary)
        m.installed = p.Var(m.S, m.T, domain=p.Binary)
        m.capacity = p.Var(m.S, m.T, domain=p.NonNegativeReals)
        m.heat = p.Var(m.S, m.T, m.HOURS, domain=p.NonNegativeReals)
    if d.mode == 'hybrid':
        m.connected = p.Var(m.DEMAND_NODES, domain=p.Binary)
    else:
        m.connected = p.Param(m.DEMAND_NODES, initialize=float(d.mode == 'central'))
    m.local_installed = p.Expression(m.DEMAND_NODES, rule=lambda _,b: 1-m.connected[b])
    if d.mode == 'central':
        m.local_capacity = p.Expression(m.DEMAND_NODES, rule=lambda *_: 0.0)
    else:
        m.local_capacity = p.Var(m.DEMAND_NODES, domain=p.NonNegativeReals)
    if deterministic_dispatch:
        m.local_heat = p.Expression(m.DEMAND_NODES, m.HOURS,
            rule=lambda _,b,h: d.heat_demand_kW[b,h]*(1-m.connected[b]))
        m.network_heat = p.Expression(m.DEMAND_NODES, m.HOURS,
            rule=lambda _,b,h: d.heat_demand_kW[b,h]*m.connected[b])
        m.unserved_heat_kW = p.Expression(m.DEMAND_NODES, m.HOURS, rule=lambda *_: 0.0)
    else:
        if d.mode == 'central':
            m.local_heat = p.Expression(m.DEMAND_NODES, m.HOURS, rule=lambda *_: 0.0)
        else:
            m.local_heat = p.Var(m.DEMAND_NODES, m.HOURS, domain=p.NonNegativeReals)
        if distributed_fastpath:
            m.network_heat = p.Expression(m.DEMAND_NODES, m.HOURS, rule=lambda *_: 0.0)
        else:
            m.network_heat = p.Var(m.DEMAND_NODES, m.HOURS, domain=p.NonNegativeReals)
        m.unserved_heat_kW = p.Var(m.DEMAND_NODES, m.HOURS, domain=p.NonNegativeReals)
    if distributed_fastpath:
        m.built = p.Expression(m.E, rule=lambda *_: 0.0)
        m.grade = p.Expression(m.E, m.K, rule=lambda *_: 0.0)
    else:
        m.built = p.Var(m.E, domain=p.Binary)
        m.grade = p.Var(m.E, m.K, domain=p.Binary)
    m.uniform_pumping_flow = p.Param(initialize=uniform_pumping, within=p.Boolean)
    if uniform_pumping:
        m._forward_var = p.Var(m.FLOW_E, m.HOURS, domain=p.NonNegativeReals)
        m._reverse_var = p.Var(m.FLOW_E, m.HOURS, domain=p.NonNegativeReals)
        def leaf_magnitude(e, h):
            b = service_leaf_building[e]
            return d.heat_demand_kW[b,h]*m.built[e] + edges[e]['length_m']*sum(
                levels[k].pair_loss_kW_per_route_m*m.grade[e,k] for k in levels)/2
        m.forward = p.Expression(m.E, m.HOURS, rule=lambda _,e,h:
            0.0 if distributed_fastpath else
            ((leaf_magnitude(e,h) if service_leaf_building[e] == edges[e]['node_v'] else 0.0)
             if e in service_leaf_edges else m._forward_var[e,h]))
        m.reverse = p.Expression(m.E, m.HOURS, rule=lambda _,e,h:
            0.0 if distributed_fastpath else
            ((leaf_magnitude(e,h) if service_leaf_building[e] == edges[e]['node_u'] else 0.0)
             if e in service_leaf_edges else m._reverse_var[e,h]))
    else:
        m._forward_var = p.Var(m.FLOW_E, m.K, m.HOURS, domain=p.NonNegativeReals)
        m._reverse_var = p.Var(m.FLOW_E, m.K, m.HOURS, domain=p.NonNegativeReals)
        def leaf_grade_magnitude(e, k, h):
            b = service_leaf_building[e]
            return (d.heat_demand_kW[b,h] + edges[e]['length_m']*levels[k].pair_loss_kW_per_route_m/2)*m.grade[e,k]
        m.forward = p.Expression(m.E, m.K, m.HOURS, rule=lambda _,e,k,h:
            0.0 if distributed_fastpath else
            ((leaf_grade_magnitude(e,k,h) if service_leaf_building[e] == edges[e]['node_v'] else 0.0)
             if e in service_leaf_edges else m._forward_var[e,k,h]))
        m.reverse = p.Expression(m.E, m.K, m.HOURS, rule=lambda _,e,k,h:
            0.0 if distributed_fastpath else
            ((leaf_grade_magnitude(e,k,h) if service_leaf_building[e] == edges[e]['node_u'] else 0.0)
             if e in service_leaf_edges else m._reverse_var[e,k,h]))
    if direction_relaxation:
        m._direction_var = p.Var(m.EXACT_DIRECTION_PAIRS, domain=p.Binary)
        public_direction_pairs = tuple(
            (e, h) for e in edges for h in d.hours
            if distributed_fastpath or e in service_leaf_edges or (e, h) in exact_direction_pair_set
        )
        m.DIRECTION_PAIRS = p.Set(dimen=2, initialize=public_direction_pairs, ordered=True)
        m.direction = p.Expression(m.DIRECTION_PAIRS, rule=lambda _,e,h:
            0.0 if distributed_fastpath else
            ((m.built[e] if service_leaf_building[e] == edges[e]['node_v'] else 0.0)
             if e in service_leaf_edges else m._direction_var[e,h]))
    else:
        # Keep the default component shape and indexing unchanged.
        m._direction_var = p.Var(m.FLOW_E, m.HOURS, domain=p.Binary)
        m.direction = p.Expression(m.E, m.HOURS, rule=lambda _,e,h:
            0.0 if distributed_fastpath else
            ((m.built[e] if service_leaf_building[e] == edges[e]['node_v'] else 0.0)
             if e in service_leaf_edges else m._direction_var[e,h]))
    if distributed_fastpath:
        m.commodity = p.Expression(m.E, rule=lambda *_: 0.0)
        m.commodity_supply = p.Expression(m.S, rule=lambda *_: 0.0)
        m.tes_built = p.Expression(m.S, rule=lambda *_: 0.0)
        m.tes_energy = p.Expression(m.S, rule=lambda *_: 0.0)
        m.tes_charge_capacity = p.Expression(m.S, rule=lambda *_: 0.0)
        m.tes_discharge_capacity = p.Expression(m.S, rule=lambda *_: 0.0)
        m.tes_power_cost_capacity = p.Expression(m.S, rule=lambda *_: 0.0)
        m.charge = p.Expression(m.S, m.HOURS, rule=lambda *_: 0.0)
        m.discharge = p.Expression(m.S, m.HOURS, rule=lambda *_: 0.0)
        m.soc = p.Expression(m.S, m.HOURS, rule=lambda *_: 0.0)
        m.charging = p.Expression(m.S, m.HOURS, rule=lambda *_: 0.0)
    else:
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
    if not distributed_fastpath:
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
        if not distributed_fastpath:
            c(sum(m.access_selected[o] for o in building_options[b]) == m.connected[b])
            c(sum(m.built[e] for e, _ in incident[b]) == m.connected[b])
        if d.mode == 'hybrid':
            c(m.connected[b] <= sum(m.station_built[s] for s in stations))
        if d.mode != 'central':
            c(m.local_capacity[b] <= local.capacity_max_kW*m.local_installed[b])
            c(m.local_capacity[b] >= local.capacity_min_kW*m.local_installed[b])
            required_capacity = max(
                (1+d.peak_capacity_margin_fraction)*d.heat_demand_kW[b,h]/ratio(local.technology_id,h)
                for h in d.hours)
            c(m.local_capacity[b] >= required_capacity*m.local_installed[b])
        if not deterministic_dispatch:
            for h in d.hours:
                demand = d.heat_demand_kW[b,h]
                if d.mode != 'central':
                    c(m.local_heat[b,h] <= m.local_capacity[b]*ratio(local.technology_id,h))
                if not distributed_fastpath:
                    c(m.network_heat[b,h] <= demand*m.connected[b])
                c(m.network_heat[b,h]+m.local_heat[b,h]+m.unserved_heat_kW[b,h] == demand)
    if d.mode == 'central':
        c(sum(m.station_built[s] for s in stations) == 1)
    m.edge_capacity = p.Expression(m.E, rule=lambda _,e: sum(levels[k].capacity_kW_th*m.grade[e,k] for k in levels))
    m.edge_loss = p.Expression(m.E, rule=lambda _,e: edges[e]['length_m']*sum(levels[k].pair_loss_kW_per_route_m*m.grade[e,k] for k in levels))
    if uniform_pumping:
        m.flow = p.Expression(m.E,m.HOURS, rule=lambda _,e,h: m.forward[e,h]-m.reverse[e,h])
        m.abs_flow = p.Expression(m.E,m.HOURS, rule=lambda _,e,h: m.forward[e,h]+m.reverse[e,h])
        m.pump = p.Expression(m.E,m.HOURS, rule=lambda _,e,h:
            edges[e]['length_m']*uniform_pumping_rate*m.abs_flow[e,h])
    else:
        m.flow = p.Expression(m.E,m.HOURS, rule=lambda _,e,h:
            sum(m.forward[e,k,h]-m.reverse[e,k,h] for k in levels))
        m.abs_flow = p.Expression(m.E,m.HOURS, rule=lambda _,e,h:
            sum(m.forward[e,k,h]+m.reverse[e,k,h] for k in levels))
        m.pump = p.Expression(m.E,m.HOURS, rule=lambda _,e,h: edges[e]['length_m']*sum(
            levels[k].pumping_kWh_e_per_kWh_th_m*(m.forward[e,k,h]+m.reverse[e,k,h]) for k in levels))
    m.direction_relaxation_lower = p.Constraint(
        m.RELAXED_DIRECTION_PAIRS,
        rule=lambda _, e, h: m.abs_flow[e, h] >= m.edge_loss[e]/2,
    )
    big_m = max(x.capacity_kW_th for x in levels.values())
    if not distributed_fastpath:
        for e, edge in edges.items():
            c(sum(m.grade[e,k] for k in levels) == m.built[e])
            c(m.built[e] <= sum(m.station_built[s] for s in stations))
            for o in edge_options[e]:
                c(m.built[e] >= m.access_selected[o])
            if edge['edge_type'] != 'road':
                c(m.built[e] <= sum(m.access_selected[o] for o in edge_options[e]))
            c(m.commodity[e] <= len(d.demand_nodes)*m.built[e])
            c(m.commodity[e] >= -len(d.demand_nodes)*m.built[e])
            if e in service_leaf_edges:
                b = service_leaf_building[e]
                peak_demand = max(d.heat_demand_kW[b,h] for h in d.hours)
                # Every hourly sending-port constraint reduces to this one
                # static peak inequality: demand*built + full pair loss <= cap.
                c(peak_demand*m.built[e] + m.edge_loss[e] <= m.edge_capacity[e])
                continue
            for h in d.hours:
                forward = m.forward[e,h] if uniform_pumping else sum(m.forward[e,k,h] for k in levels)
                reverse = m.reverse[e,h] if uniform_pumping else sum(m.reverse[e,k,h] for k in levels)
                if (e, h) in exact_direction_pair_set:
                    c(forward <= big_m*m.direction[e,h])
                    c(reverse <= big_m*(m.built[e]-m.direction[e,h]))
                    c(m.abs_flow[e,h] + m.edge_loss[e]/2 <= m.edge_capacity[e])
                    c(forward >= m.edge_loss[e]/2 - big_m*(1-m.direction[e,h]))
                    c(reverse >= m.edge_loss[e]/2 - big_m*m.direction[e,h])
                else:
                    c(m.abs_flow[e,h] + m.edge_loss[e]/2 <= m.edge_capacity[e])
                if not uniform_pumping:
                    for k, level in levels.items():
                        c(m.forward[e,k,h]+m.reverse[e,k,h] <= level.capacity_kW_th*m.grade[e,k])
        for n in nodes:
            balance = sum(sign*m.commodity[e] for e,sign in incident[n])+sum(m.commodity_supply[s] for s in sources[n])
            c(balance == (m.connected[n] if n in d.demand_nodes else 0))
            if n in leaf_buildings:
                continue
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
