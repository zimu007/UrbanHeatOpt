from __future__ import annotations

import csv
from pathlib import Path

import pytest

from urbanheatopt.parameters.v2_freeze_0907 import (
    PATCH_DATA_VERSION, PATCH_FILES, PATCH_ID, read_v2_freeze_0907,
)


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
