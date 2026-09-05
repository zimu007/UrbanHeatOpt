"""Build executable V0 cases from the accepted Guanggu v0.3 season snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any

import geopandas as gpd
import pandas as pd
import yaml

from urbanheatopt.data.adapters.guanggu_v03 import GuangguV03Adaptation
from urbanheatopt.data.adapters.provisional_v0 import load_provisional_v0_profile
from urbanheatopt.data.adapters.wuhan_v02_case import _write_pipe_types, _write_technologies
from urbanheatopt.data.canonical import CanonicalCaseData, V3_DRAFT_CONTRACT
from urbanheatopt.spatial.osm_corridor import (
    CANDIDATE_SOURCE as OSM_CANDIDATE_SOURCE,
    SPATIAL_STATUS as OSM_SPATIAL_STATUS,
    build_osm_corridor_network,
    write_osm_corridor_outputs,
)
from urbanheatopt.spatial.provisional import (
    build_multi_candidate_provisional_network,
    write_provisional_spatial_outputs,
)
from urbanheatopt.data.validation.v3_inputs import load_v3_case


class GuangguV03CaseError(ValueError):
    """The accepted v0.3 snapshot cannot be converted into a V0 debug case."""


@dataclass(frozen=True, slots=True)
class V03RunScope:
    profile: str
    building_ids: tuple[str, ...]
    timestamps: tuple[pd.Timestamp, ...]
    selection_rule: str
    peak_timestamp: str


@dataclass(frozen=True, slots=True)
class PreparedGuangguV03Case:
    case_dir: Path
    adaptation: GuangguV03Adaptation
    scope: V03RunScope
    canonical_case: CanonicalCaseData


def select_v03_run_scope(loads: pd.DataFrame, profile: str) -> V03RunScope:
    """Select deterministic 24 h, 168 h, or complete 2160 h model scope."""

    if profile not in {"v0-smoke", "v0-168h", "v0-full-season"}:
        raise GuangguV03CaseError(f"V0 v0.3 Builder 不支持 profile={profile}")
    required = {"timestamp", "building_id", "heating_kW"}
    if not required <= set(loads):
        raise GuangguV03CaseError(f"标准负荷缺少字段: {sorted(required - set(loads))}")
    table = loads[list(required)].copy()
    table["timestamp"] = pd.to_datetime(table["timestamp"], errors="raise")
    if table["timestamp"].dt.tz is None or str(table["timestamp"].dt.tz) != "Asia/Shanghai":
        raise GuangguV03CaseError("标准负荷 timestamp 必须为 Asia/Shanghai")
    annual = (
        table.groupby("building_id", sort=True)["heating_kW"]
        .sum()
        .sort_values(ascending=False, kind="stable")
    )
    annual = annual.reset_index().sort_values(
        ["heating_kW", "building_id"], ascending=[False, True], kind="stable"
    )
    all_buildings = tuple(sorted(table["building_id"].astype(str).unique()))
    selected_buildings = (
        all_buildings
        if profile == "v0-full-season"
        else tuple(annual.head(min(8, len(annual)))["building_id"].astype(str))
    )
    park_hourly = table.groupby("timestamp", sort=True)["heating_kW"].sum()
    peak_timestamp = pd.Timestamp(park_hourly[park_hourly.eq(park_hourly.max())].index[0])
    all_timestamps = tuple(pd.DatetimeIndex(sorted(table["timestamp"].unique())))
    if profile == "v0-smoke":
        chosen = tuple(
            timestamp
            for timestamp in all_timestamps
            if pd.Timestamp(timestamp).date() == peak_timestamp.date()
        )
        rule = "top_8_season_heat_then_full_park_peak_natural_day"
    elif profile == "v0-168h":
        peak_index = all_timestamps.index(peak_timestamp)
        start = max(0, min(peak_index - 84, len(all_timestamps) - 168))
        chosen = all_timestamps[start : start + 168]
        rule = "top_8_season_heat_then_168h_window_centered_on_full_park_peak"
    else:
        chosen = all_timestamps
        rule = "all_62_buildings_complete_2160h_heating_season"
    expected = {"v0-smoke": 24, "v0-168h": 168, "v0-full-season": 2160}[profile]
    if len(chosen) != expected or chosen != tuple(
        pd.date_range(chosen[0], periods=expected, freq="h")
    ):
        raise GuangguV03CaseError(f"{profile} 未生成严格连续的 {expected} 小时")
    return V03RunScope(
        profile=profile,
        building_ids=selected_buildings,
        timestamps=chosen,
        selection_rule=rule,
        peak_timestamp=peak_timestamp.isoformat(),
    )


def _case_config(
    assumptions: dict[str, Any],
    adaptation: GuangguV03Adaptation,
    scope: V03RunScope,
    *,
    osm_corridor: bool = False,
) -> dict[str, Any]:
    planning = assumptions["planning"]
    economics = assumptions["economics"]
    solver = dict(assumptions["solver"])
    complete = scope.profile == "v0-full-season"
    if complete:
        solver["time_limit_seconds"] = 600
    return {
        "contract_version": V3_DRAFT_CONTRACT,
        "software_release_track": "test_v0",
        "case_id": "guanggu_v03",
        "scenario_id": scope.profile.replace("-", "_"),
        "data_version": adaptation.canonical_data.data_version,
        "data_classification": "formal_project_loads_with_provisional_system_assumptions",
        "time": {
            "start": scope.timestamps[0].isoformat(),
            "end": (scope.timestamps[-1] + pd.Timedelta(hours=1)).isoformat(),
            "timezone": "Asia/Shanghai",
            "frequency": "1h",
            "interval": "start_inclusive_end_exclusive",
            "complete_heating_season": complete,
        },
        "units": {
            "heating_power": "kW_th", "heating_energy": "kWh_th",
            "electric_power": "kW_e", "electric_energy": "kWh_e",
            "gas_energy": "kWh_LHV", "storage_energy": "kWh_th",
            "currency": "CNY", "annual_cost": "CNY_per_year",
            "carbon": "kgCO2e_per_year", "electricity_price": "CNY_per_kWh_e",
            "gas_price": "CNY_per_kWh_LHV",
            "electricity_carbon_intensity": "kgCO2e_per_kWh_e",
            "gas_carbon_intensity": "kgCO2e_per_kWh_LHV",
            "time_weight": "h_per_year", "length": "m", "temperature": "degC",
        },
        "crs": {"input": "EPSG:4326", "projected": "EPSG:32650"},
        "files": {
            "buildings": "buildings.geojson",
            "building_archetype_map": "building_archetype_map.csv",
            "building_hourly_loads": "building_hourly_loads.parquet",
            "technologies": "technologies.csv",
            "roads_or_feasible_space": "roads_or_feasible_space.geojson",
            "external_timeseries": "external_timeseries.parquet",
            "pipe_types": "pipe_types.csv",
            "candidate_sites": "candidate_sites.geojson",
            "candidate_network": "candidate_network.geojson",
        },
        "run": {"profile": scope.profile, "modes": ["central", "distributed", "hybrid"]},
        "spatial": {
            "input_mode": "roads" if osm_corridor else "feasible_space",
            "candidate_source": "provided",
            "candidate_site_count_min": 5, "candidate_site_count_max": 5,
            "max_built_sites": 1,
            "road_constrained": osm_corridor,
            "greenbelt_alignment_assumed": osm_corridor,
            "construction_feasibility_verified": False,
            "spatial_status": (
                OSM_SPATIAL_STATUS if osm_corridor else "PROVISIONAL_ALGORITHM_VALIDATION"
            ),
        },
        "demand": {
            "area_scaling_already_applied": True, "includes_dhw": False,
            "includes_cooling": False, "ventilation_system": "dedicated_fresh_air",
            "fresh_air_load_included": True,
        },
        "features": {
            "storage_enabled": True, "temperature_cop_enabled": True,
            "pipe_loss_enabled": False, "pumping_enabled": False,
            "waste_heat_enabled": False,
        },
        "network": {
            "supply_temperature_C": 45, "return_temperature_C": 40,
            "pipe_level_count": 3, "loss_model": "disabled_for_v0",
            "pumping_model": "disabled_for_v0",
        },
        "planning": {
            "discount_rate": planning["discount_rate"],
            "price_base_year": planning["price_base_year"], "currency": "CNY",
            "unserved_policy": "penalized_for_v0",
            "peak_capacity_margin_fraction": planning["peak_capacity_margin_fraction"],
            "carbon_price_scenarios_CNY_per_tCO2e": [0, 50, 100, 150],
        },
        "economics": dict(economics),
        "performance": {
            "cop_model": "temperature_interpolated",
            "capacity_derating_model": "temperature_interpolated",
            "precompute_coefficients": True,
            "curve_file": "equipment_performance.csv",
            "performance_technology_id": "ASHP_BASE_01",
            "plr_layer": 1.0,
            "parameter_version": "0821-lhv-patch-20260823",
            "performance_boundary_policy": "clip_with_flag",
        },
        "pareto": {
            "method": "epsilon_constraint", "point_count": 5,
            "run_epsilon_scan": scope.profile == "v0-smoke",
            "second_objective": "annual_operating_physical_carbon",
            "knee_method": "normalized_max_distance_to_endpoint_chord",
            "topsis_enabled": False,
        },
        "enabled_technology_ids": ["central_hp", "central_boiler", "local_hp", "central_tes"],
        "solver": {**solver, "load_solution_only_if_optimal": True},
        "qa": {
            "balance_tolerance_kW": 1e-6, "unserved_tolerance_kWh": 1e-6,
            "cost_tolerance_CNY_per_year": 1e-6,
            "carbon_tolerance_kgCO2e_per_year": 1e-6,
            "deterministic_tolerance": 1e-9,
        },
    }


def prepare_guanggu_v03_v0_case(
    adaptation: GuangguV03Adaptation,
    work_dir: str | Path,
    *,
    assumption_profile: str = "provisional_v0",
    profile: str = "v0-smoke",
    spatial_profile: str = "provisional_geometric",
    osm_snapshot_path: str | Path | None = None,
) -> PreparedGuangguV03Case:
    """Create a self-contained executable V0 case without touching source data."""

    if assumption_profile != "provisional_v0":
        raise GuangguV03CaseError("当前只支持 assumption_profile=provisional_v0")
    if spatial_profile not in {"provisional_geometric", "osm_main_road_provisional"}:
        raise GuangguV03CaseError(f"不支持 spatial_profile={spatial_profile}")
    if spatial_profile == "osm_main_road_provisional" and osm_snapshot_path is None:
        raise GuangguV03CaseError("OSM主干路走廊必须提供冻结的osm_snapshot_path")
    root = Path(work_dir).resolve()
    if root.exists() and any(root.iterdir()):
        raise GuangguV03CaseError(f"工作目录必须为空或不存在: {root}")
    root.mkdir(parents=True, exist_ok=True)
    assumptions = load_provisional_v0_profile()
    data = adaptation.canonical_data
    buildings = data.buildings
    mapping = data.building_archetype_map
    loads = data.loads
    external = data.external_timeseries
    scope = select_v03_run_scope(loads, profile)
    selected_ids = set(scope.building_ids)
    selected_times = set(scope.timestamps)
    case_dir = root / "case"
    case_dir.mkdir()
    selected_buildings = buildings[buildings["building_id"].isin(selected_ids)].sort_values("building_id")
    selected_mapping = mapping[mapping["building_id"].isin(selected_ids)].sort_values(["building_id", "zone_id"])
    selected_loads = loads[
        loads["building_id"].isin(selected_ids) & loads["timestamp"].isin(selected_times)
    ].sort_values(["timestamp", "building_id"], kind="stable")
    selected_external = external[external["timestamp"].isin(selected_times)].sort_values("timestamp", kind="stable")
    expected = len(scope.building_ids) * len(scope.timestamps)
    if len(selected_loads) != expected or len(selected_external) != len(scope.timestamps):
        raise GuangguV03CaseError("V0 范围筛选后的负荷或外部时序行数不一致")
    selected_buildings.to_file(case_dir / "buildings.geojson", driver="GeoJSON")
    selected_mapping.to_csv(case_dir / "building_archetype_map.csv", index=False, encoding="utf-8")
    selected_loads.to_parquet(case_dir / "building_hourly_loads.parquet", index=False)
    selected_external.to_parquet(case_dir / "external_timeseries.parquet", index=False)
    shutil.copy2(adaptation.equipment_performance_path, case_dir / "equipment_performance.csv")

    osm_corridor = spatial_profile == "osm_main_road_provisional"
    if osm_corridor:
        spatial = build_osm_corridor_network(
            selected_buildings,
            selected_loads,
            osm_snapshot_path,
            data_version=data.data_version,
            candidate_count=5,
        )
        write_osm_corridor_outputs(spatial, case_dir)
    else:
        spatial = build_multi_candidate_provisional_network(
            selected_buildings,
            selected_loads,
            data_version=data.data_version,
            candidate_count=5,
            input_building_source="guanggu_v03_03_buildings",
        )
        write_provisional_spatial_outputs(spatial, case_dir)
    selected_peak = float(selected_loads.groupby("timestamp")["heating_kW"].sum().max())
    _write_technologies(case_dir / "technologies.csv", assumptions, selected_peak_kW=selected_peak)
    technology_table = pd.read_csv(case_dir / "technologies.csv", encoding="utf-8")
    hp = technology_table["technology_type"].eq("air_source_heat_pump")
    technology_table.loc[hp, "performance_model"] = "temperature_interpolated"
    technology_table.to_csv(case_dir / "technologies.csv", index=False, encoding="utf-8")
    _write_pipe_types(case_dir / "pipe_types.csv", assumptions, selected_peak_kW=selected_peak)
    config = _case_config(assumptions, adaptation, scope, osm_corridor=osm_corridor)
    (case_dir / "case_config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    assumption_record = {
        "classification": "scenario_assumption",
        "formal_use_allowed": False,
        "external_timeseries_authority": "guanggu_v03_external_timeseries",
        "external_energy_values_overridden": False,
        "parameter_version": assumptions["parameter_version"],
        "technologies": assumptions["technologies"],
        "storage": assumptions["storage"],
        "pipe_types": assumptions["pipe_types"],
        "economics": assumptions["economics"],
        "planning": assumptions["planning"],
        "solver_execution": {
            "name": config["solver"]["name"],
            "threads": config["solver"]["threads"],
            "time_limit_seconds": config["solver"]["time_limit_seconds"],
            "mip_gap": config["solver"]["mip_gap"],
            "random_seed": config["solver"]["random_seed"],
            "classification": "computational_configuration",
            "physical_or_economic_assumption": False,
        },
        "case_scope": {
            "profile": profile, "selection_rule": scope.selection_rule,
            "selected_building_count": len(scope.building_ids),
            "selected_building_ids": list(scope.building_ids),
            "selected_hour_count": len(scope.timestamps),
            "full_park_peak_timestamp": scope.peak_timestamp,
            "selected_peak_kW_th": selected_peak,
            "candidate_source": (
                OSM_CANDIDATE_SOURCE if osm_corridor else spatial.candidate_source
            ),
            "road_constrained": osm_corridor,
            "greenbelt_alignment_assumed": osm_corridor,
            "construction_feasibility_verified": False,
            "spatial_status": (
                OSM_SPATIAL_STATUS
                if osm_corridor
                else "PROVISIONAL_ALGORITHM_VALIDATION"
            ),
            "osm_snapshot_sha256": (
                spatial.metadata["osm_snapshot_sha256"] if osm_corridor else None
            ),
            "engineering_use_allowed": False,
        },
    }
    (case_dir / "assumptions_used.yaml").write_text(
        yaml.safe_dump(assumption_record, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    for path in (
        adaptation.source_report_path,
        adaptation.canonical_report_path,
        adaptation.adaptation_report_path,
        adaptation.field_mapping_path,
        adaptation.timestamp_hour_map_path,
    ):
        shutil.copy2(path, case_dir / path.name)
    canonical = load_v3_case(case_dir, profile=profile)
    return PreparedGuangguV03Case(case_dir, adaptation, scope, canonical)
