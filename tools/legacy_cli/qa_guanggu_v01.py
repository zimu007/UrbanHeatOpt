"""Focused acceptance QA for the Guanggu V0.1 exported representative solutions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd


TOL = 1e-6


def _solution_qa(case: Path, solution: Path, mode: str) -> dict[str, object]:
    performance = pd.read_csv(case / "performance_hourly.csv").rename(columns={"season_hour": "season_hour_source"})
    performance["hour"] = np.arange(1, len(performance) + 1)
    dispatch = pd.read_parquet(solution / "dispatch_hourly.parquet")
    balances = pd.read_csv(solution / "balance_check.csv")
    capacities = pd.read_csv(solution / "capacity_decisions.csv")
    connections = pd.read_csv(solution / "building_connection.csv")
    network_hourly = pd.read_csv(solution / "network_hourly.csv")
    network_decisions = gpd.read_file(solution / "network_decisions.geojson")
    storage = pd.read_csv(solution / "storage_decisions.csv").iloc[0]
    hp = dispatch[dispatch["asset_id"].str.startswith(("central_hp", "local_hp@"))].merge(
        performance[["hour", "COP", "capacity_ratio"]], on="hour", how="left"
    )
    hp_electric_error = float(
        (hp["electricity_input_kW_e"] - hp["heat_output_kW_th"] / hp["COP"]).abs().max()
    )
    capacity_by_asset = capacities.set_index("asset_id")["capacity_kW_th"].to_dict()
    hp["capacity_kW_th"] = hp["asset_id"].map(capacity_by_asset)
    hp_capacity_violation = float(
        (hp["heat_output_kW_th"] - hp["capacity_kW_th"] * hp["capacity_ratio"])
        .clip(lower=0).max()
    )
    built = network_decisions.set_index("segment_id")["built"].astype(int)
    network_hourly["built"] = network_hourly["segment_id"].map(built)
    unbuilt_flow = float(network_hourly.loc[network_hourly["built"].eq(0), "flow_kW_th"].abs().max() or 0)
    pipe_violation = float(
        (network_hourly["flow_kW_th"].abs() - network_hourly["capacity_kW_th"])
        .clip(lower=0).max()
    )
    station_incidence = network_decisions.set_index("segment_id").apply(
        lambda row: -1.0 if row["node_from"] == "synthetic_v01_station"
        else (1.0 if row["node_to"] == "synthetic_v01_station" else 0.0), axis=1
    )
    station_flow = network_hourly.assign(
        incidence=network_hourly["segment_id"].map(station_incidence)
    ).groupby("hour").apply(
        lambda frame: float((frame["incidence"] * frame["flow_kW_th"]).sum()),
        include_groups=False,
    )
    central_dispatch = dispatch[dispatch["asset_id"].isin(["central_hp", "central_boiler", "central_tes"])]
    central_hourly = central_dispatch.groupby("hour").agg(
        heat=("heat_output_kW_th", "sum"), charge=("storage_charge_kW_th", "sum"),
        discharge=("storage_discharge_kW_th", "sum"),
    )
    central_balance_residual = float(
        (central_hourly["heat"] + central_hourly["discharge"] - central_hourly["charge"] + station_flow).abs().max()
    )
    storage_rows = dispatch[dispatch["asset_id"].eq("central_tes")].sort_values("hour")
    eta_c, eta_d, loss = 0.95, 0.95, 0.0060774
    previous_soc = np.roll(storage_rows["storage_soc_kWh_th"].to_numpy(), 1)
    recurrence = storage_rows["storage_soc_kWh_th"].to_numpy() - (
        previous_soc * (1 - loss)
        + storage_rows["storage_charge_kW_th"].to_numpy() * eta_c
        - storage_rows["storage_discharge_kW_th"].to_numpy() / eta_d
    )
    charge_ratio_violation = max(
        0.0, float(storage["charge_capacity_kW_th"] - 0.25 * storage["energy_capacity_kWh_th"])
    )
    discharge_ratio_violation = max(
        0.0, float(storage["discharge_capacity_kW_th"] - 0.25 * storage["energy_capacity_kWh_th"])
    )
    built_graph = nx.Graph()
    built_edges = network_decisions[network_decisions["built"].astype(int).eq(1)]
    built_graph.add_edges_from(zip(built_edges["node_from"], built_edges["node_to"]))
    connected_ids = connections.loc[connections["connected"].eq(1), "building_id"].astype(str)
    station = "synthetic_v01_station"
    built_paths_ok = all(
        station in built_graph and node in built_graph and nx.has_path(built_graph, station, node)
        for node in connected_ids
    )
    mode_ok = {
        "central": bool(connections["connected"].eq(1).all() and balances["local_heat_kW_th"].abs().max() <= TOL),
        "distributed": bool(connections["connected"].eq(0).all() and network_hourly["flow_kW_th"].abs().max() <= TOL),
        "hybrid": True,
    }[mode]
    result = {
        "mode": mode,
        "max_building_balance_residual_kW": float(balances["residual_kW"].abs().max()),
        "max_central_balance_residual_kW": central_balance_residual,
        "unserved_heat_kWh": float(balances["unserved_kW_th"].sum()),
        "hp_electricity_recompute_error_kW": hp_electric_error,
        "hp_capacity_violation_kW": hp_capacity_violation,
        "unbuilt_pipe_flow_kW": unbuilt_flow,
        "pipe_capacity_violation_kW": pipe_violation,
        "storage_soc_recurrence_residual_kWh": float(np.abs(recurrence).max()),
        "storage_cyclic_residual_kWh": float(recurrence[0]),
        "storage_charge_ratio_violation_kW": charge_ratio_violation,
        "storage_discharge_ratio_violation_kW": discharge_ratio_violation,
        "connected_buildings_have_built_path": built_paths_ok,
        "mode_rules_ok": mode_ok,
    }
    result["passed"] = all(
        value <= TOL for key, value in result.items()
        if key.endswith(("_kW", "_kWh")) and isinstance(value, float)
    ) and built_paths_ok and mode_ok
    return result


def run(case: Path, run_dir: Path) -> dict[str, object]:
    loads = pd.read_parquet(case / "building_hourly_loads.parquet")
    external = pd.read_parquet(case / "external_timeseries.parquet")
    candidate = gpd.read_file(case / "candidate_network.geojson")
    graph = nx.Graph()
    graph.add_edges_from(zip(candidate["node_from"], candidate["node_to"]))
    input_qa = {
        "building_count": int(loads["building_id"].nunique()),
        "hour_count": int(loads["heating_season_hour"].nunique()),
        "load_rows": int(len(loads)),
        "load_external_hours_match": set(loads["heating_season_hour"]) == set(external["heating_season_hour"]),
        "data_version_ok": bool(loads["data_version"].eq("guanggu-v0.3-20260823").all()),
        "no_bad_load_values": bool(not loads.duplicated(["building_id", "timestamp"]).any() and loads["heating_kW"].notna().all() and loads["heating_kW"].ge(0).all()),
        "candidate_graph_connected": nx.is_connected(graph),
        "building_building_edge_exists": bool(((candidate["node_from"] != "synthetic_v01_station") & (candidate["node_to"] != "synthetic_v01_station")).any()),
        "non_station_direct_building_exists": len(set(loads["building_id"]) - set(candidate.loc[candidate["node_from"].eq("synthetic_v01_station"), "node_to"])) > 0,
    }
    representatives = {"central": "central-cost", "distributed": "distributed-carbon", "hybrid": "hybrid-cost"}
    modes = {mode: _solution_qa(case, run_dir / "solutions" / name, mode) for mode, name in representatives.items()}
    payload = {
        "input": input_qa,
        "modes": modes,
        "passed": input_qa["building_count"] == 6 and input_qa["hour_count"] == 168
        and input_qa["load_rows"] == 1008
        and all(value for key, value in input_qa.items() if isinstance(value, bool))
        and all(item["passed"] for item in modes.values()),
    }
    (run_dir / "qa_v01.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.case, args.run_dir), indent=2))
