"""Provisional OSM roadside-corridor inputs for unchanged terminal-node core."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import heapq
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.geometry import LineString, Point, mapping

from urbanheatopt.spatial.provisional import build_multi_candidate_provisional_network


PROJECTED_CRS = "EPSG:32650"
INPUT_CRS = "EPSG:4326"
CANDIDATE_SOURCE = "provisional_osm_roadside_corridor"
SPATIAL_STATUS = "PROVISIONAL_OSM_ROADSIDE_CORRIDOR"
DEFAULT_OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
DEFAULT_HIGHWAY_CLASSES = (
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "unclassified",
)


class OsmCorridorError(ValueError):
    """OSM snapshot cannot form the agreed provisional corridor input."""


@dataclass(frozen=True, slots=True)
class OsmSnapshot:
    response_path: Path
    metadata_path: Path
    sha256: str
    bbox_wgs84: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class OsmCorridorResult:
    sites: gpd.GeoDataFrame
    network: gpd.GeoDataFrame
    roads: gpd.GeoDataFrame
    metadata: dict[str, Any]


def _json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _stable_geojson(path: Path, frame: gpd.GeoDataFrame) -> None:
    def value(item: Any) -> Any:
        if item is None or (not isinstance(item, (list, dict)) and pd.isna(item)):
            return None
        return item.item() if hasattr(item, "item") else item

    features = []
    for _, row in frame.sort_values(frame.columns[0], kind="stable").iterrows():
        properties = {
            column: value(row[column])
            for column in frame.columns
            if column != frame.geometry.name
        }
        features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": mapping(row.geometry),
            }
        )
    path.write_text(
        json.dumps(
            {"type": "FeatureCollection", "features": features},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def buffered_bbox_wgs84(
    buildings: gpd.GeoDataFrame, *, buffer_m: float = 800.0
) -> tuple[float, float, float, float]:
    if buildings.crs is None:
        raise OsmCorridorError("建筑空间数据缺少CRS")
    if not math.isfinite(buffer_m) or buffer_m <= 0:
        raise OsmCorridorError("OSM查询缓冲距离必须大于0")
    projected = buildings.to_crs(PROJECTED_CRS)
    envelope = gpd.GeoSeries(
        [projected.geometry.unary_union.envelope.buffer(buffer_m)],
        crs=PROJECTED_CRS,
    ).to_crs(INPUT_CRS)
    west, south, east, north = (float(value) for value in envelope.total_bounds)
    return west, south, east, north


def overpass_query(
    bbox_wgs84: tuple[float, float, float, float],
    highway_classes: tuple[str, ...] = DEFAULT_HIGHWAY_CLASSES,
) -> str:
    west, south, east, north = bbox_wgs84
    classes = "|".join(highway_classes)
    return (
        "[out:json][timeout:120];"
        f'way["highway"~"^({classes})$"]'
        f"({south:.7f},{west:.7f},{north:.7f},{east:.7f});"
        "out body;>;out skel qt;"
    )


def download_osm_snapshot(
    buildings: gpd.GeoDataFrame,
    output_dir: str | Path,
    *,
    endpoint: str = DEFAULT_OVERPASS_ENDPOINT,
    buffer_m: float = 800.0,
    highway_classes: tuple[str, ...] = DEFAULT_HIGHWAY_CLASSES,
) -> OsmSnapshot:
    """Send only a bounding box and cache one immutable Overpass response."""

    root = Path(output_dir).resolve()
    response_path = root / "osm_overpass_snapshot.json"
    metadata_path = root / "osm_overpass_snapshot_metadata.json"
    if response_path.exists() or metadata_path.exists():
        raise FileExistsError("OSM快照或元数据已存在，拒绝覆盖")
    root.mkdir(parents=True, exist_ok=True)
    bbox = buffered_bbox_wgs84(buildings, buffer_m=buffer_m)
    query = overpass_query(bbox, highway_classes)
    request = Request(
        endpoint,
        data=urlencode({"data": query}).encode("ascii"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "UrbanHeatOpt-research-validation/0.1",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=180) as response:
            body = response.read()
    except Exception as exc:
        raise OsmCorridorError(f"Overpass下载失败，未生成不完整快照：{exc}") from exc
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise OsmCorridorError("Overpass响应不是有效UTF-8 JSON") from exc
    if not isinstance(payload.get("elements"), list):
        raise OsmCorridorError("Overpass响应缺少elements")
    response_path.write_bytes(body)
    digest = sha256(body).hexdigest()
    _json(
        metadata_path,
        {
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "endpoint": endpoint,
            "query": query,
            "bbox_wgs84": list(bbox),
            "buffer_m": buffer_m,
            "highway_classes": list(highway_classes),
            "response_sha256": digest,
            "response_size_bytes": len(body),
            "data_attribution": "© OpenStreetMap contributors",
            "data_license": "ODbL 1.0",
            "copyright_url": "https://www.openstreetmap.org/copyright",
            "transmitted_project_data": False,
            "transmitted_values": "bounding_box_and_highway_class_query_only",
        },
    )
    return OsmSnapshot(response_path, metadata_path, digest, bbox)


def _road_graph(
    snapshot_path: str | Path,
    highway_classes: tuple[str, ...],
) -> tuple[nx.Graph, gpd.GeoDataFrame, str]:
    path = Path(snapshot_path).resolve()
    body = path.read_bytes()
    payload = json.loads(body.decode("utf-8"))
    nodes = {
        int(row["id"]): (float(row["lon"]), float(row["lat"]))
        for row in payload.get("elements", [])
        if row.get("type") == "node" and "lon" in row and "lat" in row
    }
    ways = [
        row
        for row in payload.get("elements", [])
        if row.get("type") == "way"
        and row.get("tags", {}).get("highway") in highway_classes
    ]
    if not ways or not nodes:
        raise OsmCorridorError("OSM快照中没有约定等级的道路或坐标节点")
    records: list[dict[str, Any]] = []
    geometries: list[LineString] = []
    edge_index = 1
    for way in sorted(ways, key=lambda row: int(row["id"])):
        refs = [int(value) for value in way.get("nodes", [])]
        for left, right in zip(refs, refs[1:]):
            if left not in nodes or right not in nodes or left == right:
                continue
            geometry = LineString((nodes[left], nodes[right]))
            records.append(
                {
                    "road_edge_id": f"osm_edge_{edge_index:06d}",
                    "osm_way_id": int(way["id"]),
                    "osm_node_from": left,
                    "osm_node_to": right,
                    "highway": way["tags"]["highway"],
                    "name": way.get("tags", {}).get("name"),
                }
            )
            geometries.append(geometry)
            edge_index += 1
    if not records:
        raise OsmCorridorError("OSM道路没有可解析的相邻节点边")
    roads = gpd.GeoDataFrame(records, geometry=geometries, crs=INPUT_CRS).to_crs(
        PROJECTED_CRS
    )
    graph = nx.Graph()
    projected_nodes = gpd.GeoDataFrame(
        {"osm_node_id": list(nodes)},
        geometry=[Point(nodes[node]) for node in nodes],
        crs=INPUT_CRS,
    ).to_crs(PROJECTED_CRS)
    coordinates = {
        int(row.osm_node_id): (float(row.geometry.x), float(row.geometry.y))
        for row in projected_nodes.itertuples()
    }
    for row in roads.itertuples():
        left = int(row.osm_node_from)
        right = int(row.osm_node_to)
        length = float(row.geometry.length)
        if length <= 0:
            continue
        graph.add_node(left, xy=coordinates[left])
        graph.add_node(right, xy=coordinates[right])
        if not graph.has_edge(left, right) or length < graph[left][right]["length_m"]:
            graph.add_edge(left, right, length_m=length)
    if graph.number_of_edges() == 0:
        raise OsmCorridorError("OSM道路图为空")
    return graph, roads, sha256(body).hexdigest()


def _nearest_graph_node(point: Point, graph: nx.Graph, *, excluded: set[int] | None = None) -> int:
    blocked = excluded or set()
    candidates = (
        (
            round(point.distance(Point(data["xy"])), 12),
            int(node),
        )
        for node, data in graph.nodes(data=True)
        if int(node) not in blocked
    )
    try:
        return min(candidates)[1]
    except ValueError as exc:
        raise OsmCorridorError("道路节点不足，无法生成互不重复的候选站") from exc


def _network_voronoi_owners(
    graph: nx.Graph, terminal_seeds: dict[str, int]
) -> dict[int, str]:
    owners: dict[int, str] = {}
    distances: dict[int, float] = {}
    queue: list[tuple[float, str, int]] = []
    for terminal, node in sorted(terminal_seeds.items()):
        heapq.heappush(queue, (0.0, terminal, node))
    while queue:
        distance, owner, node = heapq.heappop(queue)
        current = (distances.get(node, math.inf), owners.get(node, ""))
        if (distance, owner) >= current:
            continue
        distances[node] = distance
        owners[node] = owner
        for neighbor, edge in graph[node].items():
            heapq.heappush(
                queue,
                (distance + float(edge["length_m"]), owner, int(neighbor)),
            )
    return owners


def _route_geometry(
    graph: nx.Graph,
    left_point: Point,
    left_seed: int,
    right_point: Point,
    right_seed: int,
) -> LineString:
    route = nx.shortest_path(graph, left_seed, right_seed, weight="length_m")
    coordinates = [(float(left_point.x), float(left_point.y))]
    coordinates.extend(tuple(graph.nodes[node]["xy"]) for node in route)
    coordinates.append((float(right_point.x), float(right_point.y)))
    deduplicated = [coordinates[0]]
    for coordinate in coordinates[1:]:
        if coordinate != deduplicated[-1]:
            deduplicated.append(coordinate)
    if len(deduplicated) < 2:
        raise OsmCorridorError("候选管段退化为零长度")
    return LineString(deduplicated)


def build_osm_corridor_network(
    buildings: gpd.GeoDataFrame,
    loads: pd.DataFrame,
    snapshot_path: str | Path,
    *,
    data_version: str,
    candidate_count: int = 5,
    highway_classes: tuple[str, ...] = DEFAULT_HIGHWAY_CLASSES,
) -> OsmCorridorResult:
    """Quotient all OSM road edges onto the core's existing terminal-node interface."""

    if candidate_count != 5:
        raise OsmCorridorError("当前冻结口径要求恰好5个候选站")
    required = {"building_id", "geometry"}
    if not required <= set(buildings):
        raise OsmCorridorError(f"建筑数据缺少字段：{sorted(required - set(buildings))}")
    if len(buildings) != 62:
        raise OsmCorridorError("OSM全季候选网络必须使用全部62栋建筑")
    graph, roads, snapshot_sha = _road_graph(snapshot_path, highway_classes)
    projected = buildings.to_crs(PROJECTED_CRS).copy()
    projected["geometry"] = projected.geometry.centroid
    building_points = {
        str(row.building_id): row.geometry
        for row in projected.sort_values("building_id").itertuples()
    }
    components = sorted(
        (set(component) for component in nx.connected_components(graph)),
        key=lambda component: (-len(component), min(component)),
    )
    component_by_node = {
        int(node): index
        for index, component in enumerate(components)
        for node in component
    }
    nearest_building_components = {
        component_by_node[_nearest_graph_node(point, graph)]
        for point in building_points.values()
    }
    if len(nearest_building_components) != 1:
        raise OsmCorridorError(
            "建筑分别邻近多个互不连通的OSM道路分量；禁止删除可行分量或添加欧氏捷径："
            f"building_serving_components={sorted(nearest_building_components)}, "
            f"component_sizes={[len(component) for component in components]}"
        )
    serving_component_index = next(iter(nearest_building_components))
    serving_nodes = components[serving_component_index]
    graph = graph.subgraph(serving_nodes).copy()
    roads = roads.copy()
    roads["included_in_candidate_corridor"] = [
        int(row.osm_node_from) in serving_nodes
        and int(row.osm_node_to) in serving_nodes
        for row in roads.itertuples()
    ]
    provisional = build_multi_candidate_provisional_network(
        buildings,
        loads,
        data_version=data_version,
        candidate_count=candidate_count,
        input_building_source="guanggu_v03_03_buildings",
    )
    raw_sites = provisional.sites.to_crs(PROJECTED_CRS).sort_values("site_id")
    used_site_nodes: set[int] = set()
    site_seeds: dict[str, int] = {}
    site_points: dict[str, Point] = {}
    for row in raw_sites.itertuples():
        node = _nearest_graph_node(row.geometry, graph, excluded=used_site_nodes)
        used_site_nodes.add(node)
        site_seeds[str(row.site_id)] = node
        site_points[str(row.site_id)] = Point(graph.nodes[node]["xy"])
    terminal_points = {**building_points, **site_points}
    terminal_seeds = {
        terminal: (
            site_seeds[terminal]
            if terminal in site_seeds
            else _nearest_graph_node(point, graph)
        )
        for terminal, point in terminal_points.items()
    }
    owners = _network_voronoi_owners(graph, terminal_seeds)
    pairs = {
        tuple(sorted((owners[int(left)], owners[int(right)])))
        for left, right in graph.edges
        if owners[int(left)] != owners[int(right)]
    }
    seed_groups: dict[int, list[str]] = {}
    for terminal, seed in terminal_seeds.items():
        seed_groups.setdefault(seed, []).append(terminal)
    for terminals in seed_groups.values():
        primary = min(terminals)
        pairs.update(tuple(sorted((primary, other))) for other in terminals if other != primary)
    quotient = nx.Graph()
    quotient.add_nodes_from(terminal_points)
    quotient.add_edges_from(pairs)
    if not nx.is_connected(quotient):
        missing = sorted(
            sorted(component) for component in nx.connected_components(quotient)
        )
        raise OsmCorridorError(
            "道路商图未覆盖全部建筑和候选站，禁止添加非道路捷径："
            f"components={missing}"
        )
    common = {
        "candidate_source": CANDIDATE_SOURCE,
        "spatial_source": "OpenStreetMap",
        "parameter_status": "provisional",
        "generation_method": "osm_road_graph_terminal_voronoi_quotient",
        "road_constrained": True,
        "greenbelt_alignment_assumed": True,
        "construction_feasibility_verified": False,
        "spatial_status": SPATIAL_STATUS,
        "shared_corridor_accounting": "terminal_quotient_without_explicit_road_junction_nodes",
        "data_version": data_version,
        "osm_snapshot_sha256": snapshot_sha,
    }
    network_records: list[dict[str, Any]] = []
    network_geometry: list[LineString] = []
    for index, (left, right) in enumerate(sorted(pairs), start=1):
        geometry = _route_geometry(
            graph,
            terminal_points[left],
            terminal_seeds[left],
            terminal_points[right],
            terminal_seeds[right],
        )
        network_records.append(
            {
                "segment_id": f"osm_corridor_segment_{index:04d}",
                "node_from": left,
                "node_to": right,
                "length_m": float(geometry.length),
                "edge_type": "road_terminal_adjacency",
                **common,
            }
        )
        network_geometry.append(geometry)
    network = gpd.GeoDataFrame(
        network_records, geometry=network_geometry, crs=PROJECTED_CRS
    )
    site_records = [
        {
            "site_id": site_id,
            "candidate_rank": index,
            "station_type": "regional_energy_station_candidate",
            "location_source": "deterministic_load_extent_then_osm_snap",
            **common,
        }
        for index, site_id in enumerate(sorted(site_points), start=1)
    ]
    sites = gpd.GeoDataFrame(
        site_records,
        geometry=[site_points[row["site_id"]] for row in site_records],
        crs=PROJECTED_CRS,
    )
    metadata = {
        **common,
        "building_count": len(building_points),
        "candidate_site_count": len(site_points),
        "osm_road_node_count": graph.number_of_nodes(),
        "osm_road_edge_count": graph.number_of_edges(),
        "osm_downloaded_component_sizes": [len(component) for component in components],
        "serving_component_index": serving_component_index,
        "excluded_nonserving_component_count": len(components) - 1,
        "nonserving_roads_retained_in_reference_layer": True,
        "candidate_segment_count": len(network),
        "highway_classes": list(highway_classes),
        "core_interface_limitation": (
            "core only accepts building/site endpoints; OSM junctions are represented by "
            "a deterministic terminal Voronoi quotient"
        ),
        "engineering_use_allowed": False,
        "data_attribution": "© OpenStreetMap contributors",
        "data_license": "ODbL 1.0",
        "copyright_url": "https://www.openstreetmap.org/copyright",
    }
    return OsmCorridorResult(
        sites=sites.to_crs(INPUT_CRS),
        network=network.to_crs(INPUT_CRS),
        roads=roads.to_crs(INPUT_CRS),
        metadata=metadata,
    )


def write_osm_corridor_outputs(
    result: OsmCorridorResult, output_dir: str | Path
) -> tuple[Path, Path, Path, Path]:
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = (
        root / "candidate_sites.geojson",
        root / "candidate_network.geojson",
        root / "roads_or_feasible_space.geojson",
        root / "osm_corridor_metadata.json",
    )
    if any(path.exists() for path in paths):
        raise FileExistsError("OSM候选站网输出已存在，拒绝覆盖")
    _stable_geojson(paths[0], result.sites)
    _stable_geojson(paths[1], result.network)
    _stable_geojson(paths[2], result.roads)
    _json(paths[3], result.metadata)
    return paths
