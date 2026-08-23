"""Regenerate the tracked two-building, 24-hour V3 synthetic fixture."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point, Polygon
import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = (REPOSITORY_ROOT / "tests" / "fixtures").resolve()


def create_case(output: Path) -> None:
    output = output.resolve()
    if not output.is_relative_to(FIXTURE_ROOT):
        raise ValueError(f"合成案例只能写入测试夹具目录：{FIXTURE_ROOT}")
    output.mkdir(parents=True, exist_ok=True)
    version = "synthetic-v3-draft1"
    timestamps = pd.date_range("2026-01-01T00:00:00", periods=24, freq="h", tz="Asia/Shanghai")
    buildings = gpd.GeoDataFrame(
        {
            "building_id": ["building_1", "building_2"],
            "use_type": ["office", "residential"],
            "heated_area_m2": [1000.0, 800.0],
            "terminal_type": ["fan_coil", "floor_radiant"],
            "ventilation_system": ["dedicated_fresh_air", "dedicated_fresh_air"],
            "fresh_air_load_included": [True, True],
            "data_version": [version, version],
        },
        geometry=[
            Polygon([(114.3000, 30.5000), (114.3002, 30.5000), (114.3002, 30.5002), (114.3000, 30.5002)]),
            Polygon([(114.3010, 30.5000), (114.3012, 30.5000), (114.3012, 30.5002), (114.3010, 30.5002)]),
        ],
        crs="EPSG:4326",
    )
    buildings.to_file(output / "buildings.geojson", driver="GeoJSON")
    pd.DataFrame(
        [
            ["building_1", "building_1-01", "office", 1000.0, "synthetic_office", 1.0],
            ["building_2", "building_2-01", "residential", 800.0, "synthetic_residential", 1.0],
        ],
        columns=["building_id", "zone_id", "zone_use_type", "zone_area_m2", "zone_archetype_id", "zone_scale_factor"],
    ).to_csv(output / "building_archetype_map.csv", index=False, encoding="utf-8-sig")

    load_rows = []
    for timestamp in timestamps:
        peak = timestamp.hour in {7, 8, 9, 18, 19, 20, 21}
        load_rows.extend(
            [
                {"timestamp": timestamp, "building_id": "building_1", "heating_kW": 60.0 if peak else 30.0, "data_version": version},
                {"timestamp": timestamp, "building_id": "building_2", "heating_kW": 40.0 if peak else 20.0, "data_version": version},
            ]
        )
    pd.DataFrame(load_rows).to_parquet(output / "building_hourly_loads.parquet", index=False)

    gas_lhv_kWh_per_Nm3 = 38.931 / 3.6
    external = pd.DataFrame(
        {
            "timestamp": timestamps,
            "outdoor_temperature_C": [-4 + abs(12 - hour) * 0.5 for hour in range(24)],
            "time_weight_h_per_year": [365.0] * 24,
            "electricity_price_CNY_per_kWh_e": [0.48 if hour < 6 or 12 <= hour < 14 else 1.49 if 16 <= hour < 24 else 1.0 for hour in range(24)],
            "gas_price_CNY_per_kWh_LHV": [3.8 / gas_lhv_kWh_per_Nm3] * 24,
            "electricity_carbon_kgCO2e_per_kWh_e": [0.4044] * 24,
            "gas_carbon_kgCO2e_per_kWh_LHV": [2.184 / gas_lhv_kWh_per_Nm3] * 24,
            "data_version": [version] * 24,
        }
    )
    external.to_parquet(output / "external_timeseries.parquet", index=False)

    technology_columns = [
        "technology_id", "technology_type", "applicable_scope", "energy_carrier",
        "performance_model", "fixed_cop", "efficiency_LHV", "capacity_min_kW_th",
        "capacity_max_kW_th", "capex_CNY_per_kW_th", "fixed_om_fraction_per_year",
        "variable_om_CNY_per_kWh_th", "lifetime_years", "source", "parameter_version",
        "assumption_flag", "storage_energy_capacity_max_kWh_th",
        "storage_charge_capacity_max_kW_th", "storage_discharge_capacity_max_kW_th",
        "storage_charge_efficiency", "storage_discharge_efficiency",
        "storage_standing_loss_fraction_per_hour", "storage_capex_CNY_per_kWh_th",
        "storage_power_capex_CNY_per_kW_th", "storage_fixed_capex_CNY",
    ]
    blank_storage = [""] * 9
    technologies = [
        ["central_hp", "air_source_heat_pump", "central", "electricity", "fixed_for_v0", 3.2, "", 0, 150, 3000, 0.02, 0, 15, "synthetic_test", "v0-1", "synthetic_test", *blank_storage],
        ["central_boiler", "gas_boiler", "central", "gas", "fixed_efficiency", "", 0.94, 0, 150, 800, 0.02, 0, 15, "synthetic_test", "v0-1", "synthetic_test", *blank_storage],
        ["local_hp", "air_source_heat_pump", "local", "electricity", "fixed_for_v0", 3.0, "", 0, 100, 2600, 0.02, 0, 15, "synthetic_test", "v0-1", "synthetic_test", *blank_storage],
        ["central_tes", "water_thermal_storage", "central", "thermal", "linear_soc", "", "", 0, 1, 0, 0, 0, 15, "synthetic_test", "v0-1", "synthetic_test", 150, 100, 100, 0.95, 0.95, 0, 50, 100, 1000],
    ]
    pd.DataFrame(technologies, columns=technology_columns).to_csv(output / "technologies.csv", index=False)
    pd.DataFrame(
        [
            ["dn_small", 1, 60, 500, 0, 0, 30, "synthetic_test", "v0-1"],
            ["dn_medium", 2, 110, 700, 0, 0, 30, "synthetic_test", "v0-1"],
            ["dn_large", 3, 170, 900, 0, 0, 30, "synthetic_test", "v0-1"],
        ],
        columns=["pipe_type_id", "level", "capacity_max_kW_th", "capex_CNY_per_m", "heat_loss_kW_per_m", "pumping_kWh_e_per_kWh_th_transferred", "lifetime_years", "source", "parameter_version"],
    ).to_csv(output / "pipe_types.csv", index=False)

    site = Point(114.3006, 30.5006)
    sites = gpd.GeoDataFrame(
        {"site_id": ["site_1"], "candidate_rank": [1], "data_version": [version]},
        geometry=[site], crs="EPSG:4326",
    )
    sites.to_file(output / "candidate_sites.geojson", driver="GeoJSON")
    network = gpd.GeoDataFrame(
        {
            "segment_id": ["segment_1", "segment_2"],
            "node_from": ["site_1", "site_1"],
            "node_to": ["building_1", "building_2"],
            "length_m": [80.0, 90.0],
            "data_version": [version, version],
        },
        geometry=[LineString([site, buildings.geometry.iloc[0].centroid]), LineString([site, buildings.geometry.iloc[1].centroid])],
        crs="EPSG:4326",
    )
    network.to_file(output / "candidate_network.geojson", driver="GeoJSON")
    roads = gpd.GeoDataFrame(
        {"feature_id": ["road_1"], "spatial_role": ["road"], "data_version": [version]},
        geometry=[LineString([(114.2995, 30.5005), (114.3020, 30.5005)])], crs="EPSG:4326",
    )
    roads.to_file(output / "roads_or_feasible_space.geojson", driver="GeoJSON")

    config = {
        "contract_version": "competition_input_3.0.0-draft.2", "software_release_track": "test_v0",
        "case_id": "minimal_v3", "scenario_id": "smoke", "data_version": version,
        "data_classification": "synthetic_test",
        "time": {"start": str(timestamps[0].isoformat()), "end": str((timestamps[-1] + pd.Timedelta(hours=1)).isoformat()), "timezone": "Asia/Shanghai", "frequency": "1h", "interval": "start_inclusive_end_exclusive", "complete_heating_season": False},
        "units": {"heating_power": "kW_th", "heating_energy": "kWh_th", "electric_power": "kW_e", "electric_energy": "kWh_e", "gas_energy": "kWh_LHV", "storage_energy": "kWh_th", "currency": "CNY", "annual_cost": "CNY_per_year", "carbon": "kgCO2e_per_year", "electricity_price": "CNY_per_kWh_e", "gas_price": "CNY_per_kWh_LHV", "electricity_carbon_intensity": "kgCO2e_per_kWh_e", "gas_carbon_intensity": "kgCO2e_per_kWh_LHV", "time_weight": "h_per_year", "length": "m", "temperature": "degC"},
        "crs": {"input": "EPSG:4326", "projected": "EPSG:32650"},
        "files": {"buildings": "buildings.geojson", "building_archetype_map": "building_archetype_map.csv", "building_hourly_loads": "building_hourly_loads.parquet", "technologies": "technologies.csv", "roads_or_feasible_space": "roads_or_feasible_space.geojson", "external_timeseries": "external_timeseries.parquet", "pipe_types": "pipe_types.csv", "candidate_sites": "candidate_sites.geojson", "candidate_network": "candidate_network.geojson"},
        "run": {"profile": "v0-smoke", "modes": ["central", "distributed", "hybrid"]},
        "spatial": {"input_mode": "roads", "candidate_source": "provided", "candidate_site_count_min": 1, "candidate_site_count_max": 10, "max_built_sites": 1},
        "demand": {"area_scaling_already_applied": True, "includes_dhw": False, "includes_cooling": False, "ventilation_system": "dedicated_fresh_air", "fresh_air_load_included": True},
        "features": {"storage_enabled": True, "temperature_cop_enabled": False, "pipe_loss_enabled": False, "pumping_enabled": False, "waste_heat_enabled": False},
        "network": {"supply_temperature_C": 50, "return_temperature_C": 40, "pipe_level_count": 3, "loss_model": "disabled_for_v0", "pumping_model": "disabled_for_v0"},
        "planning": {"discount_rate": 0.05, "price_base_year": 2026, "currency": "CNY", "unserved_policy": "penalized_for_v0", "peak_capacity_margin_fraction": 0.2, "carbon_price_scenarios_CNY_per_tCO2e": [0, 50, 100, 150]},
        "economics": {"annualization_method": "capital_recovery_factor", "station_fixed_capex_CNY": 10000, "station_lifetime_years": 30, "connection_capex_CNY_per_demand_node": 1000, "connection_lifetime_years": 30, "hns_penalty_CNY_per_kWh_th": 1000000},
        "performance": {"cop_model": "fixed_for_v0", "capacity_derating_model": "disabled_for_v0", "precompute_coefficients": True},
        "pareto": {"method": "epsilon_constraint", "point_count": 5, "second_objective": "annual_operating_physical_carbon", "knee_method": "normalized_max_distance_to_endpoint_chord", "topsis_enabled": False},
        "enabled_technology_ids": ["central_hp", "central_boiler", "local_hp", "central_tes"],
        "solver": {"name": "highs", "threads": 1, "time_limit_seconds": 60, "mip_gap": 0, "random_seed": 202611, "load_solution_only_if_optimal": True},
        "qa": {"balance_tolerance_kW": 1e-6, "unserved_tolerance_kWh": 1e-6, "cost_tolerance_CNY_per_year": 1e-6, "carbon_tolerance_kgCO2e_per_year": 1e-6, "deterministic_tolerance": 1e-9},
    }
    (output / "case_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="tests/fixtures/v3_smoke_case")
    args = parser.parse_args()
    create_case(Path(args.output).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
