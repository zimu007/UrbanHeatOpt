from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

from urbanheatopt.data.adapters.wuhan_v02 import WuhanV02Adaptation
from urbanheatopt.data.adapters.wuhan_v02_case import (
    WuhanV02CaseError,
    prepare_wuhan_v02_v0_case,
    select_v0_smoke_scope,
)
from urbanheatopt.optimization.pipelines import run_wuhan_v02_pipeline


def _loads() -> pd.DataFrame:
    timestamps = pd.date_range("2021-01-01", periods=48, freq="h", tz="Asia/Shanghai")
    rows = []
    for timestamp in timestamps:
        for number in range(1, 10):
            value = float(number)
            if timestamp == timestamps[30]:
                value += 100.0
            rows.append(
                {
                    "timestamp": timestamp,
                    "building_id": f"building_{number:02d}",
                    "heating_kW": value,
                    "data_version": "test-v02",
                }
            )
    return pd.DataFrame(rows)


def test_smoke_scope_uses_top_eight_and_full_park_peak_day() -> None:
    scope = select_v0_smoke_scope(_loads())
    assert scope.building_ids == tuple(f"building_{number:02d}" for number in range(9, 1, -1))
    assert scope.peak_day == "2021-01-02"
    assert len(scope.timestamps) == 24
    assert scope.timestamps[0].hour == 0
    assert scope.timestamps[-1].hour == 23


def _fake_adaptation(root: Path) -> WuhanV02Adaptation:
    root.mkdir(parents=True)
    version = "test-v02"
    buildings = gpd.GeoDataFrame(
        {
            "building_id": [f"building_{number:02d}" for number in range(1, 10)],
            "use_type": ["office"] * 9,
            "heated_area_m2": [1000.0] * 9,
            "terminal_type": ["fan_coil"] * 9,
            "ventilation_system": ["dedicated_fresh_air"] * 9,
            "fresh_air_load_included": [True] * 9,
            "data_version": [version] * 9,
        },
        geometry=[
            Polygon(
                [
                    (114.30 + number * 0.001, 30.50),
                    (114.3002 + number * 0.001, 30.50),
                    (114.3002 + number * 0.001, 30.5002),
                    (114.30 + number * 0.001, 30.5002),
                ]
            )
            for number in range(1, 10)
        ],
        crs="EPSG:4326",
    )
    buildings_path = root / "buildings.geojson"
    buildings.to_file(buildings_path, driver="GeoJSON")
    mapping = pd.DataFrame(
        {
            "building_id": buildings["building_id"],
            "zone_id": buildings["building_id"] + "-01",
            "zone_use_type": "office",
            "zone_area_m2": 1000.0,
            "zone_archetype_id": "office_a",
            "zone_scale_factor": 1.0,
        }
    )
    mapping_path = root / "building_archetype_map.csv"
    mapping.to_csv(mapping_path, index=False, encoding="utf-8")
    loads = _loads()
    loads_path = root / "building_hourly_loads.parquet"
    loads.to_parquet(loads_path, index=False)
    timestamps = tuple(sorted(loads["timestamp"].unique()))
    external_path = root / "external_timeseries.parquet"
    pd.DataFrame(
        {
            "timestamp": timestamps,
            "outdoor_temperature_C": [0.0] * 48,
            "time_weight_h_per_year": [1.0] * 48,
            "electricity_price_CNY_per_kWh_e": [0.48] * 48,
            "gas_price_CNY_per_kWh_LHV": [0.3273483856053017] * 48,
            "electricity_carbon_kgCO2e_per_kWh_e": [0.4044] * 48,
            "gas_carbon_kgCO2e_per_kWh_LHV": [0.199944] * 48,
            "data_version": [version] * 48,
        }
    ).to_parquet(external_path, index=False)
    report = root / "source_validation_report.json"
    adaptation_report = root / "adaptation_report.json"
    mapping_report = root / "field_mapping.csv"
    report.write_text(json.dumps({"valid": True}), encoding="utf-8")
    adaptation_report.write_text(json.dumps({"status": "adapted"}), encoding="utf-8")
    mapping_report.write_text("source_field,canonical_field\na,b\n", encoding="utf-8")
    assumptions = root / "assumptions_used.yaml"
    assumptions.write_text("assumption_profile: provisional_v0\n", encoding="utf-8")
    return WuhanV02Adaptation(
        output_dir=root,
        buildings_path=buildings_path,
        archetype_map_path=mapping_path,
        loads_path=loads_path,
        source_report_path=report,
        adaptation_report_path=adaptation_report,
        field_mapping_path=mapping_report,
        external_timeseries_path=external_path,
        assumptions_path=assumptions,
        candidate_sites_path=root / "unused-sites.geojson",
        candidate_network_path=root / "unused-network.geojson",
        feasible_space_path=root / "unused-space.geojson",
        data_version=version,
        building_count=9,
        hour_count=48,
    )


def test_prepare_case_builds_valid_canonical_snapshot(monkeypatch, tmp_path: Path) -> None:
    adaptation = _fake_adaptation(tmp_path / "adapted")
    monkeypatch.setattr(
        "urbanheatopt.data.adapters.wuhan_v02_case.adapt_wuhan_v02_sources",
        lambda *args, **kwargs: adaptation,
    )
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    prepared = prepare_wuhan_v02_v0_case(delivery, tmp_path / "work")
    case = prepared.canonical_case
    assert len(case.demand_nodes) == 8
    assert len(case.hours) == 24
    assert len(case.segments) == 8
    assert case.raw_config["planning"]["peak_capacity_margin_fraction"] == 0.2
    assert case.raw_config["network"]["supply_temperature_C"] == 45.0
    assert case.raw_config["network"]["return_temperature_C"] == 40.0
    assert case.raw_config["units"]["gas_energy"] == "kWh_LHV"
    assert case.to_core_input("central").peak_capacity_margin_fraction == 0.2
    assert (prepared.case_dir / "assumptions_used.yaml").is_file()


def test_v1_full_refuses_provisional_assumptions(tmp_path: Path) -> None:
    with pytest.raises(WuhanV02CaseError, match="禁止使用V0假设"):
        prepare_wuhan_v02_v0_case(
            tmp_path / "delivery",
            tmp_path / "work",
            profile="v1-full",
        )


def test_wuhan_v02_pipeline_exports_snapshot_pareto_and_qa(
    monkeypatch,
    tmp_path: Path,
) -> None:
    adaptation = _fake_adaptation(tmp_path / "adapted-for-run")
    monkeypatch.setattr(
        "urbanheatopt.data.adapters.wuhan_v02_case.adapt_wuhan_v02_sources",
        lambda *args, **kwargs: adaptation,
    )
    delivery = tmp_path / "delivery-for-run"
    delivery.mkdir()
    result = run_wuhan_v02_pipeline(
        delivery,
        source_profile="wuhan_v02",
        assumption_profile="provisional_v0",
        profile="v0-smoke",
        output_root=tmp_path / "runs",
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    qa_summary = json.loads(
        (result.output_dir / "qa_summary.json").read_text(encoding="utf-8")
    )
    points = pd.read_csv(result.output_dir / "pareto_points.csv")
    snapshot = result.output_dir / "standardized_input_snapshot"
    assert manifest["legacy_model_used"] is False
    assert manifest["result_classification"] == "weighted_period_test"
    assert manifest["source_profile"] == "wuhan_v02"
    assert manifest["capability_status"]["pareto"] == "epsilon_constraint_implemented"
    assert manifest["capability_status"]["candidate_generation"].startswith(
        "provisional_geometric_mst"
    )
    assert qa_summary["all_points_passed"] is True
    assert set(points["mode"]) == {"central", "distributed", "hybrid"}
    assert (result.output_dir / "source_validation_report.json").is_file()
    assert (result.output_dir / "adaptation_report.json").is_file()
    assert (result.output_dir / "assumptions_used.yaml").is_file()
    assert (snapshot / "case_config.yaml").is_file()
    assert (snapshot / "building_hourly_loads.parquet").is_file()
    first_report = json.loads(
        next((result.output_dir / "solutions").glob("*/qa_report.json")).read_text(
            encoding="utf-8"
        )
    )
    assert first_report["peak_capacity_margin_ok"] is True
    assert first_report["storage_counted_in_peak_capacity_margin"] is False
