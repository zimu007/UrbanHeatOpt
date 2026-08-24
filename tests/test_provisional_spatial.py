from __future__ import annotations

import hashlib

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from competition.provisional_spatial import (
    CANDIDATE_SOURCE,
    ProvisionalSpatialError,
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
