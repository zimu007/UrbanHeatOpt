"""Desktop missing-input list excludes delivered prices and separate code work."""
from urbanheatopt.data.gap_catalog import data_gaps, render_gap_report


def test_delivered_quotes_and_historical_pending_are_not_requested_again():
    snapshot = {"snapshot_id": "synthetic", "effective": {"station_cost_boundary": "excluded_unseparated"},
                "pending": [{"parameter_id": "pipe_dn300_cost_sensitivity", "reason": "historical", "current_value": "10"}]}
    items = data_gaps(snapshot)
    assert {item["gap_id"] for item in items} == {
        "SITE_INPUT", "PIPE_CAPACITY", "STATION_SCOPE", "VARIABLE_OM_SEMANTICS"}
    text = render_gap_report(items, snapshot)
    assert "当前共4项" in text
    assert "不增加模型变量" in text
    assert "本研究最终参数" in text
    assert "不阻止已批准基础情景" in text
    assert "PACKAGE_" not in text
    assert "CONTRACT_SCOPE" not in text
    assert "B_CONSUMER" not in text
    assert "C_RESULT_QA" not in text


def test_supplied_spatial_fields_remove_only_corresponding_data_gaps():
    supplied = {"provided": ["candidate_sites", "site_limits", "pipe_capacity_limits"]}
    assert {item["gap_id"] for item in data_gaps(spatial_status=supplied)} == {
        "STATION_SCOPE", "VARIABLE_OM_SEMANTICS"}
    partial = {"provided": ["candidate_sites"]}
    assert "SITE_INPUT" in {item["gap_id"] for item in data_gaps(spatial_status=partial)}


def test_new_validation_errors_remain_visible_not_erased_as_final_quotes():
    items = data_gaps(errors=["synthetic missing field"])
    error = next(item for item in items if item["gap_id"] == "VALIDATION_001")
    assert error["current"] == "synthetic missing field"
    assert "其他新冲突仍阻止" in error["fallback"]
