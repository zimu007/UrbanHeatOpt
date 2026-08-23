from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import geopandas as gpd
import pandas as pd
import yaml
from shapely.geometry import Polygon

from competition.adapters.wuhan_v02 import _normalize_archetype_map, adapt_wuhan_v02_sources
from competition.intake import validate_wuhan_v02_delivery


def _write_profile(tmp_path: Path) -> Path:
    profile = {
        "source_profile": "wuhan_v02",
        "profile_version": "test-profile",
        "data_version": "test-v0.2",
        "expected_inventory": {
            "total_files": 10,
            "extensions": {".csv": 7, ".geojson": 1, ".parquet": 2},
        },
        "standard_files": {
            "archetypes": {"path": "01.csv", "format": "csv", "encoding": "gb18030", "expected_rows": 1, "required_columns": ["archetype_id", "use_type", "conditioned_area_m2"]},
            "special_models": {"path": "01B.csv", "format": "csv", "encoding": "utf-8-sig", "expected_rows": 1, "required_columns": ["special_model_id", "building_id", "parent_archetype_id"]},
            "archetype_hourly_loads": {"path": "02.parquet", "format": "parquet", "expected_rows": 2, "required_columns": ["timestamp", "hour", "archetype_id", "heating_kW", "cooling_kW"]},
            "buildings": {"path": "03.geojson", "format": "geojson", "expected_rows": 1, "required_columns": ["building_id", "building_name", "conditioned_area_m2", "use_type"], "crs": "EPSG:4326"},
            "building_archetype_map": {"path": "04.csv", "format": "csv", "encoding": "utf-8-sig", "expected_rows": 1, "required_columns": ["building_id", "zone_id", "zone_use_type", "zone_area_m2", "zone_archetype_id", "zone_scale_factor"]},
            "building_hourly_loads": {"path": "05.parquet", "format": "parquet", "expected_rows": 2, "required_columns": ["timestamp", "hour", "building_id", "heating_kW", "cooling_kW"]},
            "source_log": {"path": "09.csv", "format": "csv", "encoding": "utf-8-sig", "expected_rows": 1, "required_columns": ["dataset", "source_file", "processing_step", "output_file"]},
        },
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
            "hour_count": 2,
        },
    }
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(profile, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _write_delivery(tmp_path: Path) -> None:
    pd.DataFrame({"archetype_id": ["a1"], "use_type": ["office"], "conditioned_area_m2": [10]}).to_csv(tmp_path / "01.csv", index=False, encoding="gb18030")
    pd.DataFrame({"special_model_id": ["s1"], "building_id": ["b1"], "parent_archetype_id": ["a1"]}).to_csv(tmp_path / "01B.csv", index=False, encoding="utf-8-sig")
    timestamps = pd.date_range("2026-01-01", periods=2, freq="h", tz="Asia/Shanghai")
    pd.DataFrame({"timestamp": timestamps, "hour": [0, 1], "archetype_id": ["a1", "a1"], "heating_kW": [1.0, 2.0], "cooling_kW": [0.0, 0.0]}).to_parquet(tmp_path / "02.parquet", index=False)
    gpd.GeoDataFrame({"building_id": ["b1"], "building_name": ["B1"], "conditioned_area_m2": [10.0], "use_type": ["office"]}, geometry=[Polygon([(114, 30), (114.001, 30), (114.001, 30.001), (114, 30.001)])], crs="EPSG:4326").to_file(tmp_path / "03.geojson", driver="GeoJSON")
    pd.DataFrame({"building_id": ["b1"], "use_type": ["office"], "is_mixed_use": [False], "archetype_id": ["a1"], "target_conditioned_area_m2": [10.0], "scale_factor": [1.0], "zone_id": [None], "zone_use_type": [None], "zone_area_m2": [None], "zone_archetype_id": [None], "zone_scale_factor": [None]}).to_csv(tmp_path / "04.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame({"timestamp": timestamps, "hour": [0, 1], "building_id": ["b1", "b1"], "heating_kW": [1.0, 2.0], "cooling_kW": [0.0, 0.0]}).to_parquet(tmp_path / "05.parquet", index=False)
    pd.DataFrame({"dataset": ["loads"], "source_file": ["raw"], "processing_step": ["test"], "output_file": ["05.parquet"]}).to_csv(tmp_path / "09.csv", index=False, encoding="utf-8-sig")
    raw = tmp_path / "raw" / "a"
    raw.mkdir(parents=True)
    for name in ("逐时气象参数.csv", "建筑逐时单位面积负荷.csv", "建筑逐时负荷.csv"):
        pd.DataFrame({"小时": [0, 1], "value": [1.0, 2.0]}).to_csv(raw / name, index=False, encoding="utf-8-sig")


def test_wuhan_v02_profile_full_audit_is_read_only(tmp_path: Path) -> None:
    _write_delivery(tmp_path)
    profile = _write_profile(tmp_path)
    tracked = sorted(path for path in tmp_path.rglob("*") if path.is_file() and path != profile)
    before = {path: sha256(path.read_bytes()).hexdigest() for path in tracked}
    report = validate_wuhan_v02_delivery(tmp_path, full_audit=True, profile_path=profile)
    assert report.valid, report.to_dict()
    assert report.datasets["raw_dest"]["readable_csv_count"] == 3
    assert report.datasets["raw_dest"]["hourly_weather_count"] == 1
    assert report.datasets["raw_dest"]["hourly_load_count"] == 2
    assert {path: sha256(path.read_bytes()).hexdigest() for path in tracked} == before


def test_wuhan_v02_profile_rejects_bad_hour_and_id_set(tmp_path: Path) -> None:
    _write_delivery(tmp_path)
    profile = _write_profile(tmp_path)
    loads = pd.read_parquet(tmp_path / "05.parquet")
    loads.loc[1, "building_id"] = "b2"
    loads.to_parquet(tmp_path / "05.parquet", index=False)
    raw_load = tmp_path / "raw" / "a" / "建筑逐时负荷.csv"
    pd.DataFrame({"小时": [0, 2], "value": [1.0, 2.0]}).to_csv(raw_load, index=False, encoding="utf-8-sig")
    report = validate_wuhan_v02_delivery(tmp_path, full_audit=True, profile_path=profile)
    codes = {issue.code for issue in report.issues}
    assert not report.valid
    assert "BUILDING_ID_SET_MISMATCH" in codes
    assert "HOUR_INDEX_INVALID" in codes


def test_wuhan_v02_adapter_writes_canonical_sources_without_mutating_delivery(tmp_path: Path) -> None:
    source = tmp_path / "delivery"
    source.mkdir()
    _write_delivery(source)
    profile = _write_profile(tmp_path)
    before = {path: sha256(path.read_bytes()).hexdigest() for path in source.rglob("*") if path.is_file()}
    result = adapt_wuhan_v02_sources(source, tmp_path / "adapted", profile_path=profile)
    buildings = gpd.read_file(result.buildings_path)
    mapping = pd.read_csv(result.archetype_map_path, encoding="utf-8-sig")
    loads = pd.read_parquet(result.loads_path)
    assert result.data_version == "test-v0.2"
    assert buildings.loc[0, "heated_area_m2"] == 10.0
    assert buildings.loc[0, "terminal_type"] == "fan_coil"
    assert bool(buildings.loc[0, "fresh_air_load_included"]) is True
    assert mapping.loc[0, "zone_id"] == "b1-01"
    assert list(loads.columns) == ["timestamp", "building_id", "heating_kW", "data_version"]
    assert loads["heating_kW"].tolist() == [1.0, 2.0]
    assert {path: sha256(path.read_bytes()).hexdigest() for path in source.rglob("*") if path.is_file()} == before


def test_mixed_use_mapping_preserves_each_zone() -> None:
    source = pd.DataFrame(
        {
            "building_id": ["b1", "b1"],
            "is_mixed_use": [True, True],
            "zone_id": ["b1-01", "b1-02"],
            "zone_use_type": ["office", "service"],
            "zone_area_m2": [80.0, 20.0],
            "zone_archetype_id": ["office_a", "service_a"],
            "zone_scale_factor": [0.8, 0.2],
            "use_type": ["mixed", "mixed"],
            "target_conditioned_area_m2": [100.0, 100.0],
            "archetype_id": ["office_a", "service_a"],
            "scale_factor": [0.8, 0.2],
        }
    )
    normalized = _normalize_archetype_map(source)
    assert normalized["zone_id"].tolist() == ["b1-01", "b1-02"]
    assert normalized["zone_area_m2"].sum() == 100.0
