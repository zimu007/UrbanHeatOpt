"""Offline OSM corridor tests; no public service is contacted."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import networkx as nx
import pandas as pd
import pytest
from shapely.geometry import box

from urbanheatopt.spatial.osm_corridor import (
    CANDIDATE_SOURCE,
    SPATIAL_STATUS,
    OsmCorridorError,
    build_osm_corridor_network,
    overpass_query,
    write_osm_corridor_outputs,
)


def _buildings_and_loads() -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    records = []
    geometries = []
    loads = []
    for index in range(62):
        longitude = 114.396 + index * 0.0001
        latitude = 30.478 + (index % 3 - 1) * 0.00003
        building_id = f"building_{index + 1:03d}"
        records.append({"building_id": building_id})
        geometries.append(
            box(
                longitude - 0.00001,
                latitude - 0.00001,
                longitude + 0.00001,
                latitude + 0.00001,
            )
        )
        loads.append({"building_id": building_id, "heating_kW": index + 1.0})
    return (
        gpd.GeoDataFrame(records, geometry=geometries, crs="EPSG:4326"),
        pd.DataFrame(loads),
    )


def _snapshot(path: Path, *, disconnected: bool = False) -> Path:
    nodes = [
        {
            "type": "node",
            "id": index + 1,
            "lon": 114.3955 + index * 0.0001,
            "lat": 30.478,
        }
        for index in range(75)
    ]
    ways = [
        {
            "type": "way",
            "id": 100,
            "nodes": list(range(1, 76)),
            "tags": {"highway": "tertiary", "name": "synthetic_main_road"},
        }
    ]
    if disconnected:
        nodes.extend(
            [
                {"type": "node", "id": 1001, "lon": 114.398, "lat": 30.47803},
                {"type": "node", "id": 1002, "lon": 114.402, "lat": 30.47803},
            ]
        )
        ways.append(
            {
                "type": "way",
                "id": 200,
                "nodes": [1001, 1002],
                "tags": {"highway": "primary"},
            }
        )
    path.write_text(json.dumps({"elements": [*ways, *nodes]}), encoding="utf-8")
    return path


def test_osm_corridor_uses_all_62_buildings_and_core_compatible_endpoints(tmp_path: Path) -> None:
    buildings, loads = _buildings_and_loads()
    result = build_osm_corridor_network(
        buildings,
        loads,
        _snapshot(tmp_path / "osm.json"),
        data_version="synthetic-osm-test",
    )
    assert len(result.sites) == 5
    assert result.sites["candidate_source"].eq(CANDIDATE_SOURCE).all()
    assert result.network["road_constrained"].all()
    assert result.network["greenbelt_alignment_assumed"].all()
    assert not result.network["construction_feasibility_verified"].any()
    assert result.network["spatial_status"].eq(SPATIAL_STATUS).all()
    terminals = set(buildings["building_id"]) | set(result.sites["site_id"])
    endpoints = set(result.network["node_from"]) | set(result.network["node_to"])
    assert endpoints <= terminals
    graph = nx.Graph(
        result.network[["node_from", "node_to"]].itertuples(index=False, name=None)
    )
    assert set(graph) == terminals and nx.is_connected(graph)
    assert result.metadata["osm_road_edge_count"] == 74
    assert result.metadata["engineering_use_allowed"] is False

    outputs = write_osm_corridor_outputs(result, tmp_path / "output")
    assert all(path.is_file() and path.stat().st_size > 0 for path in outputs)
    network = gpd.read_file(outputs[1])
    assert len(network) == len(result.network)


def test_osm_corridor_refuses_multiple_building_serving_components(tmp_path: Path) -> None:
    buildings, loads = _buildings_and_loads()
    with pytest.raises(OsmCorridorError, match="多个互不连通"):
        build_osm_corridor_network(
            buildings,
            loads,
            _snapshot(tmp_path / "osm.json", disconnected=True),
            data_version="synthetic-osm-test",
        )


def test_overpass_query_is_bbox_and_highway_only() -> None:
    query = overpass_query((114.39, 30.47, 114.41, 30.49))
    assert "114.3900000" in query and "30.4900000" in query
    assert 'way["highway"' in query
    assert "building_id" not in query and "heating" not in query
