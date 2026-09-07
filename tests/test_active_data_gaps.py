"""Desktop missing-input list excludes delivered prices and separate code work."""
from urbanheatopt.data.gap_catalog import data_gaps, render_gap_report


def test_delivered_quotes_and_historical_pending_are_not_requested_again():
    snapshot = {"snapshot_id": "synthetic", "effective": {"station_cost_boundary": "excluded_unseparated"},
                "pending": [{"parameter_id": "pipe_dn300_cost_sensitivity", "reason": "historical", "current_value": "10"}]}
    items = data_gaps(snapshot)
    assert {item["gap_id"] for item in items} == {"STATION_SCOPE"}
    text = render_gap_report(items, snapshot)
    assert "当前共1项" in text
    assert "不增加模型变量" in text
    assert "0907教师冻结补丁" in text
    assert "不代表求解已执行" in text
    assert "PACKAGE_" not in text
    assert "CONTRACT_SCOPE" not in text
    assert "B_CONSUMER" not in text
    assert "C_RESULT_QA" not in text


def test_generated_0906_boundaries_are_not_requested_as_external_gaps():
    supplied = {"provided": ["candidate_sites", "site_limits", "pipe_capacity_limits"]}
    assert {item["gap_id"] for item in data_gaps(spatial_status=supplied)} == {"STATION_SCOPE"}
    partial = {"provided": ["candidate_sites"]}
    assert {item["gap_id"] for item in data_gaps(spatial_status=partial)} == {"STATION_SCOPE"}


def test_teacher_confirmed_0907_station_replacement_closes_external_gap():
    snapshot = {
        "snapshot_id": "synthetic",
        "effective": {
            "station_cost_boundary": "teacher_confirmed_v2_scenario",
            "station_capex_CNY": 3_000_000.0,
        },
        "v2_freeze_patch": {
            "station_cost": {
                "replaces_other_station_fixed_cost": True,
                "add_with_other_station_fixed_cost": False,
            }
        },
    }
    assert data_gaps(snapshot) == []
    assert "当前共0项" in render_gap_report([], snapshot)


def test_new_validation_errors_remain_visible_not_erased_as_final_quotes():
    items = data_gaps(errors=["synthetic missing field"])
    error = next(item for item in items if item["gap_id"] == "VALIDATION_001")
    assert error["current"] == "synthetic missing field"
    assert "其他新冲突仍阻止" in error["fallback"]
