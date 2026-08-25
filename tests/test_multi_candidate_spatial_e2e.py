from __future__ import annotations

import json
from pathlib import Path
import shutil

import geopandas as gpd
import networkx as nx
import pandas as pd
from pyomo.environ import Constraint, value
from shapely.geometry import Polygon
import yaml

from competition.core_model import CoreSolveResult, build_core_model, solve_core_model
from competition.pareto import ParetoPoint, ParetoRun
from competition.provisional_spatial import (
    MULTI_CANDIDATE_SOURCE,
    build_multi_candidate_provisional_network,
    write_provisional_spatial_outputs,
)
from competition.results.v3_standard import export_v3_results
from competition.solvers import solve_pyomo_model
from competition.validation.v3_inputs import load_v3_case


FIXTURE = Path(__file__).parent / "fixtures" / "v3_smoke_case"


def _build_case(tmp_path: Path) -> tuple[Path, object]:
    case_dir = tmp_path / "multi_candidate_case"
    shutil.copytree(FIXTURE, case_dir)
    building_ids = tuple(f"building_{index}" for index in range(1, 7))
    origins = (
        (114.3000, 30.5000), (114.3010, 30.5000),
        (114.3022, 30.5004), (114.3018, 30.5015),
        (114.3007, 30.5018), (114.2996, 30.5010),
    )
    polygons = [
        Polygon([
            (x, y), (x + 0.0002, y), (x + 0.0002, y + 0.0002),
            (x, y + 0.0002), (x, y),
        ])
        for x, y in origins
    ]
    buildings = gpd.GeoDataFrame(
        {
            "building_id": building_ids,
            "use_type": ["office"] * 6,
            "heated_area_m2": [500.0] * 6,
            "terminal_type": ["fan_coil"] * 6,
            "ventilation_system": ["dedicated_fresh_air"] * 6,
            "fresh_air_load_included": [True] * 6,
            "data_version": ["synthetic-v3-draft1"] * 6,
        },
        geometry=polygons,
        crs="EPSG:4326",
    )
    buildings.to_file(case_dir / "buildings.geojson", driver="GeoJSON")
    external = pd.read_parquet(case_dir / "external_timeseries.parquet")
    loads = pd.DataFrame([
        {
            "timestamp": timestamp,
            "building_id": building_id,
            "heating_kW": 5.0 + index,
            "data_version": "synthetic-v3-draft1",
        }
        for timestamp in external["timestamp"]
        for index, building_id in enumerate(building_ids)
    ])
    loads.to_parquet(case_dir / "building_hourly_loads.parquet", index=False)
    pd.DataFrame([
        {
            "building_id": building_id,
            "zone_id": f"{building_id}-01",
            "zone_use_type": "office",
            "zone_area_m2": 500.0,
            "zone_archetype_id": "synthetic_office",
            "zone_scale_factor": 1.0,
        }
        for building_id in building_ids
    ]).to_csv(case_dir / "building_archetype_map.csv", index=False)

    spatial = build_multi_candidate_provisional_network(
        buildings,
        loads,
        data_version="synthetic-v3-draft1",
        candidate_count=5,
        input_building_source="synthetic_six_building_test_fixture",
        local_extra_edge_count=1,
    )
    write_provisional_spatial_outputs(spatial, case_dir)
    config_path = case_dir / "case_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["spatial"]["candidate_site_count_min"] = 5
    config["spatial"]["candidate_site_count_max"] = 5
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return case_dir, load_v3_case(case_dir, profile="v0-smoke")


def _point(mode: str, solved: CoreSolveResult) -> ParetoPoint:
    model = solved.model
    return ParetoPoint(
        point_id=f"{mode}_single_objective",
        mode=mode,
        labels=("single_objective_smoke",),
        epsilon_kgCO2e_per_year=None,
        annual_real_cost_CNY_per_year=value(model.annual_real_cost_CNY_per_year),
        annual_operating_carbon_kgCO2e_per_year=value(
            model.annual_operating_physical_carbon_kgCO2e_per_year
        ),
        annual_hns_penalty_CNY_per_year=value(model.annual_hns_penalty_CNY_per_year),
        unserved_heat_kWh=sum(
            value(model.unserved_heat_kW[node, hour])
            for node in model.DEMAND_NODES for hour in model.HOURS
        ),
    )


def test_multi_candidate_generator_runs_tiny_end_to_end(tmp_path: Path) -> None:
    case_dir, case = _build_case(tmp_path)
    assert len(case.candidate_station_nodes) == 5
    assert case.site_node is None
    sites = gpd.read_file(case_dir / "candidate_sites.geojson")
    network = gpd.read_file(case_dir / "candidate_network.geojson")
    assert sites["candidate_source"].eq(MULTI_CANDIDATE_SOURCE).all()
    assert len(network[network["edge_type"].eq("station_access")]) == 5
    graph = nx.Graph()
    graph.add_edges_from(network[["node_from", "node_to"]].itertuples(index=False, name=None))
    assert nx.is_connected(graph)
    assert all(graph.degree[station] == 1 for station in case.candidate_station_nodes)

    solutions: dict[str, CoreSolveResult] = {}
    points: dict[str, ParetoPoint] = {}
    for mode in case.modes:
        solved = solve_core_model(case.to_core_input(mode), case.solver)
        point = _point(mode, solved)
        solutions[point.point_id] = solved
        points[mode] = point

    central_model = solutions[points["central"].point_id].model
    selected = [
        str(station) for station in central_model.STATIONS
        if value(central_model.station_built[station]) > 0.5
    ]
    assert len(selected) == 1
    assert all(value(central_model.connected[node]) > 0.5 for node in central_model.DEMAND_NODES)
    assert points["central"].unserved_heat_kWh <= 1e-6

    # Prove location/network cost, not candidate_rank, drives the choice:
    # compare the unconstrained winner with the optimum under each forced site.
    forced_costs = {}
    for station in case.candidate_station_nodes:
        model = build_core_model(case.to_core_input("central"))
        model.force_test_station = Constraint(expr=model.station_built[station] == 1)
        solve_pyomo_model(model, case.solver)
        forced_costs[station] = value(model.annual_real_cost_CNY_per_year)
    assert selected[0] == min(forced_costs, key=lambda station: (forced_costs[station], station))
    assert len({round(cost, 6) for cost in forced_costs.values()}) > 1

    distributed_model = solutions[points["distributed"].point_id].model
    assert sum(round(value(distributed_model.station_built[s])) for s in distributed_model.STATIONS) == 0
    assert sum(round(value(distributed_model.pipe_built[e])) for e in distributed_model.SEGMENTS) == 0
    assert all(value(distributed_model.local_installed[node]) > 0.5 for node in distributed_model.DEMAND_NODES)
    assert points["distributed"].unserved_heat_kWh <= 1e-6

    hybrid_model = solutions[points["hybrid"].point_id].model
    assert sum(round(value(hybrid_model.station_built[s])) for s in hybrid_model.STATIONS) <= 1
    assert points["hybrid"].unserved_heat_kWh <= 1e-6

    pareto = ParetoRun(
        mode_frontiers={mode: (point,) for mode, point in points.items()},
        combined_frontier=tuple(points.values()),
        solutions=solutions,
    )
    exported = export_v3_results(case, pareto, tmp_path / "results", case_dir)
    central_dir = exported.output_dir / "solutions" / points["central"].point_id
    decisions = pd.read_csv(central_dir / "station_decisions.csv")
    assert len(decisions) == 5
    assert decisions["station_built"].sum() == 1
    assert decisions.loc[decisions.station_built.eq(1), "station_id"].item() == selected[0]
    capacities = pd.read_csv(central_dir / "capacity_decisions.csv")
    nonzero_central = capacities[
        capacities["asset_type"].eq("central_generation")
        & capacities["capacity_kW_th"].gt(1e-8)
    ]
    assert set(nonzero_central["station_id"]) == {selected[0]}
    dispatch = pd.read_parquet(central_dir / "dispatch_hourly.parquet")
    central_dispatch = dispatch[
        dispatch["asset_id"].str.startswith(("central_hp", "central_boiler"))
        & dispatch["heat_output_kW_th"].gt(1e-8)
    ]
    assert set(central_dispatch["station_id"]) == {selected[0]}
    qa = json.loads((central_dir / "qa_report.json").read_text(encoding="utf-8"))
    assert qa["passed"] is True
    assert qa["max_heat_balance_error_kW"] <= 1e-6

