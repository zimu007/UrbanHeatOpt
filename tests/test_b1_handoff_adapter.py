"""No-solver tests for the staged B1 handoff projection."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from urbanheatopt.data.bundles import INTERFACE_VERSION, CaseBundle, SolveRequest, sha256_file
from urbanheatopt.optimization.adapters.handoff_v1 import (
    HandoffConsumerError, PipeRouteEconomicProjection,
    apply_b1_economics_to_road_case, consume_handoff_v1,
)
from tests.test_road_v2_core import shared_case


def _effective() -> dict:
    # Deliberately excludes B2 capacities, TES maxima, VOM, HNS and carbon price.
    return {
        "central_hp_capex_CNY_per_kW_th": 3000.0, "local_hp_capex_CNY_per_kW_th": 3000.0,
        "boiler_capex_CNY_per_kW_th": 782.0, "hp_fixed_om_fraction_per_year": .01,
        "hp_life_years": 20.0, "boiler_fixed_om_fraction_per_year": .04, "boiler_life_years": 15.0,
        "connection_capex_CNY_per_building": 325714.2857, "connection_life_years": 20.0,
        "discount_rate": 0.05,
        "pipe_life_years": 30.0, "station_cost_boundary": "excluded_unseparated", "station_capex_CNY": None,
        "monthly_demand_CNY_per_kW_month": 42.0,
        "tes_energy_capex_CNY_per_kWh_th": 1135.212613473483, "tes_power_capex_CNY_per_kW_th": 450.0,
        "tes_fixed_capex_CNY": 12000.0, "tes_eta_charge": .95, "tes_eta_discharge": .94,
        "tes_loss_fraction_per_hour": 1 / 2400, "tes_life_years": 20.0,
    }


@pytest.fixture
def handoff(tmp_path):
    source = tmp_path / "economic_parameters_code_ready.csv"
    source.write_text("immutable registry bytes", encoding="utf-8")
    sid = "a" * 64
    snapshot = tmp_path / "effective_parameters.json"
    snapshot.write_text(json.dumps({
        "package_version": "revised_20260831", "snapshot_id": sid,
        "source_hashes": {source.name: sha256_file(source)}, "effective": _effective(),
        "pipes": [
            {"pipe_type_id": f"P{i}", "value": value, "capacity_kW_th": None,
             "capacity_status": "missing_thermal_capacity_not_inferred_from_DN"}
            for i, value in enumerate((1000., 1500., 2000.), 1)
        ],
    }), encoding="utf-8")
    hours = list(range(1, 2161))
    external = tmp_path / "external_timeseries.parquet"
    pd.DataFrame({
        "hour": hours, "time_weight_h_per_year": [1.] * 2160,
        "electricity_price_CNY_per_kWh_e": [.3 + h / 10000 for h in hours],
        "electricity_carbon_kgCO2e_per_kWh_e": [.57] * 2160,
        "gas_price_CNY_per_kWh_LHV": [.3188206142037983] * 2160,
        "gas_carbon_kgCO2e_per_kWh_LHV": [.21] * 2160,
        "billing_month": ["2025-12"] * 744 + ["2026-01"] * 744 + ["2026-02"] * 672,
        "economic_snapshot_id": [sid] * 2160,
    }).to_parquet(external, index=False)
    loads = tmp_path / "loads.parquet"
    pd.DataFrame({"building_id": [f"b{i:02d}" for i in range(62) for _ in hours],
                  "hour": hours * 62, "heating_kW": [1.] * (62 * 2160)}).to_parquet(loads, index=False)
    equipment = tmp_path / "equipment_performance.csv"
    pd.DataFrame([
        {"technology_type": "air_source_heat_pump", "efficiency": None, "energy_basis": "electricity", "COP": 2.7, "rated_capacity_kW": 11.2},
        {"technology_type": "air_source_heat_pump", "efficiency": None, "energy_basis": "electricity", "COP": 3.5, "rated_capacity_kW": 11.2},
        {"technology_type": "gas_boiler", "efficiency": .94, "energy_basis": "LHV", "COP": None, "rated_capacity_kW": 100.},
    ]).to_csv(equipment, index=False, encoding="utf-8-sig")
    artifacts = [{"role": role, "path": str(path.resolve()), "sha256": sha256_file(path)} for role, path in (
        ("effective_parameters", snapshot), ("external_timeseries", external),
        ("loads", loads), ("equipment_performance", equipment))]
    case = CaseBundle.from_dict({
        "interface_version": INTERFACE_VERSION, "data_version": "test-62x2160", "parameter_version": sid,
        "git_sha": "test", "artifacts": artifacts, "source_hashes": {str(source.resolve()): sha256_file(source)},
        "units": {"heating_kW": "kW_th"}, "capabilities_required": ["effective_parameter_mapping"],
        "status": {"input_valid": True, "parameter_valid": True, "canonical_valid": True, "snapshot_complete": True},
    })
    request = SolveRequest.from_dict({
        "interface_version": INTERFACE_VERSION, "case_bundle_id": case.bundle_id, "mode": "hybrid",
        "objective": "cost", "epsilon_carbon_kg": None, "tes_enabled": True,
        "solver": {"name": "highs", "threads": 1, "random_seed": 1, "mip_gap": .01, "time_limit_s": None},
    })
    return case, request, snapshot, external


def test_real_shape_projection_maps_without_b2_b4_limits_or_reconversion(handoff):
    result = consume_handoff_v1(handoff[0], handoff[1])
    hp = {x.technology_id: x for x in result.heat_pumps}
    assert hp["central_hp"].capex_CNY_per_kW_th == hp["local_hp"].capex_CNY_per_kW_th == 3000
    assert hp["central_hp"].capacity_max_kW_th is None
    assert hp["central_hp"].cop_projection == "TabularASHPPerformanceProvider"
    assert hp["central_hp"].performance_artifact == result.artifact_paths["equipment_performance"]
    assert result.boiler.capex_CNY_per_kW_th == 782
    assert (result.boiler.efficiency, result.boiler.energy_basis) == (.94, "LHV")
    assert result.hourly_economics.electricity_price_CNY_per_kWh_e[18] == pytest.approx(.3018)
    assert result.hourly_economics.monthly_demand_charge_CNY_per_kW_month == 42
    assert result.hourly_economics.gas_price_CNY_per_kWh_LHV[1] == pytest.approx(.3188206142037983)
    assert result.hourly_economics.gas_carbon_kgCO2e_per_kWh_LHV[1] == pytest.approx(.21)
    assert result.variable_om_boundary.status == "excluded_not_applied"
    assert result.policy_carbon_price_boundary.status == "not_applied"
    assert result.unmet_heat_penalty_boundary.status == "not_applicable"
    assert result.allow_unserved is False and result.model_ready is False


def test_tes_economics_do_not_require_operating_maxima(handoff):
    tes = consume_handoff_v1(handoff[0], handoff[1]).tes
    assert (tes.energy_capacity_max_kWh_th, tes.charge_capacity_max_kW_th, tes.discharge_capacity_max_kW_th) == (None, None, None)
    assert (tes.energy_capex_CNY_per_kWh_th, tes.power_capex_CNY_per_kW_th) == pytest.approx((1135.212613473483, 450))
    assert (tes.charge_efficiency, tes.discharge_efficiency, tes.standing_loss_fraction_per_hour) == pytest.approx((.95, .94, 1/2400))


def test_projection_overlays_economics_without_changing_physical_limits(handoff):
    projection = consume_handoff_v1(handoff[0], handoff[1])
    hourly = replace(projection.hourly_economics,
        time_weight_h_per_year={1: 1., 2: 1.},
        electricity_price_CNY_per_kWh_e={1: .41, 2: .83},
        electricity_carbon_kgCO2e_per_kWh_e={1: .57, 2: .57},
        gas_price_CNY_per_kWh_LHV={1: .32, 2: .32},
        gas_carbon_kgCO2e_per_kWh_LHV={1: .21, 2: .21},
        billing_month={1: "2025-12", 2: "2025-12"})
    base = shared_case()
    pipes = tuple(PipeRouteEconomicProjection(item.pipe_type_id, 1000. + index * 100., 30)
                  for index, item in enumerate(base.pipe_designs))
    projection = replace(projection, hourly_economics=hourly, pipe_routes=pipes,
                         tes=replace(projection.tes, enabled_in_request=False))
    scaffold = replace(base, parameter_version=projection.snapshot_id)
    projected = apply_b1_economics_to_road_case(projection, scaffold)
    specs = {item.technology_type + ":" + item.applicable_scope: item for item in projected.common.technologies}
    assert specs["air_source_heat_pump:central"].capex_CNY_per_kW == 3000
    assert specs["air_source_heat_pump:local"].capex_CNY_per_kW == 3000
    assert specs["gas_boiler:central"].capex_CNY_per_kW == 782
    assert specs["gas_boiler:central"].efficiency == .94
    assert all(item.variable_om_CNY_per_kWh_th == 0 for item in projected.common.technologies)
    assert projected.common.economics.policy_carbon_price_CNY_per_tCO2e == 0
    assert projected.common.economics.hns_penalty_CNY_per_kWh == 0
    assert projected.common.economics.station_fixed_capex_CNY == 0
    assert [item.capacity_kW_th for item in projected.pipe_designs] == [item.capacity_kW_th for item in scaffold.pipe_designs]
    assert projected.common.storage is None


def test_snapshot_mismatch_still_fails(handoff):
    case, request, snapshot, _ = handoff
    payload = json.loads(snapshot.read_text(encoding="utf-8")); payload["snapshot_id"] = "b" * 64
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    cp = case.to_dict(); next(x for x in cp["artifacts"] if x["role"] == "effective_parameters")["sha256"] = sha256_file(snapshot)
    changed = CaseBundle.from_dict(cp); rp = request.to_dict(); rp["case_bundle_id"] = changed.bundle_id
    with pytest.raises(HandoffConsumerError, match="parameter_version"):
        consume_handoff_v1(changed, SolveRequest.from_dict(rp))


def test_artifact_hash_mismatch_still_fails(handoff):
    case, request, _, external = handoff
    external.write_bytes(external.read_bytes() + b"tampered")
    with pytest.raises(HandoffConsumerError, match="hash verification failed"):
        consume_handoff_v1(case, request)


def test_adapter_never_reads_raw_economic_csv(monkeypatch, handoff):
    import urbanheatopt.optimization.adapters.handoff_v1 as adapter
    source_text = Path(adapter.__file__).read_text(encoding="utf-8")
    assert "economic" + "_parameters_code_ready.csv" not in source_text
    original = pd.read_csv
    def guarded(path, *args, **kwargs):
        assert Path(path).name == "equipment_performance.csv"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(pd, "read_csv", guarded)
    consume_handoff_v1(handoff[0], handoff[1])
