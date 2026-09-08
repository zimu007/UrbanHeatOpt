from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

import pytest
from pyomo.environ import value
from pyomo.repn import generate_standard_repn

from urbanheatopt.model.compact import build_compact_model, build_compact_tree_designs
from urbanheatopt.model.costing.annualized import capital_recovery_factor
from urbanheatopt.model.reference_core import ThermalStorageSpec
from urbanheatopt.model.road_core import PipeDesign, build_road_model
from urbanheatopt.optimization.solvers import SolverSettings, solve_pyomo_model
from urbanheatopt.parameters.v2_freeze_0907 import (
    PATCH_DATA_VERSION, PATCH_FILES, PATCH_ID, read_v2_freeze_0907,
)
from tests.test_road_v2_core import shared_case


def _csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


@pytest.fixture
def patch(tmp_path):
    root = tmp_path / "0907"
    root.mkdir()
    common = {"data_version": PATCH_DATA_VERSION, "parameter_patch_id": PATCH_ID}
    source_ids = {
        "teacher": "SRC_TEACHER_CONFIRMATION_20260907",
        "debug": "SRC_WORLD_BANK_HEBEI_CLEAN_HEATING_2017",
        "base": "SRC_LINO_TECH_PARK_STATION_2016",
        "high": "SRC_XUCHANG_PRESSURE_ISOLATION_STATION",
        "load": "SRC_GUANGGU_LOAD_V031",
    }
    _csv(root / "parameter_sources_v2.csv", [
        {"source_id": value, "title": key, "used_for": key}
        for key, value in source_ids.items()
    ])
    _csv(root / "scenario_parameter_manifest.csv", [
        {**common, "scenario_id": scenario, "pipe_capacity_file": "pipe_capacity_limits.csv",
         "pipe_types_file": pipe_file, "tes_limits_file": "tes_limits.csv",
         "capacity_margin_rule_file": "capacity_margin_rules.csv",
         "capacity_margin_validation_file": "capacity_margin_validation.csv",
         "station_cost_scenario": station, "allowed_for_primary_economic_conclusion": allowed}
        for scenario, pipe_file, station, allowed in (
            ("v2_debug", "pipe_types_v2_debug.csv", "base", 0),
            ("v2_primary_expansion_check", "pipe_types_v2_expansion_check.csv", "base", 1),
            ("v2_station_high_cost_stress", "pipe_types_v2_expansion_check.csv", "high_cost_stress", 0),
        )
    ])
    ids = ("PIPE_SMALL_PROXY", "PIPE_MEDIUM_PROXY", "PIPE_LARGE_PROXY")
    caps = (39680.345, 79360.69, 158721.38)
    _csv(root / "pipe_capacity_limits.csv", [
        {**common, "pipe_type_id": pid, "capacity_kW_th": cap,
         "capacity_share_of_design_peak": share, "design_peak_kW_th": 158721.38,
         "source_id": source_ids["teacher"], "parameter_status": "teacher_confirmed",
         "code_use_allowed": 1}
        for pid, cap, share in zip(ids, caps, (0.25, 0.5, 1.0), strict=True)
    ])
    for file_name, scenario, costs, losses, allowed, source in (
        ("pipe_types_v2_debug.csv", "v2_debug", (2969, 2984, 5010), (.02, .03, .04), 0, source_ids["debug"]),
        ("pipe_types_v2_expansion_check.csv", "v2_primary_expansion_check", (30060, 55110, 105210), (.24, .44, .84), 1, source_ids["teacher"]),
    ):
        _csv(root / file_name, [
            {**common, "scenario_id": scenario, "pipe_type_id": pid,
             "capacity_kW_th": cap, "route_cost_CNY_per_m": cost,
             "heat_loss_kW_per_m": loss, "source_id": source,
             "parameter_status": "test", "allowed_for_economic_conclusion": allowed}
            for pid, cap, cost, loss in zip(ids, caps, costs, losses, strict=True)
        ])
    _csv(root / "tes_limits.csv", [{
        **common, "technology_id": "SHORT_TERM_STORAGE_BASE_01",
        "energy_capacity_upper_kWh_th": 793606.88,
        "charge_power_upper_kW_th": 132267.81,
        "discharge_power_upper_kW_th": 132267.81,
        "capacity_margin_offset_allowed": 0, "cyclic_boundary": "SOC_end=SOC_start",
        "report_actual_capacity_required": 1, "report_actual_peak_charge_required": 1,
        "report_actual_peak_discharge_required": 1,
        "report_upper_bound_binding_required": 1,
        "source_id": source_ids["teacher"], "parameter_status": "teacher_confirmed",
        "code_use_allowed": 1,
    }])
    _csv(root / "station_cost_scenarios.csv", [
        {**common, "station_cost_scenario": name,
         "station_fixed_capex_CNY_per_site": cost, "use_for_primary_result": primary,
         "replaces_other_station_fixed_cost": 1, "add_with_other_station_fixed_cost": 0,
         "charge_if_station_built": 1, "annualize_with_common_CRF": 1,
         "source_id": source, "parameter_status": "teacher_confirmed"}
        for name, cost, primary, source in (
            ("base", 3_000_000, 1, source_ids["base"]),
            ("high_cost_stress", 18_000_000, 0, source_ids["high"]),
        )
    ])
    _csv(root / "load_peak_check.csv", [{
        **common, "building_count": 62, "simultaneous_peak_kW_th": 132267.81395,
        "qa_status": "verified",
    }])
    _csv(root / "capacity_margin_rules.csv", [
        {**common, "rule_id": "CM001", "constraint_group": "capacity_margin",
         "applies_to_modes": "centralized;hybrid", "time_scope": "each_hour",
         "left_hand_side": "available_central_capacity[t]", "operator": ">=",
         "right_hand_side": "capacity_margin_ratio*connected_building_demand[t]",
         "capacity_margin_ratio": 1.2, "include_network_heat_loss_in_constraint": 0,
         "include_tes_discharge_in_constraint": 0,
         "building_peak_demand_kW_th": 132267.81395,
         "station_total_installed_capacity_upper_kW_th": 158721.37674,
         "code_use_allowed": 1, "parameter_status": "teacher_confirmed",
         "source_id": source_ids["teacher"], "implementation_action": "replace_existing_margin_rhs",
         "notes": "test"},
        {**common, "rule_id": "EB001", "constraint_group": "heat_balance",
         "applies_to_modes": "centralized;hybrid", "time_scope": "each_hour",
         "left_hand_side": "central_heat_output[t]+tes_discharge[t]-tes_charge[t]",
         "operator": "=",
         "right_hand_side": "connected_building_demand[t]+network_heat_loss[t]",
         "capacity_margin_ratio": "", "include_network_heat_loss_in_constraint": 1,
         "include_tes_discharge_in_constraint": 1, "building_peak_demand_kW_th": "",
         "station_total_installed_capacity_upper_kW_th": "", "code_use_allowed": 1,
         "parameter_status": "teacher_confirmed", "source_id": source_ids["teacher"],
         "implementation_action": "keep_existing_heat_balance", "notes": "test"},
        {**common, "rule_id": "CU001", "constraint_group": "station_capacity_upper",
         "applies_to_modes": "centralized;hybrid", "time_scope": "planning",
         "left_hand_side": "central_hp_installed_capacity+central_boiler_installed_capacity",
         "operator": "<=", "right_hand_side": "station_total_installed_capacity_upper_kW_th",
         "capacity_margin_ratio": "", "include_network_heat_loss_in_constraint": 0,
         "include_tes_discharge_in_constraint": 0,
         "building_peak_demand_kW_th": 132267.81395,
         "station_total_installed_capacity_upper_kW_th": 158721.37674,
         "code_use_allowed": 1, "parameter_status": "teacher_confirmed",
         "source_id": source_ids["teacher"], "implementation_action": "keep_existing_station_upper_bound",
         "notes": "test"},
    ])
    _csv(root / "capacity_margin_validation.csv", [
        {"check_id": f"CM_QA_{index:03d}", "check_name": f"check_{index}",
         "applies_to": "test", "required_value_or_rule": "test",
         "pass_criterion": "test", "severity": "required", "code_use_allowed": 1,
         "source_id": source_ids["teacher"], "notes": "test"}
        for index in range(1, 9)
    ])
    _csv(root / "pipe_capacity_engineering_reference.csv", [
        {**common, "pipe_type_id": pid, "dn_mm": dn, "capacity_kW_th": ref,
         "parameter_status": "research_reference", "code_use_allowed": 0,
         "usage": "engineering_reference_only"}
        for pid, dn, ref in zip(ids, (250, 400, 500), (1283.2, 4268.9, 7593.3), strict=True)
    ])
    for name in set(PATCH_FILES) - {path.name for path in root.iterdir()}:
        (root / name).write_bytes(b"synthetic supporting file")
    return root


def test_primary_patch_selects_expansion_cost_base_station_and_tes(patch):
    result = read_v2_freeze_0907(patch, "v2_primary_expansion_check")
    assert result["allowed_for_primary_economic_conclusion"] is True
    assert [row["route_cost_CNY_per_m"] for row in result["selected_pipe_types"]] == [105210, 55110, 30060]
    assert result["station_cost"]["station_fixed_capex_CNY_per_site"] == 3_000_000
    assert result["tes_limits"]["energy_capacity_upper_kWh_th"] == 793606.88
    assert result["engineering_reference_consumed"] is False
    assert result["capacity_margin_rule"]["capacity_margin_ratio"] == 1.2
    assert result["capacity_margin_rule"]["include_network_heat_loss_in_constraint"] is False
    assert result["capacity_margin_rule"]["include_tes_discharge_in_constraint"] is False
    assert len(result["capacity_margin_validation"]) == 8
    assert [row["capacity_kW_th"] for row in result["pipe_capacity_limits"]] == pytest.approx(
        [158721.38, 79360.69, 39680.345]
    )


def test_debug_and_high_station_are_not_primary_and_station_cost_replaces(patch):
    debug = read_v2_freeze_0907(patch, "v2_debug")
    stress = read_v2_freeze_0907(patch, "v2_station_high_cost_stress")
    assert not debug["allowed_for_primary_economic_conclusion"]
    assert debug["program_feasibility_scope"]
    assert stress["station_cost"]["station_fixed_capex_CNY_per_site"] == 18_000_000
    assert stress["station_cost"]["add_with_other_station_fixed_cost"] is False
    assert all(row["allowed_for_economic_conclusion"] for row in stress["selected_pipe_types"])


def test_station_cost_cannot_be_additive(patch):
    rows = list(csv.DictReader((patch / "station_cost_scenarios.csv").open(encoding="utf-8-sig")))
    rows[0]["add_with_other_station_fixed_cost"] = "1"
    _csv(patch / "station_cost_scenarios.csv", rows)
    with pytest.raises(ValueError, match="替换而不得叠加"):
        read_v2_freeze_0907(patch, "v2_primary_expansion_check")


def test_capacity_margin_rule_cannot_include_pipe_loss(patch):
    rows = list(csv.DictReader((patch / "capacity_margin_rules.csv").open(encoding="utf-8-sig")))
    rows[0]["include_network_heat_loss_in_constraint"] = "1"
    _csv(patch / "capacity_margin_rules.csv", rows)
    with pytest.raises(ValueError, match="排除管损/TES"):
        read_v2_freeze_0907(patch, "v2_primary_expansion_check")


def _coefficient(expression, variable):
    repn = generate_standard_repn(expression)
    return next(
        coefficient
        for item, coefficient in zip(repn.linear_vars, repn.linear_coefs, strict=True)
        if item is variable
    )


def _frozen_case(patch, scenario="v2_primary_expansion_check", mode="hybrid"):
    frozen = read_v2_freeze_0907(patch, scenario)
    base = shared_case(mode)
    pipes = tuple(
        PipeDesign(
            row["pipe_type_id"],
            row["capacity_kW_th"],
            row["route_cost_CNY_per_m"],
            30,
            row["heat_loss_kW_per_m"],
            1e-5,
        )
        for row in frozen["selected_pipe_types"]
    )
    tes = frozen["tes_limits"]
    storage = ThermalStorageSpec(
        "tes",
        tes["energy_capacity_upper_kWh_th"],
        tes["charge_power_upper_kW_th"],
        tes["discharge_power_upper_kW_th"],
        .95,
        .95,
        0.0,
        1.0,
        1.0,
        0.0,
        20,
    )
    economics = replace(
        base.common.economics,
        station_fixed_capex_CNY=frozen["station_cost"]["station_fixed_capex_CNY_per_site"],
    )
    return replace(base, common=replace(base.common, economics=economics, storage=storage), pipe_designs=pipes), frozen


def test_primary_frozen_pipe_and_tes_values_are_actual_compact_constraints(patch):
    case, frozen = _frozen_case(patch)
    large_demand = {
        (building, hour): 30_000.0
        for building in case.common.demand_nodes
        for hour in case.common.hours
    }
    large_technologies = tuple(
        replace(technology, capacity_max_kW=500_000.0)
        for technology in case.common.technologies
    )
    case = replace(
        case,
        common=replace(
            case.common,
            heat_demand_kW=large_demand,
            technologies=large_technologies,
        ),
    )
    design = next(item for item in build_compact_tree_designs(case) if item.site_id == "S1")
    model = build_compact_model(case, design, enable_tes=True)
    selected_by_id = {row["pipe_type_id"]: row for row in frozen["selected_pipe_types"]}
    variable_edge = next(iter(design.variable_grade_edge_ids))
    edge_length = next(
        row["length_m"] for row in case.network["edges"] if row["edge_id"] == variable_edge
    )
    for pipe_id, row in selected_by_id.items():
        grade = model._grade_selected[variable_edge, pipe_id]
        assert _coefficient(model.edge_capacity[variable_edge].expr, grade) == pytest.approx(
            row["capacity_kW_th"]
        )
        assert _coefficient(model.edge_loss[variable_edge].expr, grade) == pytest.approx(
            edge_length * row["heat_loss_kW_per_m"]
        )
        assert _coefficient(model.pipe_investment.expr, grade) == pytest.approx(
            edge_length * row["route_cost_CNY_per_m"]
            * capital_recovery_factor(case.common.economics.discount_rate, 30)
        )
    engineering_only = {1283.2, 4268.9, 7593.3}
    assert engineering_only.isdisjoint(
        {pipe.capacity_kW_th for pipe in case.pipe_designs}
    )

    limits = frozen["tes_limits"]
    model._tes_built["S1"].set_value(1)
    for variable, constraint_index, upper in (
        (model._tes_energy["S1"], 2, limits["energy_capacity_upper_kWh_th"]),
        (model._tes_charge_capacity["S1"], 3, limits["charge_power_upper_kW_th"]),
        (model._tes_discharge_capacity["S1"], 4, limits["discharge_power_upper_kW_th"]),
    ):
        variable.set_value(upper + 1.0)
        constraint = model.storage_constraints[constraint_index]
        assert value(constraint.body) > value(constraint.upper)


def test_station_scenarios_replace_and_all_distributed_has_no_station_charge(patch):
    base_case, base = _frozen_case(patch, mode="distributed")
    stress_case, stress = _frozen_case(patch, "v2_station_high_cost_stress", mode="distributed")
    assert base["station_cost"]["station_fixed_capex_CNY_per_site"] == 3_000_000
    assert stress["station_cost"]["station_fixed_capex_CNY_per_site"] == 18_000_000
    assert stress["station_cost"]["add_with_other_station_fixed_cost"] is False
    for case in (base_case, stress_case):
        model = build_road_model(case)
        solve_pyomo_model(model, SolverSettings(mip_gap=0.0))
        assert value(model.station_investment) == pytest.approx(0.0, abs=1e-8)

    hybrid, _ = _frozen_case(patch, mode="hybrid")
    model = build_road_model(hybrid)
    for building in model.DEMAND_NODES:
        model.connected[building].fix(0)
    solve_pyomo_model(model, SolverSettings(mip_gap=0.0))
    assert value(model.station_investment) == pytest.approx(0.0, abs=1e-8)
