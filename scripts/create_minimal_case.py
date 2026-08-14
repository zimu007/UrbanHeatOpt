from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd
import yaml
from shapely.geometry import LineString, Polygon


def create_minimal_case(case_dir: str | Path) -> Path:
    """Create a deterministic 4-building, 24-hour synthetic P0 case."""

    case_path = Path(case_dir).resolve()
    case_path.mkdir(parents=True, exist_ok=True)

    timestamps = pd.date_range(
        "2026-01-01T00:00:00",
        periods=24,
        freq="h",
        tz="Asia/Shanghai",
    )
    buildings = gpd.GeoDataFrame(
        {
            "building_id": [f"building_{idx}" for idx in range(4)],
            "use_type": ["office", "office", "school", "retail"],
            "heated_area_m2": [100.0, 120.0, 140.0, 160.0],
            "archetype_id": ["synthetic_office", "synthetic_office", "synthetic_school", "synthetic_retail"],
            "geometry": [
                Polygon([(114.0, 30.0), (114.001, 30.0), (114.001, 30.001), (114.0, 30.001)]),
                Polygon([(114.002, 30.0), (114.003, 30.0), (114.003, 30.001), (114.002, 30.001)]),
                Polygon([(114.0, 30.002), (114.001, 30.002), (114.001, 30.003), (114.0, 30.003)]),
                Polygon([(114.002, 30.002), (114.003, 30.002), (114.003, 30.003), (114.002, 30.003)]),
            ],
        },
        crs="EPSG:4326",
    )
    buildings.to_file(case_path / "buildings.geojson", driver="GeoJSON")

    load_rows = []
    for hour_index, timestamp in enumerate(timestamps):
        for building_index in range(4):
            load_rows.append(
                {
                    "timestamp": timestamp,
                    "building_id": f"building_{building_index}",
                    "heating_kW": 5.0 + building_index + hour_index / 100.0,
                    "cooling_kW": 0.0,
                    "dhw_included": False,
                    "data_version": "synthetic-v1",
                    "quality_flag": "synthetic_test",
                }
            )
    pd.DataFrame(load_rows).to_parquet(case_path / "building_hourly_loads.parquet", index=False)

    pd.DataFrame(
        [
            {
                "technology_id": "synthetic_fixed_source",
                "technology_type": "fixed_heat_source",
                "applicable_scope": "central",
                "energy_carrier": "synthetic_heat",
                "cop": pd.NA,
                "efficiency": pd.NA,
                "capacity_min_kW": 0.0,
                "capacity_max_kW": 100.0,
                "capex_CNY_per_kW": 10.0,
                "capex_basis": "one_time_capex",
                "fixed_om_CNY_per_kW_year": 0.0,
                "variable_om_CNY_per_kWh_heat": 0.2,
                "lifetime_years": 20,
                "source": "synthetic fixture",
                "assumption_flag": "synthetic_test",
            }
        ]
    ).to_csv(case_path / "technologies.csv", index=False, encoding="utf-8-sig")

    roads = gpd.GeoDataFrame(
        {
            "feature_id": ["road_1"],
            "spatial_role": ["road"],
            "geometry": [LineString([(113.999, 29.999), (114.004, 30.004)])],
        },
        crs="EPSG:4326",
    )
    roads.to_file(case_path / "roads_or_feasible_space.geojson", driver="GeoJSON")

    pd.DataFrame(
        {
            "timestamp": timestamps,
            "data_version": ["synthetic-v1"] * len(timestamps),
        }
    ).to_parquet(case_path / "external_timeseries.parquet", index=False)

    config = {
        "contract_version": "competition_input_v1",
        "case_id": "minimal",
        "scenario_id": "smoke",
        "data_version": "synthetic-v1",
        "data_classification": "synthetic_test",
        "time": {
            "start": "2026-01-01T00:00:00+08:00",
            "end": "2026-01-02T00:00:00+08:00",
            "timezone": "Asia/Shanghai",
            "frequency": "1h",
            "interval": "start_inclusive_end_exclusive",
            "complete_heating_season": False,
        },
        "units": {
            "heating_power": "kW",
            "heating_energy": "kWh",
            "currency": "CNY",
            "area": "m2",
            "length": "m",
            "temperature": "degC",
            "carbon": "kgCO2e",
        },
        "crs": {"input": "EPSG:4326", "projected": "EPSG:32650"},
        "files": {
            "buildings": "buildings.geojson",
            "building_hourly_loads": "building_hourly_loads.parquet",
            "technologies": "technologies.csv",
            "roads_or_feasible_space": "roads_or_feasible_space.geojson",
            "resource_anchors": None,
            "external_timeseries": "external_timeseries.parquet",
        },
        "clustering": {
            "algorithm": "kmeans",
            "cluster_count": 2,
            "random_seed": 202611,
            "n_init": 10,
        },
        "spatial": {
            "input_mode": "roads",
            "candidate_site_rule": "cluster_centroid_nearest_feature",
            "candidate_network_rule": "delaunay_mst",
            "feasibility_tolerance_m": 0.0,
        },
        "demand": {"area_scaling_already_applied": True},
        "dhw": {"input_includes_dhw": False, "add_in_adapter": False},
        "features": {"waste_heat_enabled": False, "storage_enabled": False},
        "network": {"supply_temperature_C": 55.0, "return_temperature_C": 35.0},
        "planning": {
            "horizon_years": 20,
            "discount_rate": 0.05,
            "price_base_year": 2026,
            "currency": "CNY",
        },
        "enabled_technology_ids": ["synthetic_fixed_source"],
        "solver": {
            "name": "highs",
            "threads": 1,
            "time_limit_seconds": 60,
            "mip_gap": 0.0,
            "load_solution_only_if_optimal": True,
        },
        "qa": {
            "cluster_energy_relative_tolerance": 0.001,
            "balance_tolerance_kW": 0.000001,
            "unserved_tolerance_kWh": 0.000001,
            "cost_tolerance_CNY": 0.000001,
            "deterministic_tolerance": 1e-9,
        },
    }
    (case_path / "case_config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return case_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the checked-in minimal synthetic P0 case.")
    parser.add_argument(
        "--output",
        default="tests/fixtures/minimal_case",
        help="Directory where the minimal case files are written.",
    )
    args = parser.parse_args()
    case_path = create_minimal_case(args.output)
    print(case_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
