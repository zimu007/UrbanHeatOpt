"""Server plan tests that never instantiate the 62x2160 solver."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import pytest

import competition.full_season_server as server
from competition.full_season_server import FullSeasonTask


def _fake_plan(root: Path) -> Path:
    root.mkdir()
    (root / "tasks").mkdir()
    tasks = server._benchmark_tasks() + server._endpoint_tasks() + server._epsilon_tasks(11)
    payload = {
        "schema_version": server.SCHEMA,
        "case_dir": str(root / "case"),
        "core_freeze": server.frozen_core_record(),
        "case_input_sha256": {},
        "required_building_count": 62,
        "required_hour_count": 2160,
        "mode_order": list(server.MODES),
        "epsilon_point_count_per_mode": 11,
        "main_task_count": 39,
        "benchmark_task_count": 3,
        "tasks": [asdict(task) for task in tasks],
    }
    path = root / "task_plan.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_full_season_plan_has_three_benchmarks_and_39_original_scale_tasks(tmp_path: Path) -> None:
    path = _fake_plan(tmp_path / "run")
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks = [FullSeasonTask(**row) for row in payload["tasks"]]
    assert len([task for task in tasks if task.phase == "benchmark"]) == 3
    assert len([task for task in tasks if task.phase == "endpoint"]) == 6
    assert len([task for task in tasks if task.phase == "epsilon"]) == 33
    assert {task.threads for task in tasks if task.phase == "benchmark"} == {1, 4, 8}
    assert all(task.ready for task in tasks if task.phase == "endpoint")
    assert all(not task.ready for task in tasks if task.phase == "epsilon")
    assert all(task.time_limit_seconds == 21600 for task in tasks if task.phase != "benchmark")
    assert all(task.mip_gap == 0.01 for task in tasks if task.phase != "benchmark")


def test_epsilon_resolution_requires_all_endpoints_and_generates_11_per_mode(tmp_path: Path) -> None:
    root = tmp_path / "run"
    _fake_plan(root)
    with pytest.raises(RuntimeError, match="端点尚未完成"):
        server.resolve_epsilon_tasks(root)
    for mode_index, mode in enumerate(server.MODES):
        low = 100.0 + mode_index
        high = 200.0 + mode_index
        for name, carbon in (("cost", high), ("carbon", low)):
            task_dir = root / "tasks" / f"{mode}-{name}"
            task_dir.mkdir()
            (task_dir / "point.json").write_text(
                json.dumps({"annual_operating_carbon_kgCO2e_per_year": carbon}),
                encoding="utf-8",
            )
    server.resolve_epsilon_tasks(root)
    payload = json.loads((root / "task_plan.json").read_text(encoding="utf-8"))
    epsilon = [FullSeasonTask(**row) for row in payload["tasks"] if row["phase"] == "epsilon"]
    assert len(epsilon) == 33 and all(task.ready for task in epsilon)
    for mode in server.MODES:
        points = [task.epsilon_kgCO2e_per_year for task in epsilon if task.mode == mode]
        assert points == sorted(points)
        assert len(points) == 11


def test_status_skips_success_and_preserves_blocked_epsilon(tmp_path: Path) -> None:
    root = tmp_path / "run"
    _fake_plan(root)
    success = root / "tasks" / "central-cost" / "task_success.json"
    success.parent.mkdir()
    success.write_text("{}", encoding="utf-8")
    status = server.task_status(root)
    assert status["counts"]["success"] == 1
    assert status["counts"]["blocked_waiting_endpoints"] == 33
    assert status["core_sha256"] == server.frozen_core_record()["sha256"]


def test_worker_count_respects_cpu_memory_and_six_worker_cap(monkeypatch) -> None:
    monkeypatch.setattr(server.os, "cpu_count", lambda: 60)
    monkeypatch.setattr(server, "_available_memory_bytes", lambda: 128 * 1024**3)
    assert server.recommended_worker_count() == 6
    monkeypatch.setattr(server, "_available_memory_bytes", lambda: 32 * 1024**3)
    assert server.recommended_worker_count() == 1
    monkeypatch.setattr(server.os, "cpu_count", lambda: 120)
    monkeypatch.setattr(server, "_available_memory_bytes", lambda: 512 * 1024**3)
    assert server.recommended_worker_count(physical_core_count=24) == 3


def test_benchmark_summary_requires_one_mathematical_model_hash(tmp_path: Path) -> None:
    root = tmp_path / "run"
    _fake_plan(root)
    for threads in (1, 4, 8):
        task_id = f"benchmark-central-cost-t{threads}"
        success = root / "tasks" / task_id / "task_success.json"
        success.parent.mkdir()
        success.write_text(
            json.dumps(
                {
                    "status": "benchmark_complete_unaccepted",
                    "model_sha256": "same-mps",
                    "incumbent_objective": 100.0,
                    "best_objective_bound": 90.0 + threads,
                    "reported_mip_gap": 0.1 - threads / 100.0,
                }
            ),
            encoding="utf-8",
        )
    report = server.summarize_benchmarks(root)
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "same_mathematical_model_verified"
    assert payload["model_sha256"] == "same-mps"
    assert [row["threads"] for row in payload["benchmarks"]] == [1, 4, 8]


def _write_main_points(root: Path) -> None:
    payload = json.loads((root / "task_plan.json").read_text(encoding="utf-8"))
    for row in payload["tasks"]:
        task = FullSeasonTask(**row)
        if task.phase not in {"endpoint", "epsilon"}:
            continue
        mode_offset = server.MODES.index(task.mode) * 1000.0
        if task.phase == "epsilon":
            index = int(task.task_id.rsplit("-", 1)[1])
        elif task.objective == "carbon":
            index = 0
        else:
            index = 10
        task_dir = root / "tasks" / task.task_id
        task_dir.mkdir(exist_ok=True)
        (task_dir / "point.json").write_text(
            json.dumps(
                {
                    "point_id": task.point_id,
                    "mode": task.mode,
                    "labels": ["test"],
                    "epsilon_kgCO2e_per_year": task.epsilon_kgCO2e_per_year,
                    "annual_real_cost_CNY_per_year": (
                        400.0 - index * 10.0 - index * index + mode_offset
                    ),
                    "annual_operating_carbon_kgCO2e_per_year": 100.0 + index * 20.0,
                    "annual_hns_penalty_CNY_per_year": 0.0,
                    "unserved_heat_kWh": 0.0,
                }
            ),
            encoding="utf-8",
        )


def test_representatives_are_unique_tasks_with_point_one_percent_gap(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "run"
    _fake_plan(root)
    _write_main_points(root)

    class FakeCase:
        raw_config = {
            "qa": {
                "unserved_tolerance_kWh": 1e-6,
                "cost_tolerance_CNY_per_year": 1e-6,
                "carbon_tolerance_kgCO2e_per_year": 1e-6,
            }
        }

    monkeypatch.setattr(server, "load_v3_case", lambda *args, **kwargs: FakeCase())
    server.resolve_representative_tasks(
        root, policy_carbon_constraint_kgCO2e_per_year=200.0
    )
    payload = json.loads((root / "task_plan.json").read_text(encoding="utf-8"))
    tasks = [
        FullSeasonTask(**row)
        for row in payload["tasks"]
        if row["phase"] == "representative"
    ]
    assert 1 <= len(tasks) <= 4
    assert all(task.mip_gap == 0.001 for task in tasks)
    assert all(task.time_limit_seconds == 43200 for task in tasks)
    assert payload["representative_rules_count"] == 4
    mapping = payload["representative_selection"]
    assert set(mapping) == {
        "minimum_cost",
        "minimum_carbon",
        "normalized_knee",
        "policy_constraint",
    }
    assert all(row["certification_task_id"] for row in mapping.values())


def test_representative_resolution_requires_explicit_finite_policy_value(tmp_path: Path) -> None:
    root = tmp_path / "run"
    _fake_plan(root)
    with pytest.raises(ValueError, match="有限数值"):
        server.resolve_representative_tasks(
            root, policy_carbon_constraint_kgCO2e_per_year=float("nan")
        )
