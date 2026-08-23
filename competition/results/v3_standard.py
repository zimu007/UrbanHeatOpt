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
from competition.pareto import ParetoPoint, ParetoRun, point_to_dict


@dataclass(frozen=True, slots=True)
class V3StandardExport:
    output_dir: Path
    pareto_csv: Path
    candidate_sites_geojson: Path
    qa_summary_json: Path


def _json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _selected_pipe_type(model: Any, segment: str) -> str | None:
    if len(model.PIPE_LEVELS) == 0:
        return None
    for pipe_type in model.PIPE_LEVELS:
        if value(model.pipe_level_built[segment, pipe_type]) > 0.5:
            return str(pipe_type)
    return None


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
        value(model.central_capacity_kW[tech])
        * value(model.central_capex_CNY_per_kW[tech])
        * value(model.central_crf[tech])
        for tech in model.CENTRAL_TECHNOLOGIES
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
    station = value(model.site_built) * value(model.station_fixed_capex_CNY) * value(model.station_crf)
    storage = (
        value(model.storage_energy_capacity_kWh) * value(model.storage_capex_CNY_per_kWh)
        + value(model.storage_power_cost_capacity_kW) * value(model.storage_power_capex_CNY_per_kW)
        + value(model.storage_installed) * value(model.storage_fixed_capex_CNY)
    ) * value(model.storage_crf)
    fixed_om = sum(
        value(model.central_capacity_kW[tech]) * value(model.central_capex_CNY_per_kW[tech])
        * value(model.central_fixed_maintenance_fraction_per_year[tech])
        for tech in model.CENTRAL_TECHNOLOGIES
    ) + sum(
        value(model.local_capacity_kW[node]) * value(model.local_capex_CNY_per_kW)
        * value(model.local_fixed_maintenance_fraction_per_year)
        for node in model.DEMAND_NODES
    )
    variable_om = sum(
        value(model.central_heat_output_kW[tech, hour]) * value(model.time_weight_h_per_year[hour])
        * value(model.central_variable_om_CNY_per_kWh_th[tech])
        for tech in model.CENTRAL_TECHNOLOGIES for hour in model.HOURS
    ) + sum(
        value(model.local_heat_output_kW[node, hour]) * value(model.time_weight_h_per_year[hour])
        * value(model.local_variable_om_CNY_per_kWh_th)
        for node in model.DEMAND_NODES for hour in model.HOURS
    )
    electricity = sum(
        value(model.central_electricity_input_kW_e[tech, hour]) * value(model.time_weight_h_per_year[hour])
        * value(model.electricity_price_CNY_per_kWh_e[hour])
        for tech in model.CENTRAL_AIR_SOURCE_HEAT_PUMPS for hour in model.HOURS
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
        value(model.gas_input_kW_LHV[tech, hour]) * value(model.time_weight_h_per_year[hour])
        * value(model.gas_price_CNY_per_kWh_LHV[hour])
        for tech in model.CENTRAL_GAS_BOILERS for hour in model.HOURS
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
        value(model.central_electricity_input_kW_e[tech, hour]) * value(model.time_weight_h_per_year[hour])
        * value(model.electricity_carbon_kgCO2e_per_kWh_e[hour])
        for tech in model.CENTRAL_AIR_SOURCE_HEAT_PUMPS for hour in model.HOURS
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
        value(model.gas_input_kW_LHV[tech, hour]) * value(model.time_weight_h_per_year[hour])
        * value(model.gas_carbon_kgCO2e_per_kWh_LHV[hour])
        for tech in model.CENTRAL_GAS_BOILERS for hour in model.HOURS
    )
    return {"electricity": float(electricity), "gas": float(gas)}


def _export_solution(case: CanonicalCaseData, point: ParetoPoint, solution: Any, output: Path, network_input: gpd.GeoDataFrame) -> dict[str, Any]:
    model = solution.model
    target = output / "solutions" / point.point_id
    target.mkdir(parents=True, exist_ok=True)
    capacities = [
        {"asset_id": str(tech), "asset_type": "central_generation", "node_id": case.site_node,
         "installed": round(value(model.central_installed[tech])), "capacity_kW_th": value(model.central_capacity_kW[tech])}
        for tech in model.CENTRAL_TECHNOLOGIES
    ] + [
        {"asset_id": f"local_hp@{node}", "asset_type": "local_generation", "node_id": str(node),
         "installed": round(value(model.local_installed[node])), "capacity_kW_th": value(model.local_capacity_kW[node])}
        for node in model.DEMAND_NODES
    ]
    pd.DataFrame(capacities).to_csv(target / "capacity_decisions.csv", index=False)
    pd.DataFrame([
        {"building_id": str(node), "connected": round(value(model.connected[node])),
         "service_mode": "central" if value(model.connected[node]) > 0.5 else "distributed"}
        for node in model.DEMAND_NODES
    ]).to_csv(target / "building_connection.csv", index=False)
    pd.DataFrame([{
        "technology_id": case.storage.technology_id,
        "installed": round(value(model.storage_installed)),
        "energy_capacity_kWh_th": value(model.storage_energy_capacity_kWh),
        "charge_capacity_kW_th": value(model.storage_charge_capacity_kW),
        "discharge_capacity_kW_th": value(model.storage_discharge_capacity_kW),
    }]).to_csv(target / "storage_decisions.csv", index=False)

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
    for hour in model.HOURS:
        timestamp = timestamp_by_hour[int(hour)]
        for tech in model.CENTRAL_TECHNOLOGIES:
            dispatch_rows.append({
                "timestamp": timestamp, "hour": int(hour), "asset_id": str(tech),
                "node_id": case.site_node, "heat_output_kW_th": value(model.central_heat_output_kW[tech, hour]),
                "electricity_input_kW_e": value(model.central_electricity_input_kW_e[tech, hour]) if tech in model.CENTRAL_AIR_SOURCE_HEAT_PUMPS else 0.0,
                "gas_input_kW_LHV": value(model.gas_input_kW_LHV[tech, hour]) if tech in model.CENTRAL_GAS_BOILERS else 0.0,
                "storage_charge_kW_th": 0.0, "storage_discharge_kW_th": 0.0, "storage_soc_kWh_th": 0.0,
            })
        dispatch_rows.append({
            "timestamp": timestamp, "hour": int(hour), "asset_id": case.storage.technology_id,
            "node_id": case.site_node, "heat_output_kW_th": 0.0,
            "electricity_input_kW_e": 0.0, "gas_input_kW_LHV": 0.0,
            "storage_charge_kW_th": value(model.storage_charge_kW[hour]),
            "storage_discharge_kW_th": value(model.storage_discharge_kW[hour]),
            "storage_soc_kWh_th": value(model.storage_soc_kWh[hour]),
        })
        dispatch_rows.append({
            "timestamp": timestamp, "hour": int(hour), "asset_id": "network_pump",
            "node_id": case.site_node, "heat_output_kW_th": 0.0,
            "electricity_input_kW_e": value(model.pumping_electricity_input_kW_e[hour]),
            "gas_input_kW_LHV": 0.0, "storage_charge_kW_th": 0.0,
            "storage_discharge_kW_th": 0.0, "storage_soc_kWh_th": 0.0,
        })
        for segment in model.SEGMENTS:
            selected = _selected_pipe_type(model, str(segment))
            heat_loss = 0.0
            pumping = 0.0
            if selected is not None:
                heat_loss = (
                    value(model.segment_length_m[segment])
                    * value(model.pipe_level_heat_loss_kW_per_m[selected])
                )
                pumping = (
                    value(model.pipe_level_abs_flow_kW[segment, selected, hour])
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
                "node_id": str(node), "heat_output_kW_th": value(model.local_heat_output_kW[node, hour]),
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
    connectivity_ok = all(value(model.connected[node]) < 0.5 or _is_connected(model, case.site_node, str(node)) for node in model.DEMAND_NODES)
    site_residual = max((abs(
        sum(value(model.central_heat_output_kW[technology_id, hour]) for technology_id in model.CENTRAL_TECHNOLOGIES)
        + value(model.storage_discharge_kW[hour])
        - value(model.storage_charge_kW[hour])
        - sum(
            value(model.segment_length_m[segment])
            * value(model.pipe_level_heat_loss_kW_per_m[pipe_type])
            * value(model.pipe_level_built[segment, pipe_type])
            for segment in model.SEGMENTS for pipe_type in model.PIPE_LEVELS
        )
        + sum(
            value(model.incidence[case.site_node, segment])
            * value(model.heat_flow_kW[segment, hour])
            for segment in model.SEGMENTS
        )
    ) for hour in model.HOURS), default=0.0)
    previous = {hour: case.hours[index - 1] if index else case.hours[-1] for index, hour in enumerate(case.hours)}
    storage_residual = max((abs(value(model.storage_soc_kWh[hour]) - (value(model.storage_soc_kWh[previous[int(hour)]]) * (1 - value(model.storage_standing_loss_fraction_per_hour)) + value(model.storage_charge_kW[hour]) * value(model.storage_charge_efficiency) - value(model.storage_discharge_kW[hour]) / value(model.storage_discharge_efficiency))) for hour in model.HOURS), default=0.0)
    margin = float(case.peak_capacity_margin_fraction)
    if margin > 0:
        capacity_margin_slacks = [
            sum(
                value(model.central_capacity_kW[technology_id])
                * (
                    value(model.central_ashp_capacity_ratio[technology_id, hour])
                    if technology_id in model.CENTRAL_AIR_SOURCE_HEAT_PUMPS
                    else 1.0
                )
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
        and abs(qa["cost_reaggregation_error_CNY_per_year"]) <= qa_config["cost_tolerance_CNY_per_year"]
        and abs(qa["carbon_reaggregation_error_kgCO2e_per_year"]) <= qa_config["carbon_tolerance_kgCO2e_per_year"]
    )
    _json(target / "qa_report.json", qa)
    _json(target / "solver_report.json", {
        "solver": case.solver.name,
        "termination_condition": str(solution.solver_results.solver.termination_condition),
        "threads": case.solver.threads, "mip_gap": case.solver.mip_gap,
        "time_limit_seconds": case.solver.time_limit_seconds,
    })
    return qa


def export_v3_results(case: CanonicalCaseData, pareto: ParetoRun, output_dir: str | Path, case_dir: str | Path) -> V3StandardExport:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    points = {point.point_id: point for frontier in pareto.mode_frontiers.values() for point in frontier}
    pareto_csv = output / "pareto_points.csv"
    pd.DataFrame([point_to_dict(point) for point in points.values()]).sort_values(["mode", "annual_operating_carbon_kgCO2e_per_year"]).to_csv(pareto_csv, index=False)
    network = gpd.read_file(Path(case_dir) / case.raw_config["files"]["candidate_network"])
    qa_reports = [
        _export_solution(case, point, pareto.solutions[point_id], output, network)
        for point_id, point in sorted(points.items())
    ]
    sites = gpd.read_file(Path(case_dir) / case.raw_config["files"]["candidate_sites"])
    selected_modes = {point.mode for point in points.values() if value(pareto.solutions[point.point_id].model.site_built) > 0.5}
    sites["selected_in_any_frontier_mode"] = bool(selected_modes)
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
    return V3StandardExport(output, pareto_csv, candidate_sites, qa_summary_path)
