"""Standard V3 result export and independent physical/economic/carbon QA."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from pyomo.environ import value

from competition.canonical import CanonicalCaseData
from competition.pareto import (
    ParetoPoint,
    ParetoRun,
    point_to_dict,
    select_representative_points,
)


@dataclass(frozen=True, slots=True)
class V3StandardExport:
    output_dir: Path
    pareto_csv: Path
    candidate_sites_geojson: Path
    qa_summary_json: Path
    representatives_json: Path | None = None
    combined_frontier_csv: Path | None = None
    figure_paths: tuple[Path, ...] = ()


def _json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _render_figures(
    case: CanonicalCaseData,
    pareto: ParetoRun,
    output: Path,
    case_dir: Path,
    representatives: dict[str, Any],
) -> tuple[Path, ...]:
    """Render auditable static figures from exported numeric solution files."""

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures = output / "figures"
    figures.mkdir(exist_ok=False)
    created: list[Path] = []
    colours = {"central": "#1f77b4", "distributed": "#ff7f0e", "hybrid": "#2ca02c"}

    fig, ax = plt.subplots(figsize=(8, 5), constrained_layout=True)
    for mode, all_points in pareto.mode_all_points.items():
        ax.scatter(
            [point.annual_operating_carbon_kgCO2e_per_year / 1000 for point in all_points],
            [point.annual_real_cost_CNY_per_year for point in all_points],
            color=colours[mode], alpha=0.2, s=18,
        )
        frontier = pareto.mode_frontiers[mode]
        ax.plot(
            [point.annual_operating_carbon_kgCO2e_per_year / 1000 for point in frontier],
            [point.annual_real_cost_CNY_per_year for point in frontier],
            marker="o", color=colours[mode], label=mode,
        )
    if pareto.combined_frontier:
        ax.scatter(
            [point.annual_operating_carbon_kgCO2e_per_year / 1000 for point in pareto.combined_frontier],
            [point.annual_real_cost_CNY_per_year for point in pareto.combined_frontier],
            marker="x", color="black", s=55, label="combined non-dominated",
        )
    ax.set_xlabel("Operating carbon (tCO2e/period)")
    ax.set_ylabel("Annualized real cost (CNY/period)")
    ax.set_title("Cost-carbon Pareto frontiers")
    ax.grid(alpha=0.25)
    ax.legend()
    path = figures / "pareto_frontiers.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    created.append(path)

    mode_minimums = {
        mode: select_representative_points(frontier)["minimum_cost"]
        for mode, frontier in pareto.mode_frontiers.items()
    }
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    modes = list(pareto.mode_frontiers)
    axes[0].bar(
        modes,
        [mode_minimums[mode]["annual_real_cost_CNY_per_year"] for mode in modes],
        color=[colours[mode] for mode in modes],
    )
    axes[0].set_title("Minimum-cost point")
    axes[0].set_ylabel("CNY/period")
    axes[1].bar(
        modes,
        [mode_minimums[mode]["annual_operating_carbon_kgCO2e_per_year"] / 1000 for mode in modes],
        color=[colours[mode] for mode in modes],
    )
    axes[1].set_title("Carbon at minimum-cost point")
    axes[1].set_ylabel("tCO2e/period")
    path = figures / "three_modes_comparison.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    created.append(path)

    buildings_name = case.raw_config.get("files", {}).get("buildings")
    buildings_path = case_dir / buildings_name if isinstance(buildings_name, str) else None
    buildings = (
        gpd.read_file(buildings_path)
        if buildings_path is not None and buildings_path.is_file()
        else None
    )
    candidate_sites = gpd.read_file(case_dir / case.raw_config["files"]["candidate_sites"])
    for mode in modes:
        point_id = str(mode_minimums[mode]["point_id"])
        solution_dir = output / "solutions" / point_id
        dispatch = pd.read_parquet(solution_dir / "dispatch_hourly.parquet")
        dispatch["technology"] = dispatch["asset_id"].astype(str).str.split("@").str[0]
        hourly = dispatch.groupby(["timestamp", "technology"], sort=True)["heat_output_kW_th"].sum().unstack(fill_value=0)
        fig, ax = plt.subplots(figsize=(10, 4), constrained_layout=True)
        hourly.plot(ax=ax)
        ax.set_title(f"{mode}: minimum-cost hourly heat dispatch")
        ax.set_ylabel("kW_th")
        ax.grid(alpha=0.2)
        path = figures / f"dispatch_{mode}_minimum_cost.png"
        fig.savefig(path, dpi=180)
        plt.close(fig)
        created.append(path)

        network = gpd.read_file(solution_dir / "network_decisions.geojson")
        fig, ax = plt.subplots(figsize=(7, 7), constrained_layout=True)
        if buildings is not None and not buildings.empty:
            buildings.plot(ax=ax, facecolor="#eeeeee", edgecolor="#666666", linewidth=0.5)
        unbuilt = network.loc[network["built"].eq(0)]
        built = network.loc[network["built"].eq(1)]
        if not unbuilt.empty:
            unbuilt.plot(ax=ax, color="#bbbbbb", linewidth=0.6)
        if not built.empty:
            built.plot(ax=ax, color="#d62728", linewidth=2.0)
        if not candidate_sites.empty:
            candidate_sites.plot(ax=ax, color="#9467bd", marker="^", markersize=35)
        ax.set_title(f"{mode}: provisional candidate/built network")
        if case.raw_config.get("spatial", {}).get("road_constrained"):
            fig.text(
                0.01,
                0.01,
                "Concept roadside/greenbelt corridor; construction not verified. "
                "Map data © OpenStreetMap contributors, ODbL 1.0.",
                fontsize=7,
            )
        ax.set_axis_off()
        path = figures / f"network_{mode}_minimum_cost.png"
        fig.savefig(path, dpi=180)
        plt.close(fig)
        created.append(path)

        electricity = float(dispatch["electricity_input_kW_e"].sum())
        gas = float(dispatch["gas_input_kW_LHV"].sum())
        heat = float(dispatch["heat_output_kW_th"].sum())
        charge = float(dispatch["storage_charge_kW_th"].sum())
        discharge = float(dispatch["storage_discharge_kW_th"].sum())
        fig, ax = plt.subplots(figsize=(8, 4), constrained_layout=True)
        labels = ["Electricity input", "Gas input (LHV)", "Heat output", "TES charge", "TES discharge"]
        values = [electricity, gas, heat, charge, discharge]
        ax.barh(labels, values, color=["#1f77b4", "#ff7f0e", "#d62728", "#9467bd", "#2ca02c"])
        ax.set_xlabel("Period sum of hourly power values (kWh at 1 h steps)")
        ax.set_title(f"{mode}: source-network-load-storage energy totals")
        path = figures / f"energy_flow_{mode}_minimum_cost.png"
        fig.savefig(path, dpi=180)
        plt.close(fig)
        created.append(path)

    combined_minimum = representatives["combined"]["minimum_cost"]["point_id"]
    if combined_minimum:
        solution_dir = output / "solutions" / str(combined_minimum)
        cost = pd.read_csv(solution_dir / "cost_breakdown.csv")
        carbon = pd.read_csv(solution_dir / "carbon_breakdown.csv")
        fig, axes = plt.subplots(1, 2, figsize=(12, 5), constrained_layout=True)
        axes[0].barh(cost["category"], cost["annual_cost_CNY_per_year"])
        axes[0].set_title("Cost breakdown: combined minimum-cost point")
        axes[0].set_xlabel("CNY/period")
        axes[1].barh(carbon["category"], carbon["annual_carbon_kgCO2e_per_year"] / 1000)
        axes[1].set_title("Carbon breakdown")
        axes[1].set_xlabel("tCO2e/period")
        path = figures / "cost_carbon_breakdown_combined_minimum_cost.png"
        fig.savefig(path, dpi=180)
        plt.close(fig)
        created.append(path)
    return tuple(created)


def _selected_pipe_type(model: Any, segment: str) -> str | None:
    if len(model.PIPE_LEVELS) == 0:
        return None
    for pipe_type in model.PIPE_LEVELS:
        if value(model.pipe_level_built[segment, pipe_type]) > 0.5:
            return str(pipe_type)
    return None


def _station_ids(model: Any) -> tuple[str, ...]:
    return tuple(str(station) for station in model.STATIONS)


def _selected_station_ids(model: Any) -> tuple[str, ...]:
    return tuple(
        station for station in _station_ids(model)
        if value(model.station_built[station]) > 0.5
    )


def _is_connected(model: Any, site: str, target: str) -> bool:
    adjacency: dict[str, set[str]] = {}
    for segment in model.SEGMENTS:
        if value(model.pipe_built[segment]) <= 0.5:
            continue
        endpoints = [
            str(node)
            for node in model.NODES
            if abs(value(model.incidence[node, segment])) > 0.5
        ]
        if len(endpoints) == 2:
            adjacency.setdefault(endpoints[0], set()).add(endpoints[1])
            adjacency.setdefault(endpoints[1], set()).add(endpoints[0])
    reached = {site}
    pending = [site]
    while pending:
        node = pending.pop()
        for neighbor in adjacency.get(node, set()):
            if neighbor not in reached:
                reached.add(neighbor)
                pending.append(neighbor)
    return target in reached


def _manual_costs(model: Any) -> dict[str, float]:
    device = sum(
        value(model.central_capacity_by_station_kW[station, tech])
        * value(model.central_capex_CNY_per_kW[tech])
        * value(model.central_crf[tech])
        for station in model.STATIONS for tech in model.CENTRAL_TECHNOLOGIES
    ) + sum(
        value(model.local_capacity_kW[node])
        * value(model.local_capex_CNY_per_kW)
        * value(model.local_crf)
        for node in model.DEMAND_NODES
    )
    if len(model.PIPE_LEVELS):
        network = sum(
            value(model.pipe_level_built[segment, pipe_type])
            * value(model.segment_length_m[segment])
            * value(model.pipe_level_capex_CNY_per_m[pipe_type])
            * value(model.pipe_level_crf[pipe_type])
            for segment in model.SEGMENTS
            for pipe_type in model.PIPE_LEVELS
        )
    else:
        network = sum(
            value(model.pipe_built[segment]) * value(model.segment_length_m[segment])
            * value(model.pipe_capex_CNY_per_m[segment]) * value(model.pipe_crf[segment])
            for segment in model.SEGMENTS
        )
    connection = sum(
        value(model.connected[node]) * value(model.connection_capex_CNY[node])
        * value(model.connection_crf[node]) for node in model.DEMAND_NODES
    )
    station = sum(value(model.station_built[item]) for item in model.STATIONS) * value(model.station_fixed_capex_CNY) * value(model.station_crf)
    storage = sum(
        value(model.storage_energy_capacity_by_station_kWh[item]) * value(model.storage_capex_CNY_per_kWh)
        + value(model.storage_power_cost_capacity_by_station_kW[item]) * value(model.storage_power_capex_CNY_per_kW)
        + value(model.storage_installed_by_station[item]) * value(model.storage_fixed_capex_CNY)
        for item in model.STATIONS
    ) * value(model.storage_crf)
    fixed_om = sum(
        value(model.central_capacity_by_station_kW[station, tech]) * value(model.central_capex_CNY_per_kW[tech])
        * value(model.central_fixed_maintenance_fraction_per_year[tech])
        for station in model.STATIONS for tech in model.CENTRAL_TECHNOLOGIES
    ) + sum(
        value(model.local_capacity_kW[node]) * value(model.local_capex_CNY_per_kW)
        * value(model.local_fixed_maintenance_fraction_per_year)
        for node in model.DEMAND_NODES
    )
    variable_om = sum(
        value(model.central_heat_output_by_station_kW[station, tech, hour]) * value(model.time_weight_h_per_year[hour])
        * value(model.central_variable_om_CNY_per_kWh_th[tech])
        for station in model.STATIONS for tech in model.CENTRAL_TECHNOLOGIES for hour in model.HOURS
    ) + sum(
        value(model.local_heat_output_kW[node, hour]) * value(model.time_weight_h_per_year[hour])
        * value(model.local_variable_om_CNY_per_kWh_th)
        for node in model.DEMAND_NODES for hour in model.HOURS
    )
    electricity = sum(
        value(model.central_electricity_input_by_station_kW_e[station, tech, hour]) * value(model.time_weight_h_per_year[hour])
        * value(model.electricity_price_CNY_per_kWh_e[hour])
        for station in model.STATIONS for tech in model.CENTRAL_AIR_SOURCE_HEAT_PUMPS for hour in model.HOURS
    ) + sum(
        value(model.local_electricity_input_kW_e[node, hour]) * value(model.time_weight_h_per_year[hour])
        * value(model.electricity_price_CNY_per_kWh_e[hour])
        for node in model.DEMAND_NODES for hour in model.HOURS
    ) + sum(
        value(model.pumping_electricity_input_kW_e[hour])
        * value(model.time_weight_h_per_year[hour])
        * value(model.electricity_price_CNY_per_kWh_e[hour])
        for hour in model.HOURS
    )
    gas = sum(
        value(model.gas_input_by_station_kW_LHV[station, tech, hour]) * value(model.time_weight_h_per_year[hour])
        * value(model.gas_price_CNY_per_kWh_LHV[hour])
        for station in model.STATIONS for tech in model.CENTRAL_GAS_BOILERS for hour in model.HOURS
    )
    return {
        "device_capex": float(device), "network_capex": float(network),
        "connection_capex": float(connection), "station_capex": float(station),
        "storage_capex": float(storage), "fixed_om": float(fixed_om),
        "variable_om": float(variable_om), "electricity": float(electricity),
        "gas": float(gas),
    }


def _manual_carbon(model: Any) -> dict[str, float]:
    electricity = sum(
        value(model.central_electricity_input_by_station_kW_e[station, tech, hour]) * value(model.time_weight_h_per_year[hour])
        * value(model.electricity_carbon_kgCO2e_per_kWh_e[hour])
        for station in model.STATIONS for tech in model.CENTRAL_AIR_SOURCE_HEAT_PUMPS for hour in model.HOURS
    ) + sum(
        value(model.local_electricity_input_kW_e[node, hour]) * value(model.time_weight_h_per_year[hour])
        * value(model.electricity_carbon_kgCO2e_per_kWh_e[hour])
        for node in model.DEMAND_NODES for hour in model.HOURS
    ) + sum(
        value(model.pumping_electricity_input_kW_e[hour])
        * value(model.time_weight_h_per_year[hour])
        * value(model.electricity_carbon_kgCO2e_per_kWh_e[hour])
        for hour in model.HOURS
    )
    gas = sum(
        value(model.gas_input_by_station_kW_LHV[station, tech, hour]) * value(model.time_weight_h_per_year[hour])
        * value(model.gas_carbon_kgCO2e_per_kWh_LHV[hour])
        for station in model.STATIONS for tech in model.CENTRAL_GAS_BOILERS for hour in model.HOURS
    )
    return {"electricity": float(electricity), "gas": float(gas)}


def export_v3_solution(
    case: CanonicalCaseData,
    point: ParetoPoint,
    solution: Any,
    output: Path,
    network_input: gpd.GeoDataFrame,
    sites_input: gpd.GeoDataFrame,
) -> dict[str, Any]:
    model = solution.model
    target = output / "solutions" / point.point_id
    target.mkdir(parents=True, exist_ok=False)
    site_metadata = {
        str(row.site_id): row
        for row in sites_input.itertuples(index=False)
    }
    station_rows = []
    for station in model.STATIONS:
        station_id = str(station)
        metadata = site_metadata[station_id]
        station_rows.append({
            "mode": point.mode,
            "station_id": station_id,
            "station_built": round(value(model.station_built[station])),
            "candidate_rank": getattr(metadata, "candidate_rank", None),
            "station_type": getattr(
                metadata, "station_type", "candidate_regional_energy_station"
            ),
            "location_source": getattr(
                metadata,
                "location_source",
                getattr(metadata, "candidate_source", getattr(metadata, "source", None)),
            ),
            "parameter_status": getattr(metadata, "parameter_status", None),
        })
    pd.DataFrame(station_rows).to_csv(target / "station_decisions.csv", index=False)

    capacities = [
        {
            "mode": point.mode,
            "asset_id": (
                str(tech)
                if len(model.STATIONS) == 1
                else f"{tech}@{station}"
            ),
            "asset_type": "central_generation",
            "station_id": str(station),
            "node_id": str(station),
            "technology_id": str(tech),
            "installed": round(value(model.central_installed_by_station[station, tech])),
            "capacity_kW_th": value(model.central_capacity_by_station_kW[station, tech]),
        }
        for station in model.STATIONS for tech in model.CENTRAL_TECHNOLOGIES
    ] + [
        {"mode": point.mode, "asset_id": f"local_hp@{node}", "asset_type": "local_generation",
         "station_id": None, "node_id": str(node), "technology_id": str(model.local_technology_id),
         "installed": round(value(model.local_installed[node])), "capacity_kW_th": value(model.local_capacity_kW[node])}
        for node in model.DEMAND_NODES
    ]
    pd.DataFrame(capacities).to_csv(target / "capacity_decisions.csv", index=False)
    pd.DataFrame([
        {"building_id": str(node), "connected": round(value(model.connected[node])),
         "service_mode": "central" if value(model.connected[node]) > 0.5 else "distributed"}
        for node in model.DEMAND_NODES
    ]).to_csv(target / "building_connection.csv", index=False)
    pd.DataFrame([
        {
            "mode": point.mode,
            "station_id": str(station),
            "technology_id": case.storage.technology_id,
            "installed": round(value(model.storage_installed_by_station[station])),
            "energy_capacity_kWh_th": value(model.storage_energy_capacity_by_station_kWh[station]),
            "charge_capacity_kW_th": value(model.storage_charge_capacity_by_station_kW[station]),
            "discharge_capacity_kW_th": value(model.storage_discharge_capacity_by_station_kW[station]),
        }
        for station in model.STATIONS
    ]).to_csv(target / "storage_decisions.csv", index=False)

    network = network_input.copy()
    built = {str(segment): round(value(model.pipe_built[segment])) for segment in model.SEGMENTS}
    network["built"] = network["segment_id"].map(built).fillna(0).astype(int)
    network["selected_pipe_type_id"] = network["segment_id"].map(
        {str(segment): _selected_pipe_type(model, str(segment)) for segment in model.SEGMENTS}
    )
    network["capacity_kW_th"] = network["segment_id"].map(
        {str(segment): value(model.pipe_capacity_kW[segment]) for segment in model.SEGMENTS}
    )
    network.to_file(target / "network_decisions.geojson", driver="GeoJSON")

    dispatch_rows: list[dict[str, Any]] = []
    network_rows: list[dict[str, Any]] = []
    balance_rows: list[dict[str, Any]] = []
    timestamp_by_hour = dict(zip(case.hours, case.timestamps, strict=True))
    selected_stations = _selected_station_ids(model)
    pump_station_id = selected_stations[0] if len(selected_stations) == 1 else None
    for hour in model.HOURS:
        timestamp = timestamp_by_hour[int(hour)]
        for station in model.STATIONS:
            for tech in model.CENTRAL_TECHNOLOGIES:
                dispatch_rows.append({
                    "timestamp": timestamp, "hour": int(hour),
                    "asset_id": str(tech) if len(model.STATIONS) == 1 else f"{tech}@{station}",
                    "station_id": str(station), "node_id": str(station),
                    "heat_output_kW_th": value(model.central_heat_output_by_station_kW[station, tech, hour]),
                    "electricity_input_kW_e": value(model.central_electricity_input_by_station_kW_e[station, tech, hour]) if tech in model.CENTRAL_AIR_SOURCE_HEAT_PUMPS else 0.0,
                    "gas_input_kW_LHV": value(model.gas_input_by_station_kW_LHV[station, tech, hour]) if tech in model.CENTRAL_GAS_BOILERS else 0.0,
                    "storage_charge_kW_th": 0.0, "storage_discharge_kW_th": 0.0, "storage_soc_kWh_th": 0.0,
                })
            dispatch_rows.append({
                "timestamp": timestamp, "hour": int(hour),
                "asset_id": case.storage.technology_id if len(model.STATIONS) == 1 else f"{case.storage.technology_id}@{station}",
                "station_id": str(station), "node_id": str(station),
                "heat_output_kW_th": 0.0,
                "electricity_input_kW_e": 0.0, "gas_input_kW_LHV": 0.0,
                "storage_charge_kW_th": value(model.storage_charge_by_station_kW[station, hour]),
                "storage_discharge_kW_th": value(model.storage_discharge_by_station_kW[station, hour]),
                "storage_soc_kWh_th": value(model.storage_soc_by_station_kWh[station, hour]),
            })
        dispatch_rows.append({
            "timestamp": timestamp, "hour": int(hour), "asset_id": "network_pump",
            "station_id": pump_station_id, "node_id": pump_station_id, "heat_output_kW_th": 0.0,
            "electricity_input_kW_e": value(model.pumping_electricity_input_kW_e[hour]),
            "gas_input_kW_LHV": 0.0, "storage_charge_kW_th": 0.0,
            "storage_discharge_kW_th": 0.0, "storage_soc_kWh_th": 0.0,
        })
        for segment in model.SEGMENTS:
            selected = _selected_pipe_type(model, str(segment))
            heat_loss = 0.0
            pumping = 0.0
            if selected is not None:
                absolute_flow = abs(value(model.heat_flow_kW[segment, hour]))
                heat_loss = (
                    value(model.segment_length_m[segment])
                    * (
                        value(model.pipe_level_heat_loss_kW_per_m[selected])
                        + value(model.pipe_level_heat_loss_fraction_per_m[selected])
                        * absolute_flow
                    )
                )
                pumping = (
                    absolute_flow
                    * value(model.pipe_level_pumping_kWh_e_per_kWh_th[selected])
                )
            network_rows.append({
                "timestamp": timestamp,
                "hour": int(hour),
                "segment_id": str(segment),
                "selected_pipe_type_id": selected,
                "flow_kW_th": value(model.heat_flow_kW[segment, hour]),
                "capacity_kW_th": value(model.pipe_capacity_kW[segment]),
                "heat_loss_kW_th": heat_loss,
                "pumping_electricity_kW_e": pumping,
            })
        for node in model.DEMAND_NODES:
            dispatch_rows.append({
                "timestamp": timestamp, "hour": int(hour), "asset_id": f"local_hp@{node}",
                "station_id": None, "node_id": str(node), "heat_output_kW_th": value(model.local_heat_output_kW[node, hour]),
                "electricity_input_kW_e": value(model.local_electricity_input_kW_e[node, hour]),
                "gas_input_kW_LHV": 0.0, "storage_charge_kW_th": 0.0,
                "storage_discharge_kW_th": 0.0, "storage_soc_kWh_th": 0.0,
            })
            demand = value(model.heat_demand_kW[node, hour])
            network_heat = value(model.network_heat_kW[node, hour])
            local = value(model.local_heat_output_kW[node, hour])
            hns = value(model.unserved_heat_kW[node, hour])
            balance_rows.append({
                "timestamp": timestamp, "hour": int(hour), "node_id": str(node),
                "demand_kW_th": demand, "network_heat_kW_th": network_heat,
                "local_heat_kW_th": local, "unserved_kW_th": hns,
                "residual_kW": network_heat + local + hns - demand,
            })
    pd.DataFrame(dispatch_rows).to_parquet(target / "dispatch_hourly.parquet", index=False)
    pd.DataFrame(network_rows).to_csv(target / "network_hourly.csv", index=False)
    balance = pd.DataFrame(balance_rows)
    balance.to_csv(target / "balance_check.csv", index=False)

    costs = _manual_costs(model)
    real_total = sum(costs.values())
    cost_rows = [{"category": key, "annual_cost_CNY_per_year": amount} for key, amount in costs.items()]
    cost_rows.extend([
        {"category": "annual_real_cost", "annual_cost_CNY_per_year": real_total},
        {"category": "hns_penalty", "annual_cost_CNY_per_year": value(model.annual_hns_penalty_CNY_per_year)},
        {"category": "policy_carbon_cost", "annual_cost_CNY_per_year": value(model.annual_policy_carbon_cost_CNY_per_year)},
    ])
    pd.DataFrame(cost_rows).to_csv(target / "cost_breakdown.csv", index=False)
    carbons = _manual_carbon(model)
    carbon_total = sum(carbons.values())
    pd.DataFrame([
        {"category": key, "annual_carbon_kgCO2e_per_year": amount} for key, amount in carbons.items()
    ] + [{"category": "annual_operating_physical_carbon", "annual_carbon_kgCO2e_per_year": carbon_total}]).to_csv(target / "carbon_breakdown.csv", index=False)

    max_balance = float(balance["residual_kW"].abs().max())
    weighted_hns = float(sum(value(model.unserved_heat_kW[node, hour]) * value(model.time_weight_h_per_year[hour]) for node in model.DEMAND_NODES for hour in model.HOURS))
    pipe_violation = max((abs(value(model.heat_flow_kW[segment, hour])) - value(model.pipe_capacity_kW[segment]) for segment in model.SEGMENTS for hour in model.HOURS), default=0.0)
    connectivity_ok = all(
        value(model.connected[node]) < 0.5
        or any(_is_connected(model, station, str(node)) for station in selected_stations)
        for node in model.DEMAND_NODES
    )
    site_residual = max(
        (
            abs(value(model.station_source_heat_balance[station, hour].body))
            for station in model.STATIONS for hour in model.HOURS
        ),
        default=0.0,
    )
    previous = {hour: case.hours[index - 1] if index else case.hours[-1] for index, hour in enumerate(case.hours)}
    storage_residual = max((abs(
        value(model.storage_soc_by_station_kWh[station, hour])
        - (
            value(model.storage_soc_by_station_kWh[station, previous[int(hour)]])
            * (1 - value(model.storage_standing_loss_fraction_per_hour))
            + value(model.storage_charge_by_station_kW[station, hour])
            * value(model.storage_charge_efficiency)
            - value(model.storage_discharge_by_station_kW[station, hour])
            / value(model.storage_discharge_efficiency)
        )
    ) for station in model.STATIONS for hour in model.HOURS), default=0.0)
    margin = float(case.peak_capacity_margin_fraction)
    if margin > 0:
        capacity_margin_slacks = [
            sum(
                value(model.central_capacity_by_station_kW[station, technology_id])
                * (
                    value(model.central_ashp_capacity_ratio[technology_id, hour])
                    if technology_id in model.CENTRAL_AIR_SOURCE_HEAT_PUMPS
                    else 1.0
                )
                for station in model.STATIONS
                for technology_id in model.CENTRAL_TECHNOLOGIES
            )
            - (1 + margin)
            * sum(
                value(model.heat_demand_kW[node, hour])
                * value(model.connected[node])
                for node in model.DEMAND_NODES
            )
            for hour in model.HOURS
        ]
        capacity_margin_slacks.extend(
            value(model.local_capacity_kW[node])
            * value(model.local_ashp_capacity_ratio[hour])
            - (1 + margin)
            * value(model.heat_demand_kW[node, hour])
            * (1 - value(model.connected[node]))
            for node in model.DEMAND_NODES
            for hour in model.HOURS
        )
        minimum_capacity_margin_slack = float(min(capacity_margin_slacks))
    else:
        minimum_capacity_margin_slack = 0.0
    station_built_count = sum(
        round(value(model.station_built[station])) for station in model.STATIONS
    )
    station_count_ok = (
        station_built_count == 1
        if point.mode == "central"
        else station_built_count == 0
        if point.mode == "distributed"
        else station_built_count <= 1
    )
    unbuilt_station_zero_ok = True
    nonzero_central_dispatch_on_built_station_only = True
    for station in model.STATIONS:
        built = value(model.station_built[station]) > 0.5
        capacity = sum(
            value(model.central_capacity_by_station_kW[station, tech])
            for tech in model.CENTRAL_TECHNOLOGIES
        )
        dispatch = sum(
            value(model.central_heat_output_by_station_kW[station, tech, hour])
            for tech in model.CENTRAL_TECHNOLOGIES for hour in model.HOURS
        )
        storage_capacity = (
            value(model.storage_energy_capacity_by_station_kWh[station])
            + value(model.storage_charge_capacity_by_station_kW[station])
            + value(model.storage_discharge_capacity_by_station_kW[station])
        )
        storage_dispatch = sum(
            value(model.storage_charge_by_station_kW[station, hour])
            + value(model.storage_discharge_by_station_kW[station, hour])
            for hour in model.HOURS
        )
        if not built and max(capacity, dispatch, storage_capacity, storage_dispatch) > 1e-7:
            unbuilt_station_zero_ok = False
        if dispatch > 1e-7 and not built:
            nonzero_central_dispatch_on_built_station_only = False
    qa_config = case.raw_config["qa"]
    qa = {
        "point_id": point.point_id,
        "termination_condition": str(solution.solver_results.solver.termination_condition),
        "max_heat_balance_error_kW": max(max_balance, float(site_residual)),
        "unserved_heat_kWh": weighted_hns,
        "max_pipe_capacity_violation_kW": max(0.0, float(pipe_violation)),
        "network_connectivity_ok": connectivity_ok,
        "max_storage_soc_residual_kWh": float(storage_residual),
        "peak_capacity_margin_fraction": margin,
        "minimum_peak_capacity_margin_slack_kW": minimum_capacity_margin_slack,
        "peak_capacity_margin_ok": bool(
            minimum_capacity_margin_slack >= -qa_config["balance_tolerance_kW"]
        ),
        "storage_counted_in_peak_capacity_margin": False,
        "station_built_count": int(station_built_count),
        "station_selection_ok": bool(station_count_ok),
        "unbuilt_station_zero_ok": bool(unbuilt_station_zero_ok),
        "central_dispatch_on_built_station_only": bool(
            nonzero_central_dispatch_on_built_station_only
        ),
        "network_station_endpoints_valid": bool(
            set(_station_ids(model)) == set(case.candidate_station_nodes)
            and
            all(
                endpoint in {str(node) for node in model.NODES}
                for segment in network_input.itertuples(index=False)
                for endpoint in (str(segment.node_from), str(segment.node_to))
            )
        ),
        "cost_reaggregation_error_CNY_per_year": real_total - value(model.annual_real_cost_CNY_per_year),
        "carbon_reaggregation_error_kgCO2e_per_year": carbon_total - value(model.annual_operating_physical_carbon_kgCO2e_per_year),
    }
    qa["passed"] = bool(
        max(max_balance, site_residual) <= qa_config["balance_tolerance_kW"]
        and weighted_hns <= qa_config["unserved_tolerance_kWh"]
        and max(0.0, pipe_violation) <= qa_config["balance_tolerance_kW"]
        and connectivity_ok
        and storage_residual <= qa_config["balance_tolerance_kW"]
        and qa["peak_capacity_margin_ok"]
        and qa["station_selection_ok"]
        and qa["unbuilt_station_zero_ok"]
        and qa["central_dispatch_on_built_station_only"]
        and qa["network_station_endpoints_valid"]
        and abs(qa["cost_reaggregation_error_CNY_per_year"]) <= qa_config["cost_tolerance_CNY_per_year"]
        and abs(qa["carbon_reaggregation_error_kgCO2e_per_year"]) <= qa_config["carbon_tolerance_kgCO2e_per_year"]
    )
    _json(target / "qa_report.json", qa)
    _json(target / "solver_report.json", {
        "solver": case.solver.name,
        "solver_status": point.solver_status,
        "termination_condition": point.termination_condition,
        "solve_started_at_utc": point.solve_started_at_utc,
        "solve_finished_at_utc": point.solve_finished_at_utc,
        "solve_elapsed_seconds": point.solve_elapsed_seconds,
        "reported_mip_gap": point.reported_mip_gap,
        "incumbent_objective": point.incumbent_objective,
        "best_objective_bound": point.best_objective_bound,
        "reported_wallclock_seconds": point.reported_wallclock_seconds,
        "model_sha256": point.model_sha256,
        "solver_log_file": point.solver_log_file,
        "solver_evidence_file": point.solver_evidence_file,
        "threads": case.solver.threads, "mip_gap_target": case.solver.mip_gap,
        "time_limit_seconds": case.solver.time_limit_seconds,
        "random_seed": case.solver.random_seed,
    })
    return qa


def export_v3_results(case: CanonicalCaseData, pareto: ParetoRun, output_dir: str | Path, case_dir: str | Path) -> V3StandardExport:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    points = {
        point.point_id: point
        for all_points in pareto.mode_all_points.values()
        for point in all_points
    }
    frontier_ids = {
        point.point_id for frontier in pareto.mode_frontiers.values() for point in frontier
    }
    pareto_csv = output / "pareto_points.csv"
    point_rows = []
    for point in points.values():
        row = point_to_dict(point)
        row["is_mode_nondominated"] = point.point_id in frontier_ids
        point_rows.append(row)
    pd.DataFrame(point_rows).sort_values(
        ["mode", "annual_operating_carbon_kgCO2e_per_year", "point_id"]
    ).to_csv(pareto_csv, index=False)
    combined_frontier_csv = output / "combined_nondominated_frontier.csv"
    pd.DataFrame([point_to_dict(point) for point in pareto.combined_frontier]).to_csv(
        combined_frontier_csv, index=False
    )
    policy_constraint = case.raw_config.get("pareto", {}).get(
        "policy_carbon_constraint_kgCO2e_per_year"
    )
    representatives = {
        "by_mode": {
            mode: select_representative_points(
                frontier,
                policy_carbon_constraint_kgCO2e_per_year=policy_constraint,
            )
            for mode, frontier in pareto.mode_frontiers.items()
        },
        "combined": select_representative_points(
            pareto.combined_frontier,
            policy_carbon_constraint_kgCO2e_per_year=policy_constraint,
        ),
        "policy_carbon_constraint_kgCO2e_per_year": policy_constraint,
        "policy_status": (
            "configured"
            if policy_constraint is not None
            else "missing_teacher_or_project_policy_threshold"
        ),
    }
    representatives_path = output / "representative_solutions.json"
    _json(representatives_path, representatives)
    network = gpd.read_file(Path(case_dir) / case.raw_config["files"]["candidate_network"])
    sites = gpd.read_file(Path(case_dir) / case.raw_config["files"]["candidate_sites"])
    qa_reports: list[dict[str, Any]] = []
    for point_id, point in sorted(points.items()):
        if point_id in pareto.solutions:
            qa_reports.append(
                export_v3_solution(
                    case, point, pareto.solutions[point_id], output, network, sites
                )
            )
            continue
        qa_path = output / "solutions" / point_id / "qa_report.json"
        if not qa_path.is_file():
            raise RuntimeError(f"缺少流式导出的 Pareto 方案：{point_id}")
        report = json.loads(qa_path.read_text(encoding="utf-8"))
        if report.get("point_id") != point_id:
            raise RuntimeError(f"流式方案 QA point_id 不匹配：{point_id}")
        qa_reports.append(report)
    frontier_points = {
        point.point_id: point
        for frontier in pareto.mode_frontiers.values()
        for point in frontier
    }
    selected_modes_by_station: dict[str, list[str]] = {}
    for station in case.candidate_station_nodes:
        selected_modes: set[str] = set()
        for point in frontier_points.values():
            if point.point_id in pareto.solutions:
                built = value(
                    pareto.solutions[point.point_id].model.station_built[station]
                ) > 0.5
            else:
                decisions = pd.read_csv(
                    output / "solutions" / point.point_id / "station_decisions.csv"
                )
                selected = decisions.loc[
                    decisions["station_id"].astype(str).eq(str(station)),
                    "station_built",
                ]
                if len(selected) != 1:
                    raise RuntimeError(
                        f"方案 {point.point_id} 缺少唯一站点决策：{station}"
                    )
                built = float(selected.iloc[0]) > 0.5
            if built:
                selected_modes.add(point.mode)
        selected_modes_by_station[str(station)] = sorted(selected_modes)
    sites["station_built"] = sites["site_id"].astype(str).map(
        {station: int(bool(modes)) for station, modes in selected_modes_by_station.items()}
    ).fillna(0).astype(int)
    sites["selected_in_mode"] = sites["site_id"].astype(str).map(
        {station: ",".join(modes) for station, modes in selected_modes_by_station.items()}
    ).fillna("")
    sites["selected_in_any_frontier_mode"] = sites["site_id"].astype(str).map(
        {station: bool(modes) for station, modes in selected_modes_by_station.items()}
    ).fillna(False).astype(bool)
    candidate_sites = output / "generated_candidate_sites.geojson"
    sites.to_file(candidate_sites, driver="GeoJSON")
    qa_summary = {
        "all_points_passed": all(report["passed"] for report in qa_reports),
        "point_count": len(qa_reports),
        "failed_points": [report["point_id"] for report in qa_reports if not report["passed"]],
    }
    qa_summary_path = output / "qa_summary.json"
    _json(qa_summary_path, qa_summary)
    if not qa_summary["all_points_passed"]:
        raise RuntimeError(f"标准结果 QA 未通过：{qa_summary['failed_points']}")
    figure_paths = _render_figures(
        case,
        pareto,
        output,
        Path(case_dir).resolve(),
        representatives,
    )
    return V3StandardExport(
        output,
        pareto_csv,
        candidate_sites,
        qa_summary_path,
        representatives_path,
        combined_frontier_csv,
        figure_paths,
    )
