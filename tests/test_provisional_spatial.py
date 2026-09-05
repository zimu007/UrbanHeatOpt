from __future__ import annotations

import hashlib

import geopandas as gpd
import networkx as nx
import pandas as pd
import pytest
from shapely.geometry import Point

from urbanheatopt.spatial.provisional import (
    CANDIDATE_SOURCE,
    MULTI_CANDIDATE_SOURCE,
    MULTI_CANDIDATE_STATUS,
    ProvisionalSpatialError,
    build_multi_candidate_provisional_network,
    build_provisional_geometric_network,
    write_provisional_spatial_outputs,
)


def _inputs() -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    buildings = gpd.GeoDataFrame(
        {"building_id": ["building_1", "building_2"]},
        geometry=[Point(500000.0, 3374000.0), Point(500010.0, 3374000.0)],
        crs="EPSG:32650",
    )
    loads = pd.DataFrame(
        {
            "building_id": ["building_1", "building_2"],
            "heating_kW": [1.0, 3.0],
        }
    )
    return buildings, loads


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_load_weighted_site_and_mst_are_deterministic(tmp_path) -> None:
    buildings, loads = _inputs()
    original_wkb = tuple(buildings.geometry.to_wkb())
    first = build_provisional_geometric_network(
        buildings, loads, data_version="synthetic-v02"
    )
    second = build_provisional_geometric_network(
        buildings.iloc[::-1].reset_index(drop=True),
        loads.iloc[::-1].reset_index(drop=True),
        data_version="synthetic-v02",
    )
    first_paths = write_provisional_spatial_outputs(first, tmp_path / "first")
    second_paths = write_provisional_spatial_outputs(second, tmp_path / "second")

    site_projected = first.sites.to_crs("EPSG:32650").geometry.iloc[0]
    assert site_projected.x == pytest.approx(500007.5, rel=0, abs=1e-6)
    assert site_projected.y == pytest.approx(3374000.0, rel=0, abs=1e-6)
    assert len(first.network) == 2
    assert set(first.network["node_from"]).union(first.network["node_to"]) == {
        "site_1",
        "building_1",
        "building_2",
    }
    assert first.network["candidate_source"].eq(CANDIDATE_SOURCE).all()
    assert not first.network["road_constrained"].any()
    assert not first.network["construction_feasibility_verified"].any()
    assert [_sha256(path) for path in first_paths] == [_sha256(path) for path in second_paths]
    assert tuple(buildings.geometry.to_wkb()) == original_wkb


def test_single_building_uses_disclosed_one_metre_separation() -> None:
    buildings, loads = _inputs()
    result = build_provisional_geometric_network(
        buildings.iloc[[0]],
        loads.iloc[[0]],
        data_version="synthetic-v02",
    )
    assert result.load_center_adjustment_m == 1.0
    assert result.network.to_crs("EPSG:32650")["length_m"].iloc[0] == pytest.approx(1.0)


def test_deterministic_local_extra_edges_extend_but_do_not_replace_mst() -> None:
    buildings = gpd.GeoDataFrame(
        {"building_id": ["a", "b", "c", "d"]},
        geometry=[Point(0, 0), Point(10, 0), Point(10, 10), Point(0, 10)],
        crs="EPSG:32650",
    )
    loads = pd.DataFrame({"building_id": ["a", "b", "c", "d"], "heating_kW": [1, 1, 1, 1]})
    result = build_provisional_geometric_network(
        buildings,
        loads,
        data_version="synthetic-v02",
        local_extra_edge_count=2,
    )
    assert len(result.network) == 6  # 5-node MST plus two local extra edges.
    assert result.network[["node_from", "node_to"]].duplicated().sum() == 0


def test_multi_candidate_generator_is_deterministic_connected_and_provisional(
    tmp_path,
) -> None:
    buildings = gpd.GeoDataFrame(
        {"building_id": [f"building_{index}" for index in range(1, 7)]},
        geometry=[
            Point(500000, 3374000), Point(500100, 3374000),
            Point(500200, 3374050), Point(500150, 3374150),
            Point(500050, 3374150), Point(499950, 3374075),
        ],
        crs="EPSG:32650",
    )
    loads = pd.DataFrame({
        "building_id": buildings["building_id"],
        "heating_kW": [10, 20, 30, 40, 50, 60],
    })
    original = tuple(buildings.geometry.to_wkb())
    first = build_multi_candidate_provisional_network(
        buildings, loads, data_version="synthetic-multi", candidate_count=5
    )
    second = build_multi_candidate_provisional_network(
        buildings.iloc[::-1].reset_index(drop=True),
        loads.iloc[::-1].reset_index(drop=True),
        data_version="synthetic-multi",
        candidate_count=5,
    )
    first_paths = write_provisional_spatial_outputs(first, tmp_path / "first")
    second_paths = write_provisional_spatial_outputs(second, tmp_path / "second")

    assert len(first.sites) == 5
    assert first.sites.crs == buildings.crs
    assert first.sites["site_id"].is_unique
    assert not set(first.sites["site_id"]).intersection(buildings["building_id"])
    assert first.sites.geometry.is_valid.all() and not first.sites.geometry.has_z.any()
    assert first.sites.geometry.to_wkb().is_unique
    assert first.sites["candidate_source"].eq(MULTI_CANDIDATE_SOURCE).all()
    assert first.sites["spatial_status"].eq(MULTI_CANDIDATE_STATUS).all()
    assert first.sites["parameter_status"].eq("provisional").all()
    assert not first.sites["road_constrained"].any()
    assert not first.sites["construction_feasibility_verified"].any()

    graph = nx.Graph()
    graph.add_edges_from(first.network[["node_from", "node_to"]].itertuples(index=False, name=None))
    assert nx.is_connected(graph)
    access = first.network[first.network["edge_type"].eq("station_access")]
    assert len(access) == 5
    assert set(access["node_from"]) == set(first.sites["site_id"])
    assert all(graph.degree[station] == 1 for station in first.sites["site_id"])
    assert (first.network["length_m"] > 0).all()
    assert first.network["segment_id"].is_unique
    assert first.network["parameter_status"].eq("provisional").all()
    assert [_sha256(path) for path in first_paths] == [_sha256(path) for path in second_paths]
    assert tuple(buildings.geometry.to_wkb()) == original


@pytest.mark.parametrize("candidate_count", [0, 11, True])
def test_multi_candidate_generator_rejects_out_of_scope_counts(candidate_count) -> None:
    buildings, loads = _inputs()
    with pytest.raises(ProvisionalSpatialError, match=r"\[1, 10\]"):
        build_multi_candidate_provisional_network(
            buildings,
            loads,
            data_version="synthetic-multi",
            candidate_count=candidate_count,
        )


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda b, l: (b.set_crs(None, allow_override=True), l), "CRS"),
        (lambda b, l: (b, l.assign(building_id=["building_1", "missing"])), "ID"),
        (lambda b, l: (b, l.assign(heating_kW=[1.0, -1.0])), "不得为负"),
        (lambda b, l: (b, l.assign(heating_kW=[0.0, 0.0])), "总供暖负荷"),
    ],
)
def test_provisional_spatial_rejects_invalid_inputs(mutator, message) -> None:
    buildings, loads = _inputs()
    changed_buildings, changed_loads = mutator(buildings, loads)
    with pytest.raises(ProvisionalSpatialError, match=message):
        build_provisional_geometric_network(
            changed_buildings,
            changed_loads,
            data_version="synthetic-v02",
        )
