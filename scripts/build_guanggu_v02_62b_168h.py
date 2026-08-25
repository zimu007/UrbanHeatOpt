"""Build the isolated Guanggu V0.2 62-building/168-hour V3 scale case.

The frozen 0823 delivery is read-only.  Synthetic/provisional planning inputs
are copied into a new case and explicitly labelled; no source file is changed.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from scipy.spatial import Delaunay
from shapely.geometry import LineString, Point
import yaml

from competition.physical_interfaces import TabularASHPPerformanceProvider
from competition.provisional_spatial import build_provisional_geometric_network


ROOT = Path(__file__).resolve().parents[1]
DELIVERY = ROOT / "0823代码组交付_光谷软件园_v0.3"
V00_CASE = ROOT / "cases" / "v0_guanggu_6b_24h"
OUTPUT = ROOT / "cases" / "v0_guanggu_62b_168h"
DATA_VERSION = "guanggu-v0.3-20260823"
SEASON_HOURS = tuple(range(816, 984))
SOURCE_START = pd.Timestamp("2021-01-04 00:00:00", tz="Asia/Shanghai")
SOURCE_END = pd.Timestamp("2021-01-11 00:00:00", tz="Asia/Shanghai")


def gas_volume_to_lhv_energy_factors(
    *, price_CNY_per_Nm3: pd.Series, carbon_kgCO2e_per_Nm3: pd.Series,
    lhv_MJ_per_Nm3: float,
) -> tuple[pd.Series, pd.Series, float]:
    """Convert volume-basis gas values to the V3 kWh_LHV basis."""
    if not np.isfinite(lhv_MJ_per_Nm3) or lhv_MJ_per_Nm3 <= 0:
        raise ValueError("natural-gas LHV must be finite and positive")
    lhv_kWh_per_Nm3 = float(lhv_MJ_per_Nm3) / 3.6
    return (
        price_CNY_per_Nm3.astype(float) / lhv_kWh_per_Nm3,
        carbon_kgCO2e_per_Nm3.astype(float) / lhv_kWh_per_Nm3,
        lhv_kWh_per_Nm3,
    )


def _parameter(table: pd.DataFrame, technology_id: str, parameter_name: str) -> tuple[float, str]:
    rows = table[
        table["technology_id"].eq(technology_id)
        & table["parameter_name"].eq(parameter_name)
    ]
    if len(rows) != 1:
        raise ValueError(f"expected exactly one {technology_id}.{parameter_name}")
    row = rows.iloc[0]
    return float(row["recommended_value"]), str(row["unit"])


def _building_ids() -> tuple[str, ...]:
    frame = gpd.read_file(DELIVERY / "03_buildings.geojson")
    return tuple(frame["building_id"].astype(str).tolist())


def build_case(output: Path = OUTPUT) -> Path:
    output = output.resolve()
    if not output.is_relative_to((ROOT / "cases").resolve()):
        raise ValueError("output must be below cases/")
    output.mkdir(parents=True, exist_ok=True)
    building_ids = _building_ids()

    master = pd.read_csv(DELIVERY / "00_building_master.csv", encoding="utf-8-sig")
    source_gis = gpd.read_file(DELIVERY / "03_buildings.geojson")
    selected_master = master[master["building_id"].isin(building_ids)].copy()
    buildings = source_gis[source_gis["building_id"].isin(building_ids)].copy()
    if set(selected_master["building_id"]) != set(building_ids) or set(buildings["building_id"]) != set(building_ids):
        raise ValueError("all V0.0 building IDs must exist in frozen 0823 master and GIS")
    attrs = selected_master.set_index("building_id")
    buildings["use_type"] = buildings["building_id"].map(attrs["use_type"])
    buildings["heated_area_m2"] = buildings["building_id"].map(attrs["conditioned_area_m2"]).astype(float)
    buildings["archetype_id"] = buildings["building_id"].map(attrs["primary_archetype_id"])
    buildings["terminal_type"] = buildings["building_id"].map(attrs["terminal_type"])
    buildings["supply_temperature_C"] = buildings["building_id"].map(attrs["heating_supply_temperature_C"])
    buildings["return_temperature_C"] = buildings["building_id"].map(attrs["heating_return_temperature_C"])
    buildings["ventilation_system"] = "dedicated_fresh_air"
    buildings["fresh_air_load_included"] = True
    buildings["data_version"] = DATA_VERSION
    if set(buildings["terminal_type"]) != {"fan_coil"} or set(buildings["supply_temperature_C"]) != {45} or set(buildings["return_temperature_C"]) != {40}:
        raise ValueError("V0.2 requires frozen fan_coil 45/40 degC terminal inputs")
    buildings = buildings[[
        "building_id", "use_type", "heated_area_m2", "archetype_id", "terminal_type",
        "supply_temperature_C", "return_temperature_C", "data_version", "geometry",
        "ventilation_system", "fresh_air_load_included",
    ]].sort_values("building_id").reset_index(drop=True)
    buildings.to_file(output / "buildings.geojson", driver="GeoJSON")
    pd.DataFrame(
        {
            "building_id": buildings["building_id"],
            "zone_id": [f"{item}_zone_1" for item in buildings["building_id"]],
            "zone_use_type": buildings["use_type"],
            "zone_area_m2": buildings["heated_area_m2"],
            "zone_archetype_id": buildings["archetype_id"],
            "zone_scale_factor": 1.0,
        }
    ).to_csv(output / "building_archetype_map.csv", index=False, encoding="utf-8-sig")

    source_load = pd.read_parquet(DELIVERY / "05_building_hourly_loads.parquet")
    loads = source_load[
        source_load["building_id"].isin(building_ids)
        & source_load["heating_season_hour"].isin(SEASON_HOURS)
    ][["timestamp", "hour", "heating_season_hour", "building_id", "heating_kW", "data_version"]].copy()
    loads = loads.sort_values(["timestamp", "building_id"]).reset_index(drop=True)
    if len(loads) != 10416 or loads["building_id"].nunique() != 62 or loads["heating_season_hour"].nunique() != 168:
        raise ValueError("expected exactly 62 buildings x 168 hours")
    if loads.duplicated(["building_id", "timestamp"]).any() or loads["heating_kW"].isna().any() or (loads["heating_kW"] < 0).any():
        raise ValueError("invalid V0.1 load values")
    if loads["timestamp"].min() != SOURCE_START or loads["timestamp"].max() != SOURCE_END - pd.Timedelta(hours=1):
        raise ValueError("season-hour mapping does not resolve to the requested 168h window")
    loads.to_parquet(output / "building_hourly_loads.parquet", index=False)

    external_source = pd.read_parquet(DELIVERY / "external_timeseries.parquet")
    external = external_source[external_source["heating_season_hour"].isin(SEASON_HOURS)].copy()
    external = external.sort_values("heating_season_hour").reset_index(drop=True)
    if tuple(external["heating_season_hour"].astype(int)) != SEASON_HOURS:
        raise ValueError("external season-hour coverage mismatch")
    if set(external["heating_season_hour"]) != set(loads["heating_season_hour"]):
        raise ValueError("load and external season-hour sets differ")
    tout = external["outdoor_temperature_C"].astype(float)
    outside = external[(tout < -5) | (tout > 15)]
    if not outside.empty:
        raise ValueError("Tout outside ASHP curve; extrapolation and clamping are forbidden:\n" + outside[["timestamp", "heating_season_hour", "outdoor_temperature_C"]].to_string(index=False))

    tech_long = pd.read_csv(DELIVERY / "technologies.csv", encoding="utf-8-sig")
    lhv, unit = _parameter(tech_long, "GAS_BOILER_BASE_01", "natural_gas_LHV")
    if unit != "MJ/Nm3":
        raise ValueError(f"unexpected gas LHV unit {unit}")
    gas_price, gas_carbon, lhv_kwh = gas_volume_to_lhv_energy_factors(
        price_CNY_per_Nm3=external["natural_gas_price_CNY_per_Nm3"],
        carbon_kgCO2e_per_Nm3=external["natural_gas_carbon_factor_kgCO2e_per_Nm3"],
        lhv_MJ_per_Nm3=lhv,
    )
    external_v3 = pd.DataFrame({
        "timestamp": external["timestamp"],
        "hour": external["hour"],
        "heating_season_hour": external["heating_season_hour"],
        "outdoor_temperature_C": tout,
        "time_weight_h_per_year": 1.0,
        "electricity_price_CNY_per_kWh_e": external["electricity_price_CNY_per_kWh_e"],
        "gas_price_CNY_per_kWh_LHV": gas_price,
        "electricity_carbon_kgCO2e_per_kWh_e": external["grid_carbon_factor_kgCO2e_per_kWh_e"],
        "gas_carbon_kgCO2e_per_kWh_LHV": gas_carbon,
        "gas_LHV_MJ_per_Nm3": lhv,
        "gas_LHV_kWh_per_Nm3": lhv_kwh,
        "source": external["natural_gas_price_source_id"].astype(str)
        + ";" + external["natural_gas_carbon_source_id"].astype(str),
        "parameter_status": external["natural_gas_price_parameter_status"].astype(str)
        + ";" + external["natural_gas_carbon_parameter_status"].astype(str),
        "data_version": DATA_VERSION,
    })
    external_v3.to_parquet(output / "external_timeseries.parquet", index=False)

    technologies = pd.read_csv(V00_CASE / "technologies.csv", encoding="utf-8-sig")
    technologies["source"] = "0823_static_parameters_with_provisional_v0_capacity_bounds"
    technologies["parameter_version"] = DATA_VERSION
    technologies["assumption_flag"] = "synthetic_test"
    technologies["parameter_status"] = "provisional_v0"
    technologies["engineering_approved"] = False
    technologies["shared_performance_id"] = ""
    technologies.loc[technologies["technology_id"].isin(["central_hp", "local_hp"]), "performance_model"] = "temperature_interpolated"
    technologies.loc[technologies["technology_id"].isin(["central_hp", "local_hp"]), "shared_performance_id"] = "ASHP_BASE_01"
    # Retained only because TechnologySpec validates a positive nominal COP;
    # hourly dispatch/electricity uses the provider coefficients, not this value.
    technologies.loc[technologies["technology_id"].isin(["central_hp", "local_hp"]), "fixed_cop"] = 3.2
    technologies.loc[technologies["technology_id"] == "central_boiler", "efficiency_LHV"] = 0.94
    technologies.loc[technologies["technology_id"] == "central_tes", "storage_charge_efficiency"] = 0.95
    technologies.loc[technologies["technology_id"] == "central_tes", "storage_discharge_efficiency"] = 0.95
    technologies.loc[technologies["technology_id"] == "central_tes", "storage_standing_loss_fraction_per_hour"] = 0.0060774
    technologies["storage_max_charge_ratio_per_hour"] = ""
    technologies["storage_max_discharge_ratio_per_hour"] = ""
    technologies.loc[technologies["technology_id"] == "central_tes", "storage_max_charge_ratio_per_hour"] = 0.25
    technologies.loc[technologies["technology_id"] == "central_tes", "storage_max_discharge_ratio_per_hour"] = 0.25
    district_peak = float(loads.groupby("timestamp")["heating_kW"].sum().max())
    building_peak = float(loads.groupby("building_id")["heating_kW"].max().max())
    technical_bound_factor = 1.10
    central_bound = technical_bound_factor * district_peak
    local_bound = technical_bound_factor * building_peak
    technologies.loc[technologies["technology_id"].isin(["central_hp", "central_boiler"]), "capacity_max_kW_th"] = central_bound
    technologies.loc[technologies["technology_id"] == "local_hp", "capacity_max_kW_th"] = local_bound
    technologies["source"] = technologies["source"].astype(str) + ";synthetic_v0.2_case_peak_x1.10_technical_bound"
    technologies.to_csv(output / "technologies.csv", index=False, encoding="utf-8-sig")

    shutil.copy2(DELIVERY / "06_equipment_performance.csv", output / "equipment_performance.csv")
    provider = TabularASHPPerformanceProvider(
        output / "equipment_performance.csv", parameter_version=DATA_VERSION
    )
    audit = provider.interpolate(tuple(tout))
    audit.insert(0, "season_hour", external["heating_season_hour"].astype(int))
    audit.insert(1, "timestamp", external["timestamp"])
    audit["Tsupply_C"] = 45.0
    audit["PLR_layer"] = 1.0
    audit["source_id"] = "ASHP_BASE_01"
    audit["parameter_version"] = DATA_VERSION
    audit.to_csv(output / "performance_hourly.csv", index=False, encoding="utf-8-sig")

    shutil.copy2(V00_CASE / "pipe_types.csv", output / "pipe_types.csv")
    pipes = pd.read_csv(output / "pipe_types.csv")
    pipes["heat_loss_kW_per_m"] = 0.0
    pipes["heat_loss_fraction_per_m"] = 0.00005
    pipes["pumping_kWh_e_per_kWh_th_transferred"] = 0.01
    pipe_caps = [0.40 * district_peak, 0.75 * district_peak, 1.10 * district_peak]
    pipes["capacity_max_kW_th"] = pipe_caps
    pipes["source"] = "synthetic_v0.2_pipe_loss_5pct_per_km;pump_1pct_of_transferred_heat"
    pipes["parameter_version"] = "synthetic_v0.2"
    pipes["engineering_approved"] = False
    pipes.to_csv(output / "pipe_types.csv", index=False)

    station_id = "synthetic_v02_station"
    spatial = build_provisional_geometric_network(
        buildings,
        loads,
        data_version=DATA_VERSION,
        site_id=station_id,
        local_extra_edge_count=2,
    )
    sites = spatial.sites.rename(columns={"candidate_source": "source"})
    sites["engineering_approved"] = False
    sites["assumption_flag"] = True
    sites.to_file(output / "candidate_sites.geojson", driver="GeoJSON")
    network = spatial.network.rename(columns={"candidate_source": "source"})
    network["engineering_approved"] = False
    network["assumption_flag"] = True
    network.to_file(output / "candidate_network.geojson", driver="GeoJSON")
    spatial.feasible_space.to_file(output / "roads_or_feasible_space.geojson", driver="GeoJSON")
    graph = nx.Graph()
    graph.add_edges_from(zip(network["node_from"], network["node_to"]))
    building_edges = int(((network["node_from"] != station_id) & (network["node_to"] != station_id)).sum())
    station_links = int(((network["node_from"] == station_id) | (network["node_to"] == station_id)).sum())
    if not nx.is_connected(graph) or building_edges == 0 or station_links >= 62:
        raise ValueError("synthetic candidate graph must be connected and contain building-building edges")

    config = yaml.safe_load((V00_CASE / "case_config.yaml").read_text(encoding="utf-8"))
    config["case_id"] = "v0_guanggu_62b_168h"
    config["scenario_id"] = "v02_2021_01_04_168h"
    config["data_version"] = DATA_VERSION
    config["contract_version"] = "competition_input_3.0.0-draft.2"
    config["files"]["building_archetype_map"] = "building_archetype_map.csv"
    config["demand"]["ventilation_system"] = "dedicated_fresh_air"
    config["demand"]["fresh_air_load_included"] = True
    config["planning"]["peak_capacity_margin_fraction"] = 0.0
    config["time"]["start"] = SOURCE_START.isoformat()
    config["time"]["end"] = SOURCE_END.isoformat()
    config["features"]["temperature_cop_enabled"] = True
    config["network"]["supply_temperature_C"] = 45
    config["network"]["return_temperature_C"] = 40
    config["performance"] = {
        "cop_model": "temperature_interpolated", "capacity_derating_model": "temperature_interpolated",
        "precompute_coefficients": True, "curve_file": "equipment_performance.csv",
        "performance_technology_id": "ASHP_BASE_01", "plr_layer": 1.0,
        "parameter_version": DATA_VERSION,
    }
    config["features"]["pipe_loss_enabled"] = True
    config["features"]["pumping_enabled"] = True
    config["network"]["loss_model"] = "linear_per_m"
    config["network"]["pumping_model"] = "linear_per_kWh_transferred"
    config["solver"]["time_limit_seconds"] = 900
    config["solver"]["mip_gap"] = 0.05
    (output / "case_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (output / "README.md").write_text(
        "# Guanggu V0.2 62-building 168-hour scale case\n\n"
        "Frozen delivery: 0823 guanggu-v0.3-20260823 buildings, loads, external series, terminals and ASHP/TES performance.\n"
        "The station, candidate network, pipe levels, capacity bounds, loss/pump coefficients and some economics remain synthetic_v0.2/provisional_v0.\n"
        "Straight-line projected edges are an algorithmic candidate network, not roads or an engineering plan.\n",
        encoding="utf-8",
    )
    print(f"gas_LHV: {lhv} MJ/Nm3 = {lhv_kwh:.9f} kWh_LHV/Nm3")
    print(f"gas price: {external['natural_gas_price_CNY_per_Nm3'].iloc[0]} CNY/Nm3 -> {gas_price.iloc[0]:.9f} CNY/kWh_LHV")
    print(f"gas carbon: {external['natural_gas_carbon_factor_kgCO2e_per_Nm3'].iloc[0]} kgCO2e/Nm3 -> {gas_carbon.iloc[0]:.9f} kgCO2e/kWh_LHV")
    print(f"Tout: {tout.min():.3f}..{tout.max():.3f} degC; out_of_range=0")
    print(f"network: nodes={graph.number_of_nodes()}, edges={graph.number_of_edges()}, building-building={building_edges}, station-links={station_links}")
    return output


if __name__ == "__main__":
    print(build_case())
