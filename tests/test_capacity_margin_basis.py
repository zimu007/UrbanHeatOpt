"""B-owned reserve-basis contract tests; no production A-schema dependency."""

from dataclasses import replace

import pytest
from pyomo.environ import value

import urbanheatopt.model.compact as compact_module
from urbanheatopt.model.compact import (
    audit_compact_solution,
    build_compact_model,
    build_compact_tree_designs,
)
from urbanheatopt.model.reference_core import (
    CAPACITY_MARGIN_BASIS_BUILDING_USEFUL,
    CAPACITY_MARGIN_BASIS_SOURCE_INCLUDING_LOSS,
    CoreModelInputError,
    SegmentSpec,
    build_core_model,
    capacity_margin_requirement,
    validate_core_input,
)
from urbanheatopt.model.road_core import build_road_model
from urbanheatopt.optimization.solvers import SolverSettings, solve_pyomo_model
from urbanheatopt.qa.road_results import audit_export, export_solution
from tests.test_road_v2_core import shared_case


@pytest.mark.parametrize(
    ("loss", "building_expected", "source_expected"),
    [(0.0, 120.0, 120.0), (10.0, 120.0, 132.0)],
)
def test_two_explicit_capacity_margin_semantics(loss, building_expected, source_expected):
    assert capacity_margin_requirement(
        100.0, loss, 0.2, CAPACITY_MARGIN_BASIS_BUILDING_USEFUL
    ) == pytest.approx(building_expected)
    assert capacity_margin_requirement(
        100.0, loss, 0.2, CAPACITY_MARGIN_BASIS_SOURCE_INCLUDING_LOSS
    ) == pytest.approx(source_expected)


def test_unknown_capacity_margin_basis_fails_closed():
    case = shared_case(capacity_margin_basis="teacher_not_yet_confirmed")
    with pytest.raises(CoreModelInputError, match="capacity_margin_basis"):
        validate_core_input(case.common)


def test_zero_margin_without_basis_skips_reserve_check_consistently(tmp_path):
    base = shared_case()
    case = replace(
        base,
        common=replace(
            base.common,
            peak_capacity_margin_fraction=0.0,
            capacity_margin_basis=None,
        ),
    )
    reference_data = replace(
        case.common,
        site_node="S1",
        candidate_station_nodes=(),
        segments=(
            SegmentSpec("s1_a", "S1", "A", 10.0, 1000.0, 0.0, 20),
            SegmentSpec("a_b", "A", "B", 10.0, 1000.0, 0.0, 20),
        ),
    )
    reference = build_core_model(reference_data)
    assert len(reference.central_peak_capacity_margin) == 0
    road = build_road_model(case)
    road.station_built["S1"].fix(1)
    solve_pyomo_model(road, SolverSettings(mip_gap=0.0))
    design = next(item for item in build_compact_tree_designs(case) if item.site_id == "S1")
    compact = build_compact_model(case, design)
    solve_pyomo_model(compact, SolverSettings(mip_gap=0.0))
    assert audit_compact_solution(case, compact)["max_central_margin_shortfall_kW"] == 0.0
    export_root = tmp_path / "zero-margin"
    export_solution(case, road, export_root)
    assert audit_export(case, export_root)["passed"]


@pytest.mark.parametrize(
    "basis",
    [CAPACITY_MARGIN_BASIS_BUILDING_USEFUL, CAPACITY_MARGIN_BASIS_SOURCE_INCLUDING_LOSS],
)
def test_reference_core_consumes_the_same_explicit_basis(basis):
    data = shared_case(capacity_margin_basis=basis).common
    data = replace(
        data,
        site_node="S1",
        candidate_station_nodes=(),
        segments=(
            SegmentSpec("s1_a", "S1", "A", 10.0, 1000.0, 0.0, 20),
            SegmentSpec("a_b", "A", "B", 10.0, 1000.0, 0.0, 20),
        ),
    )
    model = build_core_model(data)
    for building in model.DEMAND_NODES:
        model.connected[building].set_value(1)
    for station in model.STATIONS:
        model.station_built[station].set_value(int(station == "S1"))
        for technology in model.CENTRAL_TECHNOLOGIES:
            model.central_capacity_by_station_kW[station, technology].set_value(
                120.0 if station == "S1" and technology == "hp" else 0.0
            )
    row = model.central_peak_capacity_margin[1]
    assert value(row.body) <= value(row.upper) + 1e-9


@pytest.mark.parametrize(
    "basis",
    [CAPACITY_MARGIN_BASIS_BUILDING_USEFUL, CAPACITY_MARGIN_BASIS_SOURCE_INCLUDING_LOSS],
)
def test_road_and_compact_use_same_basis_and_tes_does_not_offset_reserve(basis, tmp_path):
    case = shared_case(capacity_margin_basis=basis)
    road = build_road_model(case)
    road.station_built["S1"].fix(1)
    solve_pyomo_model(road, SolverSettings(mip_gap=0.0))

    design = next(item for item in build_compact_tree_designs(case) if item.site_id == "S1")
    compact = build_compact_model(case, design)
    solve_pyomo_model(compact, SolverSettings(mip_gap=0.0))

    useful_peak = max(
        sum(case.common.heat_demand_kW[b, h] for b in case.common.demand_nodes)
        for h in case.common.hours
    )
    road_loss = sum(value(road.edge_loss[e]) for e in road.E)
    compact_loss = value(compact.total_edge_loss)
    expected_road = capacity_margin_requirement(useful_peak, road_loss, 0.2, basis)
    expected_compact = capacity_margin_requirement(useful_peak, compact_loss, 0.2, basis)
    road_capacity = sum(value(road.capacity[s, t]) for s in road.S for t in road.T)
    compact_capacity = sum(value(compact._central_capacity[t]) for t in compact.T)

    assert road_capacity == pytest.approx(expected_road, abs=1e-6)
    assert compact_capacity == pytest.approx(expected_compact, abs=1e-6)
    assert audit_compact_solution(case, compact)["max_central_margin_shortfall_kW"] <= 1e-6
    # Exact equality at the approved reserve requirement proves dispatch/TES
    # variables did not increase or offset installed reserve capacity.
    export_root = tmp_path / basis
    export_solution(case, road, export_root)
    assert audit_export(case, export_root)["passed"]


def test_building_basis_dominance_keeps_a_legal_120_kw_design():
    case = shared_case(capacity_margin_basis=CAPACITY_MARGIN_BASIS_BUILDING_USEFUL)
    design = next(item for item in build_compact_tree_designs(case) if item.site_id == "S1")
    compact = build_compact_model(case, design)
    retained = set(compact._compact_design.central_capacity_hours)
    assert 1 in retained
    assert capacity_margin_requirement(100.0, 10.0, 0.2, case.common.capacity_margin_basis) == 120.0


def test_compact_dominance_model_and_audit_share_one_projection(monkeypatch):
    calls = []
    original = compact_module.capacity_margin_requirement

    def recording_projection(useful, loss, margin, basis):
        calls.append((useful, loss, margin, basis))
        return original(useful, loss, margin, basis)

    monkeypatch.setattr(compact_module, "capacity_margin_requirement", recording_projection)
    case = shared_case(capacity_margin_basis=CAPACITY_MARGIN_BASIS_BUILDING_USEFUL)
    design = next(item for item in build_compact_tree_designs(case) if item.site_id == "S1")
    model = build_compact_model(case, design)
    solve_pyomo_model(model, SolverSettings(mip_gap=0.0))
    audit_compact_solution(case, model)
    assert len(calls) >= 3
    assert {call[3] for call in calls} == {CAPACITY_MARGIN_BASIS_BUILDING_USEFUL}


def test_network_loss_stays_in_heat_balance_for_both_reserve_bases():
    for basis in (
        CAPACITY_MARGIN_BASIS_BUILDING_USEFUL,
        CAPACITY_MARGIN_BASIS_SOURCE_INCLUDING_LOSS,
    ):
        case = shared_case(capacity_margin_basis=basis)
        model = build_road_model(case)
        model.station_built["S1"].fix(1)
        solve_pyomo_model(model, SolverSettings(mip_gap=0.0))
        for hour in model.HOURS:
            source = sum(value(model.heat[s, t, hour]) for s in model.S for t in model.T)
            useful = sum(case.common.heat_demand_kW[b, hour] for b in case.common.demand_nodes)
            loss = sum(value(model.edge_loss[e]) for e in model.E)
            assert source == pytest.approx(useful + loss, abs=1e-6)
