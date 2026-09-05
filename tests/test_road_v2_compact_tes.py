"""TES-specific regression tests for the fixed-tree compact formulation."""
from dataclasses import replace

import pytest
from pyomo.environ import Var, value

from urbanheatopt.model.reference_core import ThermalStorageSpec
from urbanheatopt.model.compact import (
    audit_compact_solution,
    build_compact_model,
    build_compact_tree_design,
    compact_model_metadata,
)
from urbanheatopt.model.road_core import build_road_model
from urbanheatopt.optimization.solvers import solve_pyomo_model
from tests.test_road_v2_core import shared_case


def _tes_case():
    source = shared_case("central")
    storage = ThermalStorageSpec(
        "tes",
        200.0,
        200.0,
        200.0,
        0.9,
        0.9,
        0.01,
        0.01,
        0.01,
        0.01,
        20,
    )
    economics = replace(
        source.common.economics,
        electricity_price_CNY_per_kWh_e={1: 0.05, 2: 4.0},
        gas_price_CNY_per_kWh_LHV={1: 2.0, 2: 2.0},
    )
    return replace(
        source,
        common=replace(source.common, storage=storage, economics=economics),
    )


def test_tes_guard_and_variable_structure():
    source = shared_case("central")
    design = build_compact_tree_design(source, "S1")
    with pytest.raises(ValueError, match="no-TES"):
        build_compact_model(source, design, enable_tes=True)

    case = _tes_case()
    design = build_compact_tree_design(case, "S1")
    model = build_compact_model(case, design, enable_tes=True)
    binary_names = {
        variable.name.split("[")[0]
        for variable in model.component_data_objects(Var)
        if variable.is_binary()
    }

    assert "_tes_built" in binary_names
    assert len(model.TES_S) == 1
    assert "charging" not in binary_names
    assert all(not model._charge[index].is_binary() for index in model._charge)
    assert all(not model._discharge[index].is_binary() for index in model._discharge)
    assert all(not model._soc[index].is_binary() for index in model._soc)
    assert compact_model_metadata(model)["tes_enabled"] is True

    lossless = replace(
        case,
        common=replace(
            case.common,
            storage=replace(
                case.common.storage,
                charge_efficiency=1.0,
                discharge_efficiency=1.0,
            ),
        ),
    )
    with pytest.raises(ValueError, match=r"charge_efficiency \* discharge_efficiency < 1"):
        build_compact_model(lossless, "S1", enable_tes=True)


def test_compact_tes_matches_generic_cost_carbon_and_physics():
    case = _tes_case()
    design = build_compact_tree_design(case, "S1")
    compact = build_compact_model(case, design, enable_tes=True)
    solve_pyomo_model(compact)

    generic = build_road_model(case)
    generic.station_built["S1"].fix(1)
    solve_pyomo_model(generic)

    assert value(compact.tes_built["S1"]) == pytest.approx(1.0)
    assert sum(value(compact.charge["S1", hour]) for hour in compact.HOURS) > 0
    assert value(compact.annual_real_cost_CNY_per_year) == pytest.approx(
        value(generic.annual_real_cost_CNY_per_year), abs=1e-6
    )
    assert value(compact.annual_operating_physical_carbon_kgCO2e_per_year) == pytest.approx(
        value(generic.annual_operating_physical_carbon_kgCO2e_per_year), abs=1e-6
    )
    for tech in compact.T:
        assert value(compact.capacity["S1", tech]) == pytest.approx(
            value(generic.capacity["S1", tech]), abs=1e-6
        )
    for component in (
        "tes_energy",
        "tes_charge_capacity",
        "tes_discharge_capacity",
        "tes_power_cost_capacity",
    ):
        assert value(getattr(compact, component)["S1"]) == pytest.approx(
            value(getattr(generic, component)["S1"]), abs=1e-6
        )
    for hour in compact.HOURS:
        assert value(compact.source_generation_requirement[hour]) == pytest.approx(
            value(compact.source_heat_requirement[hour])
            + value(compact.charge["S1", hour])
            - value(compact.discharge["S1", hour]),
            abs=1e-8,
        )
        assert sum(value(compact.heat["S1", tech, hour]) for tech in compact.T) == pytest.approx(
            value(compact.source_heat_requirement[hour])
            + value(compact.charge["S1", hour])
            - value(compact.discharge["S1", hour]),
            abs=1e-7,
        )
        assert value(compact.charge["S1", hour]) == pytest.approx(
            value(generic.charge["S1", hour]), abs=1e-6
        )
        assert value(compact.discharge["S1", hour]) == pytest.approx(
            value(generic.discharge["S1", hour]), abs=1e-6
        )
        assert value(compact.soc["S1", hour]) == pytest.approx(
            value(generic.soc["S1", hour]), abs=1e-6
        )
        for tech in compact.T:
            assert value(compact.heat["S1", tech, hour]) == pytest.approx(
                value(generic.heat["S1", tech, hour]), abs=1e-6
            )
        for edge in compact.E:
            assert value(compact.flow[edge, hour]) == pytest.approx(
                value(generic.flow[edge, hour]), abs=1e-6
            )

    audit = audit_compact_solution(case, compact)
    assert audit["passed"]
    assert audit["max_storage_soc_residual_kWh"] <= 1e-7
    assert audit["max_simultaneous_charge_discharge_kW"] <= 1e-7

    first_hour = next(iter(compact.HOURS))
    original_charge = value(compact.charge["S1", first_hour])
    original_discharge = value(compact.discharge["S1", first_hour])
    compact.charge["S1", first_hour].set_value(original_charge + 1.0)
    compact.discharge["S1", first_hour].set_value(original_discharge + 0.81)
    simultaneous_audit = audit_compact_solution(case, compact)
    assert not simultaneous_audit["passed"]
    assert simultaneous_audit["max_storage_soc_residual_kWh"] <= 1e-7
    assert simultaneous_audit["max_simultaneous_charge_discharge_kW"] >= 0.81

    compact.charge["S1", first_hour].set_value(original_charge)
    compact.discharge["S1", first_hour].set_value(original_discharge)
    compact.soc["S1", first_hour].set_value(
        value(compact.soc["S1", first_hour]) + 1.0
    )
    soc_audit = audit_compact_solution(case, compact)
    assert not soc_audit["passed"]
    assert soc_audit["max_storage_soc_residual_kWh"] >= 0.99
