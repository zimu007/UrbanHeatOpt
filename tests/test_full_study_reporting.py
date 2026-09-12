from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import matplotlib.pyplot as plt
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


def test_tes_sensitivity_loader_resolves_compact_delivery_paths(tmp_path):
    study = tmp_path / "main_results"
    study.mkdir()
    (study / "REPRESENTATIVE_RESULTS.json").write_text("{}", encoding="utf-8")
    result = full_study.FullStudyResult(
        run_root=study.resolve(), root_summary={}, pareto_summary={},
        frontiers={}, knees={}, points=None, evidence={},
        combined_knee_id="hybrid_epsilon_025", building_count=62,
        hour_count=2160, git_sha="0" * 40,
        optimization_scope="five_candidate_shortest_path_trees",
    )
    root = study / "tes_sensitivity"
    selected = root / "tes_capex_x0p5"
    selected.mkdir(parents=True)
    (root / "tes_sensitivity_summary.json").write_text(json.dumps({
        "schema": "urbanheatopt_tes_capex_sensitivity_v1",
        "demonstration_achieved": True,
        "all_attempted_results_qualified": True,
        "road_case_equivalent_to_source": True,
        "source_point_id": "hybrid_epsilon_025",
        "source_study": "<source-run>/runs/v2/original",
        "selected_result_path": "<source-run>/tes_sensitivity/run/tes_capex_x0p5",
        "selected_multiplier": 0.5,
    }), encoding="utf-8")
    (selected / "tes_sensitivity_comparison.json").write_text(json.dumps({
        "passed": True, "independent_qa_passed": True,
        "tes_capex_multiplier": 0.5,
        "tes": {"tes_used": True, "energy_capacity_kWh_th": 10.0,
                "actual_peak_charge_kW_th": 2.0,
                "actual_peak_discharge_kW_th": 2.0},
    }), encoding="utf-8")
    (selected / "independent_qa.json").write_text('{"passed": true}', encoding="utf-8")
    (selected / "result_bundle.json").write_text('{"qualified": true}', encoding="utf-8")
    (selected / "storage_decisions.csv").write_text("site_id\nS1\n", encoding="utf-8")

    loaded, _ = full_study._load_tes_sensitivity(result, root)
    assert loaded["selected_root"] == selected.resolve()


def test_compact_delivery_tes_pair_uses_retained_independent_qa(tmp_path):
    pairs = {}
    for mode in ("central", "hybrid"):
        pairs[mode] = {
            "passed": True,
            "independent_qa_passed": True,
            "structure_match": True,
        }
        pair_dir = tmp_path / "tes_pairs" / mode
        pair_dir.mkdir(parents=True)
        (pair_dir / "tes_pair_qa.json").write_text(
            json.dumps({
                "passed": True,
                "independent_qa_passed": True,
                "structure_match": True,
            }),
            encoding="utf-8",
        )

    full_study.FullStudyResultAdapter._validate_tes_pairs(
        {"tes_pairs": pairs}, tmp_path
    )

    (tmp_path / "tes_pairs" / "hybrid" / "tes_pair_qa.json").write_text(
        json.dumps({
            "passed": False,
            "independent_qa_passed": True,
            "structure_match": True,
        }),
        encoding="utf-8",
    )
    with pytest.raises(full_study.FullStudyReportError, match="紧凑TES配对证据"):
        full_study.FullStudyResultAdapter._validate_tes_pairs(
            {"tes_pairs": pairs}, tmp_path
        )


def test_network_plot_does_not_draw_unselected_building_service_as_road(tmp_path):
    network = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:32650"}},
        "features": [
            {
                "type": "Feature",
                "properties": {"edge_type": "road", "built": False, "length_m": 10},
                "geometry": {"type": "LineString", "coordinates": [[0, 0], [10, 0]]},
            },
            {
                "type": "Feature",
                "properties": {"edge_type": "building_service", "built": False, "length_m": 141},
                "geometry": {"type": "LineString", "coordinates": [[100, 100], [200, 200]]},
            },
        ],
    }
    access = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {
                "building_id": "b1", "selected": False,
                "building_boundary_point": [5, 5], "length_m": 7,
            },
            "geometry": {"type": "LineString", "coordinates": [[5, 5], [12, 5]]},
        }],
    }
    (tmp_path / "network_decisions.geojson").write_text(json.dumps(network), encoding="utf-8")
    (tmp_path / "access_decisions.geojson").write_text(json.dumps(access), encoding="utf-8")
    (tmp_path / "station_decisions.csv").write_text(
        "site_id,x_m,y_m,built\ns1,5,5,0\n", encoding="utf-8",
    )
    (tmp_path / "building_connection.csv").write_text(
        "building_id,connected\nb1,0\n", encoding="utf-8",
    )

    figure = full_study._plot_network(SimpleNamespace(knee_directory=tmp_path))
    try:
        plotted_x = [float(value) for line in figure.axes[0].lines for value in line.get_xdata()]
        assert 10.0 in plotted_x
        assert 100.0 not in plotted_x
        assert 200.0 not in plotted_x
    finally:
        plt.close(figure)


def test_network_plot_places_building_marker_on_selected_access_endpoint(tmp_path):
    network = {"type": "FeatureCollection", "features": []}
    access = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "building_id": "b1", "selected": False,
                    "building_boundary_point": [5, 5], "length_m": 5,
                },
                "geometry": {"type": "LineString", "coordinates": [[0, 5], [5, 5]]},
            },
            {
                "type": "Feature",
                "properties": {
                    "building_id": "b1", "selected": True,
                    "building_boundary_point": [20, 20], "length_m": 10,
                },
                "geometry": {"type": "LineString", "coordinates": [[10, 20], [20, 20]]},
            },
        ],
    }
    (tmp_path / "network_decisions.geojson").write_text(json.dumps(network), encoding="utf-8")
    (tmp_path / "access_decisions.geojson").write_text(json.dumps(access), encoding="utf-8")
    (tmp_path / "station_decisions.csv").write_text(
        "site_id,x_m,y_m,built\ns1,10,10,0\n", encoding="utf-8",
    )
    (tmp_path / "building_connection.csv").write_text(
        "building_id,connected\nb1,1\n", encoding="utf-8",
    )

    figure = full_study._plot_network(SimpleNamespace(knee_directory=tmp_path))
    try:
        offsets = [
            tuple(map(float, point))
            for collection in figure.axes[0].collections
            for point in collection.get_offsets()
        ]
        assert (20.0, 20.0) in offsets
        assert (5.0, 5.0) not in offsets
    finally:
        plt.close(figure)
