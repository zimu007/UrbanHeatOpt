"""Regression tests for the compact full-horizon radial-tree formulation."""
from dataclasses import replace
import json

import pandas as pd
import pytest
from pyomo.environ import Objective, Var, value
from pyomo.repn import generate_standard_repn

from urbanheatopt.model.compact import (
    audit_compact_solution,
    build_compact_model,
    build_compact_tree_designs,
    compact_model_metadata,
    export_compact_solution,
)
from urbanheatopt.model.road_core import MonthlyDemandChargeInput, build_road_model
from urbanheatopt.data.road_builder import load_case, save_case
from urbanheatopt.model.reference_core import ThermalStorageSpec
from urbanheatopt.model.costing.annualized import capital_recovery_factor
from urbanheatopt.qa.road_results import export_solution
from urbanheatopt.optimization.solvers import solve_pyomo_model
from tests.test_road_v2_core import shared_case


def _design(case, site="S1"):
    return next(item for item in build_compact_tree_designs(case) if item.site_id == site)


def test_compact_and_road_core_share_monthly_virtual_meter_charge_structure():
    base = shared_case("hybrid")
    case = replace(base, monthly_demand_charge=MonthlyDemandChargeInput(
        42.0, {1: "2025-12", 2: "2025-12"}))
    road = build_road_model(case)
    compact = build_compact_model(case, _design(case))
    assert len(road.monthly_peak_constraint) == len(compact.monthly_peak_constraint) == 2
    assert set(road.BILLING_MONTHS) == set(compact.BILLING_MONTHS) == {"2025-12"}
    road.monthly_peak_kW_e["2025-12"].set_value(150)
    compact.monthly_peak_kW_e["2025-12"].set_value(150)
    assert value(road.annual_monthly_demand_charge_CNY_per_year) == 150 * 42
    assert value(compact.annual_monthly_demand_charge_CNY_per_year) == 150 * 42


def test_monthly_demand_charge_survives_case_round_trip(tmp_path):
    case = replace(shared_case(), monthly_demand_charge=MonthlyDemandChargeInput(
        42.0, {1: "2025-12", 2: "2026-01"}))
    path = tmp_path / "case.json"
    save_case(case, path)
    restored = load_case(path)
    assert restored.monthly_demand_charge == case.monthly_demand_charge


def _linear_coefficient(expression, variable):
    repn = generate_standard_repn(expression)
    return next(coef for item, coef in zip(repn.linear_vars, repn.linear_coefs, strict=True)
                if item is variable)


def test_revised_b1_economics_are_actual_compact_expression_coefficients():
    base = shared_case("hybrid")
    technologies = tuple(replace(
        tech,
        capex_CNY_per_kW=(782.0 if tech.technology_type == "gas_boiler" else 3000.0),
        lifetime_years=(15 if tech.technology_type == "gas_boiler" else 20),
        fixed_maintenance_fraction_per_year=(.04 if tech.technology_type == "gas_boiler" else .01),
        variable_om_CNY_per_kWh_th=0.0,
    ) for tech in base.common.technologies)
    economics = replace(
        base.common.economics,
        electricity_price_CNY_per_kWh_e={1: .41, 2: .83},
        gas_price_CNY_per_kWh_LHV={1: .32, 2: .32},
        gas_carbon_kgCO2e_per_kWh_LHV={1: .21, 2: .21},
        connection_capex_CNY={"A": 1000., "B": 1000.},
        connection_lifetime_years={"A": 20, "B": 20},
        station_fixed_capex_CNY=0.0,
        policy_carbon_price_CNY_per_tCO2e=0.0,
    )
    storage = ThermalStorageSpec("tes", 1000., 100., 100., .95, .94, 1/2400,
                                 1135.0, 450.0, 12000.0, 20)
    common = replace(base.common, technologies=technologies, economics=economics, storage=storage)
    pipes = tuple(replace(pipe, capex_CNY_per_route_m=cost)
                  for pipe, cost in zip(base.pipe_designs, (1000., 1500., 2000.), strict=True))
    case = replace(base, common=common, pipe_designs=pipes,
                   monthly_demand_charge=MonthlyDemandChargeInput(42., {1: "2025-12", 2: "2025-12"}))
    design = _design(case)
    model = build_compact_model(case, design, enable_tes=True)
    tech = {item.technology_id: item for item in technologies}
    hp = next(item for item in technologies if item.technology_type == "air_source_heat_pump" and item.applicable_scope == "central")
    boiler = next(item for item in technologies if item.technology_type == "gas_boiler")
    assert _linear_coefficient(model.device_investment.expr, model._central_capacity[hp.technology_id]) == pytest.approx(3000 * capital_recovery_factor(.05, 20))
    assert _linear_coefficient(model.device_investment.expr, model._central_capacity[boiler.technology_id]) == pytest.approx(782 * capital_recovery_factor(.05, 15))
    assert _linear_coefficient(model.fixed_om.expr, model._central_capacity[hp.technology_id]) == pytest.approx(3000 * .01)
    assert _linear_coefficient(model.fixed_om.expr, model._central_capacity[boiler.technology_id]) == pytest.approx(782 * .04)
    assert _linear_coefficient(model.electricity_cost.expr, model._central_hp_heat[1]) == pytest.approx(.41 / hp.cop)
    assert _linear_coefficient(model.gas_cost.expr, model._central_hp_heat[1]) == pytest.approx(-.32 / .94)
    assert _linear_coefficient(model.annual_operating_physical_carbon_kgCO2e_per_year.expr, model._central_hp_heat[1]) == pytest.approx(base.common.economics.electricity_carbon_kgCO2e_per_kWh_e[1] / hp.cop - .21 / .94)
    assert _linear_coefficient(model.connection_investment.expr, model.connected["A"]) == pytest.approx(1000 * capital_recovery_factor(.05, 20))
    edge = next(iter(design.variable_grade_edge_ids))
    grade = next(iter(model.K))
    pipe = next(item for item in pipes if item.pipe_type_id == grade)
    assert _linear_coefficient(model.pipe_investment.expr, model._grade_selected[edge, grade]) == pytest.approx(
        next(item["length_m"] for item in case.network["edges"] if item["edge_id"] == edge)
        * pipe.capex_CNY_per_route_m * capital_recovery_factor(.05, pipe.lifetime_years))
    assert _linear_coefficient(model.storage_investment.expr, model._tes_energy["S1"]) == pytest.approx(1135 * capital_recovery_factor(.05, 20))
    assert _linear_coefficient(model.storage_investment.expr, model._tes_power_cost_capacity["S1"]) == pytest.approx(450 * capital_recovery_factor(.05, 20))
    station_repn = generate_standard_repn(model.station_investment.expr)
    assert station_repn.is_constant() and station_repn.constant == 0


def _fix_generic_to_compact_design(model, case, design, connected):
    active = float(any(connected.values()))
    selected_options = set(design.selected_access_options.values())
    variable_grades = set(design.variable_grade_edge_ids)
    selected_edges = set(design.selected_edge_ids)
    for building, selected in connected.items():
        model.connected[building].fix(float(selected))
    for site in model.S:
        built = active if site == design.site_id else 0.0
        model.station_built[site].fix(built)
        model.tes_built[site].fix(0)
        for technology in model.T:
            model.installed[site, technology].fix(built)
        for hour in model.HOURS:
            model.charging[site, hour].fix(0)
    options = {
        option["option_id"]: option
        for option in __import__(
            "urbanheatopt.spatial.atomic_network", fromlist=["access_options"]
        ).access_options(case.network)
    }
    for option in model.ACCESS:
        chosen = option in selected_options and connected[options[option]["building_id"]]
        model.access_selected[option].fix(float(chosen))
    for edge in model.E:
        built = edge in selected_edges and any(
            connected[building]
            for building in design.downstream_buildings_by_edge.get(edge, ())
        )
        model.built[edge].fix(float(built))
        if not built:
            for grade in model.K:
                model.grade[edge, grade].fix(0)
        elif edge not in variable_grades:
            for grade in model.K:
                model.grade[edge, grade].fix(
                    float(grade == design.pipe_type_by_edge[edge])
                )


def test_vectorized_compact_export_matches_scalar_export(tmp_path):
    case = shared_case("hybrid")
    model = build_compact_model(case, _design(case))
    solve_pyomo_model(model)

    fast_root = tmp_path / "fast"
    fast_qa = export_compact_solution(case, model, fast_root)
    assert model._compact_export_views_materialized is True

    # Keep the identical cached public facades but force the generic scalar
    # assembly path.  It is the pre-vectorization reference implementation.
    model._compact_export_views_materialized = False
    scalar_root = tmp_path / "scalar"
    scalar_qa = export_solution(case, model, scalar_root)

    for filename in ("building_hourly.parquet", "network_hourly.parquet"):
        pd.testing.assert_frame_equal(
            pd.read_parquet(fast_root / filename),
            pd.read_parquet(scalar_root / filename),
            check_exact=False,
            atol=1e-12,
            rtol=0,
        )
    assert fast_qa["passed"] is scalar_qa["passed"] is True
    assert fast_qa["violations"] == scalar_qa["violations"]
    for key, expected in scalar_qa["max_errors"].items():
        assert fast_qa["max_errors"][key] == pytest.approx(expected, abs=1e-9)


def test_designs_are_rooted_trees_with_exact_original_edge_partition():
    case = shared_case("hybrid")
    designs = build_compact_tree_designs(case)

    assert [design.site_id for design in designs] == ["S1", "S2"]
    for design in designs:
        chain_edges = [edge for chain in design.chains for edge in chain.original_edge_ids]
        assert sorted(chain_edges) == sorted(design.selected_edge_ids)
        assert len(chain_edges) == len(set(chain_edges))
        assert set(design.edge_to_chain) == set(design.selected_edge_ids)
        assert set(design.pipe_type_by_edge) == set(design.selected_edge_ids)
        for edge in design.selected_edge_ids:
            assert design.downstream_buildings_by_edge[edge]
            assert edge in design.subtree_edges_by_edge[edge]
            assert design.orientation_by_edge[edge] in {-1, 1}


def test_degree_two_unloaded_nodes_contract_without_losing_edge_mapping():
    source = shared_case("central")
    network = source.network
    network["nodes"] = [row for row in network["nodes"] if row["node_id"] != "B"]
    network["edges"] = [row for row in network["edges"] if row["edge_id"] != "branchB"]
    economics = replace(
        source.common.economics,
        connection_capex_CNY={"A": source.common.economics.connection_capex_CNY["A"]},
        connection_lifetime_years={
            "A": source.common.economics.connection_lifetime_years["A"]
        },
    )
    common = replace(
        source.common,
        demand_nodes=("A",),
        heat_demand_kW={
            ("A", hour): source.common.heat_demand_kW["A", hour]
            for hour in source.common.hours
        },
        economics=economics,
    )
    case = replace(source, common=common, network_json=json.dumps(network))

    design = _design(case)

    assert design.selected_edge_ids == ("branchA", "trunk")
    assert len(design.chains) == 1
    assert set(design.chains[0].original_edge_ids) == {"trunk", "branchA"}
    assert design.chains[0].length_m == pytest.approx(310.0)
    assert design.edge_to_chain["trunk"] == design.edge_to_chain["branchA"]


def test_central_compact_matches_generic_physics_and_existing_export(tmp_path):
    case = shared_case("central")
    design = _design(case)
    compact = build_compact_model(case, design)
    solve_pyomo_model(compact)

    generic = build_road_model(case)
    generic.station_built["S1"].fix(1)
    solve_pyomo_model(generic)

    assert value(compact.annual_real_cost_CNY_per_year) == pytest.approx(
        value(generic.annual_real_cost_CNY_per_year), abs=1e-8
    )
    assert value(compact.annual_operating_physical_carbon_kgCO2e_per_year) == pytest.approx(
        value(generic.annual_operating_physical_carbon_kgCO2e_per_year), abs=1e-8
    )
    for edge in compact.E:
        assert value(compact.edge_loss[edge]) == pytest.approx(
            value(generic.edge_loss[edge]), abs=1e-9
        )
        for hour in compact.HOURS:
            assert value(compact.forward[edge, hour]) == pytest.approx(
                value(generic.forward[edge, hour]), abs=1e-9
            )
            assert value(compact.reverse[edge, hour]) == pytest.approx(
                value(generic.reverse[edge, hour]), abs=1e-9
            )
            assert value(compact.pump[edge, hour]) == pytest.approx(
                value(generic.pump[edge, hour]), abs=1e-9
            )
    assert audit_compact_solution(case, compact)["passed"]
    assert export_compact_solution(case, compact, tmp_path / "compact_solution")["passed"]
    assert compact._compact_export_views_materialized is True


def test_compact_capacity_margin_excludes_network_loss_and_matches_generic():
    case = shared_case("central", loss=0.1)
    design = _design(case)
    compact = build_compact_model(case, design)
    solve_pyomo_model(compact)
    generic = build_road_model(case)
    generic.station_built["S1"].fix(1)
    solve_pyomo_model(generic)

    compact_capacity = sum(value(compact.capacity["S1", tech]) for tech in compact.T)
    generic_capacity = sum(value(generic.capacity["S1", tech]) for tech in generic.T)
    building_peak = max(
        sum(case.common.heat_demand_kW[building, hour] for building in case.common.demand_nodes)
        for hour in case.common.hours
    )
    total_loss = sum(value(compact.edge_loss[edge]) for edge in design.selected_edge_ids)
    assert compact_capacity == pytest.approx(generic_capacity, abs=1e-6)
    assert compact_capacity == pytest.approx(building_peak + total_loss, abs=1e-6)
    assert compact_capacity >= 1.2 * building_peak - 1e-6
    assert compact_capacity < 1.2 * (building_peak + total_loss)
    audit = audit_compact_solution(case, compact)
    assert audit["passed"]
    assert audit["capacity_margin_basis"] == "connected_building_useful_heat_demand_only"
    assert audit["network_heat_loss_in_capacity_margin"] is False
    assert audit["storage_counted_in_capacity_margin"] is False


@pytest.mark.parametrize(
    "connected",
    [
        {"A": False, "B": False},
        {"A": True, "B": False},
        {"A": False, "B": True},
        {"A": True, "B": True},
    ],
)
def test_every_hybrid_connection_vector_matches_fixed_tree_generic_model(connected):
    case = shared_case("hybrid")
    design = _design(case)
    compact = build_compact_model(case, design)
    for building, selected in connected.items():
        compact.connected[building].fix(float(selected))
    solve_pyomo_model(compact)

    generic = build_road_model(case)
    _fix_generic_to_compact_design(generic, case, design, connected)
    solve_pyomo_model(generic)

    assert value(compact.annual_real_cost_CNY_per_year) == pytest.approx(
        value(generic.annual_real_cost_CNY_per_year), abs=1e-6
    )
    assert value(compact.annual_operating_physical_carbon_kgCO2e_per_year) == pytest.approx(
        value(generic.annual_operating_physical_carbon_kgCO2e_per_year), abs=1e-6
    )
    for edge in compact.E:
        assert value(compact.built[edge]) == pytest.approx(value(generic.built[edge]))
        for hour in compact.HOURS:
            assert value(compact.flow[edge, hour]) == pytest.approx(
                value(generic.flow[edge, hour]), abs=1e-7
            )
            assert value(compact.pump[edge, hour]) == pytest.approx(
                value(generic.pump[edge, hour]), abs=1e-7
            )
    assert audit_compact_solution(case, compact)["passed"]


def test_structure_has_only_connection_and_static_grade_binaries_no_flow_vars():
    case = shared_case("hybrid")
    design = _design(case)
    model = build_compact_model(case, design)
    metadata = compact_model_metadata(model)
    binary_names = {
        variable.name.split("[")[0]
        for variable in model.component_data_objects(Var)
        if variable.is_binary()
    }

    assert binary_names == {"connected", "_grade_selected"}
    assert metadata["binary_variable_count"] == (
        len(case.common.demand_nodes)
        + len(design.variable_grade_edge_ids) * len(case.pipe_designs)
    )
    assert metadata["network_flow_variable_count"] == 0
    assert metadata["direction_variable_count"] == 0
    assert metadata["commodity_variable_count"] == 0
    assert not any(
        token in variable.name
        for variable in model.component_data_objects(Var)
        for token in ("forward", "reverse", "direction", "commodity", "charge", "soc")
    )
    assert len(model.forward) == len(model.E) * len(model.HOURS)
    assert len(model.reverse) == len(model.E) * len(model.HOURS)


def test_small_pipe_is_fixed_and_larger_all_central_edges_keep_static_grades():
    case = shared_case("hybrid")
    design = _design(case)
    model = build_compact_model(case, design)

    assert set(design.variable_grade_edge_ids) == {"trunk", "branchB"}
    assert "branchA" not in model.VARIABLE_GRADE_E
    assert "trunk" in model.VARIABLE_GRADE_E
    assert len(model._grade_selected) == 2 * len(case.pipe_designs)


def test_dominance_witnesses_are_exact_and_all_hours_are_audited():
    case = shared_case("central")
    design = _design(case)
    for edge, witnesses in design.capacity_hour_witness_by_edge.items():
        for removed, retained in witnesses.items():
            assert all(
                case.common.heat_demand_kW[building, retained]
                >= case.common.heat_demand_kW[building, removed]
                for building in design.downstream_buildings_by_edge[edge]
            )
    for removed, retained in design.central_capacity_hour_witness.items():
        assert all(
            case.common.heat_demand_kW[building, retained]
            >= case.common.heat_demand_kW[building, removed]
            for building in case.common.demand_nodes
        )
        assert all(
            case.common.heat_pump_capacity_ratio_by_hour.get((technology, retained), 1.0)
            <= case.common.heat_pump_capacity_ratio_by_hour.get((technology, removed), 1.0)
            for technology in ("hp", "gas")
        )

    model = build_compact_model(case, design)
    solve_pyomo_model(model)
    audit = audit_compact_solution(case, model)
    assert audit["passed"]
    assert audit["checked_hour_count"] == len(case.common.hours)
    assert audit["dominance_witness_count"] > 0


def test_carbon_objective_epsilon_interface_and_no_tes_boundary():
    case = shared_case("central")
    design = _design(case)
    cost = build_compact_model(case, design)
    solve_pyomo_model(cost)
    epsilon = value(cost.annual_operating_physical_carbon_kgCO2e_per_year)

    carbon = build_compact_model(
        case,
        design,
        objective="carbon",
        epsilon_kgCO2e_per_year=epsilon,
        epsilon_tolerance_kgCO2e_per_year=1e-7,
    )
    active = list(carbon.component_data_objects(Objective, active=True))
    assert [item.name for item in active] == ["annual_carbon_objective"]
    solve_pyomo_model(carbon)
    assert value(carbon.annual_operating_physical_carbon_kgCO2e_per_year) <= epsilon + 1e-7
    assert all(value(carbon.tes_built[site]) == 0 for site in carbon.S)
    with pytest.raises(ValueError, match="no-TES"):
        build_compact_model(case, design, enable_tes=True)
