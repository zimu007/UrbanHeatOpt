import json

import pytest
from pyomo.environ import Var

from competition.road_joint_v2.budget import (
    apply_budget_network_design,
    build_budget_case,
    build_budget_network_design,
    build_shortest_path_backbone_case,
    initialize_budget_hybrid_mip_start,
)
from competition.road_joint_v2.core import build_road_model, validate_case
from competition.road_joint_v2.tasks import create_plan, run_task
from competition.solvers import SolverSettings, solve_pyomo_model
from tests.test_road_v2_core import shared_case


def test_budget_case_keeps_peak_and_scales_only_operating_terms():
    full = shared_case()
    budget, metadata = build_budget_case(full, representative_hours=1)

    validate_case(budget)
    assert budget.common.hours == (1,)
    assert budget.timestamps == (full.timestamps[0],)
    assert budget.common.heat_demand_kW["A", 1] == 40.0
    assert budget.common.heat_demand_kW["B", 1] == 60.0
    assert metadata["full_peak_source_hour"] == 1
    assert metadata["operating_annualization_scale"] == 1.8
    assert budget.common.economics.electricity_price_CNY_per_kWh_e[1] == 1.8
    assert budget.common.economics.electricity_carbon_kgCO2e_per_kWh_e[1] == pytest.approx(0.72)
    assert budget.common.economics.station_fixed_capex_CNY == 10.0
    assert budget.common.technologies[0].capex_CNY_per_kW == 2.0


def test_budget_case_full_length_is_identity_except_budget_label():
    full = shared_case()
    budget, metadata = build_budget_case(full, representative_hours=2)

    assert budget.common.hours == full.common.hours
    assert budget.common.heat_demand_kW == full.common.heat_demand_kW
    assert metadata["operating_annualization_scale"] == 1.0
    assert metadata["strict_full_season_optimum"] is False


def test_shortest_path_backbone_preserves_sites_and_demands():
    full = shared_case()
    backbone, metadata = build_shortest_path_backbone_case(full)

    validate_case(backbone)
    assert backbone.common.demand_nodes == full.common.demand_nodes
    assert backbone.common.candidate_station_nodes == full.common.candidate_station_nodes
    assert metadata["retained_edge_count"] == metadata["source_edge_count"]


def test_budget_network_design_fixes_central_structure():
    case = shared_case()
    design = build_budget_network_design(case)
    model = build_road_model(case, direction_relaxation=True)
    apply_budget_network_design(model, case, design)

    assert design["site_id"] == "S2"
    assert model.station_built["S2"].fixed
    assert model.station_built["S2"].value == 1
    assert all(model.charging[site, hour].fixed for site in model.S for hour in model.HOURS)
    assert all(model.built[edge].fixed for edge in model.E)


@pytest.mark.parametrize("policy, expected", [("all_central", 1), ("all_distributed", 0)])
def test_budget_hybrid_mip_start_initializes_a_feasible_topology(policy,expected):
    case=shared_case().with_mode('hybrid')
    design=build_budget_network_design(case)
    model=build_road_model(case,direction_relaxation=True)
    apply_budget_network_design(model,case,design)
    initialize_budget_hybrid_mip_start(model,case,design,policy=policy)

    assert model._urbanheatopt_mip_start_policy==f'budget_hybrid_{policy}'
    assert all(model.connected[building].value==expected for building in model.DEMAND_NODES)
    assert all(not model.connected[building].fixed for building in model.DEMAND_NODES)
    assert model.station_built[design['site_id']].value==expected
    assert all(model.built[edge].value==expected for edge in design['selected_edge_ids'])
    discrete=list(model.component_data_objects(Var,active=True,descend_into=True))
    discrete=[variable for variable in discrete if variable.is_integer()]
    assert discrete and all(variable.value is not None for variable in discrete)
    for variable in discrete:
        variable.fix(variable.value)
    solve_pyomo_model(model,SolverSettings(mip_gap=0.0,threads=1))


def test_budget_task_records_qualification_and_direction_audit(tmp_path):
    budget_case, metadata = build_budget_case(
        shared_case(),
        representative_hours=1,
    )
    root = tmp_path/"budget_run"
    plan = create_plan(
        budget_case,
        root,
        point_count=5,
        full_scale=False,
        budget_metadata=metadata,
    )

    point = run_task(
        root,
        "central-cost",
        settings=SolverSettings(mip_gap=0.0, threads=1),
    )
    run_task(
        root,
        "hybrid-cost",
        settings=SolverSettings(mip_gap=0.0, threads=1),
    )
    run_task(
        root,
        "hybrid-carbon",
        settings=SolverSettings(mip_gap=0.0, threads=1),
    )
    success = json.loads(
        (root/"tasks"/"central-cost"/"success.json").read_text(encoding="utf-8")
    )
    hybrid_build = json.loads(
        (root/"tasks"/"hybrid-cost"/"attempt_0001"/"model_build_completed.json").read_text(encoding="utf-8")
    )
    hybrid_evidence = json.loads(
        (root/"tasks"/"hybrid-cost"/"attempt_0001"/"solver_evidence.json").read_text(encoding="utf-8")
    )
    hybrid_carbon_build = json.loads(
        (root/"tasks"/"hybrid-carbon"/"attempt_0001"/"model_build_completed.json").read_text(encoding="utf-8")
    )
    hybrid_carbon_evidence = json.loads(
        (root/"tasks"/"hybrid-carbon"/"attempt_0001"/"solver_evidence.json").read_text(encoding="utf-8")
    )
    assert point.reported_mip_gap == 0.0
    assert plan["execution_profile"] == "budget_50m_v1"
    assert success["result_qualification"] == "budgeted_approximate_representative_horizon"
    assert success["direction_audit"]["violation_count"] == 0
    assert hybrid_build["mip_start_policy"] == "budget_hybrid_all_central"
    assert hybrid_evidence["mip_start_policy"] == "budget_hybrid_all_central"
    assert hybrid_evidence["mip_start_discrete_value_count"] > 0
    assert hybrid_evidence["mip_start_unmapped_discrete_value_count"] == 0
    assert hybrid_evidence["mip_start_install_status"].endswith("kOk")
    assert hybrid_carbon_build["mip_start_policy"] == "budget_hybrid_all_distributed"
    assert hybrid_carbon_evidence["mip_start_policy"] == "budget_hybrid_all_distributed"
