"""Build the isolated Guanggu 6-building/24-hour V3 smoke case.

Real delivery files are read only. All non-delivery parameters and spatial
features created here are explicitly classified as synthetic_v0.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point
import yaml


ROOT = Path(__file__).resolve().parents[1]
DELIVERY = ROOT / "0821代码组交付_光谷软件园"
FIXTURE = ROOT / "tests" / "fixtures" / "v3_smoke_case"
DEFAULT_OUTPUT = ROOT / "cases" / "v0_guanggu_6b_24h"
VERSION = "guanggu-real-loads-synthetic-v0-2021-01-08"
BUILDING_IDS = (
    "gsr_way_610079716",
    "gsr_way_1314562851",
    "gsr_way_1314562852",
    "gsr_way_610079720",
    "gsr_way_610079719",
    "gsr_way_610079721",
)
START = pd.Timestamp("2021-01-08 00:00:00", tz="Asia/Shanghai")
END = pd.Timestamp("2021-01-09 00:00:00", tz="Asia/Shanghai")


def build_case(
    output: Path = DEFAULT_OUTPUT,
    *,
    building_ids: tuple[str, ...] = BUILDING_IDS,
    case_id: str = "v0_guanggu_6b_24h",
    scale_synthetic_capacity_to_load: bool = False,
) -> Path:
    output = output.resolve()
    cases_root = (ROOT / "cases").resolve()
    if not output.is_relative_to(cases_root):
        raise ValueError(f"V0 case must be written below {cases_root}")
    output.mkdir(parents=True, exist_ok=True)

    # Reuse the tested V3 smoke-case contract and provisional parameters.
    for name in ("technologies.csv", "pipe_types.csv"):
        shutil.copy2(FIXTURE / name, output / name)

    source_buildings = gpd.read_file(DELIVERY / "03_buildings.geojson")
    buildings = source_buildings[source_buildings["building_id"].isin(building_ids)].copy()
    buildings["building_id"] = pd.Categorical(
        buildings["building_id"], categories=building_ids, ordered=True
    )
    buildings = buildings.sort_values("building_id").reset_index(drop=True)
    if buildings["building_id"].astype(str).tolist() != list(building_ids):
        raise ValueError("The delivery GeoJSON does not contain exactly the requested buildings")
    buildings["building_id"] = buildings["building_id"].astype(str)
    buildings["heated_area_m2"] = buildings["conditioned_area_m2"].astype(float)
    buildings["archetype_id"] = "provisional_v0_from_delivery_use_type"
    buildings["terminal_type"] = "fan_coil"
    buildings["data_version"] = VERSION
    buildings = buildings[
        ["building_id", "use_type", "heated_area_m2", "archetype_id", "terminal_type", "data_version", "geometry"]
    ]
    buildings.to_file(output / "buildings.geojson", driver="GeoJSON")

    source_loads = pd.read_parquet(DELIVERY / "05_building_hourly_loads.parquet")
    loads = source_loads[
        source_loads["building_id"].isin(building_ids)
        & (source_loads["timestamp"] >= START)
        & (source_loads["timestamp"] < END)
    ][["timestamp", "building_id", "heating_kW"]].copy()
    loads["data_version"] = VERSION
    loads = loads.sort_values(["timestamp", "building_id"]).reset_index(drop=True)
    counts = loads.groupby("building_id").size()
    expected_rows = len(building_ids) * 24
    if len(loads) != expected_rows or len(counts) != len(building_ids) or set(counts.tolist()) != {24}:
        raise ValueError(
            f"Expected {len(building_ids)} x 24 load rows, got {len(loads)} rows and counts {counts.to_dict()}"
        )
    if list(loads["timestamp"].drop_duplicates()) != list(pd.date_range(START, periods=24, freq="h")):
        raise ValueError("The selected delivery timestamps are not a continuous 24-hour series")
    loads.to_parquet(output / "building_hourly_loads.parquet", index=False)

    timestamps = pd.date_range(START, periods=24, freq="h")
    gas_lhv_kwh_per_nm3 = 38.931 / 3.6
    external = pd.DataFrame(
        {
            "timestamp": timestamps,
            "outdoor_temperature_C": [0.0] * 24,
            "time_weight_h_per_year": [1.0] * 24,
            "electricity_price_CNY_per_kWh_e": [1.0] * 24,
            "gas_price_CNY_per_kWh_LHV": [3.8 / gas_lhv_kwh_per_nm3] * 24,
            "electricity_carbon_kgCO2e_per_kWh_e": [0.4044] * 24,
            "gas_carbon_kgCO2e_per_kWh_LHV": [2.184 / gas_lhv_kwh_per_nm3] * 24,
            "data_version": [VERSION] * 24,
        }
    )
    external.to_parquet(output / "external_timeseries.parquet", index=False)

    # Keep the tested technology schema, but size synthetic ceilings above this real-load smoke case.
    technologies = pd.read_csv(output / "technologies.csv")
    technologies["capacity_max_kW_th"] = pd.to_numeric(
        technologies["capacity_max_kW_th"], errors="raise"
    ).astype(float)
    technologies.loc[technologies["technology_id"].isin(["central_hp", "central_boiler"]), "capacity_max_kW_th"] = 10000
    technologies.loc[technologies["technology_id"] == "local_hp", "fixed_cop"] = 3.2
    technologies.loc[technologies["technology_id"] == "local_hp", "capacity_max_kW_th"] = 5000
    if scale_synthetic_capacity_to_load:
        district_peak = float(loads.groupby("timestamp")["heating_kW"].sum().max())
        building_peak = float(loads.groupby("building_id")["heating_kW"].max().max())
        technologies.loc[
            technologies["technology_id"].isin(["central_hp", "central_boiler"]),
            "capacity_max_kW_th",
        ] = district_peak * 1.1
        technologies.loc[technologies["technology_id"] == "local_hp", "capacity_max_kW_th"] = building_peak * 1.1
    technologies["source"] = "synthetic_v0_reused_smoke_fixture"
    technologies["parameter_version"] = "synthetic_v0"
    # Contract enum; detailed synthetic_v0 label remains in source/version.
    technologies["assumption_flag"] = "synthetic_test"
    technologies.to_csv(output / "technologies.csv", index=False)
    pipes = pd.read_csv(output / "pipe_types.csv")
    pipes["capacity_max_kW_th"] = [6000, 8000, 10000]
    if scale_synthetic_capacity_to_load:
        branch_peak = float(loads.groupby("building_id")["heating_kW"].max().max())
        pipes["capacity_max_kW_th"] = [branch_peak * 0.6, branch_peak * 0.85, branch_peak * 1.1]
    pipes["source"] = "synthetic_v0_reused_smoke_fixture"
    pipes["parameter_version"] = "synthetic_v0"
    pipes.to_csv(output / "pipe_types.csv", index=False)

    projected = buildings.to_crs("EPSG:32650")
    centroids_m = projected.geometry.centroid
    mean_x = float(centroids_m.x.mean())
    mean_y = float(centroids_m.y.mean())
    station_m = Point(mean_x - 100.0, mean_y)
    station_geo = gpd.GeoSeries([station_m], crs="EPSG:32650").to_crs("EPSG:4326").iloc[0]
    sites = gpd.GeoDataFrame(
        {
            "site_id": ["synthetic_v0_station"],
            "candidate_rank": [1],
            "data_version": ["synthetic_v0"],
            "data_classification": ["synthetic_v0"],
        },
        geometry=[station_geo], crs="EPSG:4326",
    )
    sites.to_file(output / "candidate_sites.geojson", driver="GeoJSON")

    centroid_geo = gpd.GeoSeries(centroids_m, crs="EPSG:32650").to_crs("EPSG:4326")
    network_rows = []
    network_geometry = []
    for index, building_id in enumerate(buildings["building_id"]):
        length_m = float(station_m.distance(centroids_m.iloc[index]))
        network_rows.append(
            {
                "segment_id": f"synthetic_v0_edge_{index + 1}",
                "node_from": "synthetic_v0_station",
                "node_to": building_id,
                "length_m": length_m,
                "data_version": "synthetic_v0",
                "data_classification": "synthetic_v0_not_real_road",
            }
        )
        network_geometry.append(LineString([station_geo, centroid_geo.iloc[index]]))
    network = gpd.GeoDataFrame(network_rows, geometry=network_geometry, crs="EPSG:4326")
    network.to_file(output / "candidate_network.geojson", driver="GeoJSON")
    network[["segment_id", "data_version", "geometry"]].rename(
        columns={"segment_id": "feature_id"}
    ).assign(spatial_role="synthetic_v0_feasible_space").to_file(
        output / "roads_or_feasible_space.geojson", driver="GeoJSON"
    )

    config = yaml.safe_load((FIXTURE / "case_config.yaml").read_text(encoding="utf-8"))
    config["case_id"] = case_id
    config["scenario_id"] = "smoke_2021_01_08"
    config["data_version"] = VERSION
    # The draft V3 schema only allows synthetic_test/formal_project_data. This
    # mixed smoke case must use synthetic_test; VERSION and README preserve the
    # finer real-delivery versus synthetic-v0 provenance.
    config["data_classification"] = "synthetic_test"
    config["time"]["start"] = START.isoformat()
    config["time"]["end"] = END.isoformat()
    config["network"]["loss_model"] = "disabled_for_v0"
    config["network"]["pumping_model"] = "disabled_for_v0"
    config["economics"]["station_fixed_capex_CNY"] = 10000
    config["economics"]["connection_capex_CNY_per_demand_node"] = 1000
    # The current public pipeline/schema requires five Pareto points even for
    # v0-smoke. Reuse that setting rather than changing the pipeline here.
    config["pareto"]["point_count"] = 5
    (output / "case_config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (output / "README.md").write_text(
        "# Guanggu 6-building 24-hour V0 smoke case\n\n"
        "Real delivery data: building polygons/IDs/areas and 2021-01-08 heating_kW.\n"
        "All station, edges, terminal type, weather/economics, technologies and pipe parameters are synthetic_v0 or provisional_v0.\n"
        "The candidate edges are straight-line test links, not real roads or an engineering pipe network.\n",
        encoding="utf-8",
    )
    return output


if __name__ == "__main__":
    print(build_case())
