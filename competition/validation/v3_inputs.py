"""Read-only loader for competition_input_3.0.0-draft.1."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import geopandas as gpd
from jsonschema import Draft202012Validator, FormatChecker
import numpy as np
import pandas as pd
import yaml

from competition.canonical import (
    CANONICAL_MODES,
    TEST_RELEASE_TRACK,
    V3_DRAFT_CONTRACT,
    CanonicalCaseData,
    PipeTypeSpec,
    StorageSpec,
)
from competition.core_model import EconomicInput, SegmentSpec, TechnologySpec
from competition.solvers import SolverSettings


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "competition" / "schemas" / "case_config_v3.schema.json"


class V3InputError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = tuple(errors)
        super().__init__("V3 输入校验失败：\n- " + "\n- ".join(errors))


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.nodes.MappingNode, deep: bool = False) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"YAML 重复键：{key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite(value: object, field: str, *, minimum: float | None = None, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} 必须是有限数值")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是有限数值") from exc
    if not np.isfinite(result):
        raise ValueError(f"{field} 必须是有限数值")
    if positive and result <= 0:
        raise ValueError(f"{field} 必须大于 0")
    if minimum is not None and result < minimum:
        raise ValueError(f"{field} 必须大于等于 {minimum}")
    return result


def _clean_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} 必须是无首尾空白的非空字符串")
    return value


def _load_config(case_dir: Path) -> dict[str, Any]:
    path = case_dir / "case_config.yaml"
    if not path.is_file():
        raise V3InputError(["缺少 case_config.yaml"])
    try:
        config = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except Exception as exc:
        raise V3InputError([f"case_config.yaml 无法解析：{exc}"]) from exc
    if not isinstance(config, dict):
        raise V3InputError(["case_config.yaml 顶层必须是映射对象"])
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = [
        f"{'.'.join(map(str, error.path)) or '$'}：{error.message}"
        for error in sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(config),
            key=lambda item: list(item.path),
        )
    ]
    if errors:
        raise V3InputError(errors)
    return config


def _configured_files(case_dir: Path, config: dict[str, Any]) -> dict[str, Path]:
    paths = {"case_config.yaml": case_dir / "case_config.yaml"}
    for value in config["files"].values():
        if isinstance(value, str):
            paths[value] = case_dir / value
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise V3InputError(["缺少配置文件：" + ", ".join(sorted(missing))])
    return paths


def _read_buildings(path: Path, config: dict[str, Any]) -> gpd.GeoDataFrame:
    buildings = gpd.read_file(path)
    required = {"building_id", "use_type", "heated_area_m2", "archetype_id", "terminal_type", "data_version", "geometry"}
    missing = sorted(required - set(buildings.columns))
    if missing:
        raise V3InputError(["buildings.geojson 缺少字段：" + ", ".join(missing)])
    if str(buildings.crs) != config["crs"]["input"]:
        raise V3InputError([f"buildings.geojson CRS 必须为 {config['crs']['input']}"])
    if buildings.empty or buildings["building_id"].duplicated().any():
        raise V3InputError(["buildings.geojson 必须包含至少一栋且 building_id 唯一"])
    if not buildings.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all() or not buildings.geometry.is_valid.all():
        raise V3InputError(["buildings.geojson 必须全部为有效 Polygon/MultiPolygon"])
    for value in buildings["building_id"]:
        _clean_id(value, "building_id")
    if not buildings["terminal_type"].isin(["floor_radiant", "fan_coil"]).all():
        raise V3InputError(["terminal_type 只能是 floor_radiant 或 fan_coil"])
    if set(buildings["data_version"]) != {config["data_version"]}:
        raise V3InputError(["buildings.geojson data_version 与案例不一致"])
    areas = pd.to_numeric(buildings["heated_area_m2"], errors="coerce")
    if areas.isna().any() or (areas <= 0).any():
        raise V3InputError(["heated_area_m2 必须是有限正数"])
    return buildings


def _read_time_tables(load_path: Path, external_path: Path, config: dict[str, Any], building_ids: tuple[str, ...]) -> tuple[pd.DataFrame, pd.DataFrame, tuple[pd.Timestamp, ...]]:
    loads = pd.read_parquet(load_path)
    external = pd.read_parquet(external_path)
    load_required = {"timestamp", "building_id", "heating_kW", "data_version"}
    external_required = {
        "timestamp", "outdoor_temperature_C", "time_weight_h_per_year",
        "electricity_price_CNY_per_kWh_e", "gas_price_CNY_per_kWh_LHV",
        "electricity_carbon_kgCO2e_per_kWh_e", "gas_carbon_kgCO2e_per_kWh_LHV",
        "data_version",
    }
    missing_load = sorted(load_required - set(loads.columns))
    missing_external = sorted(external_required - set(external.columns))
    if missing_load or missing_external:
        errors = []
        if missing_load:
            errors.append("building_hourly_loads.parquet 缺少字段：" + ", ".join(missing_load))
        if missing_external:
            errors.append("external_timeseries.parquet 缺少字段：" + ", ".join(missing_external))
        raise V3InputError(errors)
    for frame, name in ((loads, "负荷"), (external, "外部时序")):
        if not isinstance(frame["timestamp"].dtype, pd.DatetimeTZDtype):
            raise V3InputError([f"{name} timestamp 必须带时区"])
        if str(frame["timestamp"].dt.tz) != "Asia/Shanghai":
            raise V3InputError([f"{name} timestamp 必须为 Asia/Shanghai"])
        if set(frame["data_version"]) != {config["data_version"]}:
            raise V3InputError([f"{name} data_version 与案例不一致"])
    timestamps = tuple(external["timestamp"])
    if not timestamps or len(set(timestamps)) != len(timestamps):
        raise V3InputError(["外部时序 timestamp 必须非空且唯一"])
    expected = tuple(pd.date_range(config["time"]["start"], config["time"]["end"], freq="h", inclusive="left"))
    if timestamps != expected:
        raise V3InputError(["外部时序必须与 time.start/end 的逐时范围完全一致"])
    if tuple(dict.fromkeys(loads["timestamp"])) != timestamps:
        raise V3InputError(["负荷 timestamp 覆盖和顺序必须与外部时序一致"])
    if set(loads["building_id"]) != set(building_ids):
        raise V3InputError(["负荷 building_id 集合必须与 buildings.geojson 完全一致"])
    if loads.duplicated(["timestamp", "building_id"]).any():
        raise V3InputError(["负荷 timestamp + building_id 不得重复"])
    expected_rows = len(timestamps) * len(building_ids)
    if len(loads) != expected_rows:
        raise V3InputError(["每栋建筑必须具有完全相同的逐时覆盖"])
    values = pd.to_numeric(loads["heating_kW"], errors="coerce")
    if values.isna().any() or not np.isfinite(values).all() or (values < 0).any():
        raise V3InputError(["heating_kW 必须是有限非负数"])
    for column in external_required - {"timestamp", "data_version"}:
        numeric = pd.to_numeric(external[column], errors="coerce")
        if numeric.isna().any() or not np.isfinite(numeric).all():
            raise V3InputError([f"{column} 必须是有限数值"])
        if column != "outdoor_temperature_C" and (numeric < 0).any():
            raise V3InputError([f"{column} 不得为负"])
    if (external["time_weight_h_per_year"] <= 0).any():
        raise V3InputError(["time_weight_h_per_year 必须大于 0"])
    return loads, external, timestamps


def _read_technologies(path: Path, config: dict[str, Any]) -> tuple[tuple[TechnologySpec, ...], StorageSpec, dict[str, str]]:
    table = pd.read_csv(path, encoding="utf-8-sig", keep_default_na=False)
    required = {
        "technology_id", "technology_type", "applicable_scope", "energy_carrier",
        "performance_model", "fixed_cop", "efficiency_LHV", "capacity_min_kW_th",
        "capacity_max_kW_th", "capex_CNY_per_kW_th", "fixed_om_fraction_per_year",
        "variable_om_CNY_per_kWh_th", "lifetime_years", "source", "parameter_version",
        "assumption_flag", "storage_energy_capacity_max_kWh_th",
        "storage_charge_capacity_max_kW_th", "storage_discharge_capacity_max_kW_th",
        "storage_charge_efficiency", "storage_discharge_efficiency",
        "storage_standing_loss_fraction_per_hour", "storage_capex_CNY_per_kWh_th",
        "storage_power_capex_CNY_per_kW_th", "storage_fixed_capex_CNY",
    }
    missing = sorted(required - set(table.columns))
    if missing:
        raise V3InputError(["technologies.csv 缺少字段：" + ", ".join(missing)])
    if table["technology_id"].duplicated().any():
        raise V3InputError(["technology_id 不得重复"])
    enabled = table[table["technology_id"].isin(config["enabled_technology_ids"])].copy()
    if set(enabled["technology_id"]) != set(config["enabled_technology_ids"]):
        raise V3InputError(["enabled_technology_ids 必须全部存在于 technologies.csv"])
    storage_rows = enabled[enabled["technology_type"].eq("water_thermal_storage")]
    if len(storage_rows) != 1:
        raise V3InputError(["必须启用且仅启用一个 water_thermal_storage"])
    generation = enabled[~enabled["technology_type"].eq("water_thermal_storage")]
    specs: list[TechnologySpec] = []
    versions: dict[str, str] = {}
    for _, row in generation.iterrows():
        tid = _clean_id(row["technology_id"], "technology_id")
        cop = None if row["fixed_cop"] == "" else _finite(row["fixed_cop"], f"{tid}.fixed_cop", positive=True)
        efficiency = None if row["efficiency_LHV"] == "" else _finite(row["efficiency_LHV"], f"{tid}.efficiency_LHV", positive=True)
        if efficiency is not None and efficiency > 1:
            raise V3InputError([f"{tid}.efficiency_LHV 必须小于等于 1"])
        lifetime = int(_finite(row["lifetime_years"], f"{tid}.lifetime_years", positive=True))
        spec = TechnologySpec(
            technology_id=tid,
            technology_type=str(row["technology_type"]),
            applicable_scope=str(row["applicable_scope"]),
            energy_carrier=str(row["energy_carrier"]),
            cop=cop,
            efficiency=efficiency,
            capacity_min_kW=_finite(row["capacity_min_kW_th"], f"{tid}.capacity_min", minimum=0),
            capacity_max_kW=_finite(row["capacity_max_kW_th"], f"{tid}.capacity_max", positive=True),
            capex_CNY_per_kW=_finite(row["capex_CNY_per_kW_th"], f"{tid}.capex", minimum=0),
            fixed_maintenance_fraction_per_year=_finite(row["fixed_om_fraction_per_year"], f"{tid}.fixed_om", minimum=0),
            variable_om_CNY_per_kWh_th=_finite(row["variable_om_CNY_per_kWh_th"], f"{tid}.variable_om", minimum=0),
            lifetime_years=lifetime,
            source=_clean_id(row["source"], f"{tid}.source"),
            assumption_flag=_clean_id(row["assumption_flag"], f"{tid}.assumption_flag"),
        )
        specs.append(spec)
        versions[tid] = _clean_id(row["parameter_version"], f"{tid}.parameter_version")
    storage_row = storage_rows.iloc[0]
    storage_id = _clean_id(storage_row["technology_id"], "storage.technology_id")
    storage = StorageSpec(
        technology_id=storage_id,
        energy_capacity_max_kWh_th=_finite(storage_row["storage_energy_capacity_max_kWh_th"], "storage.energy_capacity", positive=True),
        charge_capacity_max_kW_th=_finite(storage_row["storage_charge_capacity_max_kW_th"], "storage.charge_capacity", positive=True),
        discharge_capacity_max_kW_th=_finite(storage_row["storage_discharge_capacity_max_kW_th"], "storage.discharge_capacity", positive=True),
        charge_efficiency=_finite(storage_row["storage_charge_efficiency"], "storage.charge_efficiency", positive=True),
        discharge_efficiency=_finite(storage_row["storage_discharge_efficiency"], "storage.discharge_efficiency", positive=True),
        standing_loss_fraction_per_hour=_finite(storage_row["storage_standing_loss_fraction_per_hour"], "storage.loss", minimum=0),
        capex_CNY_per_kWh_th=_finite(storage_row["storage_capex_CNY_per_kWh_th"], "storage.energy_capex", minimum=0),
        power_capex_CNY_per_kW_th=_finite(storage_row["storage_power_capex_CNY_per_kW_th"], "storage.power_capex", minimum=0),
        fixed_capex_CNY=_finite(storage_row["storage_fixed_capex_CNY"], "storage.fixed_capex", minimum=0),
        lifetime_years=int(_finite(storage_row["lifetime_years"], "storage.lifetime_years", positive=True)),
        source=_clean_id(storage_row["source"], "storage.source"),
        parameter_version=_clean_id(storage_row["parameter_version"], "storage.parameter_version"),
    )
    versions[storage_id] = storage.parameter_version
    return tuple(specs), storage, versions


def _read_pipe_types(path: Path) -> tuple[PipeTypeSpec, ...]:
    table = pd.read_csv(path, encoding="utf-8-sig")
    required = {
        "pipe_type_id", "level", "capacity_max_kW_th", "capex_CNY_per_m",
        "heat_loss_kW_per_m", "pumping_kWh_e_per_kWh_th_transferred",
        "lifetime_years", "source", "parameter_version",
    }
    missing = sorted(required - set(table.columns))
    if missing:
        raise V3InputError(["pipe_types.csv 缺少字段：" + ", ".join(missing)])
    if len(table) != 3 or sorted(table["level"].tolist()) != [1, 2, 3]:
        raise V3InputError(["pipe_types.csv 必须恰好包含 level 1、2、3"])
    specs = tuple(
        PipeTypeSpec(
            pipe_type_id=_clean_id(row["pipe_type_id"], "pipe_type_id"),
            level=int(row["level"]),
            capacity_max_kW_th=_finite(row["capacity_max_kW_th"], "pipe.capacity", positive=True),
            capex_CNY_per_m=_finite(row["capex_CNY_per_m"], "pipe.capex", minimum=0),
            heat_loss_kW_per_m=_finite(row["heat_loss_kW_per_m"], "pipe.loss", minimum=0),
            pumping_kWh_e_per_kWh_th_transferred=_finite(row["pumping_kWh_e_per_kWh_th_transferred"], "pipe.pumping", minimum=0),
            lifetime_years=int(_finite(row["lifetime_years"], "pipe.lifetime", positive=True)),
            source=_clean_id(row["source"], "pipe.source"),
            parameter_version=_clean_id(row["parameter_version"], "pipe.parameter_version"),
        )
        for _, row in table.sort_values("level").iterrows()
    )
    capacities = [item.capacity_max_kW_th for item in specs]
    if capacities != sorted(capacities) or len(set(capacities)) != 3:
        raise V3InputError(["三档管径 capacity_max_kW_th 必须严格递增"])
    return specs


def _read_spatial(case_dir: Path, config: dict[str, Any], building_ids: tuple[str, ...], pipe_types: tuple[PipeTypeSpec, ...]) -> tuple[str, tuple[SegmentSpec, ...]]:
    if config["spatial"]["candidate_source"] != "provided":
        raise V3InputError(["candidate_source=generate 的候选生成模块尚未接入"])
    sites = gpd.read_file(case_dir / config["files"]["candidate_sites"])
    network = gpd.read_file(case_dir / config["files"]["candidate_network"])
    if len(sites) != 1 or "site_id" not in sites.columns:
        raise V3InputError(["当前 V0 核心要求 provided candidate_sites 恰好包含一个 site_id"])
    site_id = _clean_id(sites.iloc[0]["site_id"], "site_id")
    required = {"segment_id", "node_from", "node_to", "length_m", "data_version", "geometry"}
    missing = sorted(required - set(network.columns))
    if missing:
        raise V3InputError(["candidate_network.geojson 缺少字段：" + ", ".join(missing)])
    allowed_nodes = {site_id, *building_ids}
    if not set(network["node_from"]).union(network["node_to"]).issubset(allowed_nodes):
        raise V3InputError(["候选管段端点必须引用 site_id 或 building_id"])
    top = pipe_types[-1]
    segments = tuple(
        SegmentSpec(
            segment_id=_clean_id(row["segment_id"], "segment_id"),
            node_u=_clean_id(row["node_from"], "node_from"),
            node_v=_clean_id(row["node_to"], "node_to"),
            length_m=_finite(row["length_m"], "segment.length_m", positive=True),
            capacity_max_kW=top.capacity_max_kW_th,
            pipe_capex_CNY_per_m=top.capex_CNY_per_m,
            lifetime_years=top.lifetime_years,
        )
        for _, row in network.sort_values("segment_id").iterrows()
    )
    return site_id, segments


def load_v3_case(case_dir: str | Path, *, profile: str | None = None) -> CanonicalCaseData:
    """Validate and snapshot a V3 draft case without writing derived files."""

    root = Path(case_dir).resolve()
    config = _load_config(root)
    if profile is not None and config["run"]["profile"] != profile:
        raise V3InputError([f"命令 profile={profile} 与 case_config profile={config['run']['profile']} 不一致"])
    paths = _configured_files(root, config)
    before = {name: _hash(path) for name, path in paths.items()}
    try:
        buildings = _read_buildings(paths[config["files"]["buildings"]], config)
        building_ids = tuple(sorted(buildings["building_id"].tolist()))
        loads, external, timestamps = _read_time_tables(
            paths[config["files"]["building_hourly_loads"]],
            paths[config["files"]["external_timeseries"]],
            config,
            building_ids,
        )
        technologies, storage, parameter_versions = _read_technologies(
            paths[config["files"]["technologies"]], config
        )
        pipe_types = _read_pipe_types(paths[config["files"]["pipe_types"]])
        parameter_versions.update({item.pipe_type_id: item.parameter_version for item in pipe_types})
        site_node, segments = _read_spatial(root, config, building_ids, pipe_types)
    except V3InputError:
        raise
    except Exception as exc:
        raise V3InputError([f"V3 文件读取失败：{exc}"]) from exc

    hours = tuple(range(1, len(timestamps) + 1))
    hour_by_timestamp = dict(zip(timestamps, hours, strict=True))
    heat_demand = {
        (str(row.building_id), hour_by_timestamp[row.timestamp]): float(row.heating_kW)
        for row in loads.itertuples(index=False)
    }
    weights = dict(zip(hours, external["time_weight_h_per_year"].astype(float), strict=True))
    economics = EconomicInput(
        time_weight_h_per_year=weights,
        electricity_price_CNY_per_kWh_e=dict(zip(hours, external["electricity_price_CNY_per_kWh_e"].astype(float), strict=True)),
        gas_price_CNY_per_kWh_LHV=dict(zip(hours, external["gas_price_CNY_per_kWh_LHV"].astype(float), strict=True)),
        expected_weight_sum_h_per_year=sum(weights.values()),
        connection_capex_CNY={node: float(config["economics"]["connection_capex_CNY_per_demand_node"]) for node in building_ids},
        connection_lifetime_years={node: int(config["economics"]["connection_lifetime_years"]) for node in building_ids},
        hns_penalty_CNY_per_kWh=float(config["economics"]["hns_penalty_CNY_per_kWh_th"]),
    )
    solver = SolverSettings(
        name=config["solver"]["name"], mip_gap=float(config["solver"]["mip_gap"]),
        threads=int(config["solver"]["threads"]),
        time_limit_seconds=float(config["solver"]["time_limit_seconds"]),
        random_seed=int(config["solver"]["random_seed"]), tee=False,
    )
    after = {name: _hash(path) for name, path in paths.items()}
    if before != after:
        raise V3InputError(["V3 校验过程修改了原始输入文件"])
    return CanonicalCaseData(
        contract_version=V3_DRAFT_CONTRACT,
        software_release_track=TEST_RELEASE_TRACK,
        case_id=config["case_id"], scenario_id=config["scenario_id"],
        data_version=config["data_version"], profile=config["run"]["profile"],
        modes=CANONICAL_MODES, timestamps=timestamps, hours=hours,
        site_node=site_node, demand_nodes=building_ids,
        heat_demand_kW_th=heat_demand, technologies=technologies, storage=storage,
        segments=segments, pipe_types=pipe_types, economics=economics, solver=solver,
        input_sha256=before, parameter_versions=parameter_versions, raw_config=config,
    )
