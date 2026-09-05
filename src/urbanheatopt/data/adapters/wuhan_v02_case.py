"""Prepare the deterministic Guanggu v0.2 peak-day V0 case for the new core."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

import geopandas as gpd
import pandas as pd
import yaml

from urbanheatopt.data.adapters.provisional_v0 import load_provisional_v0_profile
from urbanheatopt.data.adapters.wuhan_v02 import WuhanV02Adaptation, adapt_wuhan_v02_sources
from urbanheatopt.data.canonical import CanonicalCaseData, V3_DRAFT_CONTRACT
from urbanheatopt.spatial.provisional import (
    build_provisional_geometric_network,
    write_provisional_spatial_outputs,
)
from urbanheatopt.data.validation.v3_inputs import load_v3_case


class WuhanV02CaseError(ValueError):
    """The source delivery cannot be converted into the approved V0 case."""


@dataclass(frozen=True, slots=True)
class SmokeScope:
    building_ids: tuple[str, ...]
    timestamps: tuple[pd.Timestamp, ...]
    peak_day: str


@dataclass(frozen=True, slots=True)
class PreparedWuhanV02Case:
    case_dir: Path
    source_adaptation: WuhanV02Adaptation
    scope: SmokeScope
    canonical_case: CanonicalCaseData


def select_v0_smoke_scope(
    hourly_loads: pd.DataFrame,
    *,
    building_count: int = 8,
) -> SmokeScope:
    required = {"timestamp", "building_id", "heating_kW"}
    missing = sorted(required - set(hourly_loads.columns))
    if missing:
        raise WuhanV02CaseError("负荷缺少字段：" + ", ".join(missing))
    if building_count < 1:
        raise WuhanV02CaseError("building_count 必须大于 0")
    loads = hourly_loads[list(required)].copy()
    loads["timestamp"] = pd.to_datetime(loads["timestamp"], errors="raise")
    if loads["timestamp"].dt.tz is None:
        raise WuhanV02CaseError("timestamp 必须带 Asia/Shanghai 时区")
    if str(loads["timestamp"].dt.tz) != "Asia/Shanghai":
        raise WuhanV02CaseError("timestamp 时区必须是 Asia/Shanghai")
    loads["heating_kW"] = pd.to_numeric(loads["heating_kW"], errors="raise")
    annual = (
        loads.groupby("building_id", as_index=False, sort=True)["heating_kW"]
        .sum()
        .rename(columns={"heating_kW": "annual_heating_kWh_th"})
        .sort_values(
            ["annual_heating_kWh_th", "building_id"],
            ascending=[False, True],
            kind="stable",
        )
    )
    selected = tuple(annual.head(min(building_count, len(annual)))["building_id"].astype(str))
    park_hourly = loads.groupby("timestamp", sort=True)["heating_kW"].sum()
    peak_timestamp = park_hourly[park_hourly.eq(park_hourly.max())].index[0]
    peak_date = peak_timestamp.date()
    timestamps = tuple(
        sorted(loads.loc[loads["timestamp"].dt.date.eq(peak_date), "timestamp"].unique())
    )
    if len(timestamps) != 24 or timestamps != tuple(
        pd.date_range(timestamps[0], periods=24, freq="h")
    ):
        raise WuhanV02CaseError("全园区峰值自然日必须具有连续 24 小时")
    return SmokeScope(selected, timestamps, peak_date.isoformat())


def _write_technologies(
    path: Path,
    assumptions: dict[str, object],
    *,
    selected_peak_kW: float,
) -> None:
    planning = assumptions["planning"]
    margin_peak = selected_peak_kW * (1 + float(planning["peak_capacity_margin_fraction"]))
    devices = assumptions["technologies"]
    storage = assumptions["storage"]
    columns = [
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
    version = str(assumptions["parameter_version"])
    source = "provisional_v0"
    flag = "scenario_assumption"

    def generator_row(key: str, tech_type: str, scope: str, carrier: str) -> list[object]:
        spec = devices[key]
        is_hp = tech_type == "air_source_heat_pump"
        return [
            spec["technology_id"], tech_type, scope, carrier,
            "fixed_for_v0" if is_hp else "fixed_efficiency",
            spec["fixed_COP"] if is_hp else "",
            "" if is_hp else spec["efficiency_LHV"],
            0.0,
            margin_peak * float(spec["capacity_max_multiplier_of_margin_peak"]),
            spec["capex_CNY_per_kW_th"], spec["fixed_om_fraction_per_year"],
            spec["variable_om_CNY_per_kWh_th"], spec["lifetime_years"],
            source, version, flag, *blank_storage,
        ]

    rows = [
        generator_row("central_heat_pump", "air_source_heat_pump", "central", "electricity"),
        generator_row("central_gas_boiler", "gas_boiler", "central", "gas"),
        generator_row("distributed_heat_pump", "air_source_heat_pump", "local", "electricity"),
        [
            storage["technology_id"], "water_thermal_storage", "central", "thermal",
            "linear_soc", "", "", 0.0, 1.0, 0.0, 0.0, 0.0,
            storage["lifetime_years"], source, version, flag,
            selected_peak_kW * float(storage["energy_hours_at_selected_peak"]),
            selected_peak_kW * float(storage["charge_power_multiplier_of_selected_peak"]),
            selected_peak_kW * float(storage["discharge_power_multiplier_of_selected_peak"]),
            storage["charge_efficiency"], storage["discharge_efficiency"],
            storage["standing_loss_fraction_per_hour"], storage["capex_CNY_per_kWh_th"],
            storage["power_capex_CNY_per_kW_th"], storage["fixed_capex_CNY"],
        ],
    ]
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False, encoding="utf-8")


def _write_pipe_types(
    path: Path,
    assumptions: dict[str, object],
    *,
    selected_peak_kW: float,
) -> None:
    margin = float(assumptions["planning"]["peak_capacity_margin_fraction"])
    margin_peak = selected_peak_kW * (1 + margin)
    rows = [
        {
            "pipe_type_id": item["pipe_type_id"],
            "level": item["level"],
            "capacity_max_kW_th": margin_peak * float(item["capacity_multiplier_of_margin_peak"]),
            "capex_CNY_per_m": item["capex_CNY_per_m"],
            "heat_loss_kW_per_m": 0.0,
            "pumping_kWh_e_per_kWh_th_transferred": 0.0,
            "lifetime_years": item["lifetime_years"],
            "source": "provisional_v0",
            "parameter_version": assumptions["parameter_version"],
        }
        for item in assumptions["pipe_types"]
    ]
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")


def _case_config(
    assumptions: dict[str, object],
    adaptation: WuhanV02Adaptation,
    scope: SmokeScope,
) -> dict[str, object]:
    terminal = assumptions["terminal"]
    planning = assumptions["planning"]
    economics = assumptions["economics"]
    solver = assumptions["solver"]
    return {
        "contract_version": V3_DRAFT_CONTRACT,
        "software_release_track": "test_v0",
        "case_id": "guanggu_v02",
        "scenario_id": "v0_smoke_peak_day",
        "data_version": adaptation.data_version,
        "data_classification": "formal_project_data",
        "time": {
            "start": scope.timestamps[0].isoformat(),
            "end": (scope.timestamps[-1] + pd.Timedelta(hours=1)).isoformat(),
            "timezone": "Asia/Shanghai", "frequency": "1h",
            "interval": "start_inclusive_end_exclusive", "complete_heating_season": False,
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
            "pipe_types": "pipe_types.csv", "candidate_sites": "candidate_sites.geojson",
            "candidate_network": "candidate_network.geojson",
        },
        "run": {"profile": "v0-smoke", "modes": ["central", "distributed", "hybrid"]},
        "spatial": {"input_mode": "feasible_space", "candidate_source": "provided", "candidate_site_count_min": 1, "candidate_site_count_max": 1, "max_built_sites": 1},
        "demand": {"area_scaling_already_applied": True, "includes_dhw": False, "includes_cooling": False, "ventilation_system": terminal["ventilation_system"], "fresh_air_load_included": terminal["fresh_air_load_included"]},
        "features": {"storage_enabled": True, "temperature_cop_enabled": False, "pipe_loss_enabled": False, "pumping_enabled": False, "waste_heat_enabled": False},
        "network": {"supply_temperature_C": terminal["supply_temperature_C"], "return_temperature_C": terminal["return_temperature_C"], "pipe_level_count": 3, "loss_model": "disabled_for_v0", "pumping_model": "disabled_for_v0"},
        "planning": {"discount_rate": planning["discount_rate"], "price_base_year": planning["price_base_year"], "currency": "CNY", "unserved_policy": "penalized_for_v0", "peak_capacity_margin_fraction": planning["peak_capacity_margin_fraction"], "carbon_price_scenarios_CNY_per_tCO2e": [0, 50, 100, 150]},
        "economics": {**economics},
        "performance": {"cop_model": "fixed_for_v0", "capacity_derating_model": "disabled_for_v0", "precompute_coefficients": True},
        "pareto": {"method": "epsilon_constraint", "point_count": assumptions["pareto"]["v0_point_count"], "second_objective": "annual_operating_physical_carbon", "knee_method": "normalized_max_distance_to_endpoint_chord", "topsis_enabled": False},
        "enabled_technology_ids": [
            assumptions["technologies"]["central_heat_pump"]["technology_id"],
            assumptions["technologies"]["central_gas_boiler"]["technology_id"],
            assumptions["technologies"]["distributed_heat_pump"]["technology_id"],
            assumptions["storage"]["technology_id"],
        ],
        "solver": {**solver, "load_solution_only_if_optimal": True},
        "qa": {"balance_tolerance_kW": 1e-6, "unserved_tolerance_kWh": 1e-6, "cost_tolerance_CNY_per_year": 1e-6, "carbon_tolerance_kgCO2e_per_year": 1e-6, "deterministic_tolerance": 1e-9},
    }


def prepare_wuhan_v02_v0_case(
    delivery_root: str | Path,
    work_dir: str | Path,
    *,
    source_profile: str = "wuhan_v02",
    assumption_profile: str = "provisional_v0",
    profile: str = "v0-smoke",
) -> PreparedWuhanV02Case:
    """Audit all source data and prepare an 8-building, 24-hour new-core case."""

    if source_profile != "wuhan_v02":
        raise WuhanV02CaseError("当前只支持 source_profile=wuhan_v02")
    if assumption_profile != "provisional_v0":
        raise WuhanV02CaseError("当前只支持 assumption_profile=provisional_v0")
    if profile != "v0-smoke":
        raise WuhanV02CaseError(
            "v1-full 缺少正式06设备性能、温度COP、道路网络、价格和碳因子，禁止使用V0假设"
        )
    delivery = Path(delivery_root).resolve()
    root = Path(work_dir).resolve()
    if root == delivery or root.is_relative_to(delivery):
        raise WuhanV02CaseError("工作目录不得位于源交付目录内部")
    if root.exists() and any(root.iterdir()):
        raise WuhanV02CaseError("工作目录必须为空或不存在")
    root.mkdir(parents=True, exist_ok=True)
    adapted = adapt_wuhan_v02_sources(delivery, root / "full_adaptation", full_audit=True)
    assumptions = load_provisional_v0_profile()

    buildings = gpd.read_file(adapted.buildings_path)
    mapping = pd.read_csv(adapted.archetype_map_path, encoding="utf-8-sig")
    loads = pd.read_parquet(adapted.loads_path)
    external = pd.read_parquet(adapted.external_timeseries_path)
    scope = select_v0_smoke_scope(loads)
    selected = set(scope.building_ids)
    timestamp_set = set(scope.timestamps)
    case_dir = root / "case"
    case_dir.mkdir()
    selected_buildings = buildings[buildings["building_id"].isin(selected)].sort_values("building_id")
    selected_mapping = mapping[mapping["building_id"].isin(selected)].sort_values(["building_id", "zone_id"])
    selected_loads = loads[
        loads["building_id"].isin(selected) & loads["timestamp"].isin(timestamp_set)
    ].sort_values(["timestamp", "building_id"], kind="stable")
    selected_external = external[external["timestamp"].isin(timestamp_set)].sort_values(
        "timestamp", kind="stable"
    )
    if len(selected_buildings) != 8 or len(selected_loads) != 8 * 24 or len(selected_external) != 24:
        raise WuhanV02CaseError("V0 范围必须稳定生成 8 栋×24 小时输入")
    selected_buildings.to_file(case_dir / "buildings.geojson", driver="GeoJSON")
    selected_mapping.to_csv(case_dir / "building_archetype_map.csv", index=False, encoding="utf-8")
    selected_loads.to_parquet(case_dir / "building_hourly_loads.parquet", index=False)
    selected_external.to_parquet(case_dir / "external_timeseries.parquet", index=False)
    spatial = build_provisional_geometric_network(
        selected_buildings,
        selected_loads,
        data_version=adapted.data_version,
    )
    write_provisional_spatial_outputs(spatial, case_dir)
    selected_peak = float(
        selected_loads.groupby("timestamp")["heating_kW"].sum().max()
    )
    _write_technologies(case_dir / "technologies.csv", assumptions, selected_peak_kW=selected_peak)
    _write_pipe_types(case_dir / "pipe_types.csv", assumptions, selected_peak_kW=selected_peak)
    config = _case_config(assumptions, adapted, scope)
    (case_dir / "case_config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    assumption_record = dict(assumptions)
    assumption_record["case_scope"] = {
        "classification": "weighted_period_test",
        "selection_rule": "top_8_annual_heating_then_full_park_peak_natural_day",
        "selected_building_ids": list(scope.building_ids),
        "selected_peak_day": scope.peak_day,
        "selected_peak_kW_th": selected_peak,
        "candidate_source": spatial.candidate_source,
        "road_constrained": False,
        "construction_feasibility_verified": False,
    }
    (case_dir / "assumptions_used.yaml").write_text(
        yaml.safe_dump(assumption_record, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    for path in (
        adapted.source_report_path,
        adapted.adaptation_report_path,
        adapted.field_mapping_path,
    ):
        shutil.copy2(path, case_dir / path.name)
    canonical = load_v3_case(case_dir, profile="v0-smoke")
    return PreparedWuhanV02Case(case_dir, adapted, scope, canonical)
