"""V1.0 must remain blocked until formal inputs, capabilities and QA exist."""

from __future__ import annotations

import json
from pathlib import Path

from competition.release_gate import assess_v1_release


def test_v0_smoke_artifacts_cannot_be_mislabeled_as_v1(tmp_path: Path) -> None:
    case_dir = Path(__file__).parent / "fixtures" / "v3_smoke_case"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run_manifest.json").write_text(
        json.dumps(
            {
                "software_release": "UrbanHeatOpt-test-V0.x",
                "contract_version": "competition_input_3.0.0-draft.2",
                "legacy_model_used": False,
                "capability_status": {
                    "temperature_cop": "hourly_provider_interface_implemented_v0_fixed_provider",
                    "candidate_generation": "provider_interface_only_not_executable",
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "qa_summary.json").write_text(
        json.dumps({"all_points_passed": True}), encoding="utf-8"
    )

    report = assess_v1_release(case_dir, run_dir)

    assert report.passed is False
    joined = "\n".join(report.blockers)
    assert "尚未冻结" in joined
    assert "2160" in joined
    assert "UrbanHeatOpt-V1.0" in joined
    assert "temperature_cop" in joined
    assert "candidate_generation" in joined


def test_release_gate_reports_missing_results_without_writing(tmp_path: Path) -> None:
    case_dir = Path(__file__).parent / "fixtures" / "v3_smoke_case"
    missing_run = tmp_path / "does-not-exist"
    before = set(tmp_path.iterdir())

    report = assess_v1_release(case_dir, missing_run)

    assert report.passed is False
    assert any("run_manifest.json" in blocker for blocker in report.blockers)
    assert any("qa_summary.json" in blocker for blocker in report.blockers)
    assert set(tmp_path.iterdir()) == before
