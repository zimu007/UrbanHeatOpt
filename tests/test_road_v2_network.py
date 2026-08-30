import pytest
from shapely.geometry import Polygon, LineString
import geopandas as gpd

from competition.road_joint_v2.network import (
    RoadNetworkError, atomize_snapshot, access_angle, validate_network,
)


def snapshot():
    return {'elements': [
        {'type': 'node', 'id': i, 'lon': x, 'lat': y}
        for i, x, y in [(1, 114.4, 30.4), (2, 114.401, 30.4), (3, 114.402, 30.4),
                         (4, 114.401, 30.401), (5, 114.401, 30.399)]
    ] + [
        {'type': 'way', 'id': 10, 'nodes': [1, 2, 3], 'tags': {'highway': 'primary'}},
        {'type': 'way', 'id': 11, 'nodes': [4, 2, 5], 'tags': {'highway': 'secondary'}},
        {'type': 'way', 'id': 12, 'nodes': [3, 2, 1], 'tags': {'highway': 'primary'}},
        {'type': 'way', 'id': 13, 'nodes': [1, 4], 'tags': {'highway': 'tertiary'}},
        {'type': 'way', 'id': 14, 'nodes': [3, 4], 'tags': {'highway': 'primary', 'bridge': 'yes'}},
    ]}


def buildings():
    return gpd.GeoDataFrame({'building_id': ['A', 'B'], 'geometry': [
        Polygon([(114.4002,30.4003),(114.4004,30.4003),(114.4004,30.4005),(114.4002,30.4005)]),
        Polygon([(114.4015,30.4003),(114.4017,30.4003),(114.4017,30.4005),(114.4015,30.4005)]),
    ]}, crs='EPSG:4326')


def test_atomic_roads_leaf_buildings_and_determinism():
    first = atomize_snapshot(snapshot(), buildings(), {'A': 100, 'B': 200}, candidate_count=2)
    second = atomize_snapshot(snapshot(), buildings(), {'B': 200, 'A': 100}, candidate_count=2)
    assert first == second
    validate_network(first)
    assert len(first['sites']) == 2
    for site in first['sites']:
        assert site['site_id'] != site['attachment_node_id']
    for node in ('A', 'B'):
        assert sum(node in (e['node_u'], e['node_v']) for e in first['edges']) == 1
    assert all(e['highway'] in ('primary','secondary','primary_link','secondary_link','service_branch') for e in first['edges'])
    assert first['metadata']['duplicate_segments_removed'] == 2
    assert first['metadata']['construction_feasibility_verified'] is False


def test_access_angle_is_acute_angle_only():
    assert access_angle(LineString([(0,0),(0,1)]), LineString([(-1,0),(1,0)])) == pytest.approx(90)
    assert access_angle(LineString([(0,0),(1,.1)]), LineString([(-1,0),(1,0)])) < 45


def test_no_ground_corridor_fails_explicitly():
    source = snapshot()
    for item in source['elements']:
        if item['type'] == 'way':
            item['tags']['tunnel'] = 'yes'
    with pytest.raises(RoadNetworkError, match='道路'):
        atomize_snapshot(source, buildings(), {'A': 1, 'B': 1}, candidate_count=2)


def test_invalid_duplicate_edge_contract():
    network = atomize_snapshot(snapshot(), buildings(), {'A':1,'B':1}, candidate_count=2)
    network['edges'].append(dict(network['edges'][0]))
    with pytest.raises(RoadNetworkError, match='重复'):
        validate_network(network)


def test_building_occlusion_produces_identified_failure():
    source=buildings()
    outer=source.geometry.iloc[0]
    source.loc[1,'geometry']=outer.buffer(-.00003)
    with pytest.raises(RoadNetworkError) as caught:
        atomize_snapshot(snapshot(),source,{'A':1,'B':1},candidate_count=2)
    assert any(x['building_id']=='B' for x in caught.value.failures)
