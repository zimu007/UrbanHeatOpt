"""Deterministic V0 candidate site and geometric MST network.

This module is deliberately a test-stage spatial provider.  It never reads a
road graph and must not be presented as a construction-feasible pipe layout.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import json
from math import hypot, isfinite
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point, mapping


PROJECTED_CRS = "EPSG:32650"
OUTPUT_CRS = "EPSG:4326"
CANDIDATE_SOURCE = "provisional_geometric_mst"
MULTI_CANDIDATE_SOURCE = "synthetic_multi_candidate_generator"
MULTI_CANDIDATE_STATUS = "PROVISIONAL_ALGORITHM_VALIDATION"


class ProvisionalSpatialError(ValueError):
    """V0 spatial input cannot produce a deterministic candidate network."""


@dataclass(frozen=True, slots=True)
class ProvisionalSpatialResult:
    sites: gpd.GeoDataFrame
    network: gpd.GeoDataFrame
    feasible_space: gpd.GeoDataFrame
    projected_crs: str = PROJECTED_CRS
    candidate_source: str = CANDIDATE_SOURCE
    road_constrained: bool = False
    construction_feasibility_verified: bool = False
    load_center_adjustment_m: float = 0.0
    generation_method: str = "single_load_weighted"
    candidate_count: int = 1
    input_building_source: str = "provided_building_gis"
    spatial_status: str = "provisional"


class _DisjointSet:
    def __init__(self, nodes: Iterable[str]) -> None:
        self.parent = {node: node for node in nodes}

    def find(self, node: str) -> str:
        parent = self.parent[node]
        if parent != node:
            self.parent[node] = self.find(parent)
        return self.parent[node]

    def union(self, left: str, right: str) -> bool:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return False
        if left_root < right_root:
            self.parent[right_root] = left_root
        else:
            self.parent[left_root] = right_root
        return True


def _validate_inputs(
    buildings: gpd.GeoDataFrame,
    hourly_loads: pd.DataFrame,
) -> tuple[gpd.GeoDataFrame, pd.Series]:
    if not isinstance(buildings, gpd.GeoDataFrame) or buildings.crs is None:
        raise ProvisionalSpatialError("buildings 必须是带 CRS 的 GeoDataFrame")
    if "building_id" not in buildings.columns or "geometry" not in buildings.columns:
        raise ProvisionalSpatialError("buildings 缺少 building_id 或 geometry")
    if buildings.empty:
        raise ProvisionalSpatialError("buildings 不得为空")
    ids = buildings["building_id"]
    if ids.isna().any() or ids.astype(str).str.strip().ne(ids.astype(str)).any():
        raise ProvisionalSpatialError("building_id 不得为空或包含首尾空白")
    if ids.astype(str).duplicated().any():
        raise ProvisionalSpatialError("building_id 不得重复")
    if buildings.geometry.isna().any() or buildings.geometry.is_empty.any():
        raise ProvisionalSpatialError("建筑 geometry 不得为空")
    if not buildings.geometry.is_valid.all():
        raise ProvisionalSpatialError("建筑 geometry 必须有效")
    required_load_columns = {"building_id", "heating_kW"}
    missing = sorted(required_load_columns - set(hourly_loads.columns))
    if missing:
        raise ProvisionalSpatialError("hourly_loads 缺少字段：" + ", ".join(missing))
    load_ids = set(hourly_loads["building_id"].astype(str))
    building_ids = set(ids.astype(str))
    if load_ids != building_ids:
        raise ProvisionalSpatialError("buildings 与 hourly_loads 的建筑 ID 集合不一致")
    numeric_load = pd.to_numeric(hourly_loads["heating_kW"], errors="coerce")
    if numeric_load.isna().any() or not numeric_load.map(isfinite).all():
        raise ProvisionalSpatialError("heating_kW 必须是有限数值")
    if (numeric_load < 0).any():
        raise ProvisionalSpatialError("heating_kW 不得为负")
    normalized_loads = hourly_loads[["building_id"]].copy()
    normalized_loads["building_id"] = normalized_loads["building_id"].astype(str)
    normalized_loads["heating_kW"] = numeric_load.astype(float)
    annual_heat = normalized_loads.groupby("building_id", sort=True)["heating_kW"].sum()
    if float(annual_heat.sum()) <= 0:
        raise ProvisionalSpatialError("总供暖负荷必须大于 0，无法计算负荷中心")
    normalized_buildings = buildings.copy(deep=True)
    normalized_buildings["building_id"] = normalized_buildings["building_id"].astype(str)
    normalized_buildings = normalized_buildings.sort_values("building_id", kind="stable")
    return normalized_buildings.reset_index(drop=True), annual_heat


def _kruskal_tree(points: dict[str, Point]) -> tuple[tuple[str, str, float], ...]:
    weighted_edges = []
    for left, right in combinations(sorted(points), 2):
        distance = hypot(points[left].x - points[right].x, points[left].y - points[right].y)
        weighted_edges.append((float(distance), left, right))
    weighted_edges.sort(key=lambda item: (round(item[0], 12), item[1], item[2]))
    groups = _DisjointSet(points)
    tree: list[tuple[str, str, float]] = []
    for distance, left, right in weighted_edges:
        if groups.union(left, right):
            tree.append((left, right, distance))
            if len(tree) == len(points) - 1:
                break
    if len(tree) != len(points) - 1:
        raise ProvisionalSpatialError("无法建立覆盖能源站和全部建筑的 MST")
    return tuple(tree)


def _orient_from_site(
    tree: tuple[tuple[str, str, float], ...],
    site_id: str,
) -> tuple[tuple[str, str, float], ...]:
    adjacency: dict[str, list[tuple[str, float]]] = {}
    for left, right, distance in tree:
        adjacency.setdefault(left, []).append((right, distance))
        adjacency.setdefault(right, []).append((left, distance))
    queue = [site_id]
    visited = {site_id}
    oriented: list[tuple[str, str, float]] = []
    while queue:
        parent = queue.pop(0)
        for child, distance in sorted(adjacency[parent], key=lambda item: item[0]):
            if child in visited:
                continue
            visited.add(child)
            queue.append(child)
            oriented.append((parent, child, distance))
    return tuple(oriented)


def build_provisional_geometric_network(
    buildings: gpd.GeoDataFrame,
    hourly_loads: pd.DataFrame,
    *,
    data_version: str,
    site_id: str = "site_1",
    local_extra_edge_count: int = 0,
) -> ProvisionalSpatialResult:
    """Build a load-weighted site and Euclidean MST with stable identifiers."""

    normalized_buildings, annual_heat = _validate_inputs(buildings, hourly_loads)
    if not isinstance(data_version, str) or not data_version.strip():
        raise ProvisionalSpatialError("data_version 必须是非空字符串")
    building_ids = tuple(normalized_buildings["building_id"])
    if site_id in building_ids:
        raise ProvisionalSpatialError("site_id 不得与 building_id 重复")

    if (
        isinstance(local_extra_edge_count, bool)
        or not isinstance(local_extra_edge_count, int)
        or local_extra_edge_count < 0
    ):
        raise ProvisionalSpatialError("local_extra_edge_count must be a non-negative integer")

    projected = normalized_buildings.to_crs(PROJECTED_CRS)
    centroids = projected.geometry.centroid
    weights = annual_heat.reindex(building_ids).astype(float)
    total_weight = float(weights.sum())
    site_point = Point(
        sum(point.x * weights.loc[building_id] for building_id, point in zip(building_ids, centroids))
        / total_weight,
        sum(point.y * weights.loc[building_id] for building_id, point in zip(building_ids, centroids))
        / total_weight,
    )
    load_center_adjustment_m = 0.0
    if any(site_point.distance(point) <= 1e-9 for point in centroids):
        # A one-building smoke fixture (or an exact geometric coincidence) has
        # no non-zero site-to-load segment.  Keep the rule deterministic and
        # disclose the minimal separation instead of emitting a zero-length
        # physical segment that the core correctly rejects.
        site_point = Point(site_point.x + 1.0, site_point.y)
        load_center_adjustment_m = 1.0
    points = {site_id: site_point}
    points.update({building_id: point for building_id, point in zip(building_ids, centroids)})
    oriented_tree = _orient_from_site(_kruskal_tree(points), site_id)
    tree_pairs = {frozenset((left, right)) for left, right, _ in oriented_tree}
    extra_candidates = sorted(
        (
            (float(points[left].distance(points[right])), left, right)
            for left, right in combinations(sorted(building_ids), 2)
            if frozenset((left, right)) not in tree_pairs
        ),
        key=lambda item: (round(item[0], 12), item[1], item[2]),
    )
    extra_edges = tuple(
        (left, right, distance)
        for distance, left, right in extra_candidates[:local_extra_edge_count]
    )
    candidate_edges = oriented_tree + extra_edges
    if any(distance <= 0 for _, _, distance in oriented_tree):
        raise ProvisionalSpatialError("临时候选站仍产生零长度管段")

    site_projected = gpd.GeoDataFrame(
        [
            {
                "site_id": site_id,
                "candidate_source": CANDIDATE_SOURCE,
                "road_constrained": False,
                "construction_feasibility_verified": False,
                "total_annual_heating_kWh_th": total_weight,
                "load_center_adjustment_m": load_center_adjustment_m,
                "data_version": data_version,
            }
        ],
        geometry=[site_point],
        crs=PROJECTED_CRS,
    )
    network_projected = gpd.GeoDataFrame(
        [
            {
                "segment_id": f"segment_{index:03d}",
                "node_from": node_from,
                "node_to": node_to,
                "length_m": distance,
                "candidate_source": CANDIDATE_SOURCE,
                "road_constrained": False,
                "construction_feasibility_verified": False,
                "data_version": data_version,
                "geometry": LineString((points[node_from], points[node_to])),
            }
            for index, (node_from, node_to, distance) in enumerate(candidate_edges, start=1)
        ],
        geometry="geometry",
        crs=PROJECTED_CRS,
    )
    feasible_projected = network_projected.rename(
        columns={"segment_id": "feature_id"}
    ).copy()
    feasible_projected["spatial_role"] = "provisional_candidate_corridor"
    return ProvisionalSpatialResult(
        sites=site_projected.to_crs(OUTPUT_CRS),
        network=network_projected.to_crs(OUTPUT_CRS),
        feasible_space=feasible_projected.to_crs(OUTPUT_CRS),
        load_center_adjustment_m=load_center_adjustment_m,
    )


def build_multi_candidate_provisional_network(
    buildings: gpd.GeoDataFrame,
    hourly_loads: pd.DataFrame,
    *,
    data_version: str,
    candidate_count: int = 5,
    input_building_source: str = "provided_building_gis",
    local_extra_edge_count: int = 0,
) -> ProvisionalSpatialResult:
    """Build deterministic virtual sites as leaf sources on a building-only MST.

    This is an algorithm-validation graph.  It does not read roads, parcels,
    exclusions, or constructibility evidence.
    """

    normalized_buildings, annual_heat = _validate_inputs(buildings, hourly_loads)
    if not isinstance(data_version, str) or not data_version.strip():
        raise ProvisionalSpatialError("data_version must be a non-empty string")
    if (
        isinstance(candidate_count, bool)
        or not isinstance(candidate_count, int)
        or not 1 <= candidate_count <= 10
    ):
        raise ProvisionalSpatialError("candidate_count must be an integer in [1, 10]")
    if not isinstance(input_building_source, str) or not input_building_source.strip():
        raise ProvisionalSpatialError("input_building_source must be non-empty")
    if (
        isinstance(local_extra_edge_count, bool)
        or not isinstance(local_extra_edge_count, int)
        or local_extra_edge_count < 0
    ):
        raise ProvisionalSpatialError("local_extra_edge_count must be non-negative")

    input_crs = normalized_buildings.crs
    projected = normalized_buildings.to_crs(PROJECTED_CRS)
    building_ids = tuple(projected["building_id"].astype(str))
    centroids = projected.geometry.centroid
    building_points = {
        building_id: point
        for building_id, point in zip(building_ids, centroids, strict=True)
    }
    weights = annual_heat.reindex(building_ids).astype(float)
    total_weight = float(weights.sum())
    weighted_center = Point(
        sum(building_points[node].x * weights.loc[node] for node in building_ids)
        / total_weight,
        sum(building_points[node].y * weights.loc[node] for node in building_ids)
        / total_weight,
    )
    min_x, min_y, max_x, max_y = projected.total_bounds
    span_x = max(float(max_x - min_x), 10.0)
    span_y = max(float(max_y - min_y), 10.0)
    fixed_fractions = (
        (0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75),
        (0.50, 0.25), (0.50, 0.75), (0.25, 0.50), (0.75, 0.50),
        (0.10, 0.50),
    )
    raw_points = [weighted_center] + [
        Point(min_x + fx * span_x, min_y + fy * span_y)
        for fx, fy in fixed_fractions
    ]
    candidate_points: dict[str, Point] = {}
    occupied = list(building_points.values())
    for index, raw in enumerate(raw_points[:candidate_count], start=1):
        point = raw
        attempt = 0
        while any(point.distance(other) <= 0.5 for other in (*occupied, *candidate_points.values())):
            attempt += 1
            point = Point(raw.x + attempt * (0.71 + index * 0.13), raw.y + attempt * (0.43 + index * 0.11))
        candidate_points[f"candidate_station_{index:02d}"] = point

    backbone = _kruskal_tree(building_points) if len(building_points) > 1 else ()
    backbone_pairs = {frozenset((left, right)) for left, right, _ in backbone}
    extra_candidates = sorted(
        (
            (float(building_points[left].distance(building_points[right])), left, right)
            for left, right in combinations(sorted(building_ids), 2)
            if frozenset((left, right)) not in backbone_pairs
        ),
        key=lambda item: (round(item[0], 12), item[1], item[2]),
    )
    extra_edges = tuple(
        (left, right, distance)
        for distance, left, right in extra_candidates[:local_extra_edge_count]
    )
    access_edges = tuple(
        (
            station,
            min(
                building_ids,
                key=lambda node: (
                    round(point.distance(building_points[node]), 12), node
                ),
            ),
        )
        for station, point in candidate_points.items()
    )

    edge_records: list[dict[str, object]] = []
    geometries: list[LineString] = []
    index = 1
    for edge_type, edges in (
        ("building_backbone", backbone),
        ("building_extra", extra_edges),
    ):
        for node_from, node_to, distance in edges:
            edge_records.append({
                "segment_id": f"segment_{index:03d}",
                "node_from": node_from,
                "node_to": node_to,
                "length_m": float(distance),
                "edge_type": edge_type,
            })
            geometries.append(LineString((building_points[node_from], building_points[node_to])))
            index += 1
    for station, building in access_edges:
        distance = float(candidate_points[station].distance(building_points[building]))
        if distance <= 0:
            raise ProvisionalSpatialError("station access edge length must be positive")
        edge_records.append({
            "segment_id": f"segment_{index:03d}",
            "node_from": station,
            "node_to": building,
            "length_m": distance,
            "edge_type": "station_access",
        })
        geometries.append(LineString((candidate_points[station], building_points[building])))
        index += 1

    common_network = {
        "candidate_source": MULTI_CANDIDATE_SOURCE,
        "spatial_source": MULTI_CANDIDATE_SOURCE,
        "parameter_status": "provisional",
        "generation_method": "building_backbone_mst_plus_station_leaf_access",
        "road_constrained": False,
        "construction_feasibility_verified": False,
        "data_version": data_version,
    }
    for record in edge_records:
        record.update(common_network)
    network_projected = gpd.GeoDataFrame(
        edge_records, geometry=geometries, crs=PROJECTED_CRS
    )
    sites_projected = gpd.GeoDataFrame(
        [
            {
                "site_id": station,
                "candidate_rank": rank,
                "station_type": "regional_energy_station_candidate",
                "location_source": MULTI_CANDIDATE_SOURCE,
                "candidate_source": MULTI_CANDIDATE_SOURCE,
                "parameter_status": "provisional",
                "generation_method": "load_center_and_extent_fraction_candidates",
                "input_building_source": input_building_source,
                "candidate_count": candidate_count,
                "projected_crs": PROJECTED_CRS,
                "spatial_status": MULTI_CANDIDATE_STATUS,
                "road_constrained": False,
                "construction_feasibility_verified": False,
                "data_version": data_version,
            }
            for rank, station in enumerate(candidate_points, start=1)
        ],
        geometry=list(candidate_points.values()),
        crs=PROJECTED_CRS,
    )
    feasible_projected = network_projected.rename(
        columns={"segment_id": "feature_id"}
    ).copy()
    feasible_projected["spatial_role"] = "provisional_candidate_corridor"
    return ProvisionalSpatialResult(
        sites=sites_projected.to_crs(input_crs),
        network=network_projected.to_crs(input_crs),
        feasible_space=feasible_projected.to_crs(input_crs),
        candidate_source=MULTI_CANDIDATE_SOURCE,
        generation_method="load_center_extent_sites_with_building_mst",
        candidate_count=candidate_count,
        input_building_source=input_building_source,
        spatial_status=MULTI_CANDIDATE_STATUS,
    )


def _json_value(value: object) -> object:
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def _write_geojson(path: Path, frame: gpd.GeoDataFrame) -> None:
    features = []
    for _, row in frame.iterrows():
        properties = {
            column: _json_value(row[column])
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
    payload = {"type": "FeatureCollection", "features": features}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def write_provisional_spatial_outputs(
    result: ProvisionalSpatialResult,
    output_dir: str | Path,
) -> tuple[Path, Path, Path]:
    """Write byte-stable GeoJSON files for the V0 adapter output directory."""

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    sites_path = root / "candidate_sites.geojson"
    network_path = root / "candidate_network.geojson"
    feasible_path = root / "roads_or_feasible_space.geojson"
    _write_geojson(sites_path, result.sites)
    _write_geojson(network_path, result.network)
    _write_geojson(feasible_path, result.feasible_space)
    return sites_path, network_path, feasible_path
