from __future__ import annotations

import pandas as pd
import pytest

from urbanheatopt.data.capacity_boundaries import build_capacity_boundaries


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
