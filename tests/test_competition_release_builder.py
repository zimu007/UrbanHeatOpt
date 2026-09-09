from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import zipfile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tools.build_competition_release import (
    ReleaseBuildError,
    build_release,
    collect_example_input,
    collect_figure_atlas,
    collect_result_evidence,
    collect_source_from_directory,
    collect_source_from_git,
    main,
    make_release_plan,
)


def _write(path: Path, value: str | bytes = "fixture\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8")
    return path


def _write_hourly_parquet(path: Path, filename: str) -> Path:
    hours = list(range(1, 2161))
    timestamps = [f"2026-01-{((hour - 1) // 24) + 1:02d}T{(hour - 1) % 24:02d}:00:00+08:00" for hour in hours]
    common = {"hour": hours}
    if filename == "building_hourly.parquet":
        values = {"building_id": ["b1"] * 2160, **common, "timestamp": timestamps, "demand_kW": [1.0] * 2160}
    elif filename == "dispatch_hourly.parquet":
        values = {"location_id": ["s1"] * 2160, "technology_id": ["hp"] * 2160, **common, "timestamp": timestamps, "heat_kW_th": [1.0] * 2160}
    elif filename == "network_hourly.parquet":
        values = {"edge_id": ["e1"] * 2160, **common, "timestamp": timestamps, "signed_flow_kW_th": [1.0] * 2160}
    elif filename == "storage_hourly.parquet":
        values = {"site_id": ["s1"] * 2160, **common, "timestamp": timestamps, "charge_kW": [0.0] * 2160, "discharge_kW": [0.0] * 2160, "soc_kWh": [0.0] * 2160}
    elif filename == "node_balance_check.parquet":
        values = {"node_id": ["n1"] * 2160, **common, "residual_kW": [0.0] * 2160}
    else:  # pragma: no cover - fixture programming error
        raise AssertionError(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(values), path)
    return path


def _write_qualified_result(directory: Path) -> None:
    _write(
        directory / "result_bundle.json",
        json.dumps({
            "qualified": True,
            "solver_executed": True,
            "termination_condition": "optimal",
            "solve_evidence": {"certified_gap": 0.005},
        }),
    )
    _write(directory / "independent_qa.json", '{"passed": true}\n')
    _write(directory / "storage_decisions.csv", "site_id,energy_capacity_kWh_th\ns1,1\n")


def _release_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    source = tmp_path / "clean_source"
    _write(source / "README.md", "# fixture\n")
    _write(source / "src" / "urbanheatopt" / "model.py", "VALUE = 1\n")
    _write(source / "src" / "urbanheatopt" / "gui" / "window.py", "GUI = True\n")
    _write(source / "src" / "urbanheatopt" / "data" / "adapters" / "legacy_case.py", "LEGACY = True\n")
    _write(source / "configs" / "case.yaml", "version: 1\n")
    _write(source / "configs" / "legacy" / "_config.yaml", "legacy: true\n")
    _write(source / "tests" / "test_full_study_reporting.py", "def test_ok():\n    assert True\n")
    _write(source / "tests" / "test_model.py", "def test_not_selected():\n    assert True\n")
    _write(source / "tests" / "fixture.csv", "not,source\n")
    _write(source / "secret.bin", b"must-not-be-packed")

    example = tmp_path / "sample_case"
    _write(example / "case_config.yaml", "case: sample\n")
    _write(example / "buildings.csv", "building_id\nb1\n")
    _write(example / "ignored.exe", b"not-a-sample")

    results = tmp_path / "run_results"
    _write(results / "request_set_summary.json", '{"qualified": true, "pareto": {"qualified": true}}\n')
    _write(
        results / "pareto_no_tes" / "knee_points.json",
        '{"combined": {"point_id": "hybrid_knee"}}\n',
    )
    _write(results / "central" / "independent_qa.json", '{"passed": true}\n')
    _write(results / "central" / "cost_breakdown.csv", "item,value\nenergy,1\n")
    _write(results / "central" / "dispatch_hourly.parquet", b"large-hourly-output")
    _write(results / "candidate_tasks" / "independent_qa.json", '{"duplicate": true}\n')
    knee = results / "pareto_no_tes" / "points" / "hybrid_knee"
    _write_qualified_result(knee)
    for filename in (
        "building_hourly.parquet",
        "dispatch_hourly.parquet",
        "network_hourly.parquet",
        "storage_hourly.parquet",
        "node_balance_check.parquet",
    ):
        _write_hourly_parquet(knee / filename, filename)

    figures = tmp_path / "figures"
    _write(figures / "pareto.svg", "<svg xmlns='http://www.w3.org/2000/svg'></svg>\n")
    _write(figures / "README.md", "# Figure scope\n")
    _write(figures / "raw.csv", "not,in,atlas\n")
    return source, example, results, figures


def test_builds_four_whitelist_archives_with_deterministic_hashes(tmp_path: Path) -> None:
    source, example, results, figures = _release_inputs(tmp_path)
    plans, identity = make_release_plan(
        repo_root=source,
        git_ref=None,
        source_dir=source,
        example_input=example,
        result_root=results,
        figure_root=figures,
    )

    first = tmp_path / "release_a"
    second = tmp_path / "release_b"
    first_index = build_release(first, plans, identity)
    second_index = build_release(second, plans, identity)

    assert [item["filename"] for item in first_index["packages"]] == [
        "01_source_code.zip",
        "02_example_input.zip",
        "03_result_evidence.zip",
        "04_figure_atlas.zip",
    ]
    for filename in [item["filename"] for item in first_index["packages"]]:
        assert sha256((first / filename).read_bytes()).hexdigest() == sha256(
            (second / filename).read_bytes()
        ).hexdigest()

    with zipfile.ZipFile(first / "01_source_code.zip") as archive:
        names = set(archive.namelist())
        assert "README.md" in names
        assert "src/urbanheatopt/model.py" in names
        assert "src/urbanheatopt/gui/window.py" not in names
        assert "src/urbanheatopt/data/adapters/legacy_case.py" not in names
        assert "configs/legacy/_config.yaml" not in names
        assert "tests/test_full_study_reporting.py" in names
        assert "tests/test_model.py" not in names
        assert "tests/fixture.csv" not in names
        assert "secret.bin" not in names
        assert "_PACKAGE_MANIFEST.json" in names
        manifest = json.loads(archive.read("_PACKAGE_MANIFEST.json"))
        assert manifest["package"] == "source_code"

    with zipfile.ZipFile(first / "03_result_evidence.zip") as archive:
        names = set(archive.namelist())
        assert "central/independent_qa.json" not in names
        assert "central/cost_breakdown.csv" not in names
        assert "pareto_no_tes/points/hybrid_knee/independent_qa.json" in names
        assert "central/dispatch_hourly.parquet" not in names
        assert "pareto_no_tes/points/hybrid_knee/dispatch_hourly.parquet" in names
        assert "pareto_no_tes/points/hybrid_knee/storage_decisions.csv" in names
        assert "candidate_tasks/independent_qa.json" not in names

    assert (first / "release_index.json").is_file()
    assert (first / "SHA256SUMS.txt").read_text(encoding="utf-8").count("  ") == 4
    assert "不含项目原始大数据" in (first / "INDEX.md").read_text(encoding="utf-8")


def test_dry_run_validates_without_creating_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source, example, results, figures = _release_inputs(tmp_path)
    output = tmp_path / "must_not_exist"
    exit_code = main(
        [
            "--source-dir",
            str(source),
            "--example-input",
            str(example),
            "--result-root",
            str(results),
            "--figure-root",
            str(figures),
            "--output-root",
            str(output),
            "--dry-run",
        ]
    )
    assert exit_code == 0
    assert not output.exists()
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["packages"]) == 4


def test_rejects_raw_project_data_and_incomplete_evidence(tmp_path: Path) -> None:
    raw_sample = tmp_path / "IN_DATA" / "sample"
    _write(raw_sample / "case.yaml", "case: forbidden\n")
    with pytest.raises(ReleaseBuildError, match="不得直接取自"):
        collect_example_input(raw_sample)

    incomplete = tmp_path / "incomplete_result"
    _write(incomplete / "request_set_summary.json", "{}\n")
    with pytest.raises(ReleaseBuildError, match="qualified"):
        collect_result_evidence(incomplete)

    figures = tmp_path / "no_visuals"
    _write(figures / "README.md", "text only\n")
    with pytest.raises(ReleaseBuildError, match="至少需要"):
        collect_figure_atlas(figures)

    raw_figures = tmp_path / "IN_DATA" / "figures"
    _write(raw_figures / "plot.svg", "<svg xmlns='http://www.w3.org/2000/svg'></svg>\n")
    with pytest.raises(ReleaseBuildError, match="图册目录不得"):
        collect_figure_atlas(raw_figures)


def test_result_gate_rejects_unqualified_or_damaged_representative(tmp_path: Path) -> None:
    _, _, results, _ = _release_inputs(tmp_path)
    knee = results / "pareto_no_tes" / "points" / "hybrid_knee"
    bundle = json.loads((knee / "result_bundle.json").read_text(encoding="utf-8"))
    bundle["qualified"] = False
    _write(knee / "result_bundle.json", json.dumps(bundle))
    with pytest.raises(ReleaseBuildError, match="qualified"):
        collect_result_evidence(results)

    bundle["qualified"] = True
    _write(knee / "result_bundle.json", json.dumps(bundle))
    _write(knee / "dispatch_hourly.parquet", b"not parquet")
    with pytest.raises(ReleaseBuildError, match="损坏或不可读"):
        collect_result_evidence(results)


def test_tes_sensitivity_is_relocatable_and_must_be_qualified(tmp_path: Path) -> None:
    _, _, results, _ = _release_inputs(tmp_path)
    sensitivity = tmp_path / "moved_sensitivity"
    selected = sensitivity / "tes_capex_x0p5"
    _write_qualified_result(selected)
    for filename in (
        "building_hourly.parquet",
        "dispatch_hourly.parquet",
        "network_hourly.parquet",
        "storage_hourly.parquet",
        "node_balance_check.parquet",
    ):
        _write_hourly_parquet(selected / filename, filename)
    summary = {
        "demonstration_achieved": True,
        "all_attempted_results_qualified": True,
        "selected_multiplier": 0.5,
        "selected_result_path": "D:/stale/server/path/tes_capex_x0p5",
        "attempts": [{
            "scenario_id": "tes_capex_x0p5",
            "tes_capex_multiplier": 0.5,
            "tes_used": True,
            "qualified": True,
            "pair_qa_passed": True,
        }],
    }
    _write(sensitivity / "tes_sensitivity_summary.json", json.dumps(summary))

    members = collect_result_evidence(results, sensitivity)
    names = {item.path for item in members}
    assert "tes_sensitivity/tes_capex_x0p5/storage_decisions.csv" in names
    assert "tes_sensitivity/tes_capex_x0p5/storage_hourly.parquet" in names


def test_ref_source_ignores_dirty_worktree_and_directory_mode_rejects_it(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=repo, check=True)
    _write(repo / "README.md", "committed\n")
    _write(repo / "src" / "urbanheatopt" / "core.py", "VALUE = 'committed'\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    _write(repo / "src" / "urbanheatopt" / "core.py", "VALUE = 'dirty'\n")

    members, identity = collect_source_from_git(repo, "HEAD")
    by_name = {item.path: item.data for item in members}
    assert by_name["src/urbanheatopt/core.py"] == b"VALUE = 'committed'\n"
    assert len(identity["git_sha"]) == 40
    with pytest.raises(ReleaseBuildError, match="工作树不干净"):
        collect_source_from_directory(repo)


def test_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    source, example, results, figures = _release_inputs(tmp_path)
    plans, identity = make_release_plan(
        repo_root=source,
        git_ref=None,
        source_dir=source,
        example_input=example,
        result_root=results,
        figure_root=figures,
    )
    output = tmp_path / "existing"
    output.mkdir()
    marker = _write(output / "keep.txt", "keep\n")
    with pytest.raises(ReleaseBuildError, match="拒绝覆盖"):
        build_release(output, plans, identity)
    assert marker.read_text(encoding="utf-8") == "keep\n"
