from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import sys

import geopandas as gpd
import pandas as pd
import pytest
import yaml
from shapely.geometry import Polygon

from urbanheatopt.data.adapters import adapt_guanggu_v03_sources, validate_canonical_season_data
from urbanheatopt.data.canonical import CanonicalSeasonData
from urbanheatopt.data.intake import (
    resolve_guanggu_v03_source_roots,
    validate_guanggu_v03_delivery,
)
from urbanheatopt.data.readiness import run_guanggu_v03_input_validation


DATA_VERSION = "guanggu-v0.3-test"


def test_source_resolver_accepts_v02_root_and_rejects_v01(tmp_path: Path) -> None:
    v02 = tmp_path / "v0.2"
    delivery = v02 / "0823代码组交付_光谷软件园_v0.3"
    patch = v02 / "0821设备性能曲线"
    delivery.mkdir(parents=True)
    patch.mkdir()
    (delivery / "00_building_master.csv").write_text("building_id\nb1\n", encoding="utf-8")
    roots = resolve_guanggu_v03_source_roots(v02)
    assert roots.scope_root == v02.resolve()
    assert roots.delivery_root == delivery.resolve()
    assert roots.equipment_patch_root == patch.resolve()

    v01 = tmp_path / "v0.1"
    v01.mkdir()
    with pytest.raises(ValueError, match="v0.1"):
        resolve_guanggu_v03_source_roots(v01)


def _write_lhv_patch_fixture(delivery: Path, patch: Path, profile_path: Path) -> None:
    """Expand the tiny fixture to ASHP + TES + four boiler points and overlay it."""
    base = pd.read_csv(delivery / "06_equipment_performance.csv", encoding="utf-8-sig")
    ashp = base.loc[base["technology_id"].eq("ASHP_BASE_01")].iloc[0].to_dict()
    tes = {column: None for column in base.columns}
    tes.update(
        data_version=DATA_VERSION,
        technology_id="SHORT_TERM_STORAGE_BASE_01",
        technology_type="short_term_thermal_storage",
        operating_mode="storage",
        SOC=0.5,
        startup_state=1,
        is_source_point=0,
        is_interpolated=0,
        is_assumption=1,
    )
    old_gas = base.loc[base["technology_id"].eq("GAS_BOILER_BASE_01")].iloc[0].to_dict()
    old_rows = []
    patch_rows = []
    for plr, old_efficiency in zip((0.25, 0.5, 0.75, 1.0), (0.973, 0.944429, 0.908714, 0.873), strict=True):
        old = dict(old_gas, PLR=plr, efficiency=old_efficiency, COP=None, applicable_range="HHV basis")
        new = dict(
            old,
            efficiency=0.94,
            COP=None,
            applicable_range="fixed efficiency; LHV basis",
            source_id="SRC_PROJECT_LHV_BASELINE_20260823",
            is_source_point=0,
            is_interpolated=0,
            is_assumption=1,
        )
        old_rows.append(old)
        patch_rows.append(new)
    pd.DataFrame([ashp, tes, *old_rows]).to_csv(
        delivery / "06_equipment_performance.csv", index=False, encoding="utf-8-sig"
    )
    metadata = pd.DataFrame(
        {
            "data_version": [DATA_VERSION] * 3,
            "technology_id": ["ASHP_BASE_01", "SHORT_TERM_STORAGE_BASE_01", "GAS_BOILER_BASE_01"],
        }
    )
    metadata.to_csv(delivery / "06A_equipment_metadata.csv", index=False, encoding="utf-8-sig")
    patch.mkdir()
    pd.DataFrame([ashp, tes, *patch_rows]).to_csv(
        patch / "06_equipment_performance.csv", index=False, encoding="utf-8-sig"
    )
    metadata.to_csv(patch / "06A_equipment_metadata.csv", index=False, encoding="utf-8-sig")
    sources = pd.read_csv(delivery / "06B_equipment_sources.csv", encoding="utf-8-sig")
    sources = pd.concat(
        [
            sources,
            pd.DataFrame(
                {
                    "data_version": [DATA_VERSION],
                    "source_id": ["SRC_PROJECT_LHV_BASELINE_20260823"],
                    "technology_id": ["GAS_BOILER_BASE_01"],
                }
            ),
        ],
        ignore_index=True,
    )
    sources.to_csv(patch / "06B_equipment_sources.csv", index=False, encoding="utf-8-sig")
    (patch / "06C_equipment_curve_method.md").write_text("LHV efficiency 0.94", encoding="utf-8")
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    profile["standard_files"]["equipment_performance"]["expected_rows"] = 6
    profile["standard_files"]["equipment_metadata"]["expected_rows"] = 3
    profile_path.write_text(yaml.safe_dump(profile, allow_unicode=True, sort_keys=False), encoding="utf-8")


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
        "equipment_sources": {"path": "06B_equipment_sources.csv", "format": "csv", "encoding": "utf-8-sig", "expected_rows": 16, "required_columns": ["data_version", "source_id", "technology_id"]},
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
        "expected_inventory": {"total_files": 14, "extensions": {".csv": 11, ".geojson": 1, ".parquet": 2}},
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
    pd.DataFrame(
        {
            "data_version": [DATA_VERSION] * 16,
            "source_id": [f"SRC_BASE_{index:02d}" for index in range(16)],
            "technology_id": ["ASHP_BASE_01"] * 16,
        }
    ).to_csv(root / "06B_equipment_sources.csv", index=False, encoding="utf-8-sig")
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


def test_string_false_is_not_misread_as_true(tmp_path: Path) -> None:
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    _write_delivery(delivery)
    profile = _write_profile(tmp_path)
    loads_path = delivery / "05_building_hourly_loads.parquet"
    loads = pd.read_parquet(loads_path)
    loads["dhw_included"] = "False"
    loads.to_parquet(loads_path, index=False)
    manifest_path = delivery / "scheme_input_manifest.csv"
    manifest = pd.read_csv(manifest_path, encoding="utf-8-sig")
    manifest["building_load_file_hash_sha256"] = _hash(loads_path)
    manifest.to_csv(manifest_path, index=False, encoding="utf-8-sig")

    report = validate_guanggu_v03_delivery(delivery, profile_path=profile)
    assert report.valid, report.to_dict()
    assert "DHW_BOUNDARY_INVALID" not in {issue.code for issue in report.issues}


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


def test_guanggu_v03_equipment_patch_reaches_adapter_and_preserves_other_technologies(
    tmp_path: Path,
) -> None:
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    _write_delivery(delivery)
    profile = _write_profile(tmp_path)
    patch = tmp_path / "equipment_patch"
    _write_lhv_patch_fixture(delivery, patch, profile)

    base_result = adapt_guanggu_v03_sources(
        delivery, tmp_path / "base_adapted", profile_path=profile, full_audit=False
    )
    patched_result = adapt_guanggu_v03_sources(
        delivery,
        tmp_path / "patched_adapted",
        profile_path=profile,
        full_audit=False,
        equipment_patch_root=patch,
    )
    base = base_result.canonical_data.equipment_performance
    effective = patched_result.canonical_data.equipment_performance
    gas = effective.loc[effective["technology_id"].eq("GAS_BOILER_BASE_01")]
    assert len(gas) == 4
    assert gas["efficiency"].eq(0.94).all()
    assert gas["COP"].isna().all()
    assert gas["energy_basis"].eq("LHV").all()
    assert gas["executable_in_lhv_core"].all()
    for technology_id in ("ASHP_BASE_01", "SHORT_TERM_STORAGE_BASE_01"):
        columns = [column for column in base.columns if column not in {"energy_basis", "executable_in_lhv_core"}]
        pd.testing.assert_frame_equal(
            base.loc[base["technology_id"].eq(technology_id), columns].reset_index(drop=True),
            effective.loc[effective["technology_id"].eq(technology_id), columns].reset_index(drop=True),
            check_dtype=False,
        )
    ashp = effective.loc[effective["technology_id"].eq("ASHP_BASE_01")]
    assert ashp["capacity_ratio"].eq(1.0).all()
    provenance = patched_result.canonical_data.adaptation_metadata
    assert base_result.source_report.valid
    assert base_result.source_report.datasets["equipment_sources"]["row_count"] == 16
    assert patched_result.source_report.valid
    assert patched_result.source_report.datasets["equipment_sources"]["row_count"] == 17
    assert provenance["base_data_version"] == DATA_VERSION
    assert provenance["equipment_patch_applied"] is True
    assert provenance["equipment_patch_identifier"] == patch.name
    assert set(provenance["equipment_patch_files_sha256"]) == {
        "06_equipment_performance.csv",
        "06A_equipment_metadata.csv",
        "06B_equipment_sources.csv",
        "06C_equipment_curve_method.md",
    }
    assert all(len(digest) == 64 for digest in provenance["equipment_patch_files_sha256"].values())
    assert patched_result.source_report.datasets["equipment_performance"]["path"] == "06_equipment_performance.csv"
    assert patched_result.source_report.datasets["equipment_performance"]["effective_source_path"] == str(
        patch / "06_equipment_performance.csv"
    )
    assert base_result.canonical_data.adaptation_metadata["equipment_patch_applied"] is False
    assert base_result.source_report.datasets["equipment_performance"]["path"] == "06_equipment_performance.csv"


def test_guanggu_v03_equipment_patch_requires_all_four_files(tmp_path: Path) -> None:
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    _write_delivery(delivery)
    profile = _write_profile(tmp_path)
    patch = tmp_path / "equipment_patch"
    patch.mkdir()
    for name in (
        "06_equipment_performance.csv",
        "06A_equipment_metadata.csv",
        "06B_equipment_sources.csv",
    ):
        (patch / name).write_text("placeholder", encoding="utf-8")
    with pytest.raises(ValueError, match="06C_equipment_curve_method.md"):
        validate_guanggu_v03_delivery(
            delivery,
            profile_path=profile,
            equipment_patch_root=patch,
        )


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("remove_lhv_source", "EQUIPMENT_PATCH_LHV_SOURCE_INVALID"),
        ("duplicate_source_id", "EQUIPMENT_PATCH_SOURCE_ID_DUPLICATE"),
    ],
)
def test_guanggu_v03_equipment_patch_rejects_invalid_source_registry(
    tmp_path: Path, mutation: str, expected_code: str
) -> None:
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    _write_delivery(delivery)
    profile = _write_profile(tmp_path)
    patch = tmp_path / "equipment_patch"
    _write_lhv_patch_fixture(delivery, patch, profile)
    sources_path = patch / "06B_equipment_sources.csv"
    sources = pd.read_csv(sources_path, encoding="utf-8-sig")
    if mutation == "remove_lhv_source":
        sources = sources.loc[
            ~sources["source_id"].eq("SRC_PROJECT_LHV_BASELINE_20260823")
        ]
    else:
        sources.loc[sources.index[-1], "source_id"] = sources.loc[sources.index[0], "source_id"]
    sources.to_csv(sources_path, index=False, encoding="utf-8-sig")

    report = validate_guanggu_v03_delivery(
        delivery,
        profile_path=profile,
        equipment_patch_root=patch,
    )
    assert not report.valid
    assert expected_code in {issue.code for issue in report.issues}


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


def test_guanggu_v03_readiness_report_is_generated_from_machine_results(
    tmp_path: Path,
) -> None:
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    _write_delivery(delivery)
    profile = _write_profile(tmp_path)
    guidance = tmp_path / "光谷v0.3输入校验与模型就绪状态.md"

    run = run_guanggu_v03_input_validation(
        delivery,
        tmp_path / "audit",
        guidance,
        profile_path=profile,
        workspace_root=tmp_path,
    )

    assert run.readiness.source_validation_passed is True
    assert run.readiness.canonical_validation_passed is True
    assert run.readiness.model_ready is False
    assert run.readiness.solver_executed is False
    assert {item.item_id for item in run.readiness.blockers} >= {
        "road_candidate_network",
        "pipe_types",
        "full_season_solve_qa",
    }
    statuses = {item.item_id: item.status for item in run.readiness.items}
    assert statuses["ashp_curve_coverage"] == "ready"
    assert statuses["capacity_margin_wiring"] == "ready"
    assert statuses["v03_case_builder"] == "provisional"
    machine = run.readiness_report_path.read_text(encoding="utf-8")
    rendered = guidance.read_text(encoding="utf-8")
    assert '"model_ready": false' in machine
    assert '"solver_executed": false' in machine
    assert "source_validation_passed | `true`" in rendered
    assert "canonical_validation_passed | `true`" in rendered
    assert "model_ready | `false`" in rendered
    assert "solver_executed | `false`" in rendered
    assert "在 VS Code 中复现" in rendered
    assert "全部源文件分类、读取状态与SHA-256" in rendered
    assert "roads_or_feasible_space.geojson" in rendered
    assert "未调用旧模型或求解器" in rendered
    assert "15℃ COP 边界封顶小时" in rendered
    assert "ASHP_UPPER_BOUNDARY_CLAMP_REQUIRED" in rendered


def test_guanggu_v03_run_case_stops_before_any_pipeline_when_not_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.legacy_cli.run_case as command

    blocker = SimpleNamespace(
        item_id="v03_case_builder",
        title="v0.3连接层",
        reason="尚未接通",
    )
    fake_gate = SimpleNamespace(
        readiness=SimpleNamespace(model_ready=False, blockers=(blocker,)),
        guidance_report_path=tmp_path / "report.md",
    )
    calls = {"gate": 0}

    def fake_gate_runner(*args: object, **kwargs: object) -> object:
        calls["gate"] += 1
        return fake_gate

    def forbidden_pipeline(*args: object, **kwargs: object) -> object:
        raise AssertionError("模型未就绪时不得实例化求解器或调用任何新旧求解Pipeline")

    monkeypatch.setattr(command, "run_guanggu_v03_input_validation", fake_gate_runner)
    monkeypatch.setattr(command, "run_wuhan_v02_pipeline", forbidden_pipeline)
    monkeypatch.setattr(command, "run_case_pipeline", forbidden_pipeline)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_case.py",
            "--delivery-root",
            str(tmp_path / "delivery"),
            "--source-profile",
            "guanggu_v03",
            "--profile",
            "v1-full",
            "--output-root",
            str(tmp_path / "runs"),
        ],
    )

    assert command.main() == 2
    assert calls == {"gate": 1}


def test_guanggu_v03_validate_command_reports_input_success_independently_of_model_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.legacy_cli.validate_inputs as command

    fake_run = SimpleNamespace(
        to_dict=lambda: {
            "source_validation_passed": True,
            "canonical_validation_passed": True,
            "model_ready": False,
            "solver_executed": False,
        }
    )
    calls = {"validation": 0}

    def fake_validation(*args: object, **kwargs: object) -> object:
        calls["validation"] += 1
        return fake_run

    monkeypatch.setattr(command, "run_guanggu_v03_input_validation", fake_validation)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_inputs.py",
            "--delivery-root",
            str(tmp_path / "delivery"),
            "--source-profile",
            "guanggu_v03",
            "--scope",
            "heating-season",
            "--full-audit",
            "--output-root",
            str(tmp_path / "audit"),
            "--guidance-report",
            str(tmp_path / "report.md"),
        ],
    )

    assert command.main() == 0
    assert calls == {"validation": 1}


def test_guanggu_v03_missing_fields_are_aggregated_without_key_error(
    tmp_path: Path,
) -> None:
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    _write_delivery(delivery)
    profile = _write_profile(tmp_path)
    technologies = pd.read_csv(delivery / "technologies.csv", encoding="utf-8-sig")
    technologies = technologies.drop(columns=["parameter_name"])
    technologies.to_csv(delivery / "technologies.csv", index=False, encoding="utf-8-sig")
    performance = pd.read_csv(
        delivery / "06_equipment_performance.csv", encoding="utf-8-sig"
    ).drop(columns=["technology_type"])
    performance.to_csv(
        delivery / "06_equipment_performance.csv", index=False, encoding="utf-8-sig"
    )
    mapping = pd.read_csv(
        delivery / "04_building_archetype_map.csv", encoding="utf-8-sig"
    ).drop(columns=["target_conditioned_area_m2"])
    mapping.to_csv(
        delivery / "04_building_archetype_map.csv", index=False, encoding="utf-8-sig"
    )

    report = validate_guanggu_v03_delivery(delivery, profile_path=profile)

    assert not report.valid
    issues = [issue for issue in report.issues if issue.code == "FIELD_MISSING"]
    assert len(issues) == 3


def test_guanggu_v03_rejects_duplicate_load_negative_value_and_time_mismatch(
    tmp_path: Path,
) -> None:
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    _write_delivery(delivery)
    profile = _write_profile(tmp_path)
    loads = pd.read_parquet(delivery / "05_building_hourly_loads.parquet")
    loads.loc[1, "hour"] = loads.loc[0, "hour"]
    loads.loc[2, "heating_kW"] = -1.0
    loads.to_parquet(delivery / "05_building_hourly_loads.parquet", index=False)
    external = pd.read_parquet(delivery / "external_timeseries.parquet")
    external.loc[0, "timestamp"] = external.loc[0, "timestamp"] + pd.Timedelta(hours=1)
    external.to_parquet(delivery / "external_timeseries.parquet", index=False)

    report = validate_guanggu_v03_delivery(delivery, profile_path=profile)

    codes = {issue.code for issue in report.issues}
    assert {
        "LOAD_KEY_DUPLICATE",
        "NEGATIVE_VALUE",
        "FULL_YEAR_HOUR_INVALID",
        "LOAD_EXTERNAL_TIME_MISMATCH",
    } <= codes


def test_guanggu_v03_rejects_ambiguous_gas_authority_and_wrong_data_version(
    tmp_path: Path,
) -> None:
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    _write_delivery(delivery)
    profile = _write_profile(tmp_path)
    external = pd.read_parquet(delivery / "external_timeseries.parquet")
    external["gas_price_CNY_per_kWh_LHV"] = 0.35
    external["data_version"] = "wrong-version"
    external.to_parquet(delivery / "external_timeseries.parquet", index=False)

    report = validate_guanggu_v03_delivery(delivery, profile_path=profile)

    codes = {issue.code for issue in report.issues}
    assert "GAS_AUTHORITY_AMBIGUOUS" in codes
    assert "DATA_VERSION_MISMATCH" in codes


def test_guanggu_v03_hhv_curve_remains_non_executable_after_adaptation(
    tmp_path: Path,
) -> None:
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    _write_delivery(delivery)
    profile = _write_profile(tmp_path)

    result = adapt_guanggu_v03_sources(
        delivery,
        tmp_path / "adapted",
        profile_path=profile,
    )

    gas_curve = result.canonical_data.equipment_performance.loc[
        result.canonical_data.equipment_performance["technology_type"].eq("gas_boiler")
    ]
    assert len(gas_curve) == 1
    assert gas_curve["energy_basis"].tolist() == ["HHV_provenance_only"]
    assert gas_curve["executable_in_lhv_core"].tolist() == [False]


@pytest.mark.parametrize(
    ("fault", "expected_codes"),
    [
        ("missing_file", {"FILE_MISSING", "INVENTORY_COUNT_MISMATCH"}),
        ("wrong_encoding", {"FILE_READ_ERROR"}),
        ("duplicate_building", {"BUILDING_ID_INVALID", "ROW_COUNT_MISMATCH"}),
        ("nan_load", {"NUMERIC_VALUE_INVALID"}),
        ("naive_timestamp", {"TIMESTAMP_TIMEZONE_MISSING"}),
        ("mixed_area", {"ZONE_AREA_MISMATCH"}),
    ],
)
def test_guanggu_v03_common_source_faults_return_contract_issues(
    tmp_path: Path,
    fault: str,
    expected_codes: set[str],
) -> None:
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    _write_delivery(delivery)
    profile = _write_profile(tmp_path)

    if fault == "missing_file":
        (delivery / "00_building_master.csv").unlink()
    elif fault == "wrong_encoding":
        (delivery / "technologies.csv").write_bytes(b"\xff\xff\xff")
    elif fault == "duplicate_building":
        master = pd.read_csv(delivery / "00_building_master.csv", encoding="utf-8-sig")
        pd.concat([master, master], ignore_index=True).to_csv(
            delivery / "00_building_master.csv", index=False, encoding="utf-8-sig"
        )
    elif fault == "nan_load":
        loads = pd.read_parquet(delivery / "05_building_hourly_loads.parquet")
        loads.loc[0, "heating_kW"] = float("nan")
        loads.to_parquet(delivery / "05_building_hourly_loads.parquet", index=False)
    elif fault == "naive_timestamp":
        external = pd.read_parquet(delivery / "external_timeseries.parquet")
        external["timestamp"] = external["timestamp"].dt.tz_localize(None)
        external.to_parquet(delivery / "external_timeseries.parquet", index=False)
    elif fault == "mixed_area":
        mapping = pd.read_csv(
            delivery / "04_building_archetype_map.csv", encoding="utf-8-sig"
        )
        for column in ("zone_id", "zone_use_type", "zone_archetype_id"):
            mapping[column] = mapping[column].astype("object")
        mapping.loc[0, "is_mixed_use"] = True
        mapping.loc[0, "zone_id"] = "zone_1"
        mapping.loc[0, "zone_use_type"] = "office"
        mapping.loc[0, "zone_archetype_id"] = "a1"
        mapping.loc[0, "zone_scale_factor"] = 1.0
        mapping.loc[0, "zone_area_m2"] = 99.0
        mapping.to_csv(
            delivery / "04_building_archetype_map.csv", index=False, encoding="utf-8-sig"
        )
    else:  # pragma: no cover - the parametrization is the exhaustive branch list
        raise AssertionError(f"未知测试故障: {fault}")

    report = validate_guanggu_v03_delivery(delivery, profile_path=profile)

    assert not report.valid
    assert expected_codes <= {issue.code for issue in report.issues}
