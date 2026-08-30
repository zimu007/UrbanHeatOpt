"""Deterministic road atoms in metres, never terminal-to-terminal path edges.

OSM shared node IDs define connectivity. No planar intersection is invented.
The old generator is explicit legacy regression only. The public generator
uses the versioned planning policy (roads, crossings and up to three accesses).
"""
from __future__ import annotations

from hashlib import sha256
import json
from math import acos, degrees, hypot, isfinite
from pathlib import Path

import geopandas as gpd
import networkx as nx
from pyproj import Transformer
from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import nearest_points, substring

ALLOWED_HIGHWAYS = {"primary", "primary_link", "secondary", "secondary_link"}
CRS = "EPSG:32650"


class RoadNetworkError(ValueError):
    def __init__(self, message, failures=None):
        super().__init__(message)
        self.failures = failures or []


def _id(prefix, value):
    return prefix + sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:20]


def access_angle(branch: LineString, tangent: LineString) -> float:
    bx, by = branch.coords[-1][0] - branch.coords[0][0], branch.coords[-1][1] - branch.coords[0][1]
    tx, ty = tangent.coords[-1][0] - tangent.coords[0][0], tangent.coords[-1][1] - tangent.coords[0][1]
    denominator = hypot(bx, by) * hypot(tx, ty)
    if denominator == 0:
        raise RoadNetworkError("零长度支线/道路切线")
    return degrees(acos(min(1.0, abs(bx * tx + by * ty) / denominator)))


def _eligible(tags):
    return (tags.get("highway") in ALLOWED_HIGHWAYS
            and tags.get("bridge", "no") in {"no", "false", "0"}
            and tags.get("tunnel", "no") in {"no", "false", "0"}
            and tags.get("layer", "0") in {"0", "0.0"}
            and tags.get("location", "surface") not in {"underground", "overground"})


def atomize_snapshot_legacy(snapshot: dict, buildings: gpd.GeoDataFrame, annual_heat: dict,
                     *, candidate_count: int = 5) -> dict:
    if buildings.crs is None or buildings.building_id.duplicated().any():
        raise RoadNetworkError("建筑CRS缺失或ID重复")
    projected = buildings.to_crs(CRS).sort_values("building_id")
    polygons = dict(zip(projected.building_id.astype(str), projected.geometry))
    if (not polygons or set(polygons) != set(annual_heat) or
            any(not p.is_valid or p.is_empty or p.geom_type not in {"Polygon", "MultiPolygon"} for p in polygons.values())):
        raise RoadNetworkError("建筑几何非法或负荷ID不匹配")
    if any(not isfinite(float(v)) or v < 0 for v in annual_heat.values()) or sum(annual_heat.values()) <= 0:
        raise RoadNetworkError("年度负荷权重必须有限非负且总和为正")
    transformer = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
    coords = {int(row['id']): transformer.transform(row['lon'], row['lat'])
              for row in snapshot.get('elements', []) if row.get('type') == 'node'}
    raw = {}
    duplicates, excluded = 0, []
    for way in sorted((x for x in snapshot.get('elements', []) if x.get('type') == 'way'), key=lambda x: x['id']):
        if not _eligible(way.get('tags', {})):
            excluded.append(way['id'])
            continue
        for u, v in zip(way['nodes'], way['nodes'][1:]):
            if u not in coords or v not in coords:
                raise RoadNetworkError(f"OSM way {way['id']} 缺节点坐标")
            if u == v or coords[u] == coords[v]:
                raise RoadNetworkError(f"OSM way {way['id']} 零长度道路段")
            # Directional copies of exactly the same physical segment are one edge.
            key = tuple(sorted((coords[u], coords[v])))
            if key in raw:
                duplicates += 1
                raw[key]['osm_way_ids'].add(str(way['id']))
                continue
            a, b = sorted((u, v))
            raw[key] = dict(node_u=f'road_{a}', node_v=f'road_{b}',
                            geometry=LineString([coords[a], coords[b]]),
                            osm_way_ids={str(way['id'])}, highway=way['tags']['highway'])
    roads = sorted(raw.values(), key=lambda x: (x['node_u'], x['node_v']))
    if not roads:
        raise RoadNetworkError("没有合格的地面主次干路道路")
    cuts = {i: [(0.0, road['node_u']), (road['geometry'].length, road['node_v'])] for i, road in enumerate(roads)}
    nodes = {}
    for road in roads:
        for node, xy in [(road['node_u'], road['geometry'].coords[0]), (road['node_v'], road['geometry'].coords[-1])]:
            nodes[node] = dict(node_id=node, node_type='road', x_m=xy[0], y_m=xy[1])

    def add_cut(i, distance):
        for existing_distance, node in cuts[i]:
            if abs(distance - existing_distance) <= 1e-7:
                return node
        point = roads[i]['geometry'].interpolate(distance)
        node = _id('attachment_', [i, point.x, point.y])
        nodes[node] = dict(node_id=node, node_type='road_attachment', x_m=point.x, y_m=point.y)
        cuts[i].append((distance, node))
        return node

    branches, failures, protected = [], [], set()
    for bid, polygon in polygons.items():
        candidates = sorted(range(len(roads)), key=lambda i: (roads[i]['geometry'].distance(polygon), i))
        reasons = []
        for i in candidates:
            road = roads[i]['geometry']
            endpoint, road_point = nearest_points(polygon, road)
            branch = LineString([road_point, endpoint])
            if branch.length <= 1e-7:
                reasons.append('road_intersects_building')
                continue
            angle = access_angle(branch, road)
            if angle + 1e-8 < 45:
                reasons.append('access_angle_below_45')
                continue
            if any(branch.intersects(other.buffer(-1e-7)) for other in polygons.values()):
                reasons.append('branch_crosses_building')
                continue
            if any(branch.crosses(other['geometry']) for other in roads):
                reasons.append('branch_crosses_other_corridor')
                continue
            attachment = add_cut(i, road.project(road_point))
            protected.add(attachment)
            nodes[bid] = dict(node_id=bid, node_type='building', x_m=endpoint.x, y_m=endpoint.y)
            branches.append(dict(node_u=attachment, node_v=bid, coordinates=list(branch.coords),
                length_m=branch.length, edge_type='building_service', highway='service_branch',
                osm_way_ids=sorted(roads[i]['osm_way_ids']), access_angle_deg=angle))
            break
        else:
            failures.append(dict(building_id=bid, reasons=sorted(set(reasons)),
                                 centroid=[polygon.centroid.x, polygon.centroid.y]))
    if failures:
        raise RoadNetworkError(f"当前最近直线支线生成器对{len(failures)}栋建筑未找到合格接入；不代表不存在折线路径，禁止静默添加捷径", failures)

    total = sum(annual_heat.values())
    weighted = Point(sum(polygons[b].centroid.x * annual_heat[b] for b in polygons) / total,
                     sum(polygons[b].centroid.y * annual_heat[b] for b in polygons) / total)
    xmin, ymin, xmax, ymax = projected.total_bounds
    targets = [weighted] + [Point(xmin + fx*(xmax-xmin), ymin + fy*(ymax-ymin))
                           for fx, fy in ((.25,.25),(.75,.25),(.25,.75),(.75,.75))]
    if isinstance(candidate_count, bool) or not 1 <= candidate_count <= len(targets):
        raise RoadNetworkError("候选站数量必须为1..5")
    sites, occupied = [], set()
    for index, target in enumerate(targets[:candidate_count], 1):
        for i in sorted(range(len(roads)), key=lambda j: (roads[j]['geometry'].distance(target), j)):
            line = roads[i]['geometry']
            point = line.interpolate(line.project(target))
            if any(polygon.covers(point) for polygon in polygons.values()):
                continue
            position = (point.x, point.y)
            if position in occupied:
                continue
            node = add_cut(i, line.project(target))
            protected.add(node)
            occupied.add(position)
            sites.append(dict(site_id=f'candidate_station_{index:02d}', attachment_node_id=node,
                x_m=point.x, y_m=point.y, candidate_source='deterministic_heat_centroid_extent_snapped',
                station_service_length_m=0.0, station_footprint_verified=False))
            break
        else:
            raise RoadNetworkError("无法得到5个不同且位于建筑外的测试候选站")
    edges = []
    for i, road in enumerate(roads):
        splits = sorted(cuts[i])
        for (start, u), (end, v) in zip(splits, splits[1:]):
            line = substring(road['geometry'], start, end)
            edges.append(dict(node_u=u, node_v=v, coordinates=list(line.coords), length_m=line.length,
                              edge_type='road', highway=road['highway'], osm_way_ids=sorted(road['osm_way_ids'])))
    # Contract an unbranched road node only; never remove a route alternative.
    graph = nx.MultiGraph()
    for index, edge in enumerate(edges):
        graph.add_edge(edge['node_u'], edge['node_v'], key=index, record=edge)
    for node in sorted(list(graph)):
        if node in protected or graph.degree(node) != 2:
            continue
        incident = list(graph.edges(node, keys=True, data=True))
        a, b = incident[0][1], incident[1][1]
        e1, e2 = incident[0][3]['record'], incident[1][3]['record']
        if a == b or e1['highway'] != e2['highway']:
            continue
        xy1 = e1['coordinates'] if e1['node_u'] == a else e1['coordinates'][::-1]
        xy2 = e2['coordinates'] if e2['node_u'] == node else e2['coordinates'][::-1]
        merged = dict(node_u=a, node_v=b, coordinates=xy1 + xy2[1:],
            length_m=e1['length_m'] + e2['length_m'], edge_type='road', highway=e1['highway'],
            osm_way_ids=sorted(set(e1['osm_way_ids'] + e2['osm_way_ids'])))
        graph.remove_node(node)
        graph.add_edge(a, b, record=merged)
        nodes.pop(node)
    edges = [item['record'] for _, _, item in graph.edges(data=True)] + branches
    for edge in edges:
        if edge['node_u'] > edge['node_v']:
            edge['node_u'], edge['node_v'] = edge['node_v'], edge['node_u']
            edge['coordinates'] = edge['coordinates'][::-1]
        edge['edge_id'] = _id('edge_', [edge['node_u'], edge['node_v'], edge['coordinates']])
        edge['route_basis'] = 'supply_return_pair_route_m'
    result = dict(contract_version='road_atomic_network_2.0.0', crs=CRS,
        nodes=sorted(nodes.values(), key=lambda x: x['node_id']),
        edges=sorted(edges, key=lambda x: x['edge_id']), sites=sites,
        metadata=dict(road_constrained=True, greenbelt_alignment_assumed=True,
            construction_feasibility_verified=False, spatial_status='PROVISIONAL_OSM_ROADSIDE_CORRIDOR',
            duplicate_segments_removed=duplicates, excluded_way_ids=excluded,
            min_branch_angle_deg=45, station_footprint='zero_length_corridor_attachment_test_only'))
    validate_network(result)
    result['network_sha256'] = sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def atomize_snapshot(snapshot, buildings, annual_heat, *, candidate_count=5,
                     obstacles=None, corridors=None, forbidden_areas=None, policy=None):
    from competition.road_joint_v2.planning_network import generate
    return generate(snapshot, buildings, annual_heat, candidate_count=candidate_count,
                    obstacles=obstacles, corridors=corridors, forbidden_areas=forbidden_areas, policy=policy)


def access_options(network):
    """Explicit historical one-branch fixtures remain valid regression inputs."""
    if 'access_options' in network:
        return network['access_options']
    buildings = {n['node_id'] for n in network['nodes'] if n['node_type'] == 'building'}
    result = []
    for edge in network['edges']:
        if edge['edge_type'] != 'building_service':
            continue
        b = next((n for n in (edge['node_u'], edge['node_v']) if n in buildings), None)
        if b is None:
            raise RoadNetworkError('历史支线缺少建筑端点')
        attachment = edge['node_v'] if b == edge['node_u'] else edge['node_u']
        xy = edge['coordinates'] if b == edge['node_v'] else edge['coordinates'][::-1]
        result.append(dict(option_id='legacy_'+edge['edge_id'], building_id=b,
            attachment_node_id=attachment, edge_ids=[edge['edge_id']], coordinates=xy,
            building_boundary_point=xy[-1], candidate_rank=1, length_m=edge['length_m']))
    return result


def read_optional_layers(delivery_root, *, obstacle_buildings=None, allowed_corridors=None, forbidden_areas=None):
    """Existing 63-building GIS is an obstacle layer, never extra demand."""
    delivery_root = Path(delivery_root)
    default_obstacles = delivery_root/'光谷软件园GIS成果'/'buildings_clean_named.geojson'
    choices = dict(obstacles=Path(obstacle_buildings) if obstacle_buildings else default_obstacles if default_obstacles.exists() else None,
                   corridors=Path(allowed_corridors) if allowed_corridors else None,
                   forbidden_areas=Path(forbidden_areas) if forbidden_areas else None)
    layers, paths = {}, []
    for name, path in choices.items():
        if path is not None:
            path = path.resolve(strict=True)
            layers[name] = gpd.read_file(path)
            paths.append(path)
    return layers, paths


def validate_network(network: dict) -> None:
    if network.get('crs') != CRS:
        raise RoadNetworkError(f"路网必须采用米制投影{CRS}")
    nodes = {n['node_id']: n for n in network['nodes']}
    if len(nodes) != len(network['nodes']):
        raise RoadNetworkError('重复节点ID')
    if any(not isfinite(n['x_m']) or not isfinite(n['y_m']) for n in nodes.values()):
        raise RoadNetworkError('节点坐标非有限')
    edge_ids, physical = set(), set()
    graph = nx.Graph()
    graph.add_nodes_from(nodes)
    for edge in network['edges']:
        u, v = edge['node_u'], edge['node_v']
        line = LineString(edge['coordinates'])
        shape_key = min(tuple(line.coords), tuple(line.coords)[::-1])
        if edge['edge_id'] in edge_ids or shape_key in physical:
            raise RoadNetworkError('重复物理边或ID')
        if u not in nodes or v not in nodes or u == v or not line.is_simple or line.length <= 0:
            raise RoadNetworkError('非法边端点/几何')
        if abs(line.length - edge['length_m']) > 1e-6:
            raise RoadNetworkError('长度与几何不符')
        for nid, xy in [(u, line.coords[0]), (v, line.coords[-1])]:
            node = nodes[nid]
            if node.get('display_point_only') and node['node_type'] == 'building':
                distance = shape(node['geometry']).boundary.distance(Point(xy))
            else:
                distance = Point(xy).distance(Point(node['x_m'], node['y_m']))
            if distance > 1e-6:
                raise RoadNetworkError('端点坐标不匹配')
        edge_ids.add(edge['edge_id'])
        physical.add(shape_key)
        graph.add_edge(u, v)
    sites = network['sites']
    if not sites or len({s['site_id'] for s in sites}) != len(sites):
        raise RoadNetworkError('候选站缺失或ID重复')
    buildings = {key for key, node in nodes.items() if node['node_type'] == 'building'}
    if not buildings or ('access_options' not in network and any(graph.degree(node) != 1 for node in buildings)):
        raise RoadNetworkError('建筑必须是独立叶节点')
    options = access_options(network)
    by_building = {b: [] for b in buildings}
    option_ids = set()
    edge_lookup = {e['edge_id']: e for e in network['edges']}
    for option in options:
        b, oid = option['building_id'], option['option_id']
        if b not in buildings or oid in option_ids or not option['edge_ids']:
            raise RoadNetworkError('接入候选ID、建筑或物理路径非法')
        option_ids.add(oid)
        by_building[b].append(option)
        current = option['attachment_node_id']
        if current not in nodes or current in buildings:
            raise RoadNetworkError('接入候选必须从道路开始')
        visited = {current}
        length = 0.
        for eid in option['edge_ids']:
            edge = edge_lookup.get(eid)
            if edge is None or current not in (edge['node_u'], edge['node_v']):
                raise RoadNetworkError('接入候选路径不连续')
            current = edge['node_v'] if current == edge['node_u'] else edge['node_u']
            if current in visited or (current in buildings and current != b):
                raise RoadNetworkError('接入候选穿过建筑或包含环路')
            visited.add(current)
            length += edge['length_m']
        if current != b or abs(length-option['length_m']) > 1e-6:
            raise RoadNetworkError('接入候选终点或物理长度不匹配')
    if any(not 1 <= len(row) <= 3 for row in by_building.values()):
        raise RoadNetworkError('每栋必须具有1..3条合格接入候选')
    # Candidate connectivity must NEVER use a building as a bridge between
    # roads: the alternatives are mutually exclusive in the actual model.
    road_graph = nx.Graph()
    road_graph.add_nodes_from(n for n in nodes if n not in buildings)
    road_graph.add_edges_from((e['node_u'], e['node_v']) for e in network['edges']
                             if e['edge_type'] == 'road')
    for site in sites:
        attachment = site['attachment_node_id']
        if attachment not in nodes or attachment in buildings:
            raise RoadNetworkError('站点必须关联独立道路节点')
        reachable = nx.node_connected_component(road_graph, attachment)
        if not all(any(o['attachment_node_id'] in reachable for o in row) for row in by_building.values()):
            raise RoadNetworkError(f"候选站{site['site_id']}不能到达所有建筑")


def write_network(network: dict, root: str | Path):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    (root / 'road_network.json').write_text(json.dumps(network, ensure_ascii=False, indent=2), encoding='utf-8')
    for name, rows, geom in [('nodes', network['nodes'], lambda r: Point(r['x_m'], r['y_m'])),
                              ('sites', network['sites'], lambda r: Point(r['x_m'], r['y_m'])),
                              ('edges', network['edges'], lambda r: LineString(r['coordinates']))]:
        payload = dict(type='FeatureCollection', crs={'type':'name','properties':{'name':CRS}}, features=[
            dict(type='Feature', properties={k:v for k,v in row.items() if k != 'coordinates'}, geometry=mapping(geom(row))) for row in rows])
        (root / f'candidate_{name}.geojson').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    if 'access_options' in network:
        import pandas as pd
        pd.DataFrame([{**o, 'edge_ids': json.dumps(o['edge_ids'])} for o in network['access_options']]).to_csv(root/'candidate_access_options.csv', index=False)
        pd.DataFrame(network.get('access_diagnostics', [])).to_csv(root/'building_access_diagnostics.csv', index=False)
