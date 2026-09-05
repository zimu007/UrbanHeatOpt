"""Synthetic source-like economic fixtures: never commit real delivery values."""
import csv
import json

import pandas as pd
import pytest

from urbanheatopt.parameters.revised_economics import (
    SPEC, SELECT, PackageError, read_revised_package, revised_timeseries, freeze_classification,
)


def write_rows(path, values):
    keys = list(dict.fromkeys(k for r in values for k in r))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(values)


@pytest.fixture
def package(tmp_path):
    for name in SPEC["files"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic provenance", encoding="utf-8")
    write_rows(tmp_path / "economic_parameter_sources_merged.csv", [dict(source_id="SYN", source_title="synthetic_test", code_use_allowed="1")])
    values = []
    for pid, unit in SPEC["parameter_units"].items():
        value = "0.1" if unit.startswith("fraction") else "10"
        if pid.startswith("tou_"):
            value = "1"
        if unit == "boolean":
            value = "1"
        if unit.startswith("categorical"):
            value = "synthetic_test"
        if pid == "project_basic_charge_mode":
            value = "maximum_demand"
        if pid == "project_gas_lhv_actual":
            value = "36"
        if pid == "project_gas_lhv_kwh_per_Nm3":
            value = "10"
        if pid == "project_gas_cost_per_kWh_LHV_model":
            value = "1"
        if pid == "hot_water_tes_fixed_bop_cost":
            value = "0"
        if pid == "pipe_three_sizes_actual_quotes":
            value = "DN100:100;DN200:100;DN300:100"
        if pid == "pipe_dn_mm":
            value = "DN100;DN200;DN300"
        if pid == "pipe_loss":
            value = "small:0.01;medium:0.01;large:0.01"
        if pid == "pipe_pump":
            value = "0.00001"
        row = dict(parameter_id=pid, value=value, unit=unit, source_id="SYN",
                   parameter_status="explicit_zero_to_avoid_double_counting" if value == "0" else "project_confirmed_for_code_run",
                   code_use_allowed="1", applicable_range=str(int(pid.split("_")[2][1:])) if pid.startswith("tou_") else "")
        values.append(row)
    write_rows(tmp_path / "economic_parameters_code_ready.csv", values)
    # Exact source table ID membership without including any real values.
    tariffs = [r for r in values if r["parameter_id"].startswith(("tou_", "elec_", "basic_", "gas_", "project_gas", "project_electricity", "project_basic", "electricity_basic", "high_pressure", "national_")) and r["parameter_id"] != "gas_boiler_capex_baseline" and r["parameter_id"] != "gas_boiler_capex_actual"]
    assert len(tariffs) == 46
    write_rows(tmp_path / "project_energy_tariffs.csv", tariffs)
    write_rows(tmp_path / "technology_quotes.csv", [r for r in values if r["parameter_id"] in SPEC["table_ids"]["technology_quotes"]])
    pipes = [dict(parameter_id=SPEC["table_ids"]["pipe_types"][i], pipe_type_id=f"test_{i}", value="100", unit="CNY/supply_return_route_m", source_id="SYN", parameter_status="project_confirmed_for_code_run", code_use_allowed="1", supply_return_route_pricing="1", single_or_double_pipe="supply_return_route", dn_mm=str(100*(i+1)), inner_diameter_mm="100", heat_loss_kW_per_route_m="0.01", pump_coefficient="0.00001") for i in range(3)]
    write_rows(tmp_path / "pipe_types.csv", pipes)
    write_rows(tmp_path / "pending_confirmation_remaining.csv", [dict(parameter_id="not_used",reason="not required",code_use_allowed="0")])
    return tmp_path


def mutate(path, pid, key, value):
    with path.open(encoding="utf-8-sig") as stream:
        values = list(csv.DictReader(stream))
    for row in values:
        if row["parameter_id"] == pid:
            row[key] = value
    write_rows(path, values)


def test_registration_selection_snapshot(package):
    a = read_revised_package(package)
    assert len(a["registry"]) == 87
    assert a["effective"]["gas_price_CNY_per_kWh_LHV"] == 1
    assert a["effective"]["station_capex_CNY"] is None
    assert not a["model_consumed"]
    assert "语义不足" in a["registry"]["separate_variable_om"]["selection_reason"]
    b = read_revised_package(package, "station_mixed_scope_high")
    assert b["snapshot_id"] != a["snapshot_id"]
    assert b["effective"]["station_capex_CNY"] == 10


@pytest.mark.parametrize("field,value", [("unit", "unknown"), ("value", ""), ("value", "NaN"), ("value", "0"), ("value", "-1"), ("code_use_allowed", "False"), ("code_use_allowed", "maybe"), ("source_id", "MISSING"), ("parameter_status", "unknown"), ("value_low", "999")])
def test_bad_parameter_fails(package, field, value):
    mutate(package / "economic_parameters_code_ready.csv", "ashp_central_installed_cost", field, value)
    with pytest.raises(PackageError):
        read_revised_package(package)


def test_unknown_file_and_duplicate(package):
    (package / "unexpected.txt").write_text("test")
    with pytest.raises(PackageError, match="未分类"):
        read_revised_package(package)


def test_duplicate_id(package):
    path = package / "economic_parameters_code_ready.csv"
    with path.open(encoding="utf-8-sig") as stream:
        values = list(csv.DictReader(stream))
    values.append(values[0])
    write_rows(path, values)
    with pytest.raises(PackageError, match="重复"):
        read_revised_package(package)


def season():
    return pd.DataFrame(dict(timestamp=pd.date_range("2025-12-01",periods=2160,freq="h",tz="Asia/Shanghai"), hour=range(1,2161), source_hour=list(range(8016,8760))+list(range(1416)), time_weight_h_per_year=1, natural_gas_carbon_factor_kgCO2e_per_Nm3=2, electricity_price_CNY_per_kWh_e=999))


def test_time_gas_once_and_copy(package):
    snapshot = read_revised_package(package)
    original = season()
    out = revised_timeseries(original, snapshot)
    assert original.electricity_price_CNY_per_kWh_e.eq(999).all()
    assert out.gas_price_CNY_per_kWh_LHV.eq(1).all()
    assert out.gas_carbon_kgCO2e_per_kWh_LHV.eq(.2).all()
    assert out.electricity_price_CNY_per_kWh_e.eq(10).all()
    assert out.billing_month.nunique() == 3
    assert out.electricity_price_parameter_id.iloc[18] == "tou_multiplier_m12_sharp"
    assert out.electricity_price_parameter_id.iloc[744] == "tou_multiplier_m01_valley_1"
    with pytest.raises(PackageError, match="重复"):
        revised_timeseries(out, snapshot)


@pytest.mark.parametrize("kind", ["gap", "weight", "month", "source_hour", "negative_carbon"])
def test_time_failures(package, kind):
    frame = season()
    if kind == "gap":
        frame = frame.iloc[:-1]
    elif kind == "weight":
        frame.loc[1,"time_weight_h_per_year"] = 2
    elif kind == "month":
        frame["timestamp"] = frame.timestamp + pd.Timedelta(days=1)
    elif kind == "source_hour":
        frame.loc[0,"source_hour"] = 0
    else:
        frame.loc[0,"natural_gas_carbon_factor_kgCO2e_per_Nm3"] = -1
    with pytest.raises(PackageError):
        revised_timeseries(frame, read_revised_package(package))


def test_freeze_does_not_fabricate_source():
    assert freeze_classification({"parameter_status":"proxy_reference_for_code_run"}) == "user_frozen_for_current_study"
    assert freeze_classification({"parameter_status":"pending_confirmation"}) == "explicitly_provisional"


@pytest.mark.parametrize("pid,value", [("pipe_loss", "garbage"), ("pipe_dn_mm", "DN100;DN100;DN100"), ("pipe_three_sizes_actual_quotes", "DN100:999;DN200:100;DN300:100"), ("pipe_pump", "0.8")])
def test_pipe_conflict(package, pid, value):
    mutate(package / "economic_parameters_code_ready.csv", pid, "value", value)
    with pytest.raises(PackageError, match="管型"):
        read_revised_package(package)


def test_duplicate_headers(package):
    path = package / "economic_parameters_code_ready.csv"
    path.write_text("parameter_id,parameter_id\na,b", encoding="utf-8")
    with pytest.raises(PackageError, match="重复表头"):
        read_revised_package(package)


def test_source_permission_conflict(package):
    write_rows(package / "economic_parameter_sources_merged.csv", [dict(source_id="SYN", source_title="test", code_use_allowed="0")])
    with pytest.raises(PackageError, match="来源禁止"):
        read_revised_package(package)
