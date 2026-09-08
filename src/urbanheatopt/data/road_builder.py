"""Single, fail-closed CaseBundle -> immutable RoadCase projection.

``build_season_case`` remains an explicit historical regression helper.  The
production path is ``build_road_case``: it verifies the frozen bundle bytes,
consumes the B1/B2 projections, and never searches an old run for missing
inputs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from urbanheatopt.data.bundles import CaseBundle, INTERFACE_VERSION, SolveRequest
from urbanheatopt.model.reference_core import (
    CAPACITY_MARGIN_BASIS_BUILDING_USEFUL,
    CAPACITY_MARGIN_BASIS_SOURCE_INCLUDING_LOSS,
    CoreModelInput,
    EconomicInput,
    TechnologySpec,
    ThermalStorageSpec,
)
from urbanheatopt.model.physical_interfaces import TabularASHPPerformanceProvider
from urbanheatopt.model.road_core import (
    B2CapacityInput, BoundaryEvidence, MonthlyDemandChargeInput,
    PipeCapacityBoundary, PipeDesign, RoadCase, SiteCapacityBoundary, validate_case,
)
from urbanheatopt.optimization.adapters.handoff_v1 import (
    apply_b1_economics_to_road_case,
    consume_handoff_v1,
)
from urbanheatopt.optimization.adapters.site_capacity_v1 import (
    apply_b2_capacity_input,
    consume_capacity_boundary_snapshot,
)


ROAD_CASE_BUILDER_VERSION = "road_case_builder_1.0.0"


@dataclass(frozen=True, slots=True)
class RoadCaseBuildResult:
    road_case: RoadCase | None
    report: dict[str, Any]

    @property
    def ready(self) -> bool:
        return self.road_case is not None and self.report.get("road_case_ready") is True


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _artifact_map(bundle: CaseBundle) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for row in bundle.to_dict()["artifacts"]:
        role = row["role"]
        if role in result:
            raise ValueError(f"CaseBundle存在重复artifact角色: {role}")
        result[role] = Path(row["path"]).resolve()
    required = {
        "buildings", "loads", "external_timeseries", "equipment_performance",
        "effective_parameters", "capacity_boundaries", "road_network", "network_manifest",
    }
    missing = required - result.keys()
    if missing:
        raise ValueError(f"CaseBundle缺少RoadCase必需产物: {sorted(missing)}")
    return result


def _projection_request(bundle: CaseBundle) -> SolveRequest:
    """Internal request used only to select B1's TES-aware parameter view."""
    return SolveRequest.from_dict({
        "interface_version": INTERFACE_VERSION,
        "case_bundle_id": bundle.bundle_id,
        "model_profile": "compact_five_tree_fullseason_v2",
        "optimization_scope": "five_candidate_shortest_path_trees",
        "mode": "central",
        "objective": "cost",
        "epsilon_carbon_kg": None,
        "tes_enabled": False,
        "allow_unserved": False,
        "solver": {
            "name": "highs", "threads": 1, "random_seed": 202611,
            "mip_gap": 0.01, "time_limit_s": None,
        },
    })


def _network_from_artifacts(paths: dict[str, Path]) -> tuple[dict, dict]:
    network = json.loads(paths["road_network"].read_text(encoding="utf-8"))
    manifest = json.loads(paths["network_manifest"].read_text(encoding="utf-8"))
    declared = network.get("network_sha256")
    unhashed = dict(network)
    unhashed.pop("network_sha256", None)
    computed = sha256(json.dumps(unhashed, sort_keys=True).encode()).hexdigest()
    if not declared or computed != declared:
        raise ValueError("road_network内部network_sha256复算失败")
    if manifest.get("network_sha256") != declared:
        raise ValueError("network_manifest与road_network哈希不一致")
    if manifest.get("schema") != "urbanheatopt_network_product_1.0.0":
        raise ValueError("未知network_manifest版本")
    return network, manifest


def build_road_case(case_bundle: str | Path | CaseBundle) -> RoadCaseBuildResult:
    """Compile the one accepted production RoadCase from an immutable bundle."""
    bundle = case_bundle if isinstance(case_bundle, CaseBundle) else CaseBundle.read(case_bundle)
    payload = bundle.to_dict()
    report: dict[str, Any] = {
        "builder_version": ROAD_CASE_BUILDER_VERSION,
        "case_bundle_id": bundle.bundle_id,
        "case_bundle_content_id": bundle.content_id,
        "data_version": payload["data_version"],
        "parameter_version": payload["parameter_version"],
        "git_sha": payload["git_sha"],
        "road_case_ready": False,
        "errors": [],
    }
    try:
        integrity = bundle.verify_artifacts(check_sources=True)
        report["bundle_integrity"] = {
            "passed": integrity["passed"], "checked_files": integrity["checked_files"],
        }
        if not integrity["passed"]:
            failed = [row["path"] for row in integrity["checks"] if not row["passed"]]
            raise ValueError(f"CaseBundle产物/来源哈希复验失败: {failed}")
        if not all(payload["status"].values()):
            raise ValueError("CaseBundle状态未全部通过")
        scope = payload.get("physical_scope", {})
        if scope.get("buildings") != 62 or scope.get("hours") != 2160:
            raise ValueError("生产RoadCase固定要求62栋×2160小时")
        if scope.get("supply_C") != 45 or scope.get("return_C") != 40:
            raise ValueError("末端供回水温度必须为冻结的45/40℃")

        paths = _artifact_map(bundle)
        network, network_manifest = _network_from_artifacts(paths)
        capacity_payload = json.loads(paths["capacity_boundaries"].read_text(encoding="utf-8"))
        b2 = consume_capacity_boundary_snapshot(capacity_payload)
        b1 = consume_handoff_v1(bundle, _projection_request(bundle))

        external = pd.read_parquet(paths["external_timeseries"]).sort_values("hour")
        loads = pd.read_parquet(paths["loads"])
        hours = tuple(range(1, 2161))
        if list(external["hour"]) != list(hours) or len(external) != len(hours):
            raise ValueError("外部时序必须按hour=1..2160唯一排序")
        required_external = {"timestamp", "outdoor_temperature_C"}
        if not required_external <= set(external):
            raise ValueError(f"外部时序缺字段: {sorted(required_external-set(external))}")
        timestamps_index = pd.DatetimeIndex(external["timestamp"])
        if timestamps_index.tz is None or timestamps_index.hasnans:
            raise ValueError("外部时序timestamp必须带时区且无空值")
        required_loads = {"building_id", "hour", "heating_kW"}
        if not required_loads <= set(loads) or len(loads) != 62 * 2160:
            raise ValueError("负荷必须包含62栋×2160小时标准字段")
        loads = loads.loc[:, ["building_id", "hour", "heating_kW"]].copy()
        loads["building_id"] = loads["building_id"].astype(str)
        loads["hour"] = pd.to_numeric(loads["hour"], errors="coerce")
        loads["heating_kW"] = pd.to_numeric(loads["heating_kW"], errors="coerce")
        if loads.isna().any().any() or (loads["heating_kW"] < 0).any():
            raise ValueError("标准负荷存在空值、非数值或负值")
        if not loads["hour"].map(lambda value: float(value).is_integer()).all():
            raise ValueError("负荷hour必须为整数")
        loads["hour"] = loads["hour"].astype(int)
        buildings = tuple(sorted(loads["building_id"].unique()))
        expected_index = pd.MultiIndex.from_product([buildings, hours])
        actual_index = pd.MultiIndex.from_frame(loads[["building_id", "hour"]])
        if len(buildings) != 62 or actual_index.has_duplicates or set(actual_index) != set(expected_index):
            raise ValueError("负荷建筑×小时笛卡尔积不完整或重复")
        network_buildings = {
            str(row["node_id"]) for row in network["nodes"] if row["node_type"] == "building"
        }
        if set(buildings) != network_buildings:
            raise ValueError("标准负荷与道路网络建筑ID集合不一致")

        site_ids = tuple(sorted(row.site_id for row in b2.sites))
        network_site_ids = tuple(sorted(str(row["site_id"]) for row in network["sites"]))
        if site_ids != network_site_ids:
            raise ValueError("网络候选站与容量边界site_id集合不一致")
        if network_manifest.get("building_count") != 62 or network_manifest.get("site_count") != 5:
            raise ValueError("网络清单不是62栋/5候选站生产产物")

        central_limits = {
            tech_id: max(float(site.technology_capacity_max_kW_th[tech_id]) for site in b2.sites)
            for tech_id in ("central_hp", "central_boiler")
        }
        local_limits = b2.local_hp_capacity_max_kW_th_by_building
        if local_limits is None or set(local_limits) != set(buildings):
            raise ValueError("分布式热泵容量边界未完整覆盖62栋")
        technology_rows = {item.technology_id: item for item in b1.heat_pumps}
        if set(technology_rows) != {"central_hp", "local_hp"}:
            raise ValueError("热泵技术角色必须为central_hp/local_hp")
        technologies = (
            TechnologySpec("central_hp", "air_source_heat_pump", "central", "electricity", 3.2, None,
                0.0, central_limits["central_hp"], technology_rows["central_hp"].capex_CNY_per_kW_th,
                technology_rows["central_hp"].fixed_om_fraction_per_year, 0.0,
                technology_rows["central_hp"].lifetime_years, b1.snapshot_id,
                "scenario_assumption"),
            TechnologySpec("central_boiler", "gas_boiler", "central", "gas", None,
                b1.boiler.efficiency, 0.0, central_limits["central_boiler"],
                b1.boiler.capex_CNY_per_kW_th, b1.boiler.fixed_om_fraction_per_year, 0.0,
                b1.boiler.lifetime_years, b1.snapshot_id, "scenario_assumption"),
            TechnologySpec("local_hp", "air_source_heat_pump", "local", "electricity", 3.0, None,
                0.0, max(float(value) for value in local_limits.values()),
                technology_rows["local_hp"].capex_CNY_per_kW_th,
                technology_rows["local_hp"].fixed_om_fraction_per_year, 0.0,
                technology_rows["local_hp"].lifetime_years, b1.snapshot_id,
                "scenario_assumption"),
        )
        provider = TabularASHPPerformanceProvider(
            paths["equipment_performance"], supply_temperature_C=45.0,
            performance_boundary_policy="clip_with_flag", parameter_version=b1.snapshot_id,
        )
        temperatures = tuple(float(value) for value in external["outdoor_temperature_C"])
        curve = provider.interpolate(temperatures)
        if curve["curve_boundary_flag"].eq("clipped_low_temperature").any():
            raise ValueError("低温超出性能曲线下界，禁止静默封顶")
        performance = provider.precompute(
            technologies=technologies, hours=hours, timestamps=tuple(timestamps_index),
            outdoor_temperature_C=temperatures, leaving_water_temperature_C=45.0,
        )

        hourly = b1.hourly_economics
        economics = EconomicInput(
            hourly.time_weight_h_per_year, hourly.electricity_price_CNY_per_kWh_e,
            hourly.gas_price_CNY_per_kWh_LHV, float(len(hours)),
            {building: b1.connection.capex_CNY_per_building for building in buildings},
            {building: b1.connection.lifetime_years for building in buildings},
            0.0, discount_rate=b1.discount_rate,
            station_fixed_capex_CNY=b1.station_fixed_capex_CNY,
            station_lifetime_years=b1.station_lifetime_years,
            electricity_carbon_kgCO2e_per_kWh_e=hourly.electricity_carbon_kgCO2e_per_kWh_e,
            gas_carbon_kgCO2e_per_kWh_LHV=hourly.gas_carbon_kgCO2e_per_kWh_LHV,
            policy_carbon_price_CNY_per_tCO2e=0.0,
        )
        tes_payload = capacity_payload["tes"]
        storage = ThermalStorageSpec(
            "central_tes", float(tes_payload["energy_capacity_max_kWh_th"]),
            float(tes_payload["charge_capacity_max_kW_th"]),
            float(tes_payload["discharge_capacity_max_kW_th"]),
            b1.tes.charge_efficiency, b1.tes.discharge_efficiency,
            b1.tes.standing_loss_fraction_per_hour, b1.tes.energy_capex_CNY_per_kWh_th,
            b1.tes.power_capex_CNY_per_kW_th, b1.tes.fixed_bop_capex_CNY,
            b1.tes.lifetime_years,
        )
        demand = {(building, int(hour)): float(value)
                  for building, hour, value in loads.itertuples(index=False, name=None)}
        common = CoreModelInput(
            "hybrid", hours, None, buildings, demand, technologies, (), economics,
            storage=storage,
            heat_pump_cop_by_hour=performance.cop_by_technology_hour,
            heat_pump_capacity_ratio_by_hour=performance.capacity_ratio_by_technology_hour,
            allow_unserved=False,
            peak_capacity_margin_fraction=float(capacity_payload["peak_capacity_margin_fraction"]),
            capacity_margin_basis=CAPACITY_MARGIN_BASIS_BUILDING_USEFUL,
            candidate_station_nodes=site_ids,
        )
        b1_pipe = {row.pipe_type_id: row for row in b1.pipe_routes}
        b2_pipe = {row.pipe_type_id: row for row in b2.pipes}
        snapshot = json.loads(paths["effective_parameters"].read_text(encoding="utf-8"))
        snapshot_pipe = {str(row["pipe_type_id"]): row for row in snapshot["pipes"]}
        if set(b1_pipe) != set(b2_pipe) or set(b1_pipe) != set(snapshot_pipe):
            raise ValueError("经济、容量和参数快照的三档pipe_type_id不一致")
        pipe_designs = tuple(PipeDesign(
            pipe_type_id=pipe_id,
            capacity_kW_th=float(b2_pipe[pipe_id].capacity_kW_th),
            capex_CNY_per_route_m=b1_pipe[pipe_id].capex_CNY_per_supply_return_route_m,
            lifetime_years=b1_pipe[pipe_id].lifetime_years,
            pair_loss_kW_per_route_m=b1_pipe[pipe_id].heat_loss_kW_per_supply_return_route_m,
            pumping_kWh_e_per_kWh_th_m=float(snapshot_pipe[pipe_id]["pump_coefficient"]),
            dn_mm=None,
        ) for pipe_id in sorted(b1_pipe))
        case = RoadCase(
            common, json.dumps(network, sort_keys=True), pipe_designs,
            tuple(item.isoformat() for item in timestamps_index), b1.snapshot_id,
            MonthlyDemandChargeInput(hourly.monthly_demand_charge_CNY_per_kW_month, hourly.billing_month), b2,
        )
        case = apply_b1_economics_to_road_case(b1, case)
        case = apply_b2_capacity_input(case, b2)
        validate_case(case)
        report.update({
            "road_case_ready": True,
            "road_case_content_sha256": road_case_content_sha256(case),
            "network_sha256": network["network_sha256"],
            "capacity_boundary_id": capacity_payload["capacity_boundary_id"],
            "counts": {"buildings": len(buildings), "hours": len(hours), "load_rows": len(demand),
                "network_nodes": len(network["nodes"]), "network_edges": len(network["edges"]),
                "candidate_sites": len(network["sites"]),
                "access_options": len(network.get("access_options", []))},
            "technology_role_mapping": capacity_payload["technology_role_mapping"],
            "economic_boundary": {"station_fixed_capex_CNY": b1.station_fixed_capex_CNY,
                "station_cost_replaces_other": True,
                "monthly_demand_charge_CNY_per_kW_month": hourly.monthly_demand_charge_CNY_per_kW_month,
                "gas_basis": "LHV"},
            "pipe_design_status": "planning_capacity_tier_not_hydraulic_dn",
            "tes_limits": {"energy_capacity_max_kWh_th": storage.energy_capacity_max_kWh_th,
                "charge_capacity_max_kW_th": storage.charge_capacity_max_kW_th,
                "discharge_capacity_max_kW_th": storage.discharge_capacity_max_kW_th},
            "performance_boundary": {
                "clipped_high_hour_count": int(curve["curve_boundary_flag"].eq("clipped_high_temperature").sum()),
                "clipped_low_hour_count": 0},
            "legacy_fallback_used": False,
        })
        return RoadCaseBuildResult(case, report)
    except Exception as exc:
        report["errors"].append(str(exc))
        raise ValueError(f"RoadCase构建失败: {exc}") from exc


def build_season_case(adaptation, network: dict, snapshot: dict) -> RoadCase:
    if snapshot.get('package_version') == 'revised_20260831':
        raise ValueError('revised_20260831 must use the B1 handoff adapter; legacy snapshot values are forbidden')
    if not adaptation.source_report.valid or not adaptation.canonical_report.valid:
        raise ValueError('源/标准输入校验未通过，禁止构模')
    data=adaptation.canonical_data
    if data.building_count!=62 or data.hour_count!=2160 or data.load_row_count!=133920:
        raise ValueError('V2真实入口固定62栋×2160h，不缩减数据')
    loads=data.loads
    external=data.external_timeseries.sort_values('hour')
    hours=tuple(int(h) for h in external.hour)
    buildings=tuple(sorted(data.buildings.building_id.astype(str)))
    timestamps=tuple(external.timestamp)
    peak=float(loads.groupby('hour').heating_kW.sum().max())
    if abs(peak-snapshot['full_park_peak_kW'])>1e-6:
        raise ValueError('经济快照的全园区峰值与实际负荷不一致')
    v=snapshot['values']
    max_capacity=peak*1.2*1.5
    techs=tuple(TechnologySpec(tid,kind,scope,carrier,cop,efficiency,0.,max_capacity,
        float(v[cost]),float(v[om]),float(v['separate_variable_om']),int(v[life]),
        snapshot['snapshot_sha256'],'scenario_assumption')
        for tid,kind,scope,carrier,cop,efficiency,cost,om,life in [
            ('central_hp','air_source_heat_pump','central','electricity',3.2,None,'central_hp_capex','hp_fixed_om','hp_life'),
            ('central_boiler','gas_boiler','central','gas',None,.94,'boiler_capex','boiler_fixed_om','boiler_life'),
            ('local_hp','air_source_heat_pump','local','electricity',3.,None,'local_hp_capex','hp_fixed_om','hp_life')])
    gas=data.equipment_performance
    gas=gas[gas.technology_type.eq('gas_boiler')]
    if gas.empty or not gas.energy_basis.eq('LHV').all() or not gas.efficiency.eq(.94).all():
        raise ValueError('必须使用明确LHV=0.94设备补丁；禁止HHV曲线进入V2')
    provider=TabularASHPPerformanceProvider(adaptation.equipment_performance_path,
        supply_temperature_C=45.,performance_boundary_policy='clip_with_flag')
    performance=provider.precompute(technologies=techs,hours=hours,timestamps=timestamps,
        outdoor_temperature_C=tuple(external.outdoor_temperature_C),leaving_water_temperature_C=45.)
    # No lower-temperature extrapolation is authorized; only >15C clamping.
    curve=provider.interpolate(tuple(external.outdoor_temperature_C))
    if curve.curve_boundary_flag.eq('clipped_low_temperature').any():
        raise ValueError('低温超出设备曲线，不能按边界值静默补充')
    def series(name):
        return {int(h):float(val) for h,val in zip(external.hour,external[name])}
    econ=EconomicInput(series('time_weight_h_per_year'),series('electricity_price_CNY_per_kWh_e'),
        series('gas_price_CNY_per_kWh_LHV'),float(len(hours)),
        {b:v['connection_capex'] for b in buildings},{b:v['connection_life'] for b in buildings},1e6,
        discount_rate=v['discount_rate'],station_fixed_capex_CNY=v['station_capex'],
        station_lifetime_years=v['station_life'],
        electricity_carbon_kgCO2e_per_kWh_e=series('electricity_carbon_kgCO2e_per_kWh_e'),
        gas_carbon_kgCO2e_per_kWh_LHV=series('gas_carbon_kgCO2e_per_kWh_LHV'))
    storage=ThermalStorageSpec('central_tes',6*peak,peak,peak,v['tes_eta_charge'],v['tes_eta_discharge'],
        v['tes_standing_loss'],v['tes_energy_capex'],v['tes_power_capex'],v['tes_fixed_capex'],int(v['tes_life']))
    common=CoreModelInput('central',hours,None,buildings,
        {(str(b),int(h)):float(q) for b,h,q in loads[['building_id','hour','heating_kW']].itertuples(index=False,name=None)},
        techs,(),econ,storage=storage,heat_pump_cop_by_hour=performance.cop_by_technology_hour,
        heat_pump_capacity_ratio_by_hour=performance.capacity_ratio_by_technology_hour,
        allow_unserved=False,peak_capacity_margin_fraction=v['peak_margin'],
        capacity_margin_basis=CAPACITY_MARGIN_BASIS_SOURCE_INCLUDING_LOSS,
        candidate_station_nodes=tuple(s['site_id'] for s in network['sites']))
    pipes=tuple(PipeDesign(tid,v['pipe_capacity_kW_th'][i],v['pipe_cost'][i],v['pipe_life'],
        v['pipe_loss'][i],v['pipe_pump'][i],v['pipe_dn_mm'][i]) for i,tid in enumerate(v['pipe_type_ids']))
    case=RoadCase(common,json.dumps(network,sort_keys=True),pipes,tuple(t.isoformat() for t in timestamps),snapshot['snapshot_sha256'])
    validate_case(case)
    return case


def case_payload(case: RoadCase) -> dict[str, Any]:
    d=case.common
    econ={}
    for name in d.economics.__dataclass_fields__:
        val=getattr(d.economics,name)
        econ[name]=dict(val) if hasattr(val,'items') else val
    b2 = case.b2_capacity
    b2_payload = None if b2 is None else dict(
        sites=[dict(site_id=row.site_id,
                    allowed_technology_ids=sorted(row.allowed_technology_ids),
                    total_heat_capacity_max_kW_th=row.total_heat_capacity_max_kW_th,
                    technology_capacity_max_kW_th=dict(row.technology_capacity_max_kW_th),
                    electricity_connection_max_kW_e=row.electricity_connection_max_kW_e,
                    electricity_connection_scope=row.electricity_connection_scope,
                    gas_connection_max_kW_LHV=row.gas_connection_max_kW_LHV,
                    evidence=asdict(row.evidence)) for row in b2.sites],
        pipes=[dict(pipe_type_id=row.pipe_type_id, capacity_kW_th=row.capacity_kW_th,
                    evidence=asdict(row.evidence)) for row in b2.pipes],
        local_hp_capacity_max_kW_th_by_building=(
            dict(b2.local_hp_capacity_max_kW_th_by_building)
            if b2.local_hp_capacity_max_kW_th_by_building is not None else None),
        local_hp_evidence=(asdict(b2.local_hp_evidence) if b2.local_hp_evidence else None))
    return dict(schema='road_joint_v2_case_1',network=case.network,pipes=[asdict(x) for x in case.pipe_designs],
        timestamps=case.timestamps,parameter_version=case.parameter_version,
        mode=d.mode,hours=d.hours,buildings=d.demand_nodes,sites=d.candidate_station_nodes,
        demand=[[b,h,q] for (b,h),q in sorted(d.heat_demand_kW.items())],
        technologies=[asdict(t) for t in d.technologies],economics=econ,
        storage=asdict(d.storage) if d.storage else None,
        monthly_demand_charge=(dict(
            rate_CNY_per_kW_month=case.monthly_demand_charge.rate_CNY_per_kW_month,
            billing_month_by_hour=dict(case.monthly_demand_charge.billing_month_by_hour),
        ) if case.monthly_demand_charge else None), b2_capacity=b2_payload,
        cop=[[t,h,x] for (t,h),x in sorted(d.heat_pump_cop_by_hour.items())],
        capacity_ratio=[[t,h,x] for (t,h),x in sorted(d.heat_pump_capacity_ratio_by_hour.items())],
        allow_unserved=d.allow_unserved,peak_margin=d.peak_capacity_margin_fraction,
        capacity_margin_basis=d.capacity_margin_basis)


def road_case_content_sha256(case: RoadCase) -> str:
    return _canonical_hash(case_payload(case))


def save_case(case: RoadCase, path: str | Path):
    payload = case_payload(case)
    with Path(path).open('x',encoding='utf-8') as stream:
        json.dump(payload,stream,ensure_ascii=False,sort_keys=True,allow_nan=False)


def load_case(path: str | Path) -> RoadCase:
    obj=json.loads(Path(path).read_text(encoding='utf-8'))
    if obj['schema']!='road_joint_v2_case_1':
        raise ValueError('拒绝旧核心案例快照')
    econ=obj['economics']
    for key,val in econ.items():
        if isinstance(val,dict) and not key.startswith('connection_'):
            econ[key]={int(h):x for h,x in val.items()}
    d=CoreModelInput(obj['mode'],tuple(obj['hours']),None,tuple(obj['buildings']),
        {(b,h):q for b,h,q in obj['demand']},tuple(TechnologySpec(**t) for t in obj['technologies']),(),
        EconomicInput(**econ),storage=ThermalStorageSpec(**obj['storage']) if obj['storage'] else None,
        heat_pump_cop_by_hour={(t,h):x for t,h,x in obj['cop']},
        heat_pump_capacity_ratio_by_hour={(t,h):x for t,h,x in obj['capacity_ratio']},
        allow_unserved=obj['allow_unserved'],peak_capacity_margin_fraction=obj['peak_margin'],
        capacity_margin_basis=obj['capacity_margin_basis'],
        candidate_station_nodes=tuple(obj['sites']))
    demand_charge = obj.get('monthly_demand_charge')
    if demand_charge is not None:
        demand_charge = dict(demand_charge)
        demand_charge['billing_month_by_hour'] = {
            int(hour): month for hour, month in demand_charge['billing_month_by_hour'].items()
        }
        demand_charge = MonthlyDemandChargeInput(**demand_charge)
    b2 = obj.get('b2_capacity')
    if b2 is not None:
        b2 = B2CapacityInput(
            sites=tuple(SiteCapacityBoundary(
                site_id=row['site_id'], allowed_technology_ids=frozenset(row['allowed_technology_ids']),
                total_heat_capacity_max_kW_th=row['total_heat_capacity_max_kW_th'],
                technology_capacity_max_kW_th=row['technology_capacity_max_kW_th'],
                electricity_connection_max_kW_e=row['electricity_connection_max_kW_e'],
                electricity_connection_scope=row['electricity_connection_scope'],
                gas_connection_max_kW_LHV=row['gas_connection_max_kW_LHV'],
                evidence=BoundaryEvidence(**row['evidence'])) for row in b2['sites']),
            pipes=tuple(PipeCapacityBoundary(row['pipe_type_id'], row['capacity_kW_th'],
                                               BoundaryEvidence(**row['evidence']))
                        for row in b2['pipes']),
            local_hp_capacity_max_kW_th_by_building=b2['local_hp_capacity_max_kW_th_by_building'],
            local_hp_evidence=(BoundaryEvidence(**b2['local_hp_evidence'])
                               if b2['local_hp_evidence'] else None))
    case=RoadCase(d,json.dumps(obj['network'],sort_keys=True),tuple(PipeDesign(**p) for p in obj['pipes']),
                  tuple(obj['timestamps']),obj['parameter_version'],demand_charge,b2)
    validate_case(case)
    return case
