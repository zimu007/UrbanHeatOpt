"""Single accepted-season -> immutable road-core input projection."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from urbanheatopt.model.reference_core import CoreModelInput, EconomicInput, TechnologySpec, ThermalStorageSpec
from urbanheatopt.model.physical_interfaces import TabularASHPPerformanceProvider
from urbanheatopt.model.road_core import RoadCase, PipeDesign, validate_case


def build_season_case(adaptation, network: dict, snapshot: dict) -> RoadCase:
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
        candidate_station_nodes=tuple(s['site_id'] for s in network['sites']))
    pipes=tuple(PipeDesign(tid,v['pipe_capacity_kW_th'][i],v['pipe_cost'][i],v['pipe_life'],
        v['pipe_loss'][i],v['pipe_pump'][i],v['pipe_dn_mm'][i]) for i,tid in enumerate(v['pipe_type_ids']))
    case=RoadCase(common,json.dumps(network,sort_keys=True),pipes,tuple(t.isoformat() for t in timestamps),snapshot['snapshot_sha256'])
    validate_case(case)
    return case


def save_case(case: RoadCase, path: str | Path):
    d=case.common
    econ={}
    for name in d.economics.__dataclass_fields__:
        val=getattr(d.economics,name)
        econ[name]=dict(val) if hasattr(val,'items') else val
    payload=dict(schema='road_joint_v2_case_1',network=case.network,pipes=[asdict(x) for x in case.pipe_designs],
        timestamps=case.timestamps,parameter_version=case.parameter_version,
        mode=d.mode,hours=d.hours,buildings=d.demand_nodes,sites=d.candidate_station_nodes,
        demand=[[b,h,q] for (b,h),q in sorted(d.heat_demand_kW.items())],
        technologies=[asdict(t) for t in d.technologies],economics=econ,
        storage=asdict(d.storage) if d.storage else None,
        cop=[[t,h,x] for (t,h),x in sorted(d.heat_pump_cop_by_hour.items())],
        capacity_ratio=[[t,h,x] for (t,h),x in sorted(d.heat_pump_capacity_ratio_by_hour.items())],
        allow_unserved=d.allow_unserved,peak_margin=d.peak_capacity_margin_fraction)
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
        candidate_station_nodes=tuple(obj['sites']))
    case=RoadCase(d,json.dumps(obj['network'],sort_keys=True),tuple(PipeDesign(**p) for p in obj['pipes']),
                  tuple(obj['timestamps']),obj['parameter_version'])
    validate_case(case)
    return case
