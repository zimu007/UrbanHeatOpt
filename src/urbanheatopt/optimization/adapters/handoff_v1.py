"""Fail-closed B1 projection of A's handoff; never constructs a solve case."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
from typing import Mapping

import pandas as pd

from urbanheatopt.data.bundles import CaseBundle, SolveRequest
from urbanheatopt.model.road_core import MonthlyDemandChargeInput, RoadCase


class HandoffConsumerError(ValueError):
    """The A/B handoff is incomplete, inconsistent, or modified."""


@dataclass(frozen=True, slots=True)
class BoundaryState:
    status: str
    evidence: str


@dataclass(frozen=True, slots=True)
class HeatPumpEconomicProjection:
    technology_id: str
    scope: str
    capex_CNY_per_kW_th: float
    lifetime_years: int
    fixed_om_fraction_per_year: float
    performance_artifact: Path
    performance_artifact_sha256: str
    cop_projection: str = "TabularASHPPerformanceProvider"
    capacity_max_kW_th: None = None


@dataclass(frozen=True, slots=True)
class BoilerEconomicProjection:
    capex_CNY_per_kW_th: float
    lifetime_years: int
    fixed_om_fraction_per_year: float
    efficiency: float
    energy_basis: str
    capacity_max_kW_th: None = None


@dataclass(frozen=True, slots=True)
class HourlyEconomicProjection:
    monthly_demand_charge_CNY_per_kW_month: float
    time_weight_h_per_year: Mapping[int, float]
    electricity_price_CNY_per_kWh_e: Mapping[int, float]
    electricity_carbon_kgCO2e_per_kWh_e: Mapping[int, float]
    gas_price_CNY_per_kWh_LHV: Mapping[int, float]
    gas_carbon_kgCO2e_per_kWh_LHV: Mapping[int, float]
    billing_month: Mapping[int, str]


@dataclass(frozen=True, slots=True)
class ConnectionEconomicProjection:
    capex_CNY_per_building: float
    lifetime_years: int


@dataclass(frozen=True, slots=True)
class PipeRouteEconomicProjection:
    pipe_type_id: str
    capex_CNY_per_supply_return_route_m: float
    lifetime_years: int
    capacity_kW_th: None = None
    capacity_status: str = "missing_thermal_capacity_not_inferred_from_DN"


@dataclass(frozen=True, slots=True)
class TESEconomicProjection:
    enabled_in_request: bool
    energy_capex_CNY_per_kWh_th: float
    power_capex_CNY_per_kW_th: float
    fixed_bop_capex_CNY: float
    lifetime_years: int
    charge_efficiency: float
    discharge_efficiency: float
    standing_loss_fraction_per_hour: float
    fixed_bop_boundary: BoundaryState
    energy_capacity_max_kWh_th: None = None
    charge_capacity_max_kW_th: None = None
    discharge_capacity_max_kW_th: None = None


@dataclass(frozen=True, slots=True)
class B1HandoffInputs:
    """Verified typed B1 data; intentionally insufficient for RoadCase."""
    case_bundle_id: str
    solve_request_id: str
    parameter_version: str
    package_version: str
    snapshot_id: str
    discount_rate: float
    heat_pumps: tuple[HeatPumpEconomicProjection, ...]
    boiler: BoilerEconomicProjection
    hourly_economics: HourlyEconomicProjection
    connection: ConnectionEconomicProjection
    pipe_routes: tuple[PipeRouteEconomicProjection, ...]
    tes: TESEconomicProjection
    variable_om_boundary: BoundaryState
    policy_carbon_price_boundary: BoundaryState
    unmet_heat_penalty_boundary: BoundaryState
    station_base_boundary: BoundaryState
    allow_unserved: bool
    artifact_paths: Mapping[str, Path]
    artifact_hashes: Mapping[str, str]
    source_hashes: Mapping[str, str]
    unresolved_capabilities: tuple[str, ...]
    model_ready: bool = False


def _finite(values: dict, key: str, *, positive: bool = False) -> float:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise HandoffConsumerError(f"effective parameter missing/not finite: {key}")
    value = float(value)
    if value < 0 or (positive and value <= 0):
        raise HandoffConsumerError(f"effective parameter invalid range: {key}")
    return value


def _integer(values: dict, key: str) -> int:
    value = _finite(values, key, positive=True)
    if not value.is_integer():
        raise HandoffConsumerError(f"effective parameter must be integer: {key}")
    return int(value)


def _hourly(frame: pd.DataFrame, column: str, hours: tuple[int, ...]) -> dict[int, float]:
    if column not in frame:
        raise HandoffConsumerError(f"external_timeseries missing: {column}")
    values = pd.to_numeric(frame[column], errors="coerce")
    if values.isna().any() or not values.map(lambda x: math.isfinite(float(x)) and x >= 0).all():
        raise HandoffConsumerError(f"external_timeseries invalid: {column}")
    return dict(zip(hours, map(float, values), strict=True))


def _artifacts(payload: dict) -> tuple[dict[str, Path], dict[str, str]]:
    paths, hashes = {}, {}
    for item in payload["artifacts"]:
        role = item["role"]
        if role in paths:
            raise HandoffConsumerError(f"duplicate artifact role: {role}")
        paths[role], hashes[role] = Path(item["path"]).resolve(), item["sha256"]
    missing = {"loads", "external_timeseries", "equipment_performance", "effective_parameters"} - paths.keys()
    if missing:
        raise HandoffConsumerError(f"missing required artifact roles: {sorted(missing)}")
    return paths, hashes


def _validate_snapshot_sources(snapshot: dict, case_payload: dict) -> None:
    declared = snapshot.get("source_hashes")
    if not isinstance(declared, dict) or not declared:
        raise HandoffConsumerError("effective snapshot has no source hashes")
    package_root = snapshot.get("package_root")
    normalized_sources = {
        str(Path(path).resolve()).casefold(): digest
        for path, digest in case_payload["source_hashes"].items()
    }
    for name, digest in declared.items():
        if package_root:
            expected_path = str((Path(package_root) / name).resolve()).casefold()
            if normalized_sources.get(expected_path) != digest:
                raise HandoffConsumerError(
                    f"economic source hash absent/mismatched: {name}"
                )
            continue
        suffix = str(Path(name)).replace("\\", "/").casefold()
        matches = [actual for path, actual in case_payload["source_hashes"].items()
                   if str(Path(path)).replace("\\", "/").casefold().endswith(suffix)]
        if matches != [digest]:
            raise HandoffConsumerError(f"economic source hash absent/ambiguous/mismatched: {name}")


def _validate_equipment(path: Path) -> None:
    frame = pd.read_csv(path, encoding="utf-8-sig")
    required = {"technology_type", "efficiency", "energy_basis", "COP"}
    if not required <= set(frame):
        raise HandoffConsumerError(f"equipment_performance missing: {sorted(required-set(frame))}")
    boiler = frame[frame.technology_type.eq("gas_boiler")]
    efficiency = pd.to_numeric(boiler.efficiency, errors="coerce")
    if boiler.empty or efficiency.isna().any() or not efficiency.eq(.94).all() or not boiler.energy_basis.eq("LHV").all():
        raise HandoffConsumerError("gas boiler must be LHV with efficiency exactly 0.94")
    hp = frame[frame.technology_type.eq("air_source_heat_pump")]
    cop = pd.to_numeric(hp.COP, errors="coerce")
    if hp.empty or cop.isna().any() or not cop.map(lambda x: math.isfinite(x) and x > 0).all():
        raise HandoffConsumerError("equipment_performance has no valid ASHP COP curve")


def _pipes(snapshot: dict, effective: dict) -> tuple[PipeRouteEconomicProjection, ...]:
    raw, life = snapshot.get("pipes"), _integer(effective, "pipe_life_years")
    if not isinstance(raw, list) or not raw:
        raise HandoffConsumerError("effective snapshot has no approved pipe routes")
    result = []
    for item in raw:
        if item.get("capacity_kW_th") is not None or item.get("capacity_status") != "missing_thermal_capacity_not_inferred_from_DN":
            raise HandoffConsumerError("pipe capacity must remain explicitly unresolved in B1")
        result.append(PipeRouteEconomicProjection(str(item["pipe_type_id"]), _finite(item, "value", positive=True), life))
    if len({x.pipe_type_id for x in result}) != len(result):
        raise HandoffConsumerError("duplicate pipe_type_id")
    return tuple(result)


def consume_handoff_v1(case_bundle: str | Path | CaseBundle,
                       solve_request: str | Path | SolveRequest) -> B1HandoffInputs:
    """Validate and project B1 only; never construct a mathematical model."""
    case = case_bundle if isinstance(case_bundle, CaseBundle) else CaseBundle.read(case_bundle)
    request = solve_request if isinstance(solve_request, SolveRequest) else SolveRequest.read(solve_request)
    cp, rp = case.to_dict(), request.to_dict()
    if rp["case_bundle_id"] != case.bundle_id:
        raise HandoffConsumerError("SolveRequest.case_bundle_id mismatch")
    integrity = case.verify_artifacts(check_sources=True)
    if not integrity["passed"]:
        failed = [x["path"] for x in integrity["checks"] if not x["passed"]]
        raise HandoffConsumerError(f"artifact/source hash verification failed: {failed}")
    if not all(cp["status"].values()):
        raise HandoffConsumerError("CaseBundle is not a complete A snapshot")
    artifacts, hashes = _artifacts(cp)
    snapshot = json.loads(artifacts["effective_parameters"].read_text(encoding="utf-8"))
    sid = snapshot.get("snapshot_id")
    if snapshot.get("package_version") != "revised_20260831":
        raise HandoffConsumerError("package is not revised_20260831")
    if sid != cp["parameter_version"]:
        raise HandoffConsumerError("parameter_version does not match snapshot_id")
    _validate_snapshot_sources(snapshot, cp)
    effective = snapshot.get("effective")
    if not isinstance(effective, dict):
        raise HandoffConsumerError("snapshot has no effective mapping")

    external, hours = pd.read_parquet(artifacts["external_timeseries"]), tuple(range(1, 2161))
    if len(external) != 2160 or "hour" not in external or list(external.hour) != list(hours):
        raise HandoffConsumerError("external_timeseries must contain ordered hours 1..2160")
    if "economic_snapshot_id" not in external or set(external.economic_snapshot_id.astype(str)) != {sid}:
        raise HandoffConsumerError("timeseries snapshot ID mismatch")
    if set(_hourly(external, "time_weight_h_per_year", hours).values()) != {1.0}:
        raise HandoffConsumerError("full-season weights must be one hour")
    if "billing_month" not in external or external.billing_month.isna().any():
        raise HandoffConsumerError("external_timeseries missing billing_month")
    billing = dict(zip(hours, map(str, external.billing_month), strict=True))
    loads = pd.read_parquet(artifacts["loads"])
    if not {"building_id", "hour"} <= set(loads) or len(loads) != 62 * 2160 or loads.building_id.astype(str).nunique() != 62:
        raise HandoffConsumerError("loads must contain 62 buildings x 2160 hours")
    _validate_equipment(artifacts["equipment_performance"])
    if effective.get("station_cost_boundary") != "excluded_unseparated" or effective.get("station_capex_CNY") is not None:
        raise HandoffConsumerError("revised_base station boundary is inconsistent")
    if rp.get("allow_unserved", False) is not False:
        raise HandoffConsumerError("allow_unserved=True requires a formally sourced penalty contract")

    hp_common = dict(lifetime_years=_integer(effective, "hp_life_years"),
                     fixed_om_fraction_per_year=_finite(effective, "hp_fixed_om_fraction_per_year"),
                     performance_artifact=artifacts["equipment_performance"],
                     performance_artifact_sha256=hashes["equipment_performance"])
    hps = (HeatPumpEconomicProjection("central_hp", "central", _finite(effective, "central_hp_capex_CNY_per_kW_th"), **hp_common),
           HeatPumpEconomicProjection("local_hp", "local", _finite(effective, "local_hp_capex_CNY_per_kW_th"), **hp_common))
    boiler = BoilerEconomicProjection(_finite(effective, "boiler_capex_CNY_per_kW_th"), _integer(effective, "boiler_life_years"),
                                      _finite(effective, "boiler_fixed_om_fraction_per_year"), .94, "LHV")
    hourly = HourlyEconomicProjection(_finite(effective, "monthly_demand_CNY_per_kW_month"),
                                      _hourly(external, "time_weight_h_per_year", hours),
                                      _hourly(external, "electricity_price_CNY_per_kWh_e", hours),
                                      _hourly(external, "electricity_carbon_kgCO2e_per_kWh_e", hours),
                                      _hourly(external, "gas_price_CNY_per_kWh_LHV", hours),
                                      _hourly(external, "gas_carbon_kgCO2e_per_kWh_LHV", hours), billing)
    tes = TESEconomicProjection(rp["tes_enabled"], _finite(effective, "tes_energy_capex_CNY_per_kWh_th"),
                                _finite(effective, "tes_power_capex_CNY_per_kW_th"), _finite(effective, "tes_fixed_capex_CNY"),
                                _integer(effective, "tes_life_years"), _finite(effective, "tes_eta_charge", positive=True),
                                _finite(effective, "tes_eta_discharge", positive=True), _finite(effective, "tes_loss_fraction_per_hour"),
                                BoundaryState("approved_quote_applied", "effective.tes_fixed_capex_CNY"))
    return B1HandoffInputs(
        case_bundle_id=case.bundle_id, solve_request_id=request.bundle_id,
        parameter_version=cp["parameter_version"], package_version="revised_20260831", snapshot_id=sid,
        discount_rate=_finite(effective, "discount_rate"),
        heat_pumps=hps, boiler=boiler, hourly_economics=hourly,
        connection=ConnectionEconomicProjection(_finite(effective, "connection_capex_CNY_per_building"),
                                                _integer(effective, "connection_life_years")),
        pipe_routes=_pipes(snapshot, effective), tes=tes,
        variable_om_boundary=BoundaryState("excluded_not_applied", "separate_variable_om is registered_not_applied"),
        policy_carbon_price_boundary=BoundaryState("not_applied", "revised_base has no selected policy carbon price"),
        unmet_heat_penalty_boundary=BoundaryState("not_applicable", "allow_unserved=False"),
        station_base_boundary=BoundaryState("excluded_unseparated", "revised_base station_capex_CNY is null"),
        allow_unserved=False, artifact_paths=artifacts, artifact_hashes=hashes,
        source_hashes=dict(snapshot["source_hashes"]),
        unresolved_capabilities=("site_capacity", "pipe_capacity", "tes_capacity_and_power"), model_ready=False,
    )


def apply_b1_economics_to_road_case(projection: B1HandoffInputs, case: RoadCase) -> RoadCase:
    """Overlay only approved B1 fields onto an independently bounded RoadCase."""
    if case.parameter_version != projection.snapshot_id or projection.model_ready:
        raise HandoffConsumerError("RoadCase scaffold parameter version/boundary mismatch")
    if case.common.allow_unserved:
        raise HandoffConsumerError("B1 revised_base requires allow_unserved=False")
    hp = {(item.scope, "air_source_heat_pump"): item for item in projection.heat_pumps}
    technologies = []
    for item in case.common.technologies:
        if item.technology_type == "air_source_heat_pump":
            source = hp.get((item.applicable_scope, item.technology_type))
            if source is None:
                raise HandoffConsumerError(f"unexpected heat-pump role: {item.technology_id}")
            technologies.append(replace(item, capex_CNY_per_kW=source.capex_CNY_per_kW_th,
                fixed_maintenance_fraction_per_year=source.fixed_om_fraction_per_year,
                variable_om_CNY_per_kWh_th=0.0, lifetime_years=source.lifetime_years,
                source=projection.snapshot_id, assumption_flag="revised_20260831"))
        elif item.technology_type == "gas_boiler":
            source = projection.boiler
            technologies.append(replace(item, efficiency=source.efficiency,
                capex_CNY_per_kW=source.capex_CNY_per_kW_th,
                fixed_maintenance_fraction_per_year=source.fixed_om_fraction_per_year,
                variable_om_CNY_per_kWh_th=0.0, lifetime_years=source.lifetime_years,
                source=projection.snapshot_id, assumption_flag="revised_20260831_LHV"))
        else:
            raise HandoffConsumerError(f"unexpected B1 technology: {item.technology_id}")
    if len(technologies) != 3:
        raise HandoffConsumerError("RoadCase scaffold must have exactly three B1 technologies")
    buildings = case.common.demand_nodes
    hourly = projection.hourly_economics
    economics = replace(case.common.economics,
        time_weight_h_per_year=hourly.time_weight_h_per_year,
        electricity_price_CNY_per_kWh_e=hourly.electricity_price_CNY_per_kWh_e,
        gas_price_CNY_per_kWh_LHV=hourly.gas_price_CNY_per_kWh_LHV,
        connection_capex_CNY={b: projection.connection.capex_CNY_per_building for b in buildings},
        connection_lifetime_years={b: projection.connection.lifetime_years for b in buildings},
        hns_penalty_CNY_per_kWh=0.0, discount_rate=projection.discount_rate,
        station_fixed_capex_CNY=0.0,
        electricity_carbon_kgCO2e_per_kWh_e=hourly.electricity_carbon_kgCO2e_per_kWh_e,
        gas_carbon_kgCO2e_per_kWh_LHV=hourly.gas_carbon_kgCO2e_per_kWh_LHV,
        policy_carbon_price_CNY_per_tCO2e=0.0)
    pipe_by_id = {item.pipe_type_id: item for item in projection.pipe_routes}
    if set(pipe_by_id) != {item.pipe_type_id for item in case.pipe_designs}:
        raise HandoffConsumerError("RoadCase pipe IDs do not match B1 economic projection")
    pipes = tuple(replace(item,
        capex_CNY_per_route_m=pipe_by_id[item.pipe_type_id].capex_CNY_per_supply_return_route_m,
        lifetime_years=pipe_by_id[item.pipe_type_id].lifetime_years) for item in case.pipe_designs)
    storage = case.common.storage
    if projection.tes.enabled_in_request and storage is None:
        raise HandoffConsumerError("TES-enabled request requires B4 storage limits in the scaffold")
    if storage is not None:
        storage = replace(storage, charge_efficiency=projection.tes.charge_efficiency,
            discharge_efficiency=projection.tes.discharge_efficiency,
            standing_loss_fraction_per_hour=projection.tes.standing_loss_fraction_per_hour,
            capex_CNY_per_kWh_th=projection.tes.energy_capex_CNY_per_kWh_th,
            power_capex_CNY_per_kW_th=projection.tes.power_capex_CNY_per_kW_th,
            fixed_capex_CNY=projection.tes.fixed_bop_capex_CNY,
            lifetime_years=projection.tes.lifetime_years)
    common = replace(case.common, technologies=tuple(technologies), economics=economics, storage=storage)
    return replace(case, common=common, monthly_demand_charge=MonthlyDemandChargeInput(
        hourly.monthly_demand_charge_CNY_per_kW_month, hourly.billing_month))
