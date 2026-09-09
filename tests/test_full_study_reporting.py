from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from urbanheatopt import cli
from urbanheatopt.reporting import full_study


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1.0, True), (0.0, False), (2.0e-16, False), (1.0 - 2.0e-16, True)],
)
def test_result_boolean_parser_accepts_solver_noise(value, expected):
    assert full_study._as_bool(value) is expected


def test_report_command_does_not_require_case_config(tmp_path, monkeypatch, capsys):
    run_root = tmp_path / "qualified_run"
    output_root = tmp_path / "presentation"
    captured = {}

    def fake_generate(run, output, tes_sensitivity_root=None):
        captured.update(
            run=Path(run),
            output=Path(output),
            tes_sensitivity_root=tes_sensitivity_root,
        )
        return SimpleNamespace(
            output_directory=output_root,
            manifest=output_root / "figure_manifest.json",
            atlas_pdf=output_root / "atlas.pdf",
        )

    monkeypatch.setattr(full_study, "generate_full_study_report", fake_generate)
    exit_code = cli.main([
        "report", "--run-root", str(run_root), "--output-root", str(output_root),
    ])

    assert exit_code == 0
    assert captured == {
        "run": run_root,
        "output": output_root,
        "tes_sensitivity_root": None,
    }
    assert '"status": "completed"' in capsys.readouterr().out


def test_report_command_requires_both_paths(capsys):
    assert cli.main(["report", "--run-root", "run-only"]) == 2
    assert "report必须同时提供" in capsys.readouterr().err


def test_tes_sensitivity_loader_requires_same_knee_and_positive_use(tmp_path):
    study = tmp_path / "study"
    study.mkdir()
    result = full_study.FullStudyResult(
        run_root=study.resolve(),
        root_summary={},
        pareto_summary={},
        frontiers={},
        knees={},
        points=None,
        evidence={},
        combined_knee_id="hybrid_epsilon_025",
        building_count=62,
        hour_count=2160,
        git_sha="0" * 40,
        optimization_scope="five_candidate_shortest_path_trees",
    )
    root = tmp_path / "sensitivity"
    selected = root / "tes_capex_x0p5"
    selected.mkdir(parents=True)
    summary = {
        "schema": "urbanheatopt_tes_capex_sensitivity_v1",
        "demonstration_achieved": True,
        "all_attempted_results_qualified": True,
        "road_case_equivalent_to_source": True,
        "source_point_id": "hybrid_epsilon_025",
        "source_study": str(study.resolve()),
        "selected_result_path": str(selected.resolve()),
        "selected_multiplier": 0.5,
    }
    comparison = {
        "passed": True,
        "independent_qa_passed": True,
        "scenario_id": "tes_capex_x0p5",
        "tes_capex_multiplier": 0.5,
        "tes": {
            "tes_used": True,
            "energy_capacity_kWh_th": 10.0,
            "actual_peak_charge_kW_th": 2.0,
            "actual_peak_discharge_kW_th": 2.0,
        },
    }
    (root / "tes_sensitivity_summary.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )
    (selected / "tes_sensitivity_comparison.json").write_text(
        json.dumps(comparison), encoding="utf-8"
    )
    (selected / "independent_qa.json").write_text(
        '{"passed": true}', encoding="utf-8"
    )
    (selected / "result_bundle.json").write_text(
        '{"qualified": true}', encoding="utf-8"
    )
    (selected / "storage_decisions.csv").write_text(
        "site_id,tes_used\nS1,True\n", encoding="utf-8"
    )

    loaded, evidence = full_study._load_tes_sensitivity(result, root)
    assert loaded["summary"]["selected_multiplier"] == 0.5
    assert len(evidence) == 5

    summary["source_point_id"] = "wrong_point"
    (root / "tes_sensitivity_summary.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )
    with pytest.raises(full_study.FullStudyReportError, match="权威膝点"):
        full_study._load_tes_sensitivity(result, root)
