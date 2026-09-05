"""Run the reduced three-mode V0.2 scale smoke and write compact QA metrics.

This intentionally skips Pareto: the scale gate checks the same canonical V3
data and unified core once per mode without multiplying 62x168 solve time.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import networkx as nx
from pyomo.environ import Constraint, Var, value

from urbanheatopt.model.reference_core import solve_core_model
from urbanheatopt.data.validation.v3_inputs import load_v3_case


ROOT = Path(__file__).resolve().parents[2]
CASE = ROOT / "cases" / "v0_guanggu_62b_168h"
OUTPUT = ROOT / "runs" / "v02_guanggu_62b_168h" / "scale_smoke_summary.json"
TOL = 1e-5


def _bound_and_gap(results, objective: float) -> tuple[float | None, float | None]:
    problem = results.problem
    bound = getattr(problem, "lower_bound", None)
    try:
        bound = float(bound)
    except (TypeError, ValueError):
        return None, None
    return bound, max(0.0, (objective - bound) / max(1.0, abs(objective)))


def run() -> dict[str, object]:
    case = load_v3_case(CASE, profile="v0-smoke")
    network_input = __import__("geopandas").read_file(CASE / "candidate_network.geojson")
    candidate = nx.Graph()
    candidate.add_edges_from(zip(network_input.node_from, network_input.node_to))
    summary: dict[str, object] = {
        "case": {
            "buildings": len(case.demand_nodes), "hours": len(case.hours),
            "load_rows": len(case.heat_demand_kW_th),
            "candidate_nodes": candidate.number_of_nodes(),
            "candidate_edges": candidate.number_of_edges(),
            "candidate_connected_components": nx.number_connected_components(candidate),
            "candidate_length_m": float(network_input.length_m.sum()),
            "candidate_average_degree": sum(dict(candidate.degree()).values()) / candidate.number_of_nodes(),
            "candidate_max_degree": max(dict(candidate.degree()).values()),
        },
        "modes": {},
    }
    for mode in case.modes:
        started = perf_counter()
        solved = solve_core_model(case.to_core_input(mode), case.solver)
        runtime = perf_counter() - started
        model = solved.model
        objective = float(value(model.optimization_objective_CNY_per_year))
        bound, gap = _bound_and_gap(solved.solver_results, objective)
        variables = list(model.component_data_objects(Var, active=True))
        constraints = list(model.component_data_objects(Constraint, active=True))
        built = [str(edge) for edge in model.SEGMENTS if value(model.pipe_built[edge]) > 0.5]
        graph = nx.Graph()
        for edge in built:
            nodes = [str(n) for n in model.NODES if abs(value(model.incidence[n, edge])) > 0.5]
            if len(nodes) == 2:
                graph.add_edge(*nodes)
        heat_balance = max(
            abs(value(model.network_heat_kW[n, h]) + value(model.local_heat_output_kW[n, h])
                + value(model.unserved_heat_kW[n, h]) - value(model.heat_demand_kW[n, h]))
            for n in model.DEMAND_NODES for h in model.HOURS
        )
        site_balance = max(abs(value(model.central_site_heat_balance[h].body)) for h in model.HOURS)
        unbuilt_flow = max(
            (abs(value(model.heat_flow_kW[e, h])) for e in model.SEGMENTS
             if value(model.pipe_built[e]) < 0.5 for h in model.HOURS), default=0.0
        )
        capacity_violation = max(
            (abs(value(model.heat_flow_kW[e, h])) - value(model.pipe_capacity_kW[e])
             for e in model.SEGMENTS for h in model.HOURS), default=0.0
        )
        storage_residual = max(abs(value(c.body)) for c in model.storage_soc_balance.values())
        connected = [str(n) for n in model.DEMAND_NODES if value(model.connected[n]) > 0.5]
        connected_paths_ok = all(nx.has_path(graph, case.site_node, n) for n in connected)
        result = {
            "termination": str(solved.solver_results.solver.termination_condition),
            "runtime_seconds": runtime, "objective_CNY": objective,
            "best_bound_CNY": bound, "relative_mip_gap": gap,
            "variables": len(variables), "binary_variables": sum(v.is_binary() for v in variables),
            "continuous_variables": sum(not v.is_binary() for v in variables),
            "constraints": len(constraints), "connected_buildings": len(connected),
            "unserved_kWh": sum(value(model.unserved_heat_kW[n, h]) for n in model.DEMAND_NODES for h in model.HOURS),
            "max_building_heat_balance_residual_kW": heat_balance,
            "max_central_balance_residual_kW": site_balance,
            "unbuilt_pipe_max_abs_flow_kW": unbuilt_flow,
            "pipe_capacity_max_violation_kW": max(0.0, capacity_violation),
            "connected_paths_ok": connected_paths_ok,
            "built_pipe_count": len(built),
            "built_pipe_length_m": sum(value(model.segment_length_m[e]) for e in built),
            "network_loss_kWh": sum(value(model.pipe_heat_loss_kW[h]) for h in model.HOURS),
            "pump_electricity_kWh": sum(value(model.pumping_electricity_input_kW_e[h]) for h in model.HOURS),
            "ashp_heat_kWh": sum(value(model.central_heat_output_kW[t, h]) for t in model.CENTRAL_AIR_SOURCE_HEAT_PUMPS for h in model.HOURS)
                + sum(value(model.local_heat_output_kW[n, h]) for n in model.DEMAND_NODES for h in model.HOURS),
            "boiler_heat_kWh": sum(value(model.central_heat_output_kW[t, h]) for t in model.CENTRAL_GAS_BOILERS for h in model.HOURS),
            "tes_energy_capacity_kWh": value(model.storage_energy_capacity_kWh),
            "tes_charge_kWh": sum(value(model.storage_charge_kW[h]) for h in model.HOURS),
            "tes_discharge_kWh": sum(value(model.storage_discharge_kW[h]) for h in model.HOURS),
            "tes_soc_max_kWh": max(value(model.storage_soc_kWh[h]) for h in model.HOURS),
            "tes_soc_recurrence_max_residual": storage_residual,
            "annual_real_cost_CNY_debug_168h": value(model.annual_real_cost_CNY_per_year),
            "operating_carbon_kgCO2e_debug_168h": value(model.annual_operating_physical_carbon_kgCO2e_per_year),
        }
        result["qa_pass"] = bool(
            result["termination"] == "optimal" and result["unserved_kWh"] <= TOL
            and heat_balance <= TOL and site_balance <= TOL and unbuilt_flow <= TOL
            and capacity_violation <= TOL and connected_paths_ok and storage_residual <= TOL
            and ((mode == "central" and len(connected) == 62)
                 or (mode == "distributed" and len(connected) == 0 and result["built_pipe_count"] == 0)
                 or mode == "hybrid")
        )
        summary["modes"][mode] = result
        print(mode, json.dumps(result, ensure_ascii=False), flush=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    run()
