"""Final-quote permission resolves eight known conflicts without editing sources."""
from copy import deepcopy

import pytest

from test_revised_economics import package, mutate, write_rows
from urbanheatopt.parameters import revised_economics as economics


EXPECTED_OVERRIDES = {
    "hot_water_tes_tank_body_cost_per_m3": "SRC_HOT_TES_HOSPITAL_PRICE",
    "hot_water_tes_effective_energy_cost": "SRC_HOT_TES_HOSPITAL_PRICE",
    "hot_water_tes_fixed_bop_cost": "SRC_HOT_TES_HOSPITAL_PRICE",
    "hot_water_tes_service_life_actual": "SRC_HOT_TES_HOSPITAL_SCOPE",
    "hot_water_tes_pressure_type": "SRC_HOT_TES_HOSPITAL_PRICE",
    "hot_water_tes_quote_boundary": "SRC_HOT_TES_HOSPITAL_PRICE",
    "heating_pipe_mixed_dn_installed_reference_2017": "SRC_WB_HEBEI_CLEAN_HEATING_2017",
    "building_heat_exchange_station_package_reference_2019": "SRC_WB_HEBEI_CLEAN_HEATING_2017",
}
SOURCE_USAGES = {
    "SRC_HOT_TES_HOSPITAL_PRICE": "audit_price_reference_not_code",
    "SRC_HOT_TES_HOSPITAL_SCOPE": "audit_reference",
    "SRC_WB_HEBEI_CLEAN_HEATING_2017": "sensitivity_only",
}
EXAMPLE_PID = "hot_water_tes_effective_energy_cost"


def parameter_row(pid=EXAMPLE_PID, *, allowed="1"):
    unit = economics.SPEC["parameter_units"][pid]
    value = "synthetic_test" if unit.startswith("categorical") else "10"
    if pid == "hot_water_tes_fixed_bop_cost":
        value = "0"
    return {
        "parameter_id": pid,
        "value": value,
        "unit": unit,
        "source_id": EXPECTED_OVERRIDES[pid],
        "parameter_status": "explicit_zero_to_avoid_double_counting" if value == "0" else "project_confirmed_for_code_run",
        "code_use_allowed": allowed,
    }


def audit_sources():
    return {
        sid: {
            "source_id": sid,
            "source_title": "synthetic_test: audit provenance",
            "reference_usage": usage,
            "code_use_allowed": False,
        }
        for sid, usage in SOURCE_USAGES.items()
    }


@pytest.fixture
def audit_package(package):
    """Only synthetic values; source IDs exercise the approved authority bindings."""
    source_rows = [dict(source_id="SYN", source_title="synthetic_test", code_use_allowed="1")]
    source_rows += [dict(row, code_use_allowed="0") for row in audit_sources().values()]
    write_rows(package / "economic_parameter_sources_merged.csv", source_rows)
    for pid, sid in EXPECTED_OVERRIDES.items():
        mutate(package / "economic_parameters_code_ready.csv", pid, "source_id", sid)
        for table in ("project_energy_tariffs", "technology_quotes"):
            if pid in economics.SPEC["table_ids"][table]:
                mutate(package / f"{table}.csv", pid, "source_id", sid)
    return package


def test_override_registry_is_exactly_the_eight_approved_bindings():
    assert economics.FINAL_QUOTE_OVERRIDES == EXPECTED_OVERRIDES
    assert economics.STRICT_SOURCE_POLICY == "require_consistent"
    assert economics.FINAL_QUOTE_POLICY == "final_quote_override_20260905"


@pytest.mark.parametrize("pid", EXPECTED_OVERRIDES)
def test_each_approved_binding_still_fails_in_default_strict_mode(pid):
    row = parameter_row(pid)
    with pytest.raises(economics.PackageError, match="来源禁止"):
        economics._validate_row(row, row["unit"], audit_sources())


@pytest.mark.parametrize("pid", EXPECTED_OVERRIDES)
def test_each_final_quote_resolves_permission_without_rewriting_values_or_provenance(pid):
    row, sources = parameter_row(pid), audit_sources()
    original_row, original_sources = deepcopy(row), deepcopy(sources)
    checked = economics._validate_row(
        row, row["unit"], sources, source_permission_policy=economics.FINAL_QUOTE_POLICY
    )
    assert row == original_row
    assert sources == original_sources
    assert checked["source_id"] == EXPECTED_OVERRIDES[pid]
    expected_value = row["value"] if row["unit"].startswith("categorical") else float(row["value"])
    assert checked["value"] == expected_value
    assert checked["code_use_allowed"] is True
    assert checked["source_code_use_allowed"] is False
    assert checked["permission_resolution"] == "user_approved_final_quote"
    assert checked["model_consumed"] is False
    assert checked["result_verified"] is False


@pytest.mark.parametrize("reference_usage", ["audit_reference", "audit_price_reference_not_code", "sensitivity_only"])
def test_approved_audit_reference_categories_are_explicit(reference_usage):
    row, sources = parameter_row(), audit_sources()
    sources[row["source_id"]]["reference_usage"] = reference_usage
    checked = economics._validate_row(
        row, row["unit"], sources, source_permission_policy=economics.FINAL_QUOTE_POLICY
    )
    assert checked["permission_resolution"] == "user_approved_final_quote"


@pytest.mark.parametrize("reference_usage", ["", "execution_forbidden", "retired", "contract_price", "AUDIT_REFERENCE"])
def test_unknown_or_unapproved_reference_category_cannot_be_overridden(reference_usage):
    row, sources = parameter_row(), audit_sources()
    sources[row["source_id"]]["reference_usage"] = reference_usage
    with pytest.raises(economics.PackageError):
        economics._validate_row(
            row, row["unit"], sources, source_permission_policy=economics.FINAL_QUOTE_POLICY
        )


@pytest.mark.parametrize("kind", ["unregistered_source", "wrong_approved_source", "wrong_parameter"])
def test_authority_does_not_generalize_to_other_parameter_source_pairs(kind):
    row, sources = parameter_row(), audit_sources()
    if kind == "unregistered_source":
        row["source_id"] = "SYN_UNAPPROVED_AUDIT"
        sources[row["source_id"]] = dict(next(iter(sources.values())), source_id=row["source_id"])
    elif kind == "wrong_approved_source":
        row["source_id"] = "SRC_WB_HEBEI_CLEAN_HEATING_2017"
    else:
        row["parameter_id"] = "ashp_central_installed_cost"
        row["unit"] = economics.SPEC["parameter_units"][row["parameter_id"]]
    with pytest.raises(economics.PackageError):
        economics._validate_row(
            row, row["unit"], sources, source_permission_policy=economics.FINAL_QUOTE_POLICY
        )


@pytest.mark.parametrize("policy", ["", "allow_all", "final_quote_override", None, [], {}])
def test_unknown_permission_policy_is_rejected(policy):
    row = parameter_row()
    with pytest.raises(economics.PackageError):
        economics._validate_row(row, row["unit"], audit_sources(), source_permission_policy=policy)


def test_consistent_source_is_not_relabelled_as_an_override():
    row, sources = parameter_row(), audit_sources()
    sources[row["source_id"]]["code_use_allowed"] = True
    checked = economics._validate_row(
        row, row["unit"], sources, source_permission_policy=economics.FINAL_QUOTE_POLICY
    )
    assert checked["source_code_use_allowed"] is True
    assert checked["permission_resolution"] == "consistent"


def test_disabled_parameter_stays_disabled_even_for_an_approved_binding():
    row = parameter_row(allowed="False")
    checked = economics._validate_row(
        row, row["unit"], audit_sources(), source_permission_policy=economics.FINAL_QUOTE_POLICY
    )
    assert checked["code_use_allowed"] is False
    assert checked["source_code_use_allowed"] is False
    assert checked["permission_resolution"] == "consistent"


def test_full_package_resolves_only_conflicts_and_keeps_source_files_immutable(audit_package):
    before = {name: economics.file_hash(audit_package / name) for name in economics.PACKAGE_FILES}
    with pytest.raises(economics.PackageError, match="来源禁止"):
        economics.read_revised_package(audit_package)
    snapshot = economics.read_revised_package(
        audit_package, source_permission_policy=economics.FINAL_QUOTE_POLICY
    )
    after = {name: economics.file_hash(audit_package / name) for name in economics.PACKAGE_FILES}
    assert before == after == snapshot["source_hashes"]
    assert snapshot["parameter_valid"] is True
    assert snapshot["model_consumed"] is False
    assert snapshot["source_permission_policy"] == economics.FINAL_QUOTE_POLICY
    assert len(snapshot["permission_resolutions"]) == 8
    assert all(item["original_source_code_use_allowed"] is False
               and item["parameter_code_use_allowed"] is True
               and item["authority"]
               for item in snapshot["permission_resolutions"])
    resolutions = {pid for pid, row in snapshot["registry"].items()
                   if row["permission_resolution"] == "user_approved_final_quote"}
    assert resolutions == set(EXPECTED_OVERRIDES)
    for pid, sid in EXPECTED_OVERRIDES.items():
        assert snapshot["registry"][pid]["source_id"] == sid
        assert snapshot["registry"][pid]["quotation_display_status"] == "final_value_for_current_study"
        assert snapshot["sources"][sid]["code_use_allowed"] is False
        expected_selection = "handoff_selected" if pid in economics.SELECT else "registered_not_applied"
        assert snapshot["registry"][pid]["selection"] == expected_selection
    assert snapshot["effective"]["connection_capex_CNY_per_building"] == 10


def test_disabled_selected_parameter_still_blocks_package_under_final_quote_policy(audit_package):
    for table in ("economic_parameters_code_ready", "technology_quotes"):
        mutate(audit_package / f"{table}.csv", EXAMPLE_PID, "code_use_allowed", "False")
    with pytest.raises(economics.PackageError, match="被禁用"):
        economics.read_revised_package(audit_package, source_permission_policy=economics.FINAL_QUOTE_POLICY)


@pytest.mark.parametrize("flag", ["maybe", "", "2", "yes please", "null"])
def test_malformed_source_flag_is_not_cured_by_override(audit_package, flag):
    rows = economics.rows(audit_package / "economic_parameter_sources_merged.csv", {"source_id"})
    for row in rows:
        if row["source_id"] == EXPECTED_OVERRIDES[EXAMPLE_PID]:
            row["code_use_allowed"] = flag
    write_rows(audit_package / "economic_parameter_sources_merged.csv", rows)
    with pytest.raises(economics.PackageError):
        economics.read_revised_package(audit_package, source_permission_policy=economics.FINAL_QUOTE_POLICY)


@pytest.mark.parametrize("policy", ["allow_all_sources", None, [], {}])
def test_reader_rejects_unknown_policy_even_with_otherwise_consistent_package(package, policy):
    with pytest.raises(economics.PackageError):
        economics.read_revised_package(package, source_permission_policy=policy)


def test_permission_policy_is_part_of_snapshot_identity_not_an_invisible_switch(package):
    strict = economics.read_revised_package(package)
    authorized = economics.read_revised_package(
        package, source_permission_policy=economics.FINAL_QUOTE_POLICY
    )
    assert strict["effective"] == authorized["effective"]
    assert strict["source_hashes"] == authorized["source_hashes"]
    assert strict["snapshot_id"] != authorized["snapshot_id"]
    assert strict["permission_resolutions"] == authorized["permission_resolutions"] == []
