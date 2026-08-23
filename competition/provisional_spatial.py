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
) -> ProvisionalSpatialResult:
    """Build a load-weighted site and Euclidean MST with stable identifiers."""

    normalized_buildings, annual_heat = _validate_inputs(buildings, hourly_loads)
    if not isinstance(data_version, str) or not data_version.strip():
        raise ProvisionalSpatialError("data_version 必须是非空字符串")
    building_ids = tuple(normalized_buildings["building_id"])
    if site_id in building_ids:
        raise ProvisionalSpatialError("site_id 不得与 building_id 重复")

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
            for index, (node_from, node_to, distance) in enumerate(oriented_tree, start=1)
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
