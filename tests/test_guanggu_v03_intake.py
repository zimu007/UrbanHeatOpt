from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
import yaml
from shapely.geometry import Polygon

from competition.adapters import adapt_guanggu_v03_sources, validate_canonical_season_data
from competition.canonical import CanonicalSeasonData
from competition.intake import validate_guanggu_v03_delivery


DATA_VERSION = "guanggu-v0.3-test"


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write_profile(tmp_path: Path) -> Path:
    standard = {
        "building_master": {"path": "00_building_master.csv", "format": "csv", "encoding": "utf-8-sig", "expected_rows": 1, "required_columns": ["data_version", "building_id", "conditioned_area_m2", "terminal_type", "terminal_description", "heating_supply_temperature_C", "heating_return_temperature_C", "terminal_parameter_status", "load_included"]},
        "buildings": {"path": "03_buildings.geojson", "format": "geojson", "expected_rows": 1, "required_columns": ["data_version", "building_id", "conditioned_area_m2"], "crs": "EPSG:4326"},
        "building_archetype_map": {"path": "04_building_archetype_map.csv", "format": "csv", "encoding": "utf-8-sig", "expected_rows": 1, "required_columns": ["data_version", "building_id", "target_conditioned_area_m2", "is_mixed_use", "zone_id", "zone_use_type", "zone_area_m2", "zone_archetype_id", "zone_scale_factor"]},
        "building_hourly_loads": {"path": "05_building_hourly_loads.parquet", "format": "parquet", "expected_rows": 4, "required_columns": ["timestamp", "hour", "heating_season_flag", "heating_season_hour", "building_id", "heating_kW", "dhw_included", "data_version"]},
        "equipment_performance": {"path": "06_equipment_performance.csv", "format": "csv", "encoding": "utf-8-sig", "expected_rows": 2, "required_columns": ["data_version", "technology_id", "technology_type", "operating_mode", "Tout", "Tsource", "Tsupply", "Treturn", "PLR", "SOC", "startup_state", "COP", "efficiency", "capacity_ratio", "is_source_point", "is_interpolated", "is_assumption"]},
        "equipment_metadata": {"path": "06A_equipment_metadata.csv", "format": "csv", "encoding": "utf-8-sig", "expected_rows": 2, "required_columns": ["data_version", "technology_id"]},
        "external_timeseries": {"path": "external_timeseries.parquet", "format": "parquet", "expected_rows": 4, "required_columns": ["timestamp", "hour", "heating_season_flag", "heating_season_hour", "outdoor_temperature_C", "electricity_base_price_CNY_per_kWh_e", "electricity_price_multiplier", "electricity_price_CNY_per_kWh_e", "grid_carbon_factor_kgCO2e_per_kWh_e", "natural_gas_price_CNY_per_Nm3", "natural_gas_carbon_factor_kgCO2e_per_Nm3", "time_weight_h", "data_version"]},
        "technologies": {"path": "technologies.csv", "format": "csv", "encoding": "utf-8-sig", "expected_rows": 3, "required_columns": ["data_version", "technology_id", "technology_type", "parameter_name", "recommended_value", "unit", "parameter_status"]},
        "scheme_manifest": {"path": "scheme_input_manifest.csv", "format": "csv", "encoding": "utf-8-sig", "expected_rows": 3, "required_columns": ["data_version", "scheme_id", "building_load_file", "building_load_value_hash_sha256", "external_timeseries_file", "terminal_type", "heating_supply_temperature_C", "heating_return_temperature_C", "building_master_file_hash_sha256", "mapping_file_hash_sha256", "building_load_file_hash_sha256", "external_timeseries_file_hash_sha256", "terminal_parameter_hash_sha256"]},
        "building_ts": {"path": "UrbanHeatOpt/Building_TS.csv", "format": "csv", "encoding": "utf-8-sig", "expected_rows": 4, "required_columns": ["hour"]},
    }
    root_files = [spec["path"] for spec in standard.values() if "/" not in spec["path"]]
    profile = {
        "source_profile": "guanggu_v03",
        "profile_version": "test-v1",
        "data_version": DATA_VERSION,
        "expected_inventory": {"total_files": 13, "extensions": {".csv": 10, ".geojson": 1, ".parquet": 2}},
        "excluded_building_ids": ["excluded"],
        "heating_season": {
            "full_year_hour_start": 0,
            "full_year_hour_count": 4,
            "source_segments": [
                {"start": 2, "end": 3, "season_start": 0},
                {"start": 0, "end": 1, "season_start": 2},
            ],
            "season_hour_count": 4,
            "timezone": "Asia/Shanghai",
        },
        "standard_files": standard,
        "classified_root_files": root_files,
        "classified_directories": {"raw": "dest_provenance", "UrbanHeatOpt": "legacy_compatibility"},
        "provenance_files": [],
        "raw_dest": {
            "root": "raw",
            "expected_files": 3,
            "expected_csv_files": 3,
            "expected_hourly_weather_files": 1,
            "expected_hourly_load_files": 2,
            "hourly_weather_suffix": "逐时气象参数.csv",
            "hourly_load_suffixes": ["建筑逐时单位面积负荷.csv", "建筑逐时负荷.csv"],
            "hour_column": "小时",
            "hour_start": 0,
            "hour_count": 4,
        },
        "technology_rules": {
            "allowed_parameter_status": ["source_based"],
            "natural_gas_lhv_MJ_per_Nm3": 38.931,
            "boiler_efficiency_LHV": 0.94,
            "ashp_curve_max_temperature_C": 15.0,
            "ashp_upper_boundary_policy": "clamp_to_upper_boundary",
        },
    }
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(profile, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _write_delivery(root: Path) -> None:
    pd.DataFrame(
        {
            "data_version": [DATA_VERSION],
            "building_id": ["b1"],
            "conditioned_area_m2": [100.0],
            "terminal_type": ["fan_coil"],
            "terminal_description": ["fan_coil_plus_fresh_air"],
            "heating_supply_temperature_C": [45],
            "heating_return_temperature_C": [40],
            "terminal_parameter_status": ["teacher_confirmed_baseline"],
            "load_included": [True],
        }
    ).to_csv(root / "00_building_master.csv", index=False, encoding="utf-8-sig")
    gpd.GeoDataFrame(
        {
            "data_version": [DATA_VERSION],
            "building_id": ["b1"],
            "conditioned_area_m2": [100.0],
            "use_type": ["office"],
            "terminal_type": ["fan_coil"],
            "terminal_description": ["fan_coil_plus_fresh_air"],
            "heating_supply_temperature_C": [45],
            "heating_return_temperature_C": [40],
            "terminal_parameter_status": ["teacher_confirmed_baseline"],
        },
        geometry=[Polygon([(114, 30), (114.001, 30), (114.001, 30.001), (114, 30.001)])],
        crs="EPSG:4326",
    ).to_file(root / "03_buildings.geojson", driver="GeoJSON")
    pd.DataFrame(
        {
            "data_version": [DATA_VERSION],
            "building_id": ["b1"],
            "target_conditioned_area_m2": [100.0],
            "is_mixed_use": [False],
            "use_type": ["office"],
            "archetype_id": ["a1"],
            "scale_factor": [1.0],
            "zone_id": [None],
            "zone_use_type": [None],
            "zone_area_m2": [None],
            "zone_archetype_id": [None],
            "zone_scale_factor": [None],
            "heating_setpoint_C": [22.0],
        }
    ).to_csv(root / "04_building_archetype_map.csv", index=False, encoding="utf-8-sig")
    timestamps = pd.date_range("2021-01-01", periods=4, freq="h", tz="Asia/Shanghai")
    season_hours = [2, 3, 0, 1]
    loads = pd.DataFrame(
        {
            "timestamp": timestamps,
            "hour": range(4),
            "heating_season_flag": [1] * 4,
            "heating_season_hour": season_hours,
            "building_id": ["b1"] * 4,
            "heating_kW": [1.0, 2.0, 3.0, 4.0],
            "dhw_included": [False] * 4,
            "quality_flag": ["scaled"] * 4,
            "data_version": [DATA_VERSION] * 4,
        }
    )
    loads.to_parquet(root / "05_building_hourly_loads.parquet", index=False)
    external = pd.DataFrame(
        {
            "timestamp": timestamps,
            "hour": range(4),
            "heating_season_flag": [1] * 4,
            "heating_season_hour": season_hours,
            "outdoor_temperature_C": [16.0, 15.0, 14.0, 13.0],
            "electricity_base_price_CNY_per_kWh_e": [0.8] * 4,
            "electricity_price_multiplier": [1.0] * 4,
            "electricity_price_CNY_per_kWh_e": [0.8] * 4,
            "grid_carbon_factor_kgCO2e_per_kWh_e": [0.4] * 4,
            "natural_gas_price_CNY_per_Nm3": [3.8] * 4,
            "natural_gas_carbon_factor_kgCO2e_per_Nm3": [2.184] * 4,
            "time_weight_h": [1.0] * 4,
            "data_version": [DATA_VERSION] * 4,
        }
    )
    external.to_parquet(root / "external_timeseries.parquet", index=False)
    pd.DataFrame(
        {
            "data_version": [DATA_VERSION] * 3,
            "technology_id": ["GAS_BOILER_BASE_01"] * 3,
            "technology_type": ["gas_boiler"] * 3,
            "parameter_name": [
                "natural_gas_LHV",
                "thermal_efficiency_conventional_LHV",
                "thermal_efficiency_condensing_HHV",
            ],
            "recommended_value": [38.931, 0.94, 0.91],
            "unit": ["MJ/Nm3", "fraction", "fraction"],
            "parameter_status": ["source_based"] * 3,
        }
    ).to_csv(root / "technologies.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(
        {
            "data_version": [DATA_VERSION, DATA_VERSION],
            "technology_id": ["ASHP_BASE_01", "GAS_BOILER_BASE_01"],
            "technology_type": ["air_source_heat_pump", "gas_boiler"],
            "operating_mode": ["heating", "heating"],
            "Tout": [15.0, None],
            "Tsource": [None, None],
            "Tsupply": [45.0, None],
            "Treturn": [None, 40.0],
            "PLR": [1.0, 1.0],
            "SOC": [None, None],
            "startup_state": [1, 1],
            "COP": [3.2, None],
            "efficiency": [None, 0.9],
            "capacity_ratio": [1.0, 1.0],
            "is_source_point": [1, 1],
            "is_interpolated": [0, 0],
            "is_assumption": [0, 1],
        }
    ).to_csv(root / "06_equipment_performance.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(
        {"data_version": [DATA_VERSION, DATA_VERSION], "technology_id": ["ASHP_BASE_01", "GAS_BOILER_BASE_01"]}
    ).to_csv(root / "06A_equipment_metadata.csv", index=False, encoding="utf-8-sig")
    legacy = root / "UrbanHeatOpt"
    legacy.mkdir()
    pd.DataFrame({"hour": range(4), "b1": [1.0, 2.0, 3.0, 4.0]}).to_csv(
        legacy / "Building_TS.csv", index=False, encoding="utf-8-sig"
    )
    raw = root / "raw" / "a"
    raw.mkdir(parents=True)
    pd.DataFrame({"小时": range(4), "干球温度(℃)": [1.0, 2.0, 3.0, 4.0]}).to_csv(
        raw / "逐时气象参数.csv", index=False, encoding="utf-8-sig"
    )
    for name in ("建筑逐时单位面积负荷.csv", "建筑逐时负荷.csv"):
        pd.DataFrame({"小时": range(4), "value": [1.0, 2.0, 3.0, 4.0]}).to_csv(
            raw / name, index=False, encoding="utf-8-sig"
        )
    hashes = {
        "building_master_file_hash_sha256": _hash(root / "00_building_master.csv"),
        "mapping_file_hash_sha256": _hash(root / "04_building_archetype_map.csv"),
        "building_load_file_hash_sha256": _hash(root / "05_building_hourly_loads.parquet"),
        "external_timeseries_file_hash_sha256": _hash(root / "external_timeseries.parquet"),
    }
    rows = []
    for scheme in ("centralized", "distributed", "hybrid"):
        rows.append(
            {
                "data_version": DATA_VERSION,
                "scheme_id": scheme,
                "building_load_file": "05_building_hourly_loads.parquet",
                "building_load_value_hash_sha256": "same-value-hash",
                "external_timeseries_file": "external_timeseries.parquet",
                "terminal_type": "fan_coil",
                "heating_supply_temperature_C": 45,
                "heating_return_temperature_C": 40,
                "terminal_parameter_hash_sha256": "same-terminal-hash",
                **hashes,
            }
        )
    pd.DataFrame(rows).to_csv(root / "scheme_input_manifest.csv", index=False, encoding="utf-8-sig")


def test_guanggu_v03_full_audit_recomputes_checks_and_is_read_only(tmp_path: Path) -> None:
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    _write_delivery(delivery)
    profile = _write_profile(tmp_path)
    tracked = sorted(delivery.rglob("*"))
    before = {path: _hash(path) for path in tracked if path.is_file()}

    report = validate_guanggu_v03_delivery(delivery, full_audit=True, profile_path=profile)

    assert report.valid, report.to_dict()
    assert report.inventory["unclassified_files"] == []
    assert report.datasets["raw_dest"]["readable_csv_count"] == 3
    assert report.datasets["building_ts_reconciliation"]["max_abs_error_kW"] == 0
    assert report.datasets["ashp_curve_coverage"]["upper_boundary_clamped_hour_count"] == 1
    assert {issue.code for issue in report.issues} == {
        "GAS_HHV_CURVE_PROVENANCE_ONLY",
        "ASHP_UPPER_BOUNDARY_CLAMP_REQUIRED",
    }
    assert {path: _hash(path) for path in tracked if path.is_file()} == before


def test_guanggu_v03_rejects_wrong_season_mapping_and_excluded_building(tmp_path: Path) -> None:
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    _write_delivery(delivery)
    profile = _write_profile(tmp_path)
    loads = pd.read_parquet(delivery / "05_building_hourly_loads.parquet")
    loads.loc[0, "heating_season_hour"] = 99
    loads.to_parquet(delivery / "05_building_hourly_loads.parquet", index=False)
    master = pd.read_csv(delivery / "00_building_master.csv", encoding="utf-8-sig")
    master.loc[0, "building_id"] = "excluded"
    master.to_csv(delivery / "00_building_master.csv", index=False, encoding="utf-8-sig")

    report = validate_guanggu_v03_delivery(delivery, profile_path=profile)

    codes = {issue.code for issue in report.issues}
    assert not report.valid
    assert "HEATING_SEASON_HOUR_INVALID" in codes
    assert "EXCLUDED_BUILDING_PRESENT" in codes
    assert "SCHEME_FILE_HASH_MISMATCH" in codes


def test_guanggu_v03_full_audit_rejects_unclassified_file(tmp_path: Path) -> None:
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    _write_delivery(delivery)
    profile = _write_profile(tmp_path)
    extra = delivery / "unexpected.bin"
    extra.write_bytes(b"unexpected")

    report = validate_guanggu_v03_delivery(delivery, full_audit=True, profile_path=profile)

    assert not report.valid
    assert "UNCLASSIFIED_FILE" in {issue.code for issue in report.issues}


def test_guanggu_v03_adapter_builds_immutable_canonical_season(tmp_path: Path) -> None:
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    _write_delivery(delivery)
    profile = _write_profile(tmp_path)
    before = {path: _hash(path) for path in delivery.rglob("*") if path.is_file()}

    result = adapt_guanggu_v03_sources(
        delivery,
        tmp_path / "adapted",
        profile_path=profile,
        full_audit=True,
    )

    canonical = result.canonical_data
    assert result.canonical_report.valid
    assert canonical.to_summary()["building_count"] == 1
    assert canonical.to_summary()["hour_count"] == 4
    assert canonical.to_summary()["load_row_count"] == 4
    assert canonical.timestamp_hour_map["source_hour"].tolist() == [2, 3, 0, 1]
    assert canonical.timestamp_hour_map["hour"].tolist() == [1, 2, 3, 4]
    assert canonical.loads["heating_kW"].tolist() == [3.0, 4.0, 1.0, 2.0]
    external = canonical.external_timeseries
    lhv_kwh = 38.931 / 3.6
    assert external["gas_price_CNY_per_kWh_LHV"].iloc[0] == pytest.approx(
        3.8 / lhv_kwh, rel=0, abs=1e-12
    )
    assert external["gas_carbon_kgCO2e_per_kWh_LHV"].iloc[0] == pytest.approx(
        2.184 / lhv_kwh, rel=0, abs=1e-12
    )
    assert external["cop_boundary_clamped"].tolist() == [False, False, True, False]
    assert external.loc[external["cop_boundary_clamped"], "cop_lookup_temperature_C"].tolist() == [15.0]
    assert canonical.adaptation_metadata["solver_executed"] is False
    assert canonical.adaptation_metadata["canonical_validation_passed"] is True
    returned = canonical.loads
    returned.loc[0, "heating_kW"] = 999.0
    assert canonical.loads.loc[0, "heating_kW"] == 3.0
    assert result.loads_path.is_file()
    assert result.timestamp_hour_map_path.is_file()
    assert {path: _hash(path) for path in delivery.rglob("*") if path.is_file()} == before


def test_canonical_season_revalidation_rejects_zero_based_model_hour(tmp_path: Path) -> None:
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    _write_delivery(delivery)
    profile = _write_profile(tmp_path)
    result = adapt_guanggu_v03_sources(
        delivery,
        tmp_path / "adapted",
        profile_path=profile,
    )
    valid = result.canonical_data
    bad_loads = valid.loads
    bad_loads["hour"] = bad_loads["hour"] - 1
    bad = CanonicalSeasonData(
        source_profile=valid.source_profile,
        contract_version=valid.contract_version,
        data_version=valid.data_version,
        _buildings=valid.buildings,
        _building_archetype_map=valid.building_archetype_map,
        _loads=bad_loads,
        _external_timeseries=valid.external_timeseries,
        _technology_parameters=valid.technology_parameters,
        _equipment_performance=valid.equipment_performance,
        _timestamp_hour_map=valid.timestamp_hour_map,
        input_sha256=valid.input_sha256,
        adaptation_metadata=valid.adaptation_metadata,
    )

    report = validate_canonical_season_data(
        bad,
        expected_building_count=1,
        expected_hour_count=4,
    )

    assert not report.valid
    assert "CANONICAL_LOAD_HOUR_COVERAGE_INVALID" in {
        issue.code for issue in report.issues
    }
