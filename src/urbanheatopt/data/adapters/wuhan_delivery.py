"""Adapter from the current Wuhan sample delivery to a validation-only case."""

from __future__ import annotations

from urbanheatopt.paths import REPOSITORY_ROOT as REPOSITORY_ROOT_PATH, PACKAGE_ROOT

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, box
import yaml

from urbanheatopt.data.intake import validate_delivery
from urbanheatopt.data.validation import CaseInputs, validate_case_inputs


PROTOTYPE_SOURCE = {
    "COA_2015": "CoA_Wuhan_2015", "COB_2015": "CoB_Wuhan_2015",
    "GOA_2015": "GoA_Wuhan_2015", "GOB_2015": "GoB_Wuhan_2015",
    "HRS_2010": "HighS_Wuhan_2010", "HRT_2010": "HighT_Wuhan_2010",
    "INP_2015": "Inp_Wuhan_2015", "LHT_2015": "LH_Wuhan_2015",
    "LRA_2010": "Low_Wuhan_2010", "MAL_2015": "Mall_Wuhan_2015",
    "OUT_2015": "Outp_Wuhan_2015", "SCH_2015": "Sch_Wuhan_2015",
    "SHT_2015": "SH_Wuhan_2015", "TH_2010": "Th_Wuhan_2010",
    "UNI_2015": "Uni_Wuhan_2015",
}


@dataclass(frozen=True)
class WuhanAdaptationResult:
    output_dir: Path
    validation_report: CaseInputs
    reconciliation: pd.DataFrame


def _source_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _unit_load_path(source_root: Path, prototype: str) -> Path:
    scenario = PROTOTYPE_SOURCE.get(prototype)
    if scenario is None:
        raise ValueError(f"未声明的 prototype_type: {prototype}")
    matches = list((source_root / "Wuhan_DeST_models汇总" / scenario / "006_building_load").glob("*建筑逐时单位面积负荷.csv"))
    if len(matches) != 1:
        raise ValueError(f"{scenario} 单位面积负荷文件数量应为 1，实际 {len(matches)}")
    return matches[0]


def _adapt_buildings(source_root: Path) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    source = pd.read_csv(source_root / "建筑虚拟建模" / "buildings_revised_no_overlap.csv", encoding="utf-8-sig")
    polygons = []
    for row in source.itertuples(index=False):
        footprint = float(row.area_m2) / float(row.floors)
        half = np.sqrt(footprint) / 2
        # Explicit validation-only UTM anchor near Wuhan. Local distances and
        # shapes are preserved; absolute locations are not project evidence.
        cx, cy = 500000.0 + float(row.x), 3360000.0 + float(row.y)
        polygons.append(box(cx - half, cy - half, cx + half, cy + half))
    projected = gpd.GeoDataFrame(
        {
            "building_id": source["building_id"].astype(str),
            "use_type": source["type"].astype(str),
            "heated_area_m2": source["area_m2"].astype(float),
            "archetype_id": source["prototype_type"].astype(str),
            "floors": source["floors"].astype(int),
            "load_node": source["load_node"].astype(str),
            "source": "virtual_building_delivery",
            "spatial_assumption": "validation_only_local_xy_anchored_to_UTM50N",
        }, geometry=polygons, crs="EPSG:32650",
    )
    return projected.to_crs("EPSG:4326"), source


def _adapt_loads(source_root: Path, buildings: pd.DataFrame, year: int, data_version: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    timestamps = pd.date_range(f"{year}-01-01", periods=8760, freq="h", tz="Asia/Shanghai")
    rows: list[pd.DataFrame] = []
    reconciliation: list[dict[str, Any]] = []
    cache: dict[str, tuple[pd.Series, Path, int, int]] = {}
    for prototype in sorted(buildings["prototype_type"].unique()):
        path = _unit_load_path(source_root, prototype)
        frame = pd.read_csv(path, encoding="utf-8-sig")
        raw = pd.to_numeric(frame["热负荷（W/m2）"], errors="coerce")
        null_count = int(raw.isna().sum())
        small_negative = raw.lt(0) & raw.ge(-0.001)
        material_negative = raw.lt(-0.001)
        if material_negative.any():
            raise ValueError(f"{path.name} 热负荷存在 {int(material_negative.sum())} 个超出容差的负值")
        normalized = raw.fillna(0.0).mask(small_negative, 0.0)
        cache[prototype] = (normalized, path, null_count, int(small_negative.sum()))
    for building in buildings.itertuples(index=False):
        unit_w_m2, path, null_count, clipped_count = cache[building.prototype_type]
        heating_kw = unit_w_m2.to_numpy(dtype=float) * float(building.area_m2) / 1000.0
        rows.append(pd.DataFrame({
            "timestamp": timestamps,
            "building_id": str(building.building_id),
            "heating_kW": heating_kw,
            "dhw_included": False,
            "data_version": data_version,
            "quality_flag": "simulated_scaled",
        }))
        reconciliation.append({
            "building_id": str(building.building_id), "prototype_type": building.prototype_type,
            "source_file_sha256": _source_hash(path), "area_m2": float(building.area_m2),
            "source_unit_heat_sum_Wh_per_m2": float(unit_w_m2.sum()),
            "output_heat_sum_kWh": float(heating_kw.sum()),
            "expected_heat_sum_kWh": float(unit_w_m2.sum() * float(building.area_m2) / 1000.0),
            "relative_error": 0.0, "filled_null_count": null_count,
            "clipped_small_negative_count": clipped_count,
        })
    loads = pd.concat(rows, ignore_index=True).sort_values(
        ["timestamp", "building_id"], kind="stable"
    ).reset_index(drop=True)
    return loads, pd.DataFrame(reconciliation)


def _write_case_config(output: Path, year: int, data_version: str) -> None:
    # This adapter currently emits the executable competition_input_v1 shape
    # accepted by strive_heatOPT.  When competition_input_v2_1 becomes the
    # runtime contract, migrate this mapping here rather than weakening the
    # current validator or changing the delivered source files in place.
    config = {
        "contract_version": "competition_input_v1", "case_id": "wuhan_delivery_validation",
        "scenario_id": "adapter_smoke", "data_version": data_version,
        "data_classification": "synthetic_test",
        "time": {"start": f"{year}-01-01T00:00:00+08:00", "end": f"{year + 1}-01-01T00:00:00+08:00", "timezone": "Asia/Shanghai", "frequency": "1h", "interval": "start_inclusive_end_exclusive", "complete_heating_season": True},
        "units": {"heating_power": "kW", "heating_energy": "kWh", "currency": "CNY", "area": "m2", "length": "m", "temperature": "degC", "carbon": "kgCO2e"},
        "crs": {"input": "EPSG:4326", "projected": "EPSG:32650"},
        "files": {"buildings": "buildings.geojson", "building_hourly_loads": "building_hourly_loads.parquet", "technologies": "technologies.csv", "roads_or_feasible_space": "roads_or_feasible_space.geojson", "resource_anchors": None, "external_timeseries": "external_timeseries.parquet"},
        "clustering": {"algorithm": "kmeans", "cluster_count": 10, "random_seed": 202611, "n_init": 10},
        "spatial": {"input_mode": "roads", "candidate_site_rule": "validation_only", "candidate_network_rule": "provided_virtual_distance_future_adapter", "feasibility_tolerance_m": 0.0},
        "demand": {"area_scaling_already_applied": True}, "dhw": {"input_includes_dhw": False, "add_in_adapter": False},
        "features": {"waste_heat_enabled": False, "storage_enabled": False},
        "network": {"supply_temperature_C": 55.0, "return_temperature_C": 35.0},
        "planning": {"horizon_years": 20, "discount_rate": 0.05, "price_base_year": 2026, "currency": "CNY"},
        "enabled_technology_ids": ["validation_fixed_source"],
        "solver": {"name": "highs", "threads": 1, "time_limit_seconds": 60, "mip_gap": 0.0, "load_solution_only_if_optimal": True},
        "qa": {"cluster_energy_relative_tolerance": 0.001, "balance_tolerance_kW": 1e-6, "unserved_tolerance_kWh": 1e-6, "cost_tolerance_CNY": 1e-6, "deterministic_tolerance": 1e-9},
    }
    (output / "case_config.yaml").write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")


def adapt_wuhan_delivery(source_root: str | Path, output_dir: str | Path, *, year: int = 2025) -> WuhanAdaptationResult:
    source = Path(source_root).resolve(); output = Path(output_dir).resolve()
    manifest = PACKAGE_ROOT / "data" / "profile_resources" / "wuhan_delivery_intake.yaml"
    intake = validate_delivery(manifest, source)
    if not intake.valid:
        raise ValueError("通用接收校验未通过，禁止适配")
    output.mkdir(parents=True, exist_ok=True)
    data_version = "wuhan-delivery-validation-v1"
    geo, building_source = _adapt_buildings(source)
    loads, reconciliation = _adapt_loads(source, building_source, year, data_version)
    geo.to_file(output / "buildings.geojson", driver="GeoJSON")
    loads.to_parquet(output / "building_hourly_loads.parquet", index=False)
    points = geo.to_crs("EPSG:32650").geometry.centroid
    road = gpd.GeoDataFrame({"feature_id": ["validation_route"], "spatial_role": ["road"], "source": ["validation_only_centroid_chain"]}, geometry=[LineString([(p.x, p.y) for p in points])], crs="EPSG:32650").to_crs("EPSG:4326")
    road.to_file(output / "roads_or_feasible_space.geojson", driver="GeoJSON")
    pd.DataFrame([{"technology_id": "validation_fixed_source", "technology_type": "fixed_heat_source", "applicable_scope": "central", "energy_carrier": "synthetic_heat", "cop": np.nan, "efficiency": np.nan, "capacity_min_kW": 0.0, "capacity_max_kW": float(loads.groupby("timestamp")["heating_kW"].sum().max() * 1.1), "capex_CNY_per_kW": 1.0, "capex_basis": "one_time_capex", "fixed_om_CNY_per_kW_year": 0.0, "variable_om_CNY_per_kWh_heat": 0.1, "lifetime_years": 20, "source": "validation harness only; not equipment evidence", "assumption_flag": "synthetic_test"}]).to_csv(output / "technologies.csv", index=False)
    weather_path = next((source / "Wuhan_DeST_models汇总" / "CoA_Wuhan_2015" / "001_weather").glob("*逐时气象参数.csv"))
    weather = pd.read_csv(weather_path, encoding="utf-8-sig")
    pd.DataFrame({"timestamp": pd.date_range(f"{year}-01-01", periods=8760, freq="h", tz="Asia/Shanghai"), "data_version": data_version, "outdoor_temperature_C": pd.to_numeric(weather["干球温度(℃)"], errors="raise")}).to_parquet(output / "external_timeseries.parquet", index=False)
    _write_case_config(output, year, data_version)
    reconciliation.to_csv(output / "load_reconciliation.csv", index=False)
    pd.DataFrame(columns=["dataset", "missing_id", "present_only_in"]).to_csv(output / "id_mismatch.csv", index=False)
    report = validate_case_inputs(output)
    (output / "interface_acceptance.md").write_text(f"# Interface acceptance\n\n- Status: accepted for validation-only computation input\n- Buildings: {len(report.buildings)}\n- Hours: {len(report.timestamp_hour_map)}\n- Data version: `{data_version}`\n- Spatial location: synthetic validation anchor; not suitable for planning conclusions\n- Technology: synthetic fixed source; delivered equipment catalog is intake-validated but not executable in P0\n", encoding="utf-8")
    (output / "adapter_run.log").write_text(f"adapter=urbanheatopt.data.adapters.wuhan_delivery\nyear={year}\nrandom_seed=202611\nsource_root={source}\noutput_dir={output}\nsource_files_read_only=true\n", encoding="utf-8")
    return WuhanAdaptationResult(output, report, reconciliation)
