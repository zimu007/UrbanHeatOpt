from dataclasses import replace

import pytest

from urbanheatopt.optimization.b5_paired_tasks import (
    B5ContractError, B5FixedBoundary, B5ReadinessEvidence, B5TaskOutcome,
    canonical_sha256, compare_b5_outcomes, make_b5_comparison_group,
    validate_b5_pair,
)


def _hash(label):
    return canonical_sha256({"value": label})


def _fixed(**changes):
    connection = changes.pop("connection_vector", {"b1": 1, "b2": 0})
    solver = changes.pop("solver_settings", {
        "name": "highs", "threads": 4, "mip_gap": 0.001,
        "time_limit_s": None, "random_seed": 0,
    })
    values = dict(
        point_role="economic", source_point_id="approved-economic-endpoint",
        case_bundle_id=_hash("bundle"), parameter_version="revised_20260831",
        snapshot_id=_hash("snapshot"), site_id="site-01",
        connection_vector=connection, connection_hash=canonical_sha256(connection),
        network_hash=_hash("network"), tree_hash=_hash("tree"),
        demand_hash=_hash("demand"), objective="cost",
        carbon_cap_kgCO2e_per_year=None, b2_boundary_hash=_hash("b2"),
        solver_settings=solver, solver_settings_hash=canonical_sha256(solver),
    )
    values.update(changes)
    return B5FixedBoundary(**values)


def _readiness(**changes):
    values = dict(
        hour_count=2160, parameter_version="revised_20260831",
        b1_real_bundle_smoke_pass=True, b2_capacity_boundary_complete=True,
        pipe_thermal_capacity_complete=True, electricity_scope_approved=True,
        tes_engineering_maxima_approved=True, unresolved_required_fields=(),
        model_ready=True,
    )
    values.update(changes)
    return B5ReadinessEvidence(**values)


def _tasks():
    group = make_b5_comparison_group(fixed=_fixed(), readiness=_readiness())
    return group, group.tes_off, group.tes_on


def test_exact_pair_is_valid_and_only_tes_enablement_differs():
    group, off, on = _tasks()
    validate_b5_pair(off, on)
    assert group.readiness_status == "PREPARED_NOT_RUN"
    assert off.fixed == on.fixed
    assert not off.tes_enabled and on.tes_enabled
    assert "tes_energy_capacity" not in off.allowed_reoptimization
    assert "tes_energy_capacity" in on.allowed_reoptimization


@pytest.mark.parametrize(("field", "replacement"), [
    ("site_id", "site-02"),
    ("network_hash", _hash("other-network")),
    ("objective", "carbon"),
    ("carbon_cap_kgCO2e_per_year", 123.0),
    ("snapshot_id", _hash("other-snapshot")),
])
def test_nonmatched_fixed_boundary_is_rejected(field, replacement):
    _, off, on = _tasks()
    changed = replace(on, fixed=replace(on.fixed, **{field: replacement}))
    with pytest.raises(B5ContractError, match="non-matched"):
        validate_b5_pair(off, changed)


def test_connection_vector_difference_is_rejected():
    _, off, on = _tasks()
    connection = {"b1": 0, "b2": 0}
    fixed = replace(on.fixed, connection_vector=connection,
                    connection_hash=canonical_sha256(connection))
    with pytest.raises(B5ContractError, match="connection_vector"):
        validate_b5_pair(off, replace(on, fixed=fixed))


def test_solver_settings_difference_is_rejected():
    _, off, on = _tasks()
    solver = dict(on.fixed.solver_settings, threads=1)
    fixed = replace(on.fixed, solver_settings=solver,
                    solver_settings_hash=canonical_sha256(solver))
    with pytest.raises(B5ContractError, match="solver_settings"):
        validate_b5_pair(off, replace(on, fixed=fixed))


def test_short_coverage_is_not_ready():
    group = make_b5_comparison_group(
        fixed=_fixed(), readiness=_readiness(hour_count=24))
    assert group.readiness_status == "NOT_READY"
    assert "full_2160h_coverage" in group.readiness_blockers


def test_incomplete_b2_boundary_is_not_ready():
    group = make_b5_comparison_group(
        fixed=_fixed(), readiness=_readiness(b2_capacity_boundary_complete=False))
    assert group.readiness_status == "NOT_READY"
    assert "b2_capacity_boundary" in group.readiness_blockers


def test_unapproved_tes_maxima_are_not_ready():
    group = make_b5_comparison_group(
        fixed=_fixed(), readiness=_readiness(tes_engineering_maxima_approved=False))
    assert group.readiness_status == "NOT_READY"
    assert "approved_tes_engineering_maxima" in group.readiness_blockers


def test_research_assumption_tes_maxima_allow_research_pair_only():
    group = make_b5_comparison_group(
        fixed=_fixed(), readiness=_readiness(
            tes_engineering_maxima_approved=False,
            result_class="research",
            tes_boundary_status="research_assumption",
        ))
    assert group.readiness_status == "PREPARED_NOT_RUN"
    assert not group.readiness_blockers


def test_research_tes_boundary_cannot_unlock_publication_pair():
    group = make_b5_comparison_group(
        fixed=_fixed(), readiness=_readiness(
            tes_engineering_maxima_approved=False,
            result_class="publication",
            tes_boundary_status="research_assumption",
        ))
    assert group.readiness_status == "NOT_READY"
    assert "publication_requires_verified_tes_boundary" in group.readiness_blockers


def test_legacy_20260829_point_cannot_be_formal_revised_b5_input():
    fixed = _fixed(parameter_version="provisional_20260829")
    group = make_b5_comparison_group(
        fixed=fixed, readiness=_readiness(parameter_version="provisional_20260829"))
    assert group.readiness_status == "NOT_READY"
    assert "revised_20260831_snapshot" in group.readiness_blockers


def test_model_ready_and_unresolved_fields_are_explicit_gates():
    group = make_b5_comparison_group(
        fixed=_fixed(), readiness=_readiness(
            model_ready=False, unresolved_required_fields=("pipe.capacity_kW_th",)))
    assert group.readiness_status == "NOT_READY"
    assert "model_ready" in group.readiness_blockers
    assert any(item.startswith("unresolved_required_fields:")
               for item in group.readiness_blockers)


def _outcome(task_id, *, cost, carbon, demand_charge, pipe_connection=30.0):
    return B5TaskOutcome(
        task_id=task_id, annual_real_cost_CNY_per_year=cost,
        physical_carbon_kgCO2e_per_year=carbon,
        electricity_energy_cost_CNY_per_year=100.0,
        gas_cost_CNY_per_year=50.0,
        monthly_demand_charge_CNY_per_year=demand_charge,
        hp_capex_CNY_per_year=20.0, boiler_capex_CNY_per_year=10.0,
        pipe_connection_cost_CNY_per_year=pipe_connection,
        tes_capex_CNY_per_year=0.0,
        heat_source_capacities_kW={"hp": 100.0}, tes_capacities={},
        peak_purchased_electricity_kW=25.0, unmet_heat_kWh=0.0,
        solver_status="optimal", solver_gap=0.0, hourly_coverage=2160,
    )


def test_future_result_comparison_reports_three_required_deltas():
    group, off_task, on_task = _tasks()
    off = _outcome(off_task.task_id, cost=1000.0, carbon=500.0, demand_charge=80.0)
    on = _outcome(on_task.task_id, cost=970.0, carbon=480.0, demand_charge=65.0)
    assert compare_b5_outcomes(group, off, on) == {
        "delta_cost_CNY_per_year": -30.0,
        "delta_carbon_kgCO2e_per_year": -20.0,
        "delta_demand_charge_CNY_per_year": -15.0,
    }


def test_fixed_pipe_connection_cost_must_match_in_future_results():
    group, off_task, on_task = _tasks()
    off = _outcome(off_task.task_id, cost=1000.0, carbon=500.0, demand_charge=80.0)
    on = _outcome(on_task.task_id, cost=970.0, carbon=480.0,
                  demand_charge=65.0, pipe_connection=31.0)
    with pytest.raises(B5ContractError, match="pipe/connection"):
        compare_b5_outcomes(group, off, on)
