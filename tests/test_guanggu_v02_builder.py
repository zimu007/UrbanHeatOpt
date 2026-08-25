from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import networkx as nx
import pandas as pd
import pytest

from competition.validation.v3_inputs import load_v3_case


ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "cases" / "v0_guanggu_62b_168h"


@pytest.mark.skipif(not CASE.exists(), reason="generated V0.2 scale case is not present")
def test_v02_case_has_62_buildings_168_hours_and_active_physics() -> None:
    case = load_v3_case(CASE, profile="v0-smoke")
    assert len(case.demand_nodes) == 62
    assert len(case.hours) == 168
    assert len(case.heat_demand_kW_th) == 62 * 168
    assert case.peak_capacity_margin_fraction == 0
    assert min(case.heat_pump_performance.cop_by_technology_hour.values()) > 0
    assert min(case.heat_pump_performance.capacity_ratio_by_technology_hour.values()) > 0
    assert all(pipe.heat_loss_kW_per_m == 0 for pipe in case.pipe_types)
    assert all(pipe.heat_loss_fraction_per_m == pytest.approx(0.00005) for pipe in case.pipe_types)
    assert all(pipe.pumping_kWh_e_per_kWh_th_transferred == pytest.approx(0.01) for pipe in case.pipe_types)


@pytest.mark.skipif(not CASE.exists(), reason="generated V0.2 scale case is not present")
def test_v02_candidate_graph_is_sparse_connected_and_not_a_star() -> None:
    sites = gpd.read_file(CASE / "candidate_sites.geojson")
    network = gpd.read_file(CASE / "candidate_network.geojson")
    site = str(sites.iloc[0].site_id)
    graph = nx.Graph()
    graph.add_edges_from(zip(network.node_from, network.node_to))
    assert graph.number_of_nodes() == 63
    assert graph.number_of_edges() == 64
    assert nx.is_connected(graph)
    assert sum(site in edge for edge in graph.edges()) < 62
    assert any(site not in edge for edge in graph.edges())
    assert network.length_m.gt(0).all()


@pytest.mark.skipif(not CASE.exists(), reason="generated V0.2 scale case is not present")
def test_v02_source_time_and_performance_audit() -> None:
    loads = pd.read_parquet(CASE / "building_hourly_loads.parquet")
    external = pd.read_parquet(CASE / "external_timeseries.parquet")
    performance = pd.read_csv(CASE / "performance_hourly.csv")
    assert tuple(sorted(loads.heating_season_hour.unique())) == tuple(range(816, 984))
    assert set(loads.heating_season_hour) == set(external.heating_season_hour)
    assert len(loads) == 10416
    assert external.outdoor_temperature_C.min() == pytest.approx(-1.9)
    assert external.outdoor_temperature_C.max() == pytest.approx(10.6)
    assert performance.COP.gt(0).all()
    assert performance.capacity_ratio.gt(0).all()
