from __future__ import annotations

import pandas as pd
import pytest

from urbanheatopt.data.capacity_boundaries import build_capacity_boundaries
from urbanheatopt.optimization.adapters.site_capacity_v1 import (
    consume_capacity_boundary_snapshot,
)


def _supplement():
    ids = ("PIPE_SMALL_PROXY", "PIPE_MEDIUM_PROXY", "PIPE_LARGE_PROXY")
    return {
        "supplement_id": "a" * 64,
        "execution_capacity_policy": {
            "reference_capacity_consumed": False,
            "factors_of_margin_peak": dict(zip(ids, (0.6, 1.0, 1.5), strict=True)),
        },
        "reference_pipe_capacities": [
            {"pipe_type_id": pid, "dn_mm": dn, "reference_capacity_kW_th": cap}
            for pid, dn, cap in zip(ids, (250, 400, 500), (1, 2, 3), strict=True)
        ],
    }


def _loads():
    return pd.DataFrame([
        {"building_id": building, "hour": hour, "heating_kW": load}
        for building, values in {"A": (10, 20), "B": (5, 15)}.items()
        for hour, load in enumerate(values, 1)
    ])


def test_builds_planning_not_dn_capacity_and_all_b2_limits():
    result = build_capacity_boundaries(
        loads=_loads(), site_ids=[f"S{i}" for i in range(5)],
        cop_by_hour={1: 2.0, 2: 4.0},
        capacity_ratio_by_hour={1: 0.5, 2: 1.0},
        supplement=_supplement(), expected_building_count=2, expected_hour_count=2,
    )
    assert result["full_park_peak_kW_th"] == 35
    assert result["design_peak_kW_th"] == 42
    assert [row["capacity_kW_th"] for row in result["pipes"]] == pytest.approx([25.2, 42, 63])
    assert all(row["reference_capacity_consumed"] is False for row in result["pipes"])
    site = result["sites"][0]
    assert site["total_heat_capacity_max_kW_th"] == 42
    assert site["technology_capacity_max_kW_th"] == {"central_hp": 42, "central_boiler": 42}
    assert site["electricity_connection_max_kW_e"] == 21
    assert site["gas_connection_max_kW_LHV"] == pytest.approx(42 / .94)
    assert result["local_hp"]["capacity_max_kW_th_by_building"] == {"A": 24, "B": 18}
    assert result["tes"]["energy_capacity_max_kWh_th"] == 210
    assert result["tes"]["charge_capacity_max_kW_th"] == 35
    assert result["station_cost"]["publication_ready"] is False


def test_rejects_incomplete_hourly_performance():
    with pytest.raises(ValueError, match="完整覆盖"):
        build_capacity_boundaries(
            loads=_loads(), site_ids=[f"S{i}" for i in range(5)],
            cop_by_hour={1: 2.0}, capacity_ratio_by_hour={1: 1.0, 2: 1.0},
            supplement=_supplement(), expected_building_count=2, expected_hour_count=2,
        )


def test_b2_consumer_uses_planning_capacity_and_retains_dn_as_audit_only():
    buildings = {f"B{i:02d}": 10.0 for i in range(62)}
    payload = {
        "schema_version": "capacity_boundaries_1.0.0",
        "building_count": 62,
        "hour_count": 2160,
        "sites": [{
            "site_id": f"S{i}",
            "allowed_technology_ids": ["central_hp", "central_boiler"],
            "total_heat_capacity_max_kW_th": 120.0,
            "technology_capacity_max_kW_th": {
                "central_hp": 120.0, "central_boiler": 120.0,
            },
            "electricity_connection_max_kW_e": 50.0,
            "electricity_connection_scope": "central_hp_only",
            "gas_connection_max_kW_LHV": 130.0,
            "source": "test", "status": "research_assumption",
            "evidence_id": "test-site",
        } for i in range(5)],
        "pipes": [{
            "pipe_type_id": pipe_id,
            "capacity_kW_th": capacity,
            "pipe_design_status": "planning_capacity_tier_not_hydraulic_dn",
            "reference_capacity_kW_th": reference,
            "reference_capacity_consumed": False,
            "source": "test", "status": "research_assumption",
            "evidence_id": "test-pipe",
        } for pipe_id, capacity, reference in zip(
            ("P1", "P2", "P3"), (60.0, 100.0, 150.0), (1.0, 2.0, 3.0), strict=True
        )],
        "local_hp": {
            "capacity_max_kW_th_by_building": buildings,
            "source": "test", "status": "research_assumption",
            "evidence_id": "test-local",
        },
        "tes": {
            "energy_capacity_max_kWh_th": 600.0,
            "charge_capacity_max_kW_th": 100.0,
            "discharge_capacity_max_kW_th": 100.0,
        },
    }
    result = consume_capacity_boundary_snapshot(payload)
    assert [item.capacity_kW_th for item in result.pipes] == [60.0, 100.0, 150.0]
    assert result.sites[0].total_heat_capacity_max_kW_th == 120.0
    assert len(result.local_hp_capacity_max_kW_th_by_building) == 62


def test_b2_consumer_rejects_dn_reference_as_executable_capacity():
    # A compact invalid snapshot reaches the DN-specific guard before projection.
    payload = {
        "schema_version": "capacity_boundaries_1.0.0", "building_count": 62,
        "hour_count": 2160, "sites": [{}] * 5,
        "pipes": [{"reference_capacity_consumed": True}] * 3,
        "local_hp": {"capacity_max_kW_th_by_building": {str(i): 1 for i in range(62)}},
        "tes": {"energy_capacity_max_kWh_th": 1, "charge_capacity_max_kW_th": 1,
                "discharge_capacity_max_kW_th": 1},
    }
    with pytest.raises(ValueError, match="DN物理参考容量"):
        consume_capacity_boundary_snapshot(payload)
