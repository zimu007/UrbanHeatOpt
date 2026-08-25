"""End-to-end acceptance for the tracked V3 synthetic smoke case."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pandas as pd

from competition.pipelines import run_case_pipeline


CASE_DIR = Path(__file__).parent / "fixtures" / "v3_smoke_case"


def _hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _assert_standard_outputs(run_dir: Path) -> pd.DataFrame:
    qa = json.loads((run_dir / "qa_summary.json").read_text(encoding="utf-8"))
    assert qa["all_points_passed"] is True
    assert qa["failed_points"] == []
    assert qa["point_count"] > 0

    points = pd.read_csv(run_dir / "pareto_points.csv").sort_values("point_id").reset_index(drop=True)
    assert set(points["mode"]) == {"central", "distributed", "hybrid"}
    assert (points["unserved_heat_kWh"].abs() <= 1e-6).all()

    required = {
        "station_decisions.csv",
        "capacity_decisions.csv",
        "building_connection.csv",
        "storage_decisions.csv",
        "network_decisions.geojson",
        "network_hourly.csv",
        "dispatch_hourly.parquet",
        "balance_check.csv",
        "cost_breakdown.csv",
        "carbon_breakdown.csv",
        "qa_report.json",
        "solver_report.json",
    }
    for point_id in points["point_id"]:
        solution = run_dir / "solutions" / point_id
        assert required <= {path.name for path in solution.iterdir() if path.is_file()}
        report = json.loads((solution / "qa_report.json").read_text(encoding="utf-8"))
        assert report["passed"] is True
        assert abs(report["cost_reaggregation_error_CNY_per_year"]) <= 1e-6
        assert abs(report["carbon_reaggregation_error_kgCO2e_per_year"]) <= 1e-6
        assert report["max_heat_balance_error_kW"] <= 1e-6
        assert report["max_storage_soc_residual_kWh"] <= 1e-6
        assert report["network_connectivity_ok"] is True
        assert report["peak_capacity_margin_fraction"] == 0.2
        assert report["peak_capacity_margin_ok"] is True
        assert report["minimum_peak_capacity_margin_slack_kW"] >= -1e-6
        assert report["storage_counted_in_peak_capacity_margin"] is False
    return points


def test_v3_smoke_pipeline_is_read_only_complete_and_deterministic(tmp_path: Path) -> None:
    before = _hashes(CASE_DIR)
    first = run_case_pipeline(CASE_DIR, profile="v0-smoke", output_root=tmp_path / "first")
    second = run_case_pipeline(CASE_DIR, profile="v0-smoke", output_root=tmp_path / "second")

    first_points = _assert_standard_outputs(first.output_dir)
    second_points = _assert_standard_outputs(second.output_dir)
    pd.testing.assert_frame_equal(
        first_points,
        second_points,
        check_exact=False,
        rtol=0.0,
        atol=1e-9,
    )
    assert _hashes(CASE_DIR) == before

    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert manifest["legacy_model_used"] is False
    assert manifest["contract_version"] == "competition_input_3.0.0-draft.2"
    assert manifest["capability_status"]["storage"] == "linear_core_and_cyclic_soc_implemented"
    assert manifest["capability_status"]["discrete_pipe_capacity"] == "three_levels_implemented"
