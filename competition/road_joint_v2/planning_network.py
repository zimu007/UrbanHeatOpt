"""Planning-scale corridors and exclusive building access options.

Crossing a road is allowed and does not imply a tee. Positive-length shared
routes are atomized, however, so physical length/capacity is never duplicated.
Coordinates are rounded to a micrometre for numerical identity, not clearance.
"""
from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import isfinite

import networkx as nx
from pyproj import Transformer
from shapely.geometry import LineString, Point, mapping
from shapely.ops import nearest_points, substring
from shapely.strtree import STRtree

from competition.road_joint_v2.network import CRS, RoadNetworkError, _id, validate_network


@dataclass(frozen=True)
class PlanningNetworkPolicy:
    version: str = 'planning_corridor_2.1.0'
    max_access_options: int = 3
    allowed_highways: tuple = ('primary', 'primary_link', 'secondary', 'secondary_link',
                              'tertiary', 'tertiary_link', 'unclassified',
                              'residential', 'living_street', 'service')


def _xy(value):
    return tuple(round(float(x), 6) for x in value)


def _layer(frame, key, types):
    if frame is None:
        return {}
    if frame.crs is None or key not in frame or frame[key].isna().any() or frame[key].duplicated().any():
        raise RoadNetworkError(f'{key}缺失/重复或图层缺少CRS')
    result = dict(zip(frame[key].astype(str), frame.to_crs(CRS).geometry))
    if any(not k.strip() or g is None or g.is_empty or not g.is_valid or g.geom_type not in types for k, g in result.items()):
        raise RoadNetworkError(f'{key}图层几何或ID非法')
    return result


def _overlap_parts(geometry):
    if geometry.geom_type == 'LineString' and geometry.length > 1e-6:
        return [geometry]
    if hasattr(geometry, 'geoms'):
        return [line for part in geometry.geoms for line in _overlap_parts(part)]
    return []


def _atomic_union(records, nodes, options):
    """Split only overlaps/explicit attachments, not incidental point crossings."""
    cuts = [[(0., row['node_u']), (row['geometry'].length, row['node_v'])] for row in records]
    parents = {}

    def find(n):
        while n in parents:
            n = parents[n]
        return n

    def unite(a, b):
        a, b = find(a), find(b)
        if a == b:
            return a
        buildings = [n for n in (a, b) if nodes[n]['node_type'] == 'building']
        if len(buildings) > 1:
            raise RoadNetworkError('共享路由不能合并两个不同的建筑负荷节点')
        winner = buildings[0] if buildings else min(a, b)
        parents[b if winner == a else a] = winner
        return winner

    def cut(i, point):
        distance = records[i]['geometry'].project(point)
        for old, node in cuts[i]:
            if abs(old-distance) <= 1e-6:
                return node
        xy = _xy(records[i]['geometry'].interpolate(distance).coords[0])
        node = _id('shared_', xy)
        nodes.setdefault(node, dict(node_id=node, node_type='service_junction', x_m=xy[0], y_m=xy[1]))
        cuts[i].append((distance, node))
        return node

    tree = STRtree([r['geometry'] for r in records])
    for i, row in enumerate(records):
        for j in sorted(int(j) for j in tree.query(row['geometry'])):
            if j <= i:
                continue
            intersection = row['geometry'].intersection(records[j]['geometry'])
            for shared in _overlap_parts(intersection):
                for xy in (shared.coords[0], shared.coords[-1]):
                    point = Point(xy)
                    unite(cut(i, point), cut(j, point))

    physical, record_edges = {}, {}
    for i, row in enumerate(records):
        ordered = sorted(cuts[i])
        path = []
        for (start, u), (end, v) in zip(ordered, ordered[1:]):
            if end-start <= 1e-6:
                continue
            u, v = find(u), find(v)
            line = substring(row['geometry'], start, end)
            coordinates = [_xy(xy) for xy in line.coords]
            if u > v:
                u, v, coordinates = v, u, coordinates[::-1]
            eid = _id('edge_', [u, v, coordinates])
            entry = physical.setdefault(eid, dict(edge_id=eid, node_u=u, node_v=v, coordinates=coordinates,
                length_m=LineString(coordinates).length, edge_type='building_service', highway='service_branch',
                osm_way_ids=[], corridor_ids=[], access_option_ids=[], corridor_available=False,
                route_basis='supply_return_pair_route_m'))
            if row['edge_type'] == 'road':
                entry.update(edge_type='road', highway=row['highway'], corridor_available=True)
            for name in ('osm_way_ids', 'corridor_ids'):
                entry[name] = sorted(set(entry[name]) | set(row.get(name, [])))
            if row.get('option_id'):
                entry['access_option_ids'] = sorted(set(entry['access_option_ids']) | {row['option_id']})
            path.append(eid)
        record_edges[i] = path
    for option in options:
        option['edge_ids'] = record_edges[option.pop('_record_index')]
        option['attachment_node_id'] = find(option['attachment_node_id'])
        option['length_m'] = sum(physical[e]['length_m'] for e in option['edge_ids'])
    kept = {find(n): {**nodes[find(n)], 'node_id': find(n)} for n in nodes}
    used = {n for e in physical.values() for n in (e['node_u'], e['node_v'])}
    return [kept[n] for n in sorted(used)], sorted(physical.values(), key=lambda e: e['edge_id']), find


def generate(snapshot, buildings, annual_heat, *, candidate_count=5, obstacles=None,
             corridors=None, forbidden_areas=None, policy=None):
    policy = policy or PlanningNetworkPolicy()
    if policy.version != 'planning_corridor_2.1.0' or type(policy.max_access_options) is not int or not 1 <= policy.max_access_options <= 3:
        raise RoadNetworkError('规划网络策略版本非法或候选接入数不在1..3')
    polygons = _layer(buildings, 'building_id', {'Polygon', 'MultiPolygon'})
    polygons = dict(sorted(polygons.items()))
    if not polygons or set(polygons) != set(annual_heat) or any(not isfinite(float(v)) or v < 0 for v in annual_heat.values()) or sum(annual_heat.values()) <= 0:
        raise RoadNetworkError('建筑ID与负荷不一致或权重非有限非负')
    all_polygons = dict(polygons)
    for bid, geom in _layer(obstacles, 'building_id', {'Polygon', 'MultiPolygon'}).items():
        if bid in polygons and not polygons[bid].equals_exact(geom, 1e-6):
            raise RoadNetworkError(f'建筑障碍与负荷几何冲突: {bid}')
        all_polygons[bid] = geom
    forbidden = _layer(forbidden_areas, 'area_id', {'Polygon', 'MultiPolygon'})
    allowed = _layer(corridors, 'corridor_id', {'LineString', 'MultiLineString'})
    inside = [g.buffer(-1e-7) for g in list(all_polygons.values())+list(forbidden.values())]
    obstacle_tree = STRtree(inside)

    def blocked(line):
        return any(line.intersects(inside[int(i)]) for i in obstacle_tree.query(line))

    transformer = Transformer.from_crs('EPSG:4326', CRS, always_xy=True)
    coords = {int(n['id']): _xy(transformer.transform(n['lon'], n['lat'])) for n in snapshot.get('elements', []) if n.get('type') == 'node'}
    nodes, roads, excluded = {}, [], []

    def road_node(xy):
        xy = _xy(xy)
        node = _id('road_', xy)
        nodes.setdefault(node, dict(node_id=node, node_type='road', x_m=xy[0], y_m=xy[1]))
        return node

    def add_road(xy, highway, osm_ids, corridor_ids):
        line = LineString([_xy(p) for p in xy])
        if not line.is_simple or line.length <= 1e-6:
            raise RoadNetworkError('道路/允许走廊含零长度或不简单线')
        if blocked(line):
            excluded.append(dict(source=osm_ids or corridor_ids, reason='known_obstacle'))
            return
        roads.append(dict(node_u=road_node(line.coords[0]), node_v=road_node(line.coords[-1]),
            geometry=line, edge_type='road', highway=highway, osm_way_ids=osm_ids, corridor_ids=corridor_ids))

    for way in sorted((r for r in snapshot.get('elements', []) if r.get('type') == 'way'), key=lambda r: r['id']):
        tags = way.get('tags', {})
        if (tags.get('highway') not in policy.allowed_highways or tags.get('bridge', 'no') not in ('no', 'false', '0')
                or tags.get('tunnel', 'no') not in ('no', 'false', '0') or tags.get('layer', '0') not in ('0', '0.0')
                or tags.get('location', 'surface') in ('underground', 'overground')):
            excluded.append(dict(source=[str(way['id'])], reason='road_class_or_source_level'))
            continue
        for u, v in zip(way['nodes'], way['nodes'][1:]):
            if u not in coords or v not in coords:
                raise RoadNetworkError(f"OSM way {way['id']} 缺节点坐标")
            add_road([coords[u], coords[v]], tags['highway'], [str(way['id'])], [])
    for cid, geom in sorted(allowed.items()):
        for part in (geom.geoms if hasattr(geom, 'geoms') else [geom]):
            for u, v in zip(part.coords, list(part.coords)[1:]):
                add_road([u, v], 'permitted_corridor', [], [cid])
    if not roads:
        raise RoadNetworkError('没有可用于规划的道路/允许走廊')

    cuts = [[(0., r['node_u']), (r['geometry'].length, r['node_v'])] for r in roads]

    def add_cut(i, point):
        line = roads[i]['geometry']
        distance = line.project(point)
        for previous, node in cuts[i]:
            if abs(distance-previous) <= 1e-6:
                return node
        xy = _xy(line.interpolate(distance).coords[0])
        node = road_node(xy)
        nodes[node]['node_type'] = 'road_attachment'
        cuts[i].append((distance, node))
        return node

    # Explicit allowed corridors join roads at their geometric intersections.
    # OSM-only incidental crossings keep source topology; bridges are excluded.
    for i, row in enumerate(roads):
        if not row['corridor_ids']:
            continue
        for j, other in enumerate(roads):
            if i == j:
                continue
            crossing = row['geometry'].intersection(other['geometry'])
            for point in ([crossing] if crossing.geom_type == 'Point' else crossing.geoms if crossing.geom_type == 'MultiPoint' else []):
                add_cut(i, point)
                add_cut(j, point)

    branches, options, failures, diagnostic = [], [], [], []
    for bid, polygon in polygons.items():
        reasons, seen, found = Counter(), set(), []
        for i in sorted(range(len(roads)), key=lambda i: (roads[i]['geometry'].distance(polygon), i)):
            endpoint, target = nearest_points(polygon, roads[i]['geometry'])
            coordinates = [_xy(target.coords[0]), _xy(endpoint.coords[0])]
            line = LineString(coordinates)
            if line.length <= 1e-6:
                reasons['road_intersects_building'] += 1
                continue
            if blocked(line):
                reasons['branch_crosses_known_obstacle'] += 1
                continue
            if coordinates[0] in seen:
                continue
            seen.add(coordinates[0])
            attachment = add_cut(i, target)
            oid = _id('access_', [bid, coordinates])
            # A building is one logical load; each option keeps its true port.
            nodes[bid] = dict(node_id=bid, node_type='building', x_m=polygon.centroid.x,
                             y_m=polygon.centroid.y, geometry=mapping(polygon), display_point_only=True)
            option = dict(option_id=oid, building_id=bid, attachment_node_id=attachment,
                          building_boundary_point=list(coordinates[-1]), coordinates=coordinates,
                          candidate_rank=len(found)+1, length_m=line.length,
                          crossed_corridor_count=sum(line.crosses(r['geometry']) for r in roads),
                          crossing_semantics='no_tee_except_explicit_attachment_or_shared_route')
            options.append(option)
            branches.append(dict(node_u=attachment, node_v=bid, geometry=line, edge_type='building_service',
                                 highway='service_branch', option_id=oid))
            found.append(oid)
            if len(found) == policy.max_access_options:
                break
        diagnostic.append(dict(building_id=bid, candidate_count=len(found), rejection_counts=dict(reasons)))
        if not found:
            failures.append(dict(building_id=bid, reasons=list(reasons), centroid=list(polygon.centroid.coords[0])))
    if failures:
        raise RoadNetworkError(f'{len(failures)}栋建筑没有合格的规划接入候选；不添加穿楼捷径', failures)

    projected = buildings.to_crs(CRS)
    xmin, ymin, xmax, ymax = projected.total_bounds
    total = sum(annual_heat.values())
    weighted = Point(sum(polygons[b].centroid.x*annual_heat[b] for b in polygons)/total,
                     sum(polygons[b].centroid.y*annual_heat[b] for b in polygons)/total)
    targets = [weighted]+[Point(xmin+fx*(xmax-xmin), ymin+fy*(ymax-ymin)) for fx, fy in ((.25,.25),(.75,.25),(.25,.75),(.75,.75))]
    if type(candidate_count) is not int or not 1 <= candidate_count <= 5:
        raise RoadNetworkError('候选站数量必须为1..5')
    sites, occupied = [], set()
    for number, target in enumerate(targets[:candidate_count], 1):
        for i in sorted(range(len(roads)), key=lambda i: (roads[i]['geometry'].distance(target), i)):
            line = roads[i]['geometry']
            point = line.interpolate(line.project(target))
            xy = _xy(point.coords[0])
            if xy in occupied or any(g.covers(point) for g in list(all_polygons.values())+list(forbidden.values())):
                continue
            occupied.add(xy)
            sites.append(dict(site_id=f'candidate_station_{number:02d}', attachment_node_id=add_cut(i, point),
                x_m=xy[0], y_m=xy[1], candidate_source='deterministic_heat_centroid_extent_snapped',
                station_service_length_m=0., station_footprint_verified=False))
            break
        else:
            raise RoadNetworkError('没有足够的不同测试候选站')

    records = []
    for i, row in enumerate(roads):
        splits = sorted(cuts[i])
        for (start, u), (end, v) in zip(splits, splits[1:]):
            if end-start > 1e-6:
                line = substring(row['geometry'], start, end)
                records.append({**row, 'node_u': u, 'node_v': v,
                                'geometry': LineString([_xy(xy) for xy in line.coords])})
    # Only equivalent degree-2 road contractions; no route alternatives cut.
    protected = {o['attachment_node_id'] for o in options} | {s['attachment_node_id'] for s in sites}
    graph = nx.MultiGraph()
    for r in records:
        graph.add_edge(r['node_u'], r['node_v'], record=r)
    for node in sorted(list(graph)):
        if node in protected or graph.degree(node) != 2:
            continue
        incident = list(graph.edges(node, keys=True, data=True))
        a, b = incident[0][1], incident[1][1]
        r1, r2 = incident[0][3]['record'], incident[1][3]['record']
        if a == b or r1['highway'] != r2['highway']:
            continue
        xy1 = list(r1['geometry'].coords) if r1['node_u'] == a else list(r1['geometry'].coords)[::-1]
        xy2 = list(r2['geometry'].coords) if r2['node_u'] == node else list(r2['geometry'].coords)[::-1]
        merged = dict(node_u=a, node_v=b, geometry=LineString(xy1+xy2[1:]), edge_type='road', highway=r1['highway'],
                      osm_way_ids=sorted(set(r1['osm_way_ids']+r2['osm_way_ids'])),
                      corridor_ids=sorted(set(r1['corridor_ids']+r2['corridor_ids'])))
        graph.remove_node(node)
        graph.add_edge(a, b, record=merged)
    records = [x['record'] for _, _, x in graph.edges(data=True)]
    for option, branch in zip(options, branches):
        option['_record_index'] = len(records)
        records.append(branch)
    result_nodes, edges, find = _atomic_union(records, nodes, options)
    for site in sites:
        site['attachment_node_id'] = find(site['attachment_node_id'])
    network = dict(contract_version='road_atomic_network_2.1.0', crs=CRS, nodes=result_nodes, edges=edges,
        sites=sites, access_options=options, access_diagnostics=diagnostic,
        planning_obstacles=[dict(building_id=b, geometry=mapping(g), included_in_loads=b in polygons) for b, g in sorted(all_polygons.items())],
        metadata=dict(network_policy_version=policy.version, policy=asdict(policy), road_constrained=True,
            installation_concept='underground_planning', construction_details_modeled=False,
            construction_feasibility_verified=False, construction_feasibility_required=False,
            spatial_status='PLANNING_ROAD_CORRIDOR', branch_angle_limit_deg=None, road_crossings_allowed=True,
            forbidden_area_coverage='provided_layer_only' if forbidden_areas is not None else 'not_provided',
            obstacle_building_count=len(all_polygons), permitted_corridor_count=len(allowed), excluded_roads=excluded))
    validate_network(network)
    network['network_sha256'] = sha256(json.dumps(network, sort_keys=True).encode()).hexdigest()
    return network
