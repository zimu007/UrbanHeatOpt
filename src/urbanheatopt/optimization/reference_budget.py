"""Deterministic representative-horizon projection for wall-clock-budget runs.

This module does not claim that a representative horizon is the exact 2160-hour
season.  It preserves the global seasonal peak for capacity decisions and
annualizes operating terms by the ratio of full-season to selected-window heat
energy.  Capital and fixed maintenance terms remain unscaled.
"""
from __future__ import annotations

from dataclasses import replace
import json
from math import isfinite

import networkx as nx

from urbanheatopt.model.road_core import RoadCase, validate_case
from urbanheatopt.spatial.atomic_network import access_options


BUDGET_CASE_SCHEMA = "road_joint_v2_budget_case_1"


def _total_heat_by_hour(case: RoadCase) -> tuple[float, ...]:
    data = case.common
    return tuple(
        sum(float(data.heat_demand_kW[building, hour]) for building in data.demand_nodes)
        for hour in data.hours
    )


def _peak_containing_window(total_heat: tuple[float, ...], length: int) -> int:
    """Return the maximum-energy window among windows containing the peak."""

    peak_index = max(range(len(total_heat)), key=total_heat.__getitem__)
    first = max(0, peak_index-length+1)
    last = min(peak_index, len(total_heat)-length)
    return max(
        range(first, last+1),
        key=lambda start: (sum(total_heat[start:start+length]), -start),
    )


def build_shortest_path_backbone_case(case: RoadCase) -> tuple[RoadCase, dict]:
    """Keep the union of shortest paths from every station to every building.

    The reduction retains all candidate stations and demand nodes but removes
    road alternatives that are not on any minimum-length station/building path.
    Parallel edges are resolved by ``(length, edge_id)`` before path finding so
    the result is deterministic.
    """

    validate_case(case)
    network = case.network
    graph = nx.Graph()
    for edge in sorted(network["edges"], key=lambda row: row["edge_id"]):
        if edge["edge_type"] != "road":
            continue
        node_u = edge["node_u"]
        node_v = edge["node_v"]
        candidate = (float(edge["length_m"]), edge["edge_id"])
        if graph.has_edge(node_u, node_v):
            existing = (
                float(graph[node_u][node_v]["weight"]),
                graph[node_u][node_v]["edge_id"],
            )
            if candidate >= existing:
                continue
        graph.add_edge(
            node_u,
            node_v,
            weight=candidate[0],
            edge_id=candidate[1],
        )

    building_nodes = tuple(
        sorted(
            node["node_id"]
            for node in network["nodes"]
            if node["node_type"] == "building"
        )
    )
    options_by_building = {building: [] for building in building_nodes}
    for option in access_options(network):
        options_by_building[option["building_id"]].append(option)
    used_edge_ids: set[str] = set()
    for site in sorted(network["sites"], key=lambda row: row["site_id"]):
        source = site["attachment_node_id"]
        distances, paths = nx.single_source_dijkstra(graph, source, weight="weight")
        for building in building_nodes:
            candidates = [
                option
                for option in options_by_building[building]
                if option["attachment_node_id"] in distances
            ]
            if not candidates:
                raise ValueError(f"station {site['site_id']} cannot reach building {building}")
            selected = min(
                candidates,
                key=lambda option: (
                    distances[option["attachment_node_id"]]+float(option["length_m"]),
                    option["option_id"],
                ),
            )
            path = paths[selected["attachment_node_id"]]
            for node_u, node_v in zip(path, path[1:]):
                used_edge_ids.add(graph[node_u][node_v]["edge_id"])
            used_edge_ids.update(selected["edge_ids"])

    retained_edges = [
        edge for edge in network["edges"] if edge["edge_id"] in used_edge_ids
    ]
    retained_node_ids = {
        endpoint
        for edge in retained_edges
        for endpoint in (edge["node_u"], edge["node_v"])
    }
    retained_node_ids.update(building_nodes)
    retained_node_ids.update(site["attachment_node_id"] for site in network["sites"])
    retained_nodes = [
        node for node in network["nodes"] if node["node_id"] in retained_node_ids
    ]
    projected_network = {
        **network,
        "nodes": retained_nodes,
        "edges": retained_edges,
    }
    if "access_options" in network:
        projected_network["access_options"] = [
            option
            for option in network["access_options"]
            if set(option["edge_ids"]).issubset(used_edge_ids)
        ]
    projected = replace(case, network_json=json.dumps(projected_network, sort_keys=True))
    validate_case(projected)
    metadata = {
        "network_reduction": "all-station/all-building shortest-path union",
        "source_node_count": len(network["nodes"]),
        "source_edge_count": len(network["edges"]),
        "retained_node_count": len(retained_nodes),
        "retained_edge_count": len(retained_edges),
        "source_road_edge_count": sum(
            edge["edge_type"] == "road" for edge in network["edges"]
        ),
        "retained_road_edge_count": sum(
            edge["edge_type"] == "road" for edge in retained_edges
        ),
    }
    return projected, metadata


def _site_tree(case: RoadCase, site_id: str) -> dict:
    network = case.network
    edge_lookup = {edge["edge_id"]: edge for edge in network["edges"]}
    site_lookup = {site["site_id"]: site for site in network["sites"]}
    if site_id not in site_lookup:
        raise ValueError(f"unknown station site {site_id!r}")
    graph = nx.Graph()
    for edge in sorted(network["edges"], key=lambda row: row["edge_id"]):
        if edge["edge_type"] != "road":
            continue
        candidate = (float(edge["length_m"]), edge["edge_id"])
        if graph.has_edge(edge["node_u"], edge["node_v"]):
            existing = (
                float(graph[edge["node_u"]][edge["node_v"]]["weight"]),
                graph[edge["node_u"]][edge["node_v"]]["edge_id"],
            )
            if candidate >= existing:
                continue
        graph.add_edge(
            edge["node_u"],
            edge["node_v"],
            weight=candidate[0],
            edge_id=candidate[1],
        )
    options_by_building = {building: [] for building in case.common.demand_nodes}
    for option in access_options(network):
        options_by_building[option["building_id"]].append(option)
    root = site_lookup[site_id]["attachment_node_id"]
    distances, paths = nx.single_source_dijkstra(graph, root, weight="weight")
    used_edge_ids: set[str] = set()
    selected_options = {}
    for building in sorted(case.common.demand_nodes):
        candidates = [
            option
            for option in options_by_building[building]
            if option["attachment_node_id"] in distances
        ]
        if not candidates:
            raise ValueError(f"station {site_id} cannot reach building {building}")
        option = min(
            candidates,
            key=lambda row: (
                distances[row["attachment_node_id"]]+float(row["length_m"]),
                row["option_id"],
            ),
        )
        selected_options[building] = option["option_id"]
        path = paths[option["attachment_node_id"]]
        for node_u, node_v in zip(path, path[1:]):
            used_edge_ids.add(graph[node_u][node_v]["edge_id"])
        used_edge_ids.update(option["edge_ids"])
    tree = nx.Graph()
    for edge_id in used_edge_ids:
        edge = edge_lookup[edge_id]
        tree.add_edge(edge["node_u"], edge["node_v"], edge_id=edge_id)
    if not nx.is_tree(tree) or root not in tree:
        raise ValueError(f"budget routing for {site_id} is not a rooted tree")
    return {
        "site_id": site_id,
        "root_node_id": root,
        "selected_edge_ids": tuple(sorted(used_edge_ids)),
        "selected_access_options": selected_options,
        "total_route_length_m": sum(
            float(edge_lookup[edge_id]["length_m"]) for edge_id in used_edge_ids
        ),
        "tree": tree,
    }


def build_budget_network_design(case: RoadCase) -> dict:
    """Create a deterministic, capacity-feasible single-station routing tree."""

    validate_case(case)
    candidates = [_site_tree(case, site["site_id"]) for site in case.network["sites"]]
    selected = min(
        candidates,
        key=lambda row: (row["total_route_length_m"], row["site_id"]),
    )
    tree = selected.pop("tree")
    root = selected["root_node_id"]
    edge_lookup = {edge["edge_id"]: edge for edge in case.network["edges"]}
    levels = sorted(
        case.pipe_designs,
        key=lambda level: (level.capacity_kW_th, level.pipe_type_id),
    )
    parent = {root: None}
    parent_edge = {}
    order = [root]
    for node in order:
        for neighbor in sorted(tree.neighbors(node)):
            if neighbor in parent:
                continue
            parent[neighbor] = node
            parent_edge[neighbor] = tree[node][neighbor]["edge_id"]
            order.append(neighbor)
    requirements = {
        node: [
            float(case.common.heat_demand_kW[node, hour])
            if node in case.common.demand_nodes else 0.0
            for hour in case.common.hours
        ]
        for node in tree.nodes
    }
    pipe_type_by_edge = {}
    sending_peak_by_edge = {}
    for node in reversed(order[1:]):
        edge_id = parent_edge[node]
        edge = edge_lookup[edge_id]
        child_requirement = requirements[node]
        chosen = None
        for level in levels:
            loss = float(edge["length_m"])*level.pair_loss_kW_per_route_m
            if max(child_requirement)+loss <= level.capacity_kW_th+1e-6:
                chosen = level
                break
        if chosen is None:
            raise ValueError(f"no pipe grade can carry budget-tree edge {edge_id}")
        edge_loss = float(edge["length_m"])*chosen.pair_loss_kW_per_route_m
        pipe_type_by_edge[edge_id] = chosen.pipe_type_id
        sending_peak_by_edge[edge_id] = max(child_requirement)+edge_loss
        upstream = requirements[parent[node]]
        for index, value in enumerate(child_requirement):
            upstream[index] += value+edge_loss
    return {
        "schema": "road_joint_v2_budget_network_design_1",
        "qualification": "deterministic_fixed-routing_heuristic",
        **selected,
        "selected_edge_count": len(selected["selected_edge_ids"]),
        "pipe_type_by_edge": pipe_type_by_edge,
        "sending_peak_kW_by_edge": sending_peak_by_edge,
        "root_peak_including_losses_kW": max(requirements[root]),
        "site_candidates": [
            {
                "site_id": row["site_id"],
                "total_route_length_m": row["total_route_length_m"],
                "selected_edge_count": len(row["selected_edge_ids"]),
            }
            for row in candidates
        ],
    }


def apply_budget_network_design(model, case: RoadCase, design: dict) -> None:
    """Restrict a model to the deterministic budget routing/design policy."""

    if bool(model.distributed_fastpath.value):
        return
    selected_site = design["site_id"]
    selected_edges = frozenset(design["selected_edge_ids"])
    selected_options = frozenset(design["selected_access_options"].values())
    pipe_type_by_edge = design["pipe_type_by_edge"]
    central = case.common.mode == "central"
    for site in model.S:
        if site != selected_site:
            model.station_built[site].fix(0)
        elif central:
            model.station_built[site].fix(1)
        model.tes_built[site].fix(0)
        for technology in model.T:
            if site != selected_site:
                model.installed[site, technology].fix(0)
            elif central:
                model.installed[site, technology].fix(1)
        for hour in model.HOURS:
            model.charging[site, hour].fix(0)
    for option in model.ACCESS:
        if option not in selected_options:
            model.access_selected[option].fix(0)
        elif central:
            model.access_selected[option].fix(1)
    for edge in model.E:
        if edge not in selected_edges:
            model.built[edge].fix(0)
            for pipe_type in model.K:
                model.grade[edge, pipe_type].fix(0)
            continue
        selected_pipe_type = pipe_type_by_edge[edge]
        for pipe_type in model.K:
            if pipe_type != selected_pipe_type:
                model.grade[edge, pipe_type].fix(0)
        if central:
            model.built[edge].fix(1)
            model.grade[edge, selected_pipe_type].fix(1)


def initialize_budget_hybrid_mip_start(
    model,
    case: RoadCase,
    design: dict,
    *,
    policy: str,
) -> None:
    """Initialize a feasible all-central or all-distributed hybrid topology."""

    if case.common.mode != "hybrid":
        raise ValueError("budget hybrid MIP start 只能用于 hybrid 模式")
    if policy not in {"all_central", "all_distributed"}:
        raise ValueError("budget hybrid MIP start policy 必须是 all_central 或 all_distributed")
    selected = float(policy == "all_central")
    selected_site = design["site_id"]
    selected_edges = frozenset(design["selected_edge_ids"])
    selected_options = frozenset(design["selected_access_options"].values())
    pipe_type_by_edge = design["pipe_type_by_edge"]
    for building in model.DEMAND_NODES:
        model.connected[building].set_value(selected)
    for site in model.S:
        if site == selected_site:
            model.station_built[site].set_value(selected)
            for technology in model.T:
                model.installed[site, technology].set_value(selected)
    for option in model.ACCESS:
        if option in selected_options:
            model.access_selected[option].set_value(selected)
    for edge in model.E:
        if edge not in selected_edges:
            continue
        model.built[edge].set_value(selected)
        model.grade[edge, pipe_type_by_edge[edge]].set_value(selected)
    model._urbanheatopt_mip_start_policy = f"budget_hybrid_{policy}"


def build_budget_case(
    full_case: RoadCase,
    *,
    representative_hours: int = 168,
    reduce_network: bool = True,
) -> tuple[RoadCase, dict]:
    """Project a full case to one contiguous, peak-containing horizon.

    Hour identifiers are remapped to ``1..representative_hours``.  The
    representative window is chosen deterministically: it must contain the
    full-season system peak, and among all such windows it maximizes total heat
    demand.  Hourly operating prices/carbon factors and technology variable O&M
    are multiplied by ``full_heat / selected_heat``.  This makes operating
    totals annual estimates while leaving investment terms untouched.
    """

    validate_case(full_case)
    if (
        isinstance(representative_hours, bool)
        or not isinstance(representative_hours, int)
        or representative_hours < 1
        or representative_hours > len(full_case.common.hours)
    ):
        raise ValueError(
            "representative_hours must be an integer between 1 and the full horizon"
        )

    data = full_case.common
    old_hours = data.hours
    total_heat = _total_heat_by_hour(full_case)
    start = _peak_containing_window(total_heat, representative_hours)
    selected_hours = old_hours[start:start+representative_hours]
    selected_heat = sum(total_heat[start:start+representative_hours])
    full_heat = sum(total_heat)
    if not isfinite(selected_heat) or selected_heat <= 0 or not isfinite(full_heat):
        raise ValueError("full and representative heat energy must be finite and positive")
    operating_scale = full_heat/selected_heat
    new_hours = tuple(range(1, representative_hours+1))
    old_to_new = dict(zip(selected_hours, new_hours, strict=True))

    def remap_hourly(source, *, scale: float = 1.0):
        return {
            old_to_new[old_hour]: float(source[old_hour])*scale
            for old_hour in selected_hours
        }

    economics = data.economics
    projected_economics = replace(
        economics,
        time_weight_h_per_year={hour: 1.0 for hour in new_hours},
        electricity_price_CNY_per_kWh_e=remap_hourly(
            economics.electricity_price_CNY_per_kWh_e,
            scale=operating_scale,
        ),
        gas_price_CNY_per_kWh_LHV=remap_hourly(
            economics.gas_price_CNY_per_kWh_LHV,
            scale=operating_scale,
        ),
        expected_weight_sum_h_per_year=float(representative_hours),
        hns_penalty_CNY_per_kWh=(
            float(economics.hns_penalty_CNY_per_kWh)*operating_scale
        ),
        electricity_carbon_kgCO2e_per_kWh_e=remap_hourly(
            economics.electricity_carbon_kgCO2e_per_kWh_e,
            scale=operating_scale,
        ),
        gas_carbon_kgCO2e_per_kWh_LHV=remap_hourly(
            economics.gas_carbon_kgCO2e_per_kWh_LHV,
            scale=operating_scale,
        ),
    )
    technologies = tuple(
        replace(
            technology,
            variable_om_CNY_per_kWh_th=(
                float(technology.variable_om_CNY_per_kWh_th)*operating_scale
            ),
        )
        for technology in data.technologies
    )
    projected_common = replace(
        data,
        hours=new_hours,
        heat_demand_kW={
            (building, old_to_new[old_hour]): float(data.heat_demand_kW[building, old_hour])
            for building in data.demand_nodes
            for old_hour in selected_hours
        },
        technologies=technologies,
        economics=projected_economics,
        heat_pump_cop_by_hour={
            (technology, old_to_new[old_hour]): float(value)
            for (technology, old_hour), value in data.heat_pump_cop_by_hour.items()
            if old_hour in old_to_new
        },
        heat_pump_capacity_ratio_by_hour={
            (technology, old_to_new[old_hour]): float(value)
            for (technology, old_hour), value in data.heat_pump_capacity_ratio_by_hour.items()
            if old_hour in old_to_new
        },
    )
    parameter_version = (
        f"{full_case.parameter_version}|budget50m:v1:"
        f"peak-window-{representative_hours}h:scale={operating_scale:.12g}"
    )
    projected = replace(
        full_case,
        common=projected_common,
        timestamps=full_case.timestamps[start:start+representative_hours],
        parameter_version=parameter_version,
    )
    validate_case(projected)
    network_metadata = {}
    if reduce_network:
        projected, network_metadata = build_shortest_path_backbone_case(projected)

    peak_index = max(range(len(total_heat)), key=total_heat.__getitem__)
    metadata = {
        "schema": BUDGET_CASE_SCHEMA,
        "qualification": "budgeted_approximate_representative_horizon",
        "strict_full_season_optimum": False,
        "selection_method": "maximum-energy contiguous window containing full-season peak",
        "full_hour_count": len(old_hours),
        "representative_hour_count": representative_hours,
        "source_hour_start": selected_hours[0],
        "source_hour_end": selected_hours[-1],
        "source_timestamp_start": full_case.timestamps[start],
        "source_timestamp_end": full_case.timestamps[start+representative_hours-1],
        "full_peak_source_hour": old_hours[peak_index],
        "full_peak_kW": total_heat[peak_index],
        "representative_peak_kW": max(total_heat[start:start+representative_hours]),
        "full_heat_kWh": full_heat,
        "representative_heat_kWh": selected_heat,
        "operating_annualization_scale": operating_scale,
        "network_reduced": reduce_network,
        **network_metadata,
        "limitations": [
            "investment feasibility is checked only at representative hours",
            "storage cycles over the representative window rather than all full-season hours",
            "operating costs and carbon are annualized estimates, not a strict full-season replay",
            "road alternatives outside the retained shortest-path backbone are excluded",
        ],
    }
    return projected, metadata
