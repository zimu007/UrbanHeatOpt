"""Compact full-season fixed-root radial-tree formulation.

The generic road model deliberately supports loops, alternative accesses,
directional flow, pipe-grade decisions and storage.  Those capabilities make
it a useful reference model, but they are unnecessary once a candidate station
and a deterministic shortest-path tree have been selected.  This module keeps
the original physical edges and the complete hourly input while eliminating
all network flow and direction variables.

For a rooted edge, midpoint flow is an affine expression consisting of the
connected downstream demand, every proper downstream edge loss and one half of
the edge's own loss.  Thus the public ``forward``/``reverse`` interface can be
reconstructed exactly without putting either quantity in the optimization
matrix.  Degree-two contraction only shares an activation decision; costs,
losses, pumping and exports remain defined on the original edge atoms.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any, Callable, Iterable, Mapping

import networkx as nx
import numpy as np
import pyomo.environ as p

from urbanheatopt.model.costing.annualized import capital_recovery_factor as crf
from urbanheatopt.optimization.reference_budget import _site_tree
from urbanheatopt.model.road_core import PipeDesign, RoadCase, validate_case
from urbanheatopt.spatial.atomic_network import access_options


COMPACT_MODEL_VERSION = "road_joint_v2_compact_fullseason_v1"


@dataclass(frozen=True)
class CompactChain:
    """One strictly serial, unloaded part of a rooted physical tree."""

    chain_id: str
    parent_node_id: str
    child_node_id: str
    original_edge_ids: tuple[str, ...]
    downstream_buildings: tuple[str, ...]
    length_m: float


@dataclass(frozen=True)
class CompactTreeDesign:
    """Solver-independent certificate for one fixed candidate-station tree."""

    site_id: str
    root_node_id: str
    selected_edge_ids: tuple[str, ...]
    selected_access_options: Mapping[str, str]
    pipe_type_by_edge: Mapping[str, str]
    variable_grade_edge_ids: tuple[str, ...]
    sending_peak_kW_by_edge: Mapping[str, float]
    parent_by_node: Mapping[str, str | None]
    parent_edge_by_node: Mapping[str, str]
    orientation_by_edge: Mapping[str, int]
    downstream_buildings_by_edge: Mapping[str, tuple[str, ...]]
    subtree_edges_by_edge: Mapping[str, tuple[str, ...]]
    chains: tuple[CompactChain, ...]
    edge_to_chain: Mapping[str, str]
    capacity_hours_by_edge: Mapping[str, tuple[int, ...]]
    capacity_hour_witness_by_edge: Mapping[str, Mapping[int, int]]
    central_capacity_hours: tuple[int, ...]
    central_capacity_hour_witness: Mapping[int, int]
    root_peak_including_losses_kW: float
    total_route_length_m: float
    source_edge_count: int
    hour_count: int

    def as_metadata(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = "road_joint_v2_compact_tree_design_1"
        payload["model_version"] = COMPACT_MODEL_VERSION
        payload["selected_edge_count"] = len(self.selected_edge_ids)
        payload["chain_count"] = len(self.chains)
        payload["variable_grade_edge_count"] = len(self.variable_grade_edge_ids)
        payload["retained_pipe_capacity_row_count"] = sum(
            len(hours) for hours in self.capacity_hours_by_edge.values()
        )
        payload["full_season_qualified"] = self.hour_count == 2160
        return payload


class _LazyIndex:
    """Small read-only indexed facade for export-only affine expressions.

    A Pyomo ``Expression(E, HOURS)`` over all 578 source edges and 2160 hours
    would create more than a million component objects even though only a tree
    is active.  Existing exporters only require indexing and ``len``; this
    facade constructs an expression when it is actually requested.
    """

    def __init__(self, dimensions: tuple[tuple[Any, ...], ...], getter: Callable[..., Any]):
        self._dimensions = dimensions
        self._getter = getter

    def __getitem__(self, key):
        if len(self._dimensions) == 1:
            if isinstance(key, tuple):
                if len(key) != 1:
                    raise KeyError(key)
                key = key[0]
            return self._getter(key)
        if not isinstance(key, tuple) or len(key) != len(self._dimensions):
            raise KeyError(key)
        return self._getter(*key)

    def __len__(self) -> int:
        result = 1
        for dimension in self._dimensions:
            result *= len(dimension)
        return result

    def __iter__(self):
        if len(self._dimensions) == 1:
            return iter(self._dimensions[0])
        return iter(_cartesian(self._dimensions))


def _cartesian(dimensions: tuple[tuple[Any, ...], ...]):
    if not dimensions:
        yield ()
        return
    for head in dimensions[0]:
        for tail in _cartesian(dimensions[1:]):
            yield (head, *tail)


def _nondominated_rows(
    hours: tuple[int, ...],
    rows: np.ndarray,
) -> tuple[tuple[int, ...], dict[int, int]]:
    """Keep componentwise-maximal rows and return exact dominance witnesses."""

    if rows.shape[0] != len(hours) or rows.ndim != 2:
        raise ValueError("dominance matrix shape does not match hours")
    frontier: list[int] = []
    for index in range(len(hours)):
        row = rows[index]
        if frontier:
            existing = rows[np.asarray(frontier)]
            # An earlier equal row is retained deterministically.
            if np.any(np.all(existing >= row, axis=1)):
                continue
            keep = ~np.all(row >= existing, axis=1)
            frontier = [candidate for candidate, retain in zip(frontier, keep) if retain]
        frontier.append(index)
    retained = tuple(hours[index] for index in frontier)
    witnesses: dict[int, int] = {}
    frontier_rows = rows[np.asarray(frontier)]
    retained_set = set(frontier)
    for index, hour in enumerate(hours):
        if index in retained_set:
            continue
        matches = np.flatnonzero(np.all(frontier_rows >= rows[index], axis=1))
        if not len(matches):  # defensive: transitivity guarantees a witness
            raise RuntimeError(f"no dominance witness for hour {hour}")
        witnesses[hour] = retained[int(matches[0])]
    return retained, witnesses


def _hour_matrix(case: RoadCase, buildings: Iterable[str]) -> np.ndarray:
    d = case.common
    ordered = tuple(buildings)
    if not ordered:
        return np.zeros((len(d.hours), 1), dtype=float)
    return np.asarray(
        [[float(d.heat_demand_kW[building, hour]) for building in ordered]
         for hour in d.hours],
        dtype=float,
    )


def _central_dominance(case: RoadCase) -> tuple[tuple[int, ...], dict[int, int]]:
    d = case.common
    central = tuple(t for t in d.technologies if t.applicable_scope == "central")
    demand = _hour_matrix(case, d.demand_nodes)
    # Higher demand and lower available-capacity ratios make a row stronger.
    inverse_ratios = np.asarray(
        [[-float(d.heat_pump_capacity_ratio_by_hour.get((tech.technology_id, hour), 1.0))
          for tech in central] for hour in d.hours],
        dtype=float,
    )
    return _nondominated_rows(d.hours, np.concatenate((demand, inverse_ratios), axis=1))


def _root_tree(tree: nx.Graph, root: str) -> tuple[dict[str, str | None], dict[str, str], list[str]]:
    parent: dict[str, str | None] = {root: None}
    parent_edge: dict[str, str] = {}
    order = [root]
    for node in order:
        for neighbor in sorted(tree.neighbors(node)):
            if neighbor in parent:
                continue
            parent[neighbor] = node
            parent_edge[neighbor] = tree[node][neighbor]["edge_id"]
            order.append(neighbor)
    if len(order) != len(tree):
        raise ValueError("selected station tree is disconnected")
    return parent, parent_edge, order


def _build_design(case: RoadCase, site_id: str) -> CompactTreeDesign:
    d = case.common
    raw = _site_tree(case, site_id)
    tree: nx.Graph = raw["tree"]
    root = raw["root_node_id"]
    parent, parent_edge, order = _root_tree(tree, root)
    edge_lookup = {edge["edge_id"]: edge for edge in case.network["edges"]}
    levels = sorted(case.pipe_designs, key=lambda x: (x.capacity_kW_th, x.pipe_type_id))
    level_lookup = {level.pipe_type_id: level for level in levels}
    building_order = {building: index for index, building in enumerate(d.demand_nodes)}

    requirements = {node: np.zeros(len(d.hours), dtype=float) for node in tree.nodes}
    for building in d.demand_nodes:
        requirements[building] = np.asarray(
            [float(d.heat_demand_kW[building, hour]) for hour in d.hours], dtype=float
        )
    pipe_type_by_edge: dict[str, str] = {}
    sending_peak_by_edge: dict[str, float] = {}
    for node in reversed(order[1:]):
        edge_id = parent_edge[node]
        edge = edge_lookup[edge_id]
        child_requirement = requirements[node]
        chosen: PipeDesign | None = None
        for level in levels:
            pair_loss = float(edge["length_m"]) * level.pair_loss_kW_per_route_m
            if float(np.max(child_requirement)) + pair_loss <= level.capacity_kW_th + 1e-9:
                chosen = level
                break
        if chosen is None:
            raise ValueError(
                f"no pipe design can carry the full-season all-central load on edge {edge_id}"
            )
        loss = float(edge["length_m"]) * chosen.pair_loss_kW_per_route_m
        pipe_type_by_edge[edge_id] = chosen.pipe_type_id
        sending_peak_by_edge[edge_id] = float(np.max(child_requirement)) + loss
        requirements[parent[node]] += child_requirement + loss

    downstream_by_node: dict[str, set[str]] = {
        node: ({node} if node in building_order else set()) for node in tree.nodes
    }
    subtree_edges_by_node: dict[str, set[str]] = {node: set() for node in tree.nodes}
    for node in reversed(order[1:]):
        upstream = parent[node]
        assert upstream is not None
        downstream_by_node[upstream].update(downstream_by_node[node])
        subtree_edges_by_node[upstream].update(subtree_edges_by_node[node])
        subtree_edges_by_node[upstream].add(parent_edge[node])

    downstream_by_edge: dict[str, tuple[str, ...]] = {}
    subtree_by_edge: dict[str, tuple[str, ...]] = {}
    orientation_by_edge: dict[str, int] = {}
    child_by_edge: dict[str, str] = {}
    selected_order = {edge: index for index, edge in enumerate(raw["selected_edge_ids"])}
    for node in order[1:]:
        edge_id = parent_edge[node]
        edge = edge_lookup[edge_id]
        downstream_by_edge[edge_id] = tuple(
            sorted(downstream_by_node[node], key=building_order.__getitem__)
        )
        subtree_by_edge[edge_id] = tuple(
            sorted(subtree_edges_by_node[node] | {edge_id}, key=selected_order.__getitem__)
        )
        orientation_by_edge[edge_id] = 1 if edge["node_u"] == parent[node] else -1
        child_by_edge[edge_id] = node

    # Strict degree-two contraction, split at a pipe-grade boundary.  Every
    # physical edge remains in all accounting maps.
    visited: set[str] = set()
    chains: list[CompactChain] = []
    edge_to_chain: dict[str, str] = {}
    children = {node: [] for node in tree.nodes}
    for node in order[1:]:
        upstream = parent[node]
        assert upstream is not None
        children[upstream].append(node)
    for row in children.values():
        row.sort()
    for first_child in order[1:]:
        first_edge = parent_edge[first_child]
        if first_edge in visited:
            continue
        original_edges = [first_edge]
        visited.add(first_edge)
        tail = first_child
        while (
            tail not in building_order
            and tree.degree(tail) == 2
            and len(children[tail]) == 1
        ):
            next_child = children[tail][0]
            next_edge = parent_edge[next_child]
            if pipe_type_by_edge[next_edge] != pipe_type_by_edge[original_edges[-1]]:
                break
            original_edges.append(next_edge)
            visited.add(next_edge)
            tail = next_child
        chain_id = f"{site_id}::chain_{len(chains)+1:04d}"
        chain = CompactChain(
            chain_id=chain_id,
            parent_node_id=parent[first_child] or root,
            child_node_id=tail,
            original_edge_ids=tuple(original_edges),
            downstream_buildings=downstream_by_edge[first_edge],
            length_m=sum(float(edge_lookup[e]["length_m"]) for e in original_edges),
        )
        chains.append(chain)
        edge_to_chain.update({edge: chain_id for edge in original_edges})
    if visited != set(raw["selected_edge_ids"]):
        raise RuntimeError("degree-two contraction did not cover the selected tree")

    capacity_hours_by_edge: dict[str, tuple[int, ...]] = {}
    capacity_witness_by_edge: dict[str, dict[int, int]] = {}
    dominance_cache: dict[tuple[str, ...], tuple[tuple[int, ...], dict[int, int]]] = {}
    for edge_id in raw["selected_edge_ids"]:
        downstream = downstream_by_edge[edge_id]
        if downstream not in dominance_cache:
            dominance_cache[downstream] = _nondominated_rows(
                d.hours, _hour_matrix(case, downstream)
            )
        retained, witness = dominance_cache[downstream]
        capacity_hours_by_edge[edge_id] = retained
        capacity_witness_by_edge[edge_id] = witness
    central_hours, central_witness = _central_dominance(case)
    smallest = levels[0]
    # A small pipe is fixed only when it can carry the all-central peak and is
    # weakly better than every alternative in capex, loss and pumping.  Edges
    # failing that dominance screen retain the three static grade binaries, so
    # a hybrid subset is not charged its all-central pipe size.
    smallest_dominates = all(
        smallest.capex_CNY_per_route_m <= level.capex_CNY_per_route_m
        and smallest.pair_loss_kW_per_route_m <= level.pair_loss_kW_per_route_m
        and smallest.pumping_kWh_e_per_kWh_th_m <= level.pumping_kWh_e_per_kWh_th_m
        for level in levels
    )
    variable_grade_edges = tuple(
        edge for edge in raw["selected_edge_ids"]
        if pipe_type_by_edge[edge] != smallest.pipe_type_id or not smallest_dominates
    )

    return CompactTreeDesign(
        site_id=site_id,
        root_node_id=root,
        selected_edge_ids=tuple(raw["selected_edge_ids"]),
        selected_access_options=dict(raw["selected_access_options"]),
        pipe_type_by_edge=pipe_type_by_edge,
        variable_grade_edge_ids=variable_grade_edges,
        sending_peak_kW_by_edge=sending_peak_by_edge,
        parent_by_node=parent,
        parent_edge_by_node=parent_edge,
        orientation_by_edge=orientation_by_edge,
        downstream_buildings_by_edge=downstream_by_edge,
        subtree_edges_by_edge=subtree_by_edge,
        chains=tuple(chains),
        edge_to_chain=edge_to_chain,
        capacity_hours_by_edge=capacity_hours_by_edge,
        capacity_hour_witness_by_edge=capacity_witness_by_edge,
        central_capacity_hours=central_hours,
        central_capacity_hour_witness=central_witness,
        root_peak_including_losses_kW=float(np.max(requirements[root])),
        total_route_length_m=float(raw["total_route_length_m"]),
        source_edge_count=len(case.network["edges"]),
        hour_count=len(d.hours),
    )


def build_compact_tree_designs(case: RoadCase) -> tuple[CompactTreeDesign, ...]:
    """Build one deterministic full-horizon rooted tree for every station."""

    validate_case(case)
    return tuple(
        _build_design(case, site["site_id"])
        for site in sorted(case.network["sites"], key=lambda row: row["site_id"])
    )


def build_compact_tree_design(case: RoadCase, site_id: str) -> CompactTreeDesign:
    """Build only one station design (the efficient worker-process entrypoint)."""

    validate_case(case)
    return _build_design(case, site_id)


def _resolve_design(case: RoadCase, design: CompactTreeDesign | str) -> CompactTreeDesign:
    if isinstance(design, CompactTreeDesign):
        known_sites = {site["site_id"] for site in case.network["sites"]}
        if design.site_id not in known_sites:
            raise ValueError(f"compact design site {design.site_id!r} is not in this case")
        if design.hour_count != len(case.common.hours):
            raise ValueError("compact design horizon does not match the case")
        return design
    if not isinstance(design, str) or not design:
        raise TypeError("design must be a CompactTreeDesign or site_id")
    return _build_design(case, design)


def build_compact_model(
    case: RoadCase,
    design: CompactTreeDesign | str,
    *,
    objective: str = "cost",
    epsilon_kgCO2e_per_year: float | None = None,
    epsilon_tolerance_kgCO2e_per_year: float = 1e-6,
    enable_tes: bool = False,
):
    """Build the compact MILP for one fixed candidate-station tree.

    Only ``connected`` is integer in hybrid mode.  Tree/station activations are
    continuous OR lifts that become exactly zero or one for every integer
    connection vector.  ``objective`` is ``"cost"`` or ``"carbon"`` and an
    optional physical-carbon epsilon bound can be attached in the same call.
    TES is opt-in; its hourly charge, discharge and SOC variables are continuous
    because the guarded marginal assumptions make simultaneous cycling
    removable without worsening either supported objective.
    """

    validate_case(case)
    if case.common.allow_unserved:
        raise ValueError("compact_fullseason_v1 requires allow_unserved=False")
    if enable_tes:
        storage = case.common.storage
        if storage is None:
            raise ValueError(
                "enable_tes=True requires case.common.storage; use enable_tes=False "
                "for the no-TES baseline"
            )
        guarded_values = {
            "electricity prices": case.common.economics.electricity_price_CNY_per_kWh_e.values(),
            "gas prices": case.common.economics.gas_price_CNY_per_kWh_LHV.values(),
            "electricity carbon factors": (
                case.common.economics.electricity_carbon_kgCO2e_per_kWh_e.values()
            ),
            "gas carbon factors": (
                case.common.economics.gas_carbon_kgCO2e_per_kWh_LHV.values()
            ),
            "technology variable O&M": (
                tech.variable_om_CNY_per_kWh_th for tech in case.common.technologies
            ),
        }
        for label, values in guarded_values.items():
            if any(float(value) < 0 for value in values):
                raise ValueError(f"compact TES requires nonnegative {label}")
        if storage.charge_efficiency * storage.discharge_efficiency >= 1:
            raise ValueError(
                "compact TES requires charge_efficiency * discharge_efficiency < 1"
            )
    if objective not in {"cost", "carbon"}:
        raise ValueError("objective must be 'cost' or 'carbon'")
    for name, number in (
        ("epsilon_kgCO2e_per_year", epsilon_kgCO2e_per_year),
        ("epsilon_tolerance_kgCO2e_per_year", epsilon_tolerance_kgCO2e_per_year),
    ):
        if number is not None and (
            isinstance(number, bool) or not isinstance(number, (int, float))
            or not isfinite(float(number)) or float(number) < 0
        ):
            raise ValueError(f"{name} must be a finite nonnegative number")
    design = _resolve_design(case, design)

    d, econ, net = case.common, case.common.economics, case.network
    edges = {edge["edge_id"]: edge for edge in net["edges"]}
    levels = {level.pipe_type_id: level for level in case.pipe_designs}
    options = {option["option_id"]: option for option in access_options(net)}
    stations = {site["site_id"]: site["attachment_node_id"] for site in net["sites"]}
    central = {tech.technology_id: tech for tech in d.technologies if tech.applicable_scope == "central"}
    local = next(tech for tech in d.technologies if tech.applicable_scope == "local")
    electric_techs = [tech for tech in central.values() if tech.energy_carrier == "electricity"]
    gas_techs = [tech for tech in central.values() if tech.energy_carrier == "gas"]
    if len(electric_techs) != 1 or len(gas_techs) != 1:
        raise ValueError("compact_fullseason_v1 requires one central heat pump and one gas boiler")
    if any(tech.capacity_min_kW != 0 for tech in central.values()):
        raise ValueError("compact_fullseason_v1 requires zero central minimum capacities")
    hp, boiler = electric_techs[0], gas_techs[0]
    selected_edges = frozenset(design.selected_edge_ids)
    variable_grade_edges = frozenset(design.variable_grade_edge_ids)
    selected_options = {value for value in design.selected_access_options.values()}
    chain_lookup = {chain.chain_id: chain for chain in design.chains}
    edge_order = tuple(edges)
    hour_order = tuple(d.hours)
    grade_order = tuple(levels)
    station_order = tuple(stations)
    technology_order = tuple(central)
    b2_site = (next(row for row in case.b2_capacity.sites if row.site_id == design.site_id)
               if case.b2_capacity is not None else None)
    local_b2 = (case.b2_capacity.local_hp_capacity_max_kW_th_by_building
                if case.b2_capacity is not None else None)

    model = p.ConcreteModel(name=COMPACT_MODEL_VERSION)
    model.HOURS = p.Set(initialize=hour_order, ordered=True)
    model.DEMAND_NODES = p.Set(initialize=d.demand_nodes, ordered=True)
    model.N = p.Set(initialize=tuple(node["node_id"] for node in net["nodes"]), ordered=True)
    model.S = p.Set(initialize=station_order, ordered=True)
    model.T = p.Set(initialize=technology_order, ordered=True)
    model.E = p.Set(initialize=edge_order, ordered=True)
    model.K = p.Set(initialize=grade_order, ordered=True)
    model.ACCESS = p.Set(initialize=tuple(options), ordered=True)
    model.CHAINS = p.Set(initialize=tuple(chain_lookup), ordered=True)
    model.TES_S = p.Set(
        initialize=(design.site_id,) if enable_tes else (), ordered=True
    )
    model.VARIABLE_GRADE_E = p.Set(
        initialize=tuple(edge for edge in design.selected_edge_ids if edge in variable_grade_edges),
        ordered=True,
    )
    model.FLOW_E = p.Set(initialize=(), ordered=True)
    model.SERVICE_LEAF_E = p.Set(initialize=(), ordered=True)
    model.EXACT_DIRECTION_PAIRS = p.Set(dimen=2, initialize=(), ordered=True)
    model.RELAXED_DIRECTION_PAIRS = p.Set(dimen=2, initialize=(), ordered=True)
    model.DIRECTION_PAIRS = p.Set(dimen=2, initialize=(), ordered=True)
    model.time_weight_h_per_year = p.Param(
        model.HOURS, initialize=dict(econ.time_weight_h_per_year)
    )
    model.uniform_pumping_flow = p.Param(initialize=True, within=p.Boolean)
    model.direction_relaxation = p.Param(initialize=False, within=p.Boolean)
    model.deterministic_demand_dispatch = p.Param(initialize=True, within=p.Boolean)
    model.distributed_fastpath = p.Param(
        initialize=d.mode == "distributed", within=p.Boolean
    )
    model.service_leaf_flow_elimination = p.Param(initialize=True, within=p.Boolean)
    model.compact_no_tes = p.Param(initialize=not enable_tes, within=p.Boolean)

    if d.mode == "hybrid":
        model.connected = p.Var(model.DEMAND_NODES, domain=p.Binary)
        model._chain_active = p.Var(model.CHAINS, bounds=(0.0, 1.0))
        model._station_active = p.Var(bounds=(0.0, 1.0))
    else:
        connection = float(d.mode == "central")
        model.connected = p.Param(model.DEMAND_NODES, initialize=connection)
        model._chain_active = p.Expression(model.CHAINS, rule=lambda *_: connection)
        model._station_active = p.Expression(expr=connection)

    model.local_installed = p.Expression(
        model.DEMAND_NODES, rule=lambda _, building: 1 - model.connected[building]
    )
    model.access_selected = p.Expression(
        model.ACCESS,
        rule=lambda _, option: (
            model.connected[options[option]["building_id"]]
            if option in selected_options else 0.0
        ),
    )
    model.station_built = p.Expression(
        model.S,
        rule=lambda _, site: model._station_active if site == design.site_id else 0.0,
    )
    model.installed = p.Expression(
        model.S, model.T,
        rule=lambda _, site, tech: (
            0.0 if b2_site is not None and site == design.site_id
            and tech not in b2_site.allowed_technology_ids
            else model.station_built[site]
        ),
    )
    model.built = p.Expression(
        model.E,
        rule=lambda _, edge: (
            model._chain_active[design.edge_to_chain[edge]] if edge in selected_edges else 0.0
        ),
    )
    if variable_grade_edges:
        pumping_rates = {level.pumping_kWh_e_per_kWh_th_m for level in levels.values()}
        if len(pumping_rates) != 1:
            raise ValueError(
                "selectable compact pipe grades require a common pumping coefficient"
            )
        model._grade_selected = p.Var(model.VARIABLE_GRADE_E, model.K, domain=p.Binary)
    else:
        model._grade_selected = p.Expression(
            model.VARIABLE_GRADE_E, model.K, rule=lambda *_: 0.0
        )
    model.grade = p.Expression(
        model.E, model.K,
        rule=lambda _, edge, grade: (
            model._grade_selected[edge, grade]
            if edge in variable_grade_edges else
            (model.built[edge]
             if edge in selected_edges and design.pipe_type_by_edge[edge] == grade else 0.0)
        ),
    )
    model.edge_capacity = p.Expression(
        model.E,
        rule=lambda _, edge: (
            sum(levels[grade].capacity_kW_th * model.grade[edge, grade] for grade in grade_order)
            if edge in selected_edges else 0.0
        ),
    )
    model.edge_loss = p.Expression(
        model.E,
        rule=lambda _, edge: (
            float(edges[edge]["length_m"])
            * sum(
                levels[grade].pair_loss_kW_per_route_m * model.grade[edge, grade]
                for grade in grade_order
            )
            if edge in selected_edges else 0.0
        ),
    )

    model.topology_constraints = p.ConstraintList()
    if d.mode == "hybrid":
        for chain in design.chains:
            active = model._chain_active[chain.chain_id]
            for building in chain.downstream_buildings:
                model.topology_constraints.add(active >= model.connected[building])
            model.topology_constraints.add(
                active <= sum(model.connected[b] for b in chain.downstream_buildings)
            )
        for building in d.demand_nodes:
            model.topology_constraints.add(model._station_active >= model.connected[building])
        model.topology_constraints.add(
            model._station_active <= sum(model.connected[b] for b in d.demand_nodes)
        )
    for edge in model.VARIABLE_GRADE_E:
        model.topology_constraints.add(
            sum(model._grade_selected[edge, grade] for grade in model.K) == model.built[edge]
        )

    def edge_midpoint(edge: str, hour: int):
        if edge not in selected_edges:
            return 0.0
        demand = sum(
            d.heat_demand_kW[building, hour] * model.connected[building]
            for building in design.downstream_buildings_by_edge[edge]
        )
        losses = sum(model.edge_loss[item] for item in design.subtree_edges_by_edge[edge])
        return demand + losses - model.edge_loss[edge] / 2

    def forward(edge: str, hour: int):
        return edge_midpoint(edge, hour) if design.orientation_by_edge.get(edge) == 1 else 0.0

    def reverse(edge: str, hour: int):
        return edge_midpoint(edge, hour) if design.orientation_by_edge.get(edge) == -1 else 0.0

    model.forward = _LazyIndex((edge_order, hour_order), forward)
    model.reverse = _LazyIndex((edge_order, hour_order), reverse)
    model.flow = _LazyIndex(
        (edge_order, hour_order),
        lambda edge, hour: design.orientation_by_edge.get(edge, 0) * edge_midpoint(edge, hour),
    )
    model.abs_flow = _LazyIndex((edge_order, hour_order), edge_midpoint)
    model.direction = _LazyIndex(
        (edge_order, hour_order),
        lambda edge, hour: (
            model.built[edge] if design.orientation_by_edge.get(edge) == 1 else 0.0
        ),
    )
    model.pump = _LazyIndex(
        (edge_order, hour_order),
        lambda edge, hour: (
            float(edges[edge]["length_m"])
            * sum(
                levels[grade].pumping_kWh_e_per_kWh_th_m * model.grade[edge, grade]
                for grade in grade_order
            )
            * edge_midpoint(edge, hour)
            if edge in selected_edges else 0.0
        ),
    )
    model.commodity = p.Expression(
        model.E,
        rule=lambda _, edge: (
            design.orientation_by_edge[edge]
            * sum(model.connected[b] for b in design.downstream_buildings_by_edge[edge])
            if edge in selected_edges else 0.0
        ),
    )
    model.commodity_supply = p.Expression(
        model.S,
        rule=lambda _, site: (
            sum(model.connected[b] for b in d.demand_nodes) if site == design.site_id else 0.0
        ),
    )

    # Full-horizon local capacity is exact because local dispatch is fixed by
    # the connection decision and capacity has nonnegative annual cost.
    local_required = {
        building: max(
            local.capacity_min_kW,
            max(
                (1 + d.peak_capacity_margin_fraction)
                * d.heat_demand_kW[building, hour]
                / d.heat_pump_capacity_ratio_by_hour.get((local.technology_id, hour), 1.0)
                for hour in d.hours
            ),
        )
        for building in d.demand_nodes
    }
    if any(value > (local_b2[building] if local_b2 is not None else local.capacity_max_kW) + 1e-9
           for building, value in local_required.items()):
        raise ValueError("a full-season local peak exceeds local technology capacity_max_kW")
    model.local_capacity = p.Expression(
        model.DEMAND_NODES,
        rule=lambda _, building: local_required[building] * model.local_installed[building],
    )
    model.local_heat = p.Expression(
        model.DEMAND_NODES, model.HOURS,
        rule=lambda _, building, hour: (
            d.heat_demand_kW[building, hour] * model.local_installed[building]
        ),
    )
    model.network_heat = p.Expression(
        model.DEMAND_NODES, model.HOURS,
        rule=lambda _, building, hour: d.heat_demand_kW[building, hour] * model.connected[building],
    )
    model.unserved_heat_kW = p.Expression(
        model.DEMAND_NODES, model.HOURS, rule=lambda *_: 0.0
    )

    model.total_edge_loss = p.Expression(
        expr=sum(model.edge_loss[edge] for edge in design.selected_edge_ids)
    )

    if enable_tes:
        storage = d.storage
        assert storage is not None
        # The station/topology is fixed for each candidate model.  Keep TES
        # variables only at that one station; public expressions still expose
        # every original station ID for the unchanged exporter/QA interface.
        model._tes_built = p.Var(model.TES_S, domain=p.Binary)
        model._tes_energy = p.Var(model.TES_S, domain=p.NonNegativeReals)
        model._tes_charge_capacity = p.Var(model.TES_S, domain=p.NonNegativeReals)
        model._tes_discharge_capacity = p.Var(model.TES_S, domain=p.NonNegativeReals)
        model._tes_power_cost_capacity = p.Var(model.TES_S, domain=p.NonNegativeReals)
        model._charge = p.Var(model.TES_S, model.HOURS, domain=p.NonNegativeReals)
        model._discharge = p.Var(model.TES_S, model.HOURS, domain=p.NonNegativeReals)
        model._soc = p.Var(model.TES_S, model.HOURS, domain=p.NonNegativeReals)
        model.tes_built = p.Expression(
            model.S,
            rule=lambda _, site: model._tes_built[site] if site in model.TES_S else 0.0,
        )
        model.tes_energy = p.Expression(
            model.S,
            rule=lambda _, site: model._tes_energy[site] if site in model.TES_S else 0.0,
        )
        model.tes_charge_capacity = p.Expression(
            model.S,
            rule=lambda _, site: (
                model._tes_charge_capacity[site] if site in model.TES_S else 0.0
            ),
        )
        model.tes_discharge_capacity = p.Expression(
            model.S,
            rule=lambda _, site: (
                model._tes_discharge_capacity[site] if site in model.TES_S else 0.0
            ),
        )
        model.tes_power_cost_capacity = p.Expression(
            model.S,
            rule=lambda _, site: (
                model._tes_power_cost_capacity[site] if site in model.TES_S else 0.0
            ),
        )
        model.charge = p.Expression(
            model.S, model.HOURS,
            rule=lambda _, site, hour: (
                model._charge[site, hour] if site in model.TES_S else 0.0
            ),
        )
        model.discharge = p.Expression(
            model.S, model.HOURS,
            rule=lambda _, site, hour: (
                model._discharge[site, hour] if site in model.TES_S else 0.0
            ),
        )
        model.soc = p.Expression(
            model.S, model.HOURS,
            rule=lambda _, site, hour: (
                model._soc[site, hour] if site in model.TES_S else 0.0
            ),
        )
        # Export compatibility only: unlike the generic reference MILP, the
        # guarded compact formulation has no hourly charging binary.
        model.charging = p.Expression(model.S, model.HOURS, rule=lambda *_: 0.0)
        model.storage_constraints = p.ConstraintList()
        for site in model.TES_S:
            built = model._tes_built[site]
            model.storage_constraints.add(built <= model.station_built[site])
            model.storage_constraints.add(
                model.tes_energy[site] <= storage.energy_capacity_max_kWh_th * built
            )
            model.storage_constraints.add(
                model.tes_charge_capacity[site]
                <= storage.charge_capacity_max_kW_th * built
            )
            model.storage_constraints.add(
                model.tes_discharge_capacity[site]
                <= storage.discharge_capacity_max_kW_th * built
            )
            model.storage_constraints.add(
                model.tes_power_cost_capacity[site] >= model.tes_charge_capacity[site]
            )
            model.storage_constraints.add(
                model.tes_power_cost_capacity[site] >= model.tes_discharge_capacity[site]
            )
            model.storage_constraints.add(
                model.tes_power_cost_capacity[site]
                <= max(
                    storage.charge_capacity_max_kW_th,
                    storage.discharge_capacity_max_kW_th,
                ) * built
            )
            if storage.max_charge_ratio_per_hour is not None:
                model.storage_constraints.add(
                    model.tes_charge_capacity[site]
                    <= storage.max_charge_ratio_per_hour * model.tes_energy[site]
                )
            if storage.max_discharge_ratio_per_hour is not None:
                model.storage_constraints.add(
                    model.tes_discharge_capacity[site]
                    <= storage.max_discharge_ratio_per_hour * model.tes_energy[site]
                )
            for index, hour in enumerate(hour_order):
                previous = hour_order[index - 1]
                model.storage_constraints.add(
                    model.soc[site, hour] <= model.tes_energy[site]
                )
                model.storage_constraints.add(
                    model.charge[site, hour] <= model.tes_charge_capacity[site]
                )
                model.storage_constraints.add(
                    model.discharge[site, hour] <= model.tes_discharge_capacity[site]
                )
                model.storage_constraints.add(
                    model.soc[site, hour]
                    == model.soc[site, previous]
                    * (1 - storage.standing_loss_fraction_per_hour)
                    + storage.charge_efficiency * model.charge[site, hour]
                    - model.discharge[site, hour] / storage.discharge_efficiency
                )
    else:
        # No-TES compatibility surface for the existing exporter and QA.
        model.tes_built = p.Expression(model.S, rule=lambda *_: 0.0)
        model.tes_energy = p.Expression(model.S, rule=lambda *_: 0.0)
        model.tes_charge_capacity = p.Expression(model.S, rule=lambda *_: 0.0)
        model.tes_discharge_capacity = p.Expression(model.S, rule=lambda *_: 0.0)
        model.tes_power_cost_capacity = p.Expression(model.S, rule=lambda *_: 0.0)
        model.charge = p.Expression(model.S, model.HOURS, rule=lambda *_: 0.0)
        model.discharge = p.Expression(model.S, model.HOURS, rule=lambda *_: 0.0)
        model.soc = p.Expression(model.S, model.HOURS, rule=lambda *_: 0.0)
        model.charging = p.Expression(model.S, model.HOURS, rule=lambda *_: 0.0)

    model.source_heat_requirement = p.Expression(
        model.HOURS,
        rule=lambda _, hour: (
            sum(d.heat_demand_kW[b, hour] * model.connected[b] for b in d.demand_nodes)
            + model.total_edge_loss
        ),
    )
    model.source_generation_requirement = p.Expression(
        model.HOURS,
        rule=lambda _, hour: (
            model.source_heat_requirement[hour]
            + sum(model.charge[site, hour] - model.discharge[site, hour] for site in model.S)
        ),
    )
    if d.mode == "distributed":
        model._central_capacity = p.Expression(model.T, rule=lambda *_: 0.0)
        model._central_hp_heat = p.Expression(model.HOURS, rule=lambda *_: 0.0)
    else:
        model._central_capacity = p.Var(model.T, domain=p.NonNegativeReals)
        model._central_hp_heat = p.Var(model.HOURS, domain=p.NonNegativeReals)
    model.capacity = p.Expression(
        model.S, model.T,
        rule=lambda _, site, tech: (
            model._central_capacity[tech] if site == design.site_id else 0.0
        ),
    )

    def heat_rule(_, site, tech, hour):
        if site != design.site_id or d.mode == "distributed":
            return 0.0
        if tech == hp.technology_id:
            return model._central_hp_heat[hour]
        if tech == boiler.technology_id:
            return model.source_generation_requirement[hour] - model._central_hp_heat[hour]
        raise KeyError(tech)

    model.heat = p.Expression(model.S, model.T, model.HOURS, rule=heat_rule)
    model.central_constraints = p.ConstraintList()
    if d.mode != "distributed":
        for tech in central.values():
            if b2_site is not None and tech.technology_id not in b2_site.allowed_technology_ids:
                model._central_capacity[tech.technology_id].fix(0)
            capacity_max = (b2_site.technology_capacity_max_kW_th[tech.technology_id]
                            if b2_site is not None and tech.technology_id in b2_site.allowed_technology_ids
                            else tech.capacity_max_kW)
            model.central_constraints.add(
                model._central_capacity[tech.technology_id]
                <= capacity_max * model._station_active
            )
        for hour in d.hours:
            model.central_constraints.add(
                model._central_hp_heat[hour]
                <= model._central_capacity[hp.technology_id]
                * d.heat_pump_capacity_ratio_by_hour.get((hp.technology_id, hour), 1.0)
            )
            model.central_constraints.add(
                model._central_hp_heat[hour] <= model.source_generation_requirement[hour]
            )
            model.central_constraints.add(
                model.source_generation_requirement[hour] - model._central_hp_heat[hour]
                <= model._central_capacity[boiler.technology_id]
                * d.heat_pump_capacity_ratio_by_hour.get((boiler.technology_id, hour), 1.0)
            )
        for hour in design.central_capacity_hours:
            model.central_constraints.add(
                sum(
                    model._central_capacity[tech.technology_id]
                    * d.heat_pump_capacity_ratio_by_hour.get((tech.technology_id, hour), 1.0)
                    for tech in central.values()
                )
                >= (1 + d.peak_capacity_margin_fraction)
                * model.source_heat_requirement[hour]
            )
        if b2_site is not None:
            for hour in d.hours:
                if hp.technology_id in b2_site.allowed_technology_ids:
                    model.central_constraints.add(
                        model._central_hp_heat[hour]
                        / d.heat_pump_cop_by_hour.get((hp.technology_id, hour), hp.cop)
                        <= b2_site.electricity_connection_max_kW_e * model._station_active
                    )
                if boiler.technology_id in b2_site.allowed_technology_ids:
                    model.central_constraints.add(
                        (model.source_generation_requirement[hour] - model._central_hp_heat[hour])
                        / boiler.efficiency
                        <= b2_site.gas_connection_max_kW_LHV * model._station_active
                    )
            model.b2_site_capacity_limit = p.Constraint(
                model.T,
                rule=lambda _, tech: (
                    model._central_capacity[tech] == 0
                    if tech not in b2_site.allowed_technology_ids
                    else model._central_capacity[tech]
                    <= b2_site.technology_capacity_max_kW_th[tech] * model._station_active))
            model.b2_electricity_connection_limit = p.Constraint(
                model.HOURS,
                rule=lambda _, hour: (
                    model._central_hp_heat[hour]
                    / d.heat_pump_cop_by_hour.get((hp.technology_id, hour), hp.cop)
                    <= b2_site.electricity_connection_max_kW_e * model._station_active
                    if hp.technology_id in b2_site.allowed_technology_ids else p.Constraint.Skip))
            model.b2_gas_connection_limit = p.Constraint(
                model.HOURS,
                rule=lambda _, hour: (
                    (model.source_generation_requirement[hour] - model._central_hp_heat[hour])
                    / boiler.efficiency
                    <= b2_site.gas_connection_max_kW_LHV * model._station_active
                    if boiler.technology_id in b2_site.allowed_technology_ids else p.Constraint.Skip))

    # Dominance-reduced physical capacity rows.  The witness maps in ``design``
    # certify every omitted hour, while audit_compact_solution checks all hours.
    model.EDGE_CAPACITY_HOURS = p.Set(
        dimen=2,
        initialize=tuple(
            (edge, hour)
            for edge in design.selected_edge_ids
            for hour in design.capacity_hours_by_edge[edge]
        ),
        ordered=True,
    )
    model.edge_capacity_constraints = p.Constraint(
        model.EDGE_CAPACITY_HOURS,
        rule=lambda _, edge, hour: (
            sum(
                d.heat_demand_kW[building, hour] * model.connected[building]
                for building in design.downstream_buildings_by_edge[edge]
            )
            + sum(model.edge_loss[item] for item in design.subtree_edges_by_edge[edge])
            <= model.edge_capacity[edge]
        ),
    )

    # Aggregate pumping algebraically so the objective has O(B*H + E) terms,
    # rather than expanding every downstream set once per edge and hour.
    edge_rate = {
        edge: float(edges[edge]["length_m"])
        * levels[design.pipe_type_by_edge[edge]].pumping_kWh_e_per_kWh_th_m
        for edge in design.selected_edge_ids
    }
    building_pump_coefficient = {
        building: sum(
            edge_rate[edge]
            for edge in design.selected_edge_ids
            if building in design.downstream_buildings_by_edge[edge]
        )
        for building in d.demand_nodes
    }
    loss_pump_coefficient: dict[str, float] = {}
    for loss_edge in design.selected_edge_ids:
        coefficient = 0.5 * edge_rate[loss_edge]
        loss_child = next(
            node for node, edge in design.parent_edge_by_node.items() if edge == loss_edge
        )
        ancestor = design.parent_by_node[loss_child]
        while ancestor is not None and ancestor != design.root_node_id:
            ancestor_edge = design.parent_edge_by_node[ancestor]
            coefficient += edge_rate[ancestor_edge]
            ancestor = design.parent_by_node[ancestor]
        loss_pump_coefficient[loss_edge] = coefficient
    model.pump_loss_electricity = p.Expression(
        expr=sum(
            loss_pump_coefficient[edge] * model.edge_loss[edge]
            for edge in design.selected_edge_ids
        )
    )
    model.total_pump = p.Expression(
        model.HOURS,
        rule=lambda _, hour: (
            sum(
                building_pump_coefficient[b]
                * d.heat_demand_kW[b, hour]
                * model.connected[b]
                for b in d.demand_nodes
            )
            + model.pump_loss_electricity
        ),
    )

    def cop(tech_id: str, hour: int, fallback: float | None) -> float:
        value = d.heat_pump_cop_by_hour.get((tech_id, hour), fallback)
        assert value is not None
        return value

    model.electricity = p.Expression(
        model.HOURS,
        rule=lambda _, hour: (
            model._central_hp_heat[hour] / cop(hp.technology_id, hour, hp.cop)
            + sum(
                model.local_heat[b, hour] / cop(local.technology_id, hour, local.cop)
                for b in d.demand_nodes
            )
            + model.total_pump[hour]
        ),
    )
    billing_month_by_hour = (case.monthly_demand_charge.billing_month_by_hour
                             if case.monthly_demand_charge is not None else {
        hour: "not_applied" for hour in d.hours
    })
    monthly_demand_rate = (case.monthly_demand_charge.rate_CNY_per_kW_month
                           if case.monthly_demand_charge is not None else 0.0)
    billing_months = tuple(dict.fromkeys(billing_month_by_hour[hour] for hour in d.hours))
    model.BILLING_MONTHS = p.Set(initialize=billing_months, ordered=True)
    model.monthly_peak_kW_e = p.Var(model.BILLING_MONTHS, domain=p.NonNegativeReals)
    model.monthly_peak_constraint = p.Constraint(
        model.HOURS,
        rule=lambda _, hour: model.monthly_peak_kW_e[billing_month_by_hour[hour]]
        >= model.electricity[hour],
    )
    model.gas = p.Expression(
        model.HOURS,
        rule=lambda _, hour: (
            model.source_generation_requirement[hour] - model._central_hp_heat[hour]
        ) / boiler.efficiency,
    )

    rate = econ.discount_rate
    model.device_investment = p.Expression(
        expr=sum(
            model._central_capacity[tech.technology_id]
            * tech.capex_CNY_per_kW * crf(rate, tech.lifetime_years)
            for tech in central.values()
        ) + sum(
            model.local_capacity[b] * local.capex_CNY_per_kW * crf(rate, local.lifetime_years)
            for b in d.demand_nodes
        )
    )
    model.fixed_om = p.Expression(
        expr=sum(
            model._central_capacity[tech.technology_id]
            * tech.capex_CNY_per_kW * tech.fixed_maintenance_fraction_per_year
            for tech in central.values()
        ) + sum(
            model.local_capacity[b] * local.capex_CNY_per_kW
            * local.fixed_maintenance_fraction_per_year
            for b in d.demand_nodes
        )
    )
    model.pipe_investment = p.Expression(
        expr=sum(
            float(edges[edge]["length_m"])
            * levels[grade].capex_CNY_per_route_m
            * crf(rate, levels[grade].lifetime_years)
            * model.grade[edge, grade]
            for edge in design.selected_edge_ids for grade in grade_order
        )
    )
    model.station_investment = p.Expression(
        expr=model._station_active * econ.station_fixed_capex_CNY
        * crf(rate, econ.station_lifetime_years)
    )
    model.connection_investment = p.Expression(
        expr=sum(
            model.connected[b] * econ.connection_capex_CNY[b]
            * crf(rate, econ.connection_lifetime_years[b])
            for b in d.demand_nodes
        )
    )
    storage = d.storage
    model.storage_investment = p.Expression(
        expr=(
            sum(
                model.tes_energy[site] * storage.capex_CNY_per_kWh_th
                + model.tes_power_cost_capacity[site]
                * storage.power_capex_CNY_per_kW_th
                + model.tes_built[site] * storage.fixed_capex_CNY
                for site in model.S
            ) * crf(rate, storage.lifetime_years)
            if enable_tes and storage is not None else 0.0
        )
    )
    model.electricity_cost = p.Expression(
        expr=sum(
            model.electricity[hour] * econ.electricity_price_CNY_per_kWh_e[hour]
            * econ.time_weight_h_per_year[hour]
            for hour in d.hours
        )
    )
    model.gas_cost = p.Expression(
        expr=sum(
            model.gas[hour] * econ.gas_price_CNY_per_kWh_LHV[hour]
            * econ.time_weight_h_per_year[hour]
            for hour in d.hours
        )
    )
    model.annual_monthly_demand_charge_CNY_per_year = p.Expression(
        expr=sum(model.monthly_peak_kW_e[month] for month in model.BILLING_MONTHS)
        * monthly_demand_rate
    )
    model.variable_om = p.Expression(
        expr=sum(
            (
                model._central_hp_heat[hour] * hp.variable_om_CNY_per_kWh_th
                + (model.source_generation_requirement[hour] - model._central_hp_heat[hour])
                * boiler.variable_om_CNY_per_kWh_th
                + sum(
                    model.local_heat[b, hour] * local.variable_om_CNY_per_kWh_th
                    for b in d.demand_nodes
                )
            ) * econ.time_weight_h_per_year[hour]
            for hour in d.hours
        )
    )
    model.annual_real_cost_CNY_per_year = p.Expression(
        expr=model.device_investment + model.fixed_om + model.pipe_investment
        + model.station_investment + model.connection_investment
        + model.storage_investment + model.electricity_cost + model.gas_cost
        + model.variable_om
        + model.annual_monthly_demand_charge_CNY_per_year
    )
    model.annual_hns_penalty_CNY_per_year = p.Expression(expr=0.0)
    model.annual_operating_physical_carbon_kgCO2e_per_year = p.Expression(
        expr=sum(
            (
                model.electricity[hour]
                * econ.electricity_carbon_kgCO2e_per_kWh_e[hour]
                + model.gas[hour] * econ.gas_carbon_kgCO2e_per_kWh_LHV[hour]
            ) * econ.time_weight_h_per_year[hour]
            for hour in d.hours
        )
    )
    model.policy_carbon_cost = p.Expression(
        expr=model.annual_operating_physical_carbon_kgCO2e_per_year / 1000
        * econ.policy_carbon_price_CNY_per_tCO2e
    )
    if epsilon_kgCO2e_per_year is not None:
        model.pareto_carbon_limit = p.Constraint(
            expr=model.annual_operating_physical_carbon_kgCO2e_per_year
            <= float(epsilon_kgCO2e_per_year) + float(epsilon_tolerance_kgCO2e_per_year)
        )
    model.annual_cost_objective = p.Objective(
        expr=model.annual_real_cost_CNY_per_year,
        sense=p.minimize,
    )
    if objective == "carbon":
        model.annual_cost_objective.deactivate()
        model.annual_carbon_objective = p.Objective(
            expr=model.annual_operating_physical_carbon_kgCO2e_per_year,
            sense=p.minimize,
        )

    # Underscore attributes are deliberately plain Python evidence, not Pyomo
    # components and therefore never enter the optimization matrix.
    model._compact_design = design
    model._compact_case_hour_count = len(d.hours)
    model._compact_objective = objective
    model._compact_tes_enabled = enable_tes
    return model


def compact_model_metadata(model) -> dict[str, Any]:
    """Return JSON-ready structural metadata without requiring a solve."""

    design = getattr(model, "_compact_design", None)
    if not isinstance(design, CompactTreeDesign):
        raise TypeError("model was not built by build_compact_model")
    binary_count = sum(
        variable.is_binary()
        for variable in model.component_data_objects(p.Var, active=True)
    )
    return {
        "schema": "road_joint_v2_compact_model_metadata_1",
        "model_version": COMPACT_MODEL_VERSION,
        "site_id": design.site_id,
        "root_node_id": design.root_node_id,
        "source_edge_count": design.source_edge_count,
        "selected_edge_count": len(design.selected_edge_ids),
        "chain_count": len(design.chains),
        "variable_grade_edge_count": len(design.variable_grade_edge_ids),
        "retained_pipe_capacity_row_count": sum(
            len(hours) for hours in design.capacity_hours_by_edge.values()
        ),
        "retained_central_capacity_row_count": len(design.central_capacity_hours),
        "hour_count": design.hour_count,
        "full_season_qualified": design.hour_count == 2160,
        "root_peak_including_losses_kW": design.root_peak_including_losses_kW,
        "total_route_length_m": design.total_route_length_m,
        "objective": model._compact_objective,
        "variable_count": model.nvariables(),
        "binary_variable_count": binary_count,
        "constraint_count": model.nconstraints(),
        "network_flow_variable_count": 0,
        "direction_variable_count": 0,
        "commodity_variable_count": 0,
        "tes_enabled": bool(getattr(model, "_compact_tes_enabled", False)),
    }


def materialize_compact_export_views(case: RoadCase, model) -> None:
    """Cache solved edge-hour views as dense arrays before full export.

    The optimization model intentionally keeps flow expressions lazy.  That is
    excellent for model construction, but a full 578 x 2160 export otherwise
    expands the same affine Pyomo expression several million times.  This
    routine evaluates the identical rooted-tree algebra once with NumPy and
    replaces only the plain-Python export facades; no optimization component is
    changed.
    """

    design = getattr(model, "_compact_design", None)
    if not isinstance(design, CompactTreeDesign):
        raise TypeError("model was not built by build_compact_model")
    edge_order = tuple(model.E)
    hour_order = tuple(model.HOURS)
    building_order = tuple(case.common.demand_nodes)
    edge_index = {edge: index for index, edge in enumerate(edge_order)}
    hour_index = {hour: index for index, hour in enumerate(hour_order)}
    building_index = {
        building: index for index, building in enumerate(building_order)
    }
    selected = tuple(design.selected_edge_ids)
    demand = np.asarray(
        [
            [case.common.heat_demand_kW[building, hour] for hour in hour_order]
            for building in building_order
        ],
        dtype=float,
    )
    connection = np.asarray(
        [p.value(model.connected[building]) for building in building_order],
        dtype=float,
    )
    incidence = np.zeros((len(selected), len(building_order)), dtype=float)
    for row, edge in enumerate(selected):
        for building in design.downstream_buildings_by_edge[edge]:
            incidence[row, building_index[building]] = 1.0
    selected_midpoint = incidence @ (connection[:, None] * demand)
    loss = {
        edge: float(p.value(model.edge_loss[edge]))
        for edge in selected
    }
    for row, edge in enumerate(selected):
        selected_midpoint[row, :] += (
            sum(loss[item] for item in design.subtree_edges_by_edge[edge])
            - loss[edge] / 2.0
        )
    midpoint = np.zeros((len(edge_order), len(hour_order)), dtype=float)
    orientation = np.zeros(len(edge_order), dtype=float)
    direction = np.zeros(len(edge_order), dtype=float)
    edge_loss = np.zeros(len(edge_order), dtype=float)
    pump_rate = np.zeros(len(edge_order), dtype=float)
    edge_lookup = {edge["edge_id"]: edge for edge in case.network["edges"]}
    levels = {level.pipe_type_id: level for level in case.pipe_designs}
    for row, edge in enumerate(selected):
        target = edge_index[edge]
        midpoint[target, :] = selected_midpoint[row, :]
        orientation[target] = design.orientation_by_edge[edge]
        edge_loss[target] = loss[edge]
        direction[target] = (
            float(p.value(model.built[edge]))
            if design.orientation_by_edge[edge] == 1 else 0.0
        )
        pump_rate[target] = float(edge_lookup[edge]["length_m"]) * sum(
            levels[grade].pumping_kWh_e_per_kWh_th_m
            * float(p.value(model.grade[edge, grade]))
            for grade in model.K
        )
    if not np.isfinite(midpoint).all() or float(np.min(midpoint)) < -1e-6:
        raise ValueError("materialized compact flow contains invalid values")

    def mid(edge: str, hour: int) -> float:
        return float(midpoint[edge_index[edge], hour_index[hour]])

    model.abs_flow = _LazyIndex((edge_order, hour_order), mid)
    model.forward = _LazyIndex(
        (edge_order, hour_order),
        lambda edge, hour: mid(edge, hour) if orientation[edge_index[edge]] == 1 else 0.0,
    )
    model.reverse = _LazyIndex(
        (edge_order, hour_order),
        lambda edge, hour: mid(edge, hour) if orientation[edge_index[edge]] == -1 else 0.0,
    )
    model.flow = _LazyIndex(
        (edge_order, hour_order),
        lambda edge, hour: orientation[edge_index[edge]] * mid(edge, hour),
    )
    model.direction = _LazyIndex(
        (edge_order, hour_order),
        lambda edge, _hour: float(direction[edge_index[edge]]),
    )
    model.pump = _LazyIndex(
        (edge_order, hour_order),
        lambda edge, hour: pump_rate[edge_index[edge]] * mid(edge, hour),
    )
    # Plain NumPy evidence used by the full-horizon audit.  Keeping this cache
    # avoids re-expanding 400k+ affine Pyomo expressions after every scan solve.
    model._compact_midpoint_matrix = midpoint
    model._compact_edge_index = edge_index
    model._compact_orientation_vector = orientation
    model._compact_direction_vector = direction
    model._compact_edge_loss_vector = edge_loss
    model._compact_pump_rate_vector = pump_rate
    model._compact_demand_matrix = demand
    model._compact_connection_vector = connection
    model._compact_export_views_materialized = True


def export_compact_solution(case: RoadCase, model, root: Any):
    """Export through the existing interface after one-time flow caching."""

    materialize_compact_export_views(case, model)
    from urbanheatopt.qa.road_results import export_solution

    return export_solution(case, model, root)


def audit_compact_solution(
    case: RoadCase,
    model,
    *,
    tolerance_kW: float = 1e-6,
) -> dict[str, Any]:
    """Check all omitted full-horizon capacity rows and affine flow identities."""

    if isinstance(tolerance_kW, bool) or not isfinite(tolerance_kW) or tolerance_kW < 0:
        raise ValueError("tolerance_kW must be a finite nonnegative number")
    design = getattr(model, "_compact_design", None)
    if not isinstance(design, CompactTreeDesign):
        raise TypeError("model was not built by build_compact_model")
    d = case.common
    # Recompute on every audit so a caller that deliberately perturbs a solved
    # decision for a negative test cannot observe a stale cached flow matrix.
    materialize_compact_export_views(case, model)
    midpoint = np.asarray(model._compact_midpoint_matrix, dtype=float)
    edge_index = model._compact_edge_index
    selected = tuple(design.selected_edge_ids)
    selected_rows = np.asarray([edge_index[edge] for edge in selected], dtype=int)
    losses = np.asarray(
        [float(p.value(model.edge_loss[edge])) for edge in selected], dtype=float
    )
    capacities = np.asarray(
        [float(p.value(model.edge_capacity[edge])) for edge in selected], dtype=float
    )
    pipe_excess = (
        midpoint[selected_rows, :] + losses[:, None] / 2.0 - capacities[:, None]
    )
    max_pipe_excess = 0.0
    worst_pipe: tuple[str, int] | None = None
    if pipe_excess.size:
        flat_index = int(np.argmax(pipe_excess))
        candidate = float(pipe_excess.flat[flat_index])
        if candidate > 0.0:
            row, column = np.unravel_index(flat_index, pipe_excess.shape)
            max_pipe_excess = candidate
            worst_pipe = (selected[int(row)], tuple(d.hours)[int(column)])

    technology_order = tuple(model.T)
    capacities_by_technology = np.asarray(
        [
            sum(float(p.value(model.capacity[site, tech])) for site in model.S)
            for tech in technology_order
        ],
        dtype=float,
    )
    availability_ratios = np.asarray(
        [
            [
                float(d.heat_pump_capacity_ratio_by_hour.get((tech, hour), 1.0))
                for tech in technology_order
            ]
            for hour in d.hours
        ],
        dtype=float,
    )
    available_by_hour = availability_ratios @ capacities_by_technology
    source_by_hour = (
        np.asarray(model._compact_connection_vector, dtype=float)
        @ np.asarray(model._compact_demand_matrix, dtype=float)
        + float(np.sum(losses))
    )
    margin_shortfall = (
        (1.0 + d.peak_capacity_margin_fraction) * source_by_hour
        - available_by_hour
    )
    max_margin_shortfall = 0.0
    worst_margin_hour: int | None = None
    if margin_shortfall.size:
        hour_index = int(np.argmax(margin_shortfall))
        candidate = float(margin_shortfall[hour_index])
        if candidate > 0.0:
            max_margin_shortfall = candidate
            worst_margin_hour = tuple(d.hours)[hour_index]
    max_soc_residual = 0.0
    worst_soc_pair: tuple[str, int] | None = None
    max_simultaneous = 0.0
    worst_simultaneous_pair: tuple[str, int] | None = None
    if bool(getattr(model, "_compact_tes_enabled", False)):
        storage = d.storage
        assert storage is not None
        hours = tuple(d.hours)
        for site in model.TES_S:
            charge = np.asarray(
                [float(p.value(model.charge[site, hour])) for hour in hours],
                dtype=float,
            )
            discharge = np.asarray(
                [float(p.value(model.discharge[site, hour])) for hour in hours],
                dtype=float,
            )
            soc = np.asarray(
                [float(p.value(model.soc[site, hour])) for hour in hours],
                dtype=float,
            )
            expected_soc = (
                np.roll(soc, 1) * (1 - storage.standing_loss_fraction_per_hour)
                + storage.charge_efficiency * charge
                - discharge / storage.discharge_efficiency
            )
            residual = np.abs(soc - expected_soc)
            residual_index = int(np.argmax(residual))
            if float(residual[residual_index]) > max_soc_residual:
                max_soc_residual = float(residual[residual_index])
                worst_soc_pair = (site, hours[residual_index])
            simultaneous = np.minimum(charge, discharge)
            simultaneous_index = int(np.argmax(simultaneous))
            if float(simultaneous[simultaneous_index]) > max_simultaneous:
                max_simultaneous = float(simultaneous[simultaneous_index])
                worst_simultaneous_pair = (site, hours[simultaneous_index])
    return {
        "passed": max(
            max_pipe_excess,
            max_margin_shortfall,
            max_soc_residual,
            max_simultaneous,
        ) <= tolerance_kW,
        "tolerance_kW": tolerance_kW,
        "max_pipe_capacity_excess_kW": max_pipe_excess,
        "worst_pipe_capacity_pair": worst_pipe,
        "max_central_margin_shortfall_kW": max_margin_shortfall,
        "worst_central_margin_hour": worst_margin_hour,
        "max_storage_soc_residual_kWh": max_soc_residual,
        "worst_storage_soc_pair": worst_soc_pair,
        "max_simultaneous_charge_discharge_kW": max_simultaneous,
        "worst_simultaneous_charge_discharge_pair": worst_simultaneous_pair,
        "checked_hour_count": len(d.hours),
        "checked_tree_edge_count": len(design.selected_edge_ids),
        "dominance_witness_count": (
            len(design.central_capacity_hour_witness)
            + sum(len(row) for row in design.capacity_hour_witness_by_edge.values())
        ),
    }


# Explicit alias used by some orchestration prototypes.
build_compact_road_model = build_compact_model


__all__ = [
    "COMPACT_MODEL_VERSION",
    "CompactChain",
    "CompactTreeDesign",
    "audit_compact_solution",
    "build_compact_model",
    "build_compact_road_model",
    "build_compact_tree_design",
    "build_compact_tree_designs",
    "compact_model_metadata",
    "export_compact_solution",
    "materialize_compact_export_views",
]
