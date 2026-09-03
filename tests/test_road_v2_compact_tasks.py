from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from competition.pareto import ParetoPoint
from competition.core_model import ThermalStorageSpec
from competition.road_joint_v2.economic_package import file_hash
from competition.road_joint_v2.compact_tasks import (
    RESULT_SCHEMA,
    _reserve_attempt,
    _strictly_exceeds_with_roundoff,
    assemble_compact_run,
    collect_run_status,
    create_compact_plan,
    epsilon_for_task,
    mark_tes_fallback,
    mark_tes_qualified,
    parse_highs_progress,
    process_is_alive,
    run_compact_task,
    verify_compact_plan,
)
from test_road_v2_core import shared_case
from scripts.run_compact_fullseason import (
    _acquire_driver_reservation,
    _release_driver_reservation,
)


def test_process_liveness_probe_never_terminates_the_worker():
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    worker = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    try:
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from competition.road_joint_v2.compact_tasks import "
                    "process_is_alive; "
                    f"raise SystemExit(0 if process_is_alive({worker.pid}) else 2)"
                ),
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            creationflags=creationflags,
            timeout=10,
        )
        assert probe.returncode == 0, probe.stderr
        assert worker.poll() is None
    finally:
        worker.terminate()
        worker.wait(timeout=10)
    assert not process_is_alive(worker.pid)


def test_live_task_reservation_is_rejected_without_killing_owner(tmp_path):
    task_root = tmp_path / "task"
    _reserve_attempt(task_root)
    with pytest.raises(RuntimeError, match="live worker"):
        _reserve_attempt(task_root)
    assert process_is_alive(os.getpid())


def test_driver_lock_rejects_a_second_process_and_recovers_after_crash(tmp_path):
    run_root = tmp_path / "run"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    child_code = "\n".join(
        (
            "import time",
            "from pathlib import Path",
            "from scripts.run_compact_fullseason import _acquire_driver_reservation",
            f"lock = _acquire_driver_reservation(Path({json.dumps(str(run_root))}))",
            "print('READY', flush=True)",
            "time.sleep(30)",
        )
    )
    owner = subprocess.Popen(
        [sys.executable, "-c", child_code],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
    )
    try:
        assert owner.stdout is not None
        ready = owner.stdout.readline().strip()
        assert ready == "READY"
        with pytest.raises(RuntimeError, match="already running"):
            _acquire_driver_reservation(run_root)
        assert owner.poll() is None
    finally:
        if owner.poll() is None:
            owner.terminate()
        owner.wait(timeout=10)

    recovered = _acquire_driver_reservation(run_root)
    _release_driver_reservation(run_root, recovered)
    assert not (run_root / "driver_reservation.json").exists()


def test_two_processes_racing_for_new_driver_lock_have_one_winner(tmp_path):
    run_root = tmp_path / "run"
    gate = tmp_path / "start"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    child_code = "\n".join(
        (
            "import time",
            "from pathlib import Path",
            "from scripts.run_compact_fullseason import _acquire_driver_reservation",
            f"root = Path({json.dumps(str(run_root))})",
            f"gate = Path({json.dumps(str(gate))})",
            "while not gate.exists(): time.sleep(0.001)",
            "try:",
            "    lock = _acquire_driver_reservation(root)",
            "    print('ACQUIRED', flush=True)",
            "    time.sleep(30)",
            "except RuntimeError:",
            "    print('REJECTED', flush=True)",
        )
    )
    contenders = [
        subprocess.Popen(
            [sys.executable, "-c", child_code],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=creationflags,
        )
        for _ in range(2)
    ]
    try:
        gate.touch()
        outcomes = []
        for contender in contenders:
            assert contender.stdout is not None
            outcomes.append(contender.stdout.readline().strip())
        assert sorted(outcomes) == ["ACQUIRED", "REJECTED"]
    finally:
        for contender in contenders:
            if contender.poll() is None:
                contender.terminate()
        for contender in contenders:
            contender.wait(timeout=10)


def _small_plan(tmp_path: Path):
    root = tmp_path / "compact"
    plan = create_compact_plan(
        shared_case(),
        root,
        full_scale=False,
        expected_site_count=2,
        max_parallel=1,
        threads=1,
        task_time_limit_seconds=60,
    )
    return root, plan


def _fake_success(root: Path, task: dict, *, cost: float, carbon: float) -> None:
    point = ParetoPoint(
        point_id=task["task_id"],
        mode=task["mode"],
        labels=("synthetic_test",),
        epsilon_kgCO2e_per_year=None,
        annual_real_cost_CNY_per_year=cost,
        annual_operating_carbon_kgCO2e_per_year=carbon,
        annual_hns_penalty_CNY_per_year=0.0,
        unserved_heat_kWh=0.0,
        solver_status="ok",
        termination_condition="optimal",
        reported_mip_gap=0.0,
    )
    target = root / "compact_tasks" / task["task_id"] / "success.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "schema": RESULT_SCHEMA,
                "task_id": task["task_id"],
                "mode": task["mode"],
                "site_id": task["site_id"],
                "topology_id": task["topology_id"],
                "point": asdict(point),
                "qa": {"passed": True},
                "acceptance_mip_gap": 0.03,
                "output_sha256": {},
                "solution_directory": "unused",
                "enable_tes": bool(task.get("enable_tes", False)),
            },
            allow_nan=False,
        ),
        encoding="utf-8",
    )


def test_compact_plan_is_fresh_hash_locked_and_has_dependency_shape(tmp_path):
    root, plan = _small_plan(tmp_path)

    assert plan["schema"] == "road_joint_v2_compact_fullseason_tasks_1"
    assert len(plan["tree_designs"]) == 2
    assert len(plan["tasks"]) == 21  # 1 distributed + 2 modes * 2 sites * (2 + 3)
    assert sum(task["phase"] == "endpoint" for task in plan["tasks"]) == 9
    assert sum(task["phase"] == "epsilon" for task in plan["tasks"]) == 12
    assert plan["solver"]["threads_per_task"] == 1
    assert plan["wallclock_budget_seconds"] == 3000
    assert plan["storage_policy"]["enable_tes"] is False
    assert verify_compact_plan(root) == plan
    with pytest.raises(FileExistsError, match="fresh root"):
        create_compact_plan(
            shared_case(), root, full_scale=False, expected_site_count=2
        )
    with pytest.raises(ValueError, match="requires case.common.storage"):
        create_compact_plan(
            shared_case(),
            tmp_path / "tes",
            full_scale=False,
            expected_site_count=2,
            enable_tes=True,
        )


def test_global_epsilon_is_derived_after_all_endpoints_and_site_infeasibility_is_skipped(tmp_path):
    root, plan = _small_plan(tmp_path)
    endpoints = [task for task in plan["tasks"] if task["phase"] == "endpoint"]
    with pytest.raises(ValueError, match="endpoints"):
        epsilon_for_task(root, "central-site-01-epsilon-025")

    for task in endpoints:
        if task["mode"] == "distributed":
            cost, carbon = 55.0, 100.0
        elif task["mode"] == "central" and task["objective"] == "cost":
            cost, carbon = 40.0 + (task["topology_id"] == "site-02"), 120.0
        elif task["mode"] == "central":
            cost, carbon = 80.0, 90.0  # this site cannot meet the first global cap
        elif task["objective"] == "cost":
            cost, carbon = 30.0 + (task["topology_id"] == "site-02"), 110.0
        else:
            cost, carbon = 70.0, 50.0
        _fake_success(root, task, cost=cost, carbon=carbon)

    epsilon, reason = epsilon_for_task(root, "hybrid-site-01-epsilon-025")
    assert epsilon == pytest.approx(65.0)  # 50 + .25 * (110 - 50)
    assert reason is None
    central_epsilon, central_reason = epsilon_for_task(
        root, "central-site-01-epsilon-025"
    )
    assert central_epsilon == pytest.approx(65.0)
    assert "site_certified_lower_bound=90" in central_reason

    # A carbon incumbent is not an infeasibility proof when the primary solve
    # still has a gap.  Only the certified objective lower bound may skip a cap.
    certificate_path = (
        root / "compact_tasks" / "central-site-01-carbon" / "success.json"
    )
    certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
    certificate["point"]["reported_mip_gap"] = 0.01
    certificate["point"]["termination_condition"] = "maxTimeLimit"
    certificate["carbon_certificate"] = {
        "solver_evidence": {"best_objective_bound": 60.0}
    }
    certificate_path.write_text(json.dumps(certificate), encoding="utf-8")
    _, bounded_reason = epsilon_for_task(root, "central-site-01-epsilon-025")
    assert bounded_reason is None

    status = collect_run_status(root)
    assert status["qualified_task_count"] == len(endpoints)
    assert status["endpoint_barrier_complete"]
    result = assemble_compact_run(root, allow_partial=True, replay_final=False)
    assert set(result.mode_frontiers) == {"central", "distributed", "hybrid"}
    summary = json.loads(
        (root / "compact_pareto_frontiers.json").read_text(encoding="utf-8")
    )
    assert summary["complete"] is False
    assert summary["global_epsilon_range_kgCO2e_per_year"] == [50.0, 110.0]
    assert summary["final_replay_complete"] is False
    assert not (root / "completion_manifest.json").exists()


def test_tes_plan_adds_an_independent_family_and_a_single_gate(tmp_path):
    source = shared_case()
    storage = ThermalStorageSpec(
        "tes", 200.0, 200.0, 200.0, 0.9, 0.9,
        0.01, 0.01, 0.01, 0.01, 20,
    )
    case = replace(source, common=replace(source.common, storage=storage))
    root = tmp_path / "compact_tes"
    plan = create_compact_plan(
        case,
        root,
        full_scale=False,
        expected_site_count=2,
        max_parallel=1,
        threads=1,
        task_time_limit_seconds=60,
        enable_tes=True,
    )

    assert len(plan["tasks"]) == 41
    assert sum(task["result_family"] == "no_tes" for task in plan["tasks"]) == 21
    assert sum(task["result_family"] == "tes" for task in plan["tasks"]) == 20
    gates = [task for task in plan["tasks"] if task["tes_gate"]]
    assert [task["task_id"] for task in gates] == ["tes-hybrid-site-01-cost"]
    assert plan["storage_policy"]["tes_gate_time_limit_seconds"] == 120.0
    assert verify_compact_plan(root) == plan

    mark_tes_fallback(root, "synthetic gate failure")
    with pytest.raises(ValueError, match="fallback is terminal"):
        mark_tes_qualified(root)


def test_qualified_tes_result_still_targets_no_tes_fallback_for_replay(tmp_path):
    source = shared_case()
    storage = ThermalStorageSpec(
        "tes", 200.0, 200.0, 200.0, 0.9, 0.9,
        0.01, 0.01, 0.01, 0.01, 20,
    )
    case = replace(source, common=replace(source.common, storage=storage))
    root = tmp_path / "compact_tes_replay"
    plan = create_compact_plan(
        case,
        root,
        full_scale=False,
        expected_site_count=2,
        max_parallel=1,
        threads=1,
        task_time_limit_seconds=60,
        enable_tes=True,
    )
    for index, task in enumerate(plan["tasks"]):
        is_tes = bool(task.get("enable_tes"))
        _fake_success(
            root,
            task,
            cost=100.0 + index - (50.0 if is_tes else 0.0),
            carbon=100.0 - index - (50.0 if is_tes else 0.0),
        )
    mark_tes_qualified(root)

    result = assemble_compact_run(root, replay_final=False)
    payload = json.loads(
        (root / "compact_pareto_frontiers.json").read_text(encoding="utf-8")
    )
    combined_ids = {point.point_id for point in result.combined_frontier}
    baseline_ids = {
        point["point_id"] for point in payload["no_tes_baseline_frontier"]
    }
    replay_ids = set(payload["final_replay_target_point_ids"])

    assert any(point_id.startswith("tes-") for point_id in combined_ids)
    assert baseline_ids
    assert baseline_ids <= replay_ids
    assert combined_ids <= replay_ids
    assert payload["final_replay_complete"] is False


def test_highs_progress_parser_reports_bound_incumbent_and_gap():
    log = """
        Nodes      |    B&B Tree     |            Objective Bounds
         0       0         0   0.00%   23900669.73866  45713580.96116    47.72%   0 0 0 33420 7.4s
         0       0         0   0.00%   23910785.88016  45713580.96116    47.69%   0 0 0 33452 119.3s
    """
    row = parse_highs_progress(log)
    assert row == {
        "best_objective_bound": pytest.approx(23910785.88016),
        "incumbent_objective": pytest.approx(45713580.96116),
        "relative_mip_gap": pytest.approx(0.4769),
    }
    assert parse_highs_progress("Presolving model") is None


def test_certified_boundary_comparison_ignores_only_float_roundoff():
    bound = 21.697430774410776
    allowed = bound + 1e-6

    assert not _strictly_exceeds_with_roundoff(21.69743177441078, allowed)
    assert not _strictly_exceeds_with_roundoff(7_000_000.000004, 7_000_000.0)
    assert _strictly_exceeds_with_roundoff(allowed + 1e-4, allowed)
    assert _strictly_exceeds_with_roundoff(7_000_000.001, 7_000_000.0)


def test_real_small_distributed_task_closes_solver_export_and_qa_loop(tmp_path):
    root, _ = _small_plan(tmp_path)
    point = run_compact_task(root, "distributed-unique")

    assert point is not None
    assert point.mode == "distributed"
    assert point.reported_mip_gap == pytest.approx(0.0)
    success = json.loads(
        (root / "compact_tasks" / "distributed-unique" / "success.json").read_text(
            encoding="utf-8"
        )
    )
    assert success["qa"]["passed"]
    assert success["qa"]["compact_full_horizon_qa"]["passed"]
    assert success["enable_tes"] is False
    assert success["output_sha256"]
    checkpoint = root / success["checkpoint_file"]
    assert checkpoint.is_file()
    solver_evidence = json.loads(
        (checkpoint.parent / "solver_evidence.json").read_text(encoding="utf-8")
    )
    assert solver_evidence["model_file"] is None
    assert not (checkpoint.parent / "model.mps").exists()

    result = assemble_compact_run(root, allow_partial=True)
    assert len(result.combined_frontier) == 1
    replay = json.loads(
        (root / "final_replay" / "distributed-unique" / "success.json").read_text(
            encoding="utf-8"
        )
    )
    assert replay["continuous_replay"]
    assert replay["fixed_pipe_grade_binary_count"] == 6
    assert replay["fixed_tes_install_binary_count"] == 0
    assert replay["qa"]["export_independent_qa"]["passed"]
    replay_attempt = root / "final_replay" / "distributed-unique" / "attempt_0001"
    assert (replay_attempt / "model.mps").is_file()
    replay_evidence = json.loads(
        (replay_attempt / "solver_evidence.json").read_text(encoding="utf-8")
    )
    assert Path(replay_evidence["model_file"]).resolve() == (
        replay_attempt / "model.mps"
    ).resolve()
    assert replay_evidence["model_sha256"] == file_hash(replay_attempt / "model.mps")
    assert replay["task_plan_sha256"]
    assert replay["case_sha256"]
    assert replay["mathematics_sha256"]
