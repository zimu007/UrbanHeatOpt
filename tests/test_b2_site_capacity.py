from dataclasses import replace

import pytest
from pyomo.environ import value

from tests.test_road_v2_core import shared_case
from urbanheatopt.data.road_builder import load_case, save_case
from urbanheatopt.model.compact import build_compact_model, build_compact_tree_design
from urbanheatopt.model.road_core import build_road_model
from urbanheatopt.optimization.adapters.site_capacity_v1 import (
    apply_b2_capacity_input, build_b2_capacity_input,
)


def _boundary(case, *, s1_allowed=('hp', 'gas'), hp_max=1000., gas_max=5000.,
              electric_max=400., scope='central_hp_only', pipe_missing=False,
              local=False):
    sites = []
    for site in case.network['sites']:
        allowed = s1_allowed if site['site_id'] == 'S1' else ('hp', 'gas')
        maxima = {tech: (hp_max if tech == 'hp' else gas_max * .94) for tech in allowed}
        sites.append(dict(
            site_id=site['site_id'], allowed_technology_ids=allowed,
            total_heat_capacity_max_kW_th=(hp_max if hp_max is not None else 1000.) + gas_max * .94,
            technology_capacity_max_kW_th=maxima,
            electricity_connection_max_kW_e=electric_max if 'hp' in allowed else None,
            electricity_connection_scope=scope if 'hp' in allowed else None,
            gas_connection_max_kW_LHV=gas_max if 'gas' in allowed else None,
            source='approved synthetic fixture', status='verified', evidence_id='B2-SITE-1'))
    pipes = [dict(pipe_type_id=row.pipe_type_id,
                  capacity_kW_th=None if pipe_missing else row.capacity_kW_th,
                  source='approved synthetic fixture', status='verified', evidence_id='B2-PIPE-1')
             for row in case.pipe_designs]
    local_limits = {building: 200. for building in case.common.demand_nodes} if local else None
    local_evidence = (dict(source='approved synthetic fixture', status='verified',
                           evidence_id='B2-LOCAL-1') if local else None)
    return build_b2_capacity_input(
        site_records=sites, pipe_records=pipes,
        local_hp_capacity_max_kW_th_by_building=local_limits,
        local_hp_evidence=local_evidence)


def test_disallowed_technology_is_fixed_off_and_capacity_limit_is_explicit():
    case = apply_b2_capacity_input(shared_case(), _boundary(shared_case(), s1_allowed=('hp',)))
    model = build_road_model(case)
    assert model.installed['S1', 'gas'].fixed and value(model.installed['S1', 'gas']) == 0
    assert model.capacity['S1', 'gas'].fixed and value(model.capacity['S1', 'gas']) == 0
    compact = build_compact_model(case, design=build_compact_tree_design(case, 'S1'))
    assert value(compact.installed['S1', 'gas']) == 0
    model.station_built['S1'].set_value(1)
    model.installed['S1', 'hp'].set_value(1)
    model.capacity['S1', 'hp'].set_value(1200)
    row = model.b2_site_capacity_limit['S1', 'hp']
    assert value(row.body) > value(row.upper)


def test_boiler_capacity_and_lhv_gas_input_limits_are_enforced():
    base = shared_case()
    case = apply_b2_capacity_input(base, _boundary(base, gas_max=5000.))
    model = build_road_model(case)
    model.station_built['S1'].set_value(1)
    model.installed['S1', 'gas'].set_value(1)
    model.capacity['S1', 'gas'].set_value(4701)
    assert value(model.b2_site_capacity_limit['S1', 'gas'].body) > 0
    model.heat['S1', 'gas', 1].set_value(4700)
    row = model.b2_gas_connection_limit['S1', 1]
    assert value(row.body) == pytest.approx(0)
    model.heat['S1', 'gas', 1].set_value(4701)
    assert value(row.body) > 0


def test_total_station_heat_capacity_is_not_two_independent_maxima():
    base = shared_case()
    records = _boundary(base, hp_max=1000., gas_max=5000.)
    sites = []
    for row in records.sites:
        sites.append(dict(
            site_id=row.site_id,
            allowed_technology_ids=row.allowed_technology_ids,
            total_heat_capacity_max_kW_th=1000.,
            technology_capacity_max_kW_th=row.technology_capacity_max_kW_th,
            electricity_connection_max_kW_e=row.electricity_connection_max_kW_e,
            electricity_connection_scope=row.electricity_connection_scope,
            gas_connection_max_kW_LHV=row.gas_connection_max_kW_LHV,
            source=row.evidence.source,
            status=row.evidence.status,
            evidence_id=row.evidence.evidence_id,
        ))
    boundary = build_b2_capacity_input(
        site_records=sites,
        pipe_records=[dict(
            pipe_type_id=row.pipe_type_id,
            capacity_kW_th=row.capacity_kW_th,
            source=row.evidence.source,
            status=row.evidence.status,
            evidence_id=row.evidence.evidence_id,
        ) for row in records.pipes],
    )
    case = apply_b2_capacity_input(base, boundary)
    road = build_road_model(case)
    road.station_built['S1'].set_value(1)
    road.capacity['S1', 'hp'].set_value(600)
    road.capacity['S1', 'gas'].set_value(500)
    assert value(road.b2_site_total_heat_capacity_limit['S1'].body) > 0
    compact = build_compact_model(case, design=build_compact_tree_design(case, 'S1'))
    compact._central_capacity['hp'].set_value(600)
    compact._central_capacity['gas'].set_value(500)
    assert value(compact.b2_site_total_heat_capacity_limit.body) > 0


def test_electricity_connection_uses_central_hp_input_power():
    base = shared_case()
    case = apply_b2_capacity_input(base, _boundary(base, electric_max=25.))
    model = build_road_model(case)
    model.station_built['S1'].set_value(1)
    model.heat['S1', 'hp', 1].set_value(100)
    row = model.b2_electricity_connection_limit['S1', 1]
    assert value(row.body) == pytest.approx(0)
    model.heat['S1', 'hp', 1].set_value(104)
    assert value(row.body) == pytest.approx(1)


@pytest.mark.parametrize('change,match', [
    ({'hp_max': None}, 'capacity maximum'),
    ({'scope': None}, 'scope is unresolved'),
    ({'pipe_missing': True}, 'pipe thermal capacity'),
])
def test_missing_or_semantically_unapproved_boundaries_fail_closed(change, match):
    base = shared_case()
    kwargs = dict(hp_max=1000., scope='central_hp_only', pipe_missing=False)
    kwargs.update(change)
    with pytest.raises((ValueError, TypeError), match=match):
        apply_b2_capacity_input(base, _boundary(base, **kwargs))


def test_local_limits_are_required_for_distributed_and_not_replaced_by_infinity():
    base = shared_case('distributed')
    with pytest.raises(ValueError, match='local HP capacity'):
        apply_b2_capacity_input(base, _boundary(base))
    case = apply_b2_capacity_input(base, _boundary(base, local=True))
    assert case.b2_capacity.local_hp_capacity_max_kW_th_by_building['A'] == 200


def test_compact_and_road_consume_the_same_site_and_gas_limits():
    base = shared_case()
    case = apply_b2_capacity_input(base, _boundary(base, hp_max=1000., gas_max=5000.))
    road = build_road_model(case)
    compact = build_compact_model(case, design=build_compact_tree_design(case, 'S1'))
    road.station_built['S1'].set_value(1)
    road.installed['S1', 'hp'].set_value(1)
    road.capacity['S1', 'hp'].set_value(1000)
    compact._central_capacity['hp'].set_value(1000)
    assert value(road.b2_site_capacity_limit['S1', 'hp'].body) == pytest.approx(0)
    assert value(compact.b2_site_capacity_limit['hp'].body) == pytest.approx(0)
    compact._central_hp_heat[1].set_value(0)
    assert value(compact.b2_gas_connection_limit[1].upper) == pytest.approx(0)


def test_b2_boundary_survives_case_round_trip(tmp_path):
    base = shared_case()
    case = apply_b2_capacity_input(base, _boundary(base))
    path = tmp_path / 'case.json'
    save_case(case, path)
    restored = load_case(path)
    assert restored.b2_capacity == case.b2_capacity
