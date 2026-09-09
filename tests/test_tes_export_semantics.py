"""TES export semantics must distinguish solver binaries from physical use."""
from __future__ import annotations

import pandas as pd
import pytest

from urbanheatopt.data.bundles import INTERFACE_VERSION, SolveRequest
from urbanheatopt.model.compact import (
    build_compact_model,
    build_compact_tree_design,
    export_compact_solution,
)
from urbanheatopt.optimization.solvers import solve_pyomo_model
from urbanheatopt.optimization.solve_executor import _effective_case_for_request
from urbanheatopt.qa.tes_semantics import (
    TES_RESULT_TOLERANCE,
    activity_flag,
    classify_tes_semantics,
    clean_near_zero,
)
from tests.test_road_v2_compact_tes import _tes_case


def test_zero_capacity_solver_binary_is_not_reported_as_installed():
    semantics = classify_tes_semantics(
        solver_built_binary=1.0,
        energy_capacity_kWh_th=-0.0,
        charge_capacity_kW_th=-0.0,
        discharge_capacity_kW_th=0.0,
        power_cost_capacity_kW_th=0.0,
        actual_peak_charge_kW_th=1e-12,
        actual_peak_discharge_kW_th=0.0,
    )

    assert semantics.solver_built_binary == 1.0
    assert semantics.tes_installed is False
    assert semantics.tes_used is False
    assert semantics.semantic_built == 0


def test_positive_capacity_and_hourly_activity_have_distinct_semantics():
    semantics = classify_tes_semantics(
        solver_built_binary=0.999999999,
        energy_capacity_kWh_th=10.0,
        charge_capacity_kW_th=2.0,
        discharge_capacity_kW_th=2.0,
        power_cost_capacity_kW_th=2.0,
        actual_peak_charge_kW_th=1.5,
        actual_peak_discharge_kW_th=1.0,
    )

    assert semantics.tes_installed is True
    assert semantics.tes_used is True
    assert semantics.semantic_built == 1
    assert clean_near_zero(-1e-12) == 0.0
    assert not activity_flag(TES_RESULT_TOLERANCE)
    assert activity_flag(TES_RESULT_TOLERANCE * 1.01)


def test_compact_export_includes_physical_and_solver_tes_fields(tmp_path):
    case = _tes_case()
    design = build_compact_tree_design(case, "S1")
    model = build_compact_model(case, design, enable_tes=True)
    solve_pyomo_model(model)

    result_root = tmp_path / "result"
    qa = export_compact_solution(case, model, result_root)
    assert qa["passed"] is True

    decisions = pd.read_csv(result_root / "storage_decisions.csv")
    assert {
        "built",
        "solver_built_binary",
        "tes_installed",
        "tes_used",
        "result_activity_tolerance",
    } <= set(decisions)
    active = decisions.loc[decisions["site_id"].eq("S1")].iloc[0]
    assert active["solver_built_binary"] > 0.5
    assert bool(active["tes_installed"])
    assert bool(active["tes_used"])
    assert active["built"] == 1

    hourly = pd.read_parquet(result_root / "storage_hourly.parquet")
    assert {
        "charging",
        "is_charging",
        "is_discharging",
        "result_activity_tolerance",
    } <= set(hourly)
    expected_charge = hourly["charge_kW"].gt(TES_RESULT_TOLERANCE)
    expected_discharge = hourly["discharge_kW"].gt(TES_RESULT_TOLERANCE)
    assert hourly["is_charging"].astype(bool).equals(expected_charge)
    assert hourly["is_discharging"].astype(bool).equals(expected_discharge)
    assert expected_charge.any()
    assert expected_discharge.any()


def _sensitivity_request(*, tes_enabled=True, multiplier=0.5):
    return {
        "interface_version": INTERFACE_VERSION,
        "case_bundle_id": "0" * 64,
        "model_profile": "compact_five_tree_fullseason_v2",
        "optimization_scope": "five_candidate_shortest_path_trees",
        "mode": "central",
        "objective": "cost",
        "epsilon_carbon_kg": None,
        "tes_enabled": tes_enabled,
        "allow_unserved": False,
        "solver": {
            "name": "highs", "threads": 4, "candidate_workers": 1,
            "random_seed": 202611, "mip_gap": 0.01,
            "time_limit_s": None, "presolve": "on",
        },
        "sensitivity": {
            "scenario_id": "tes_capex_x05",
            "tes_capex_multiplier": multiplier,
        },
    }


def test_tes_cost_sensitivity_changes_only_explicit_investment_fields():
    case = _tes_case()
    payload = SolveRequest.from_dict(_sensitivity_request()).to_dict()
    effective = _effective_case_for_request(case, payload)

    original = case.common.storage
    changed = effective.common.storage
    assert changed.capex_CNY_per_kWh_th == original.capex_CNY_per_kWh_th * 0.5
    assert changed.power_capex_CNY_per_kW_th == original.power_capex_CNY_per_kW_th * 0.5
    assert changed.fixed_capex_CNY == original.fixed_capex_CNY * 0.5
    assert changed.charge_efficiency == original.charge_efficiency
    assert changed.standing_loss_fraction_per_hour == original.standing_loss_fraction_per_hour
    assert case.common.storage == original
    assert "tes_capex_multiplier=0.5" in effective.parameter_version


@pytest.mark.parametrize(("tes_enabled", "multiplier"), [(False, 0.5), (True, 0.0), (True, 1.1)])
def test_invalid_tes_cost_sensitivity_fails_at_request_boundary(tes_enabled, multiplier):
    with pytest.raises(ValueError):
        SolveRequest.from_dict(
            _sensitivity_request(tes_enabled=tes_enabled, multiplier=multiplier)
        )
