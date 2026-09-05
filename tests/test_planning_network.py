"""Planning geometry, not trench/depth/construction feasibility tests."""
import copy

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Polygon
from pyproj import Transformer

from urbanheatopt.spatial.atomic_network import atomize_snapshot, validate_network, RoadNetworkError


def inputs():
    inverse = Transformer.from_crs('EPSG:32650', 'EPSG:4326', always_xy=True)
    coordinates = {1: (250000, 3370000), 2: (250300, 3370000),
                   3: (250000, 3370100), 4: (250300, 3370100),
                   5: (250000, 3370200), 6: (250300, 3370200)}
    snapshot = {'elements': [dict(type='node', id=k, lon=inverse.transform(*xy)[0],
                                lat=inverse.transform(*xy)[1]) for k, xy in coordinates.items()]
                + [dict(type='way', id=10, nodes=[1, 2], tags={'highway': 'tertiary'}),
                   dict(type='way', id=11, nodes=[3, 4], tags={'highway': 'unclassified'}),
                   dict(type='way', id=12, nodes=[5, 6], tags={'highway': 'secondary'}),
                   dict(type='way', id=13, nodes=[1, 3, 5], tags={'highway': 'secondary'})]}
    buildings = gpd.GeoDataFrame({'building_id': ['A'], 'geometry': [
        Polygon([(250100, 3369970), (250110, 3369970), (250110, 3369980), (250100, 3369980)])]}, crs='EPSG:32650')
    return snapshot, buildings


def test_planning_relaxes_road_class_angle_and_crossing_without_randomness():
    source, buildings = inputs()
    first = atomize_snapshot(source, buildings, {'A': 1}, candidate_count=1)
    assert first == atomize_snapshot(source, buildings, {'A': 1}, candidate_count=1)
    assert first['metadata']['network_policy_version'] == 'planning_corridor_2.1.0'
    assert first['metadata']['installation_concept'] == 'underground_planning'
    assert not first['metadata']['construction_details_modeled']
    assert first['metadata']['branch_angle_limit_deg'] is None
    assert len(first['access_options']) == 3
    assert any(e['highway'] == 'tertiary' for e in first['edges'])
    assert any(o['crossed_corridor_count'] > 0 for o in first['access_options'])
    validate_network(first)


def test_obstacles_are_not_additional_heat_demands():
    source, buildings = inputs()
    obstacles = gpd.GeoDataFrame({'building_id': ['non_heated'], 'geometry': [
        Polygon([(250098, 3369985), (250112, 3369985), (250112, 3369995), (250098, 3369995)])]}, crs=buildings.crs)
    network = atomize_snapshot(source, buildings, {'A': 1}, candidate_count=1, obstacles=obstacles)
    assert {n['node_id'] for n in network['nodes'] if n['node_type'] == 'building'} == {'A'}
    assert network['metadata']['obstacle_building_count'] == 2
    for option in network['access_options']:
        assert not LineString(option['coordinates']).intersects(obstacles.geometry.iloc[0].buffer(-1e-7))


def test_explicit_forbidden_area_still_blocks_but_missing_layer_does_not():
    source, buildings = inputs()
    area = gpd.GeoDataFrame({'area_id': ['X'], 'geometry': [buildings.geometry.iloc[0].buffer(5)]}, crs=buildings.crs)
    with pytest.raises(RoadNetworkError):
        atomize_snapshot(source, buildings, {'A': 1}, candidate_count=1, forbidden_areas=area)
    network = atomize_snapshot(source, buildings, {'A': 1}, candidate_count=1)
    assert network['metadata']['forbidden_area_coverage'] == 'not_provided'


def test_permitted_corridor_is_consumed_and_bridge_not_auto_connected():
    source, buildings = inputs()
    for row in source['elements']:
        if row['type'] == 'way':
            row['tags']['bridge'] = 'yes'
    corridors = gpd.GeoDataFrame({'corridor_id': ['allowed_1'], 'geometry': [
        LineString([(250000, 3370000), (250300, 3370000)])]}, crs=buildings.crs)
    network = atomize_snapshot(source, buildings, {'A': 1}, candidate_count=1, corridors=corridors)
    assert all(e['highway'] in ('permitted_corridor', 'service_branch') for e in network['edges'])
    assert network['metadata']['permitted_corridor_count'] == 1


def test_access_paths_have_physical_lengths_and_reject_tampering():
    source, buildings = inputs()
    net = atomize_snapshot(source, buildings, {'A': 1}, candidate_count=1)
    edges = {e['edge_id']: e for e in net['edges']}
    for option in net['access_options']:
        assert sum(edges[e]['length_m'] for e in option['edge_ids']) == pytest.approx(option['length_m'], abs=1e-6)
    broken = copy.deepcopy(net)
    broken['access_options'][0]['edge_ids'] = []
    with pytest.raises(RoadNetworkError):
        validate_network(broken)


def test_collinear_candidate_branches_share_unique_physical_atoms():
    source, buildings = inputs()
    net = atomize_snapshot(source, buildings, {'A': 1}, candidate_count=1)
    options = net['access_options']
    assert any(set(a['edge_ids']) & set(b['edge_ids']) for i, a in enumerate(options) for b in options[i+1:])
    assert len(net['edges']) == len({e['edge_id'] for e in net['edges']})
