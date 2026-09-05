"""Recover the interrupted R3 run without changing its hash-locked sources."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from time import sleep, time
from typing import Any


WORKSPACE = Path(__file__).resolve().parent
REPOSITORY = WORKSPACE / "UrbanHeatOpt"
sys.path.insert(0, str(REPOSITORY))

from competition.road_joint_v2.compact_tasks import (  # noqa: E402
    assemble_compact_run,
    collect_run_status,
    family_task_ids,
    verify_compact_plan,
)
from competition.road_joint_v2.economic_package import file_hash  # noqa: E402
from scripts.run_compact_fullseason import (  # noqa: E402
    _acquire_driver_reservation,
    _release_driver_reservation,
    _run_replay_stage,
)


EXPECTED_MISSING_TASK = "hybrid-site-05-epsilon-075"
FINALIZATION_RESERVE_SECONDS = 45.0


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _next_log(root: Path, stem: str) -> Path:
    directory = root / "recovery_driver_logs"
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(1, 1000):
        candidate = directory / f"{stem}.attempt_{index:04d}.log"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"recovery log IDs exhausted: {stem}")


def _run_missing_task(root: Path, task_id: str, deadline: float) -> None:
    if (root / "compact_tasks" / task_id / "success.json").is_file():
        print(f"[recovery] missing scan task is already complete: {task_id}", flush=True)
        return
    log_path = _next_log(root, task_id)
    command = [
        sys.executable,
        str(REPOSITORY / "scripts" / "run_compact_fullseason.py"),
        "--run-root",
        str(root),
        "--action",
        "run-task",
        "--task-id",
        task_id,
    ]
    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    )
    print(f"[recovery] rerunning only {task_id}; log={log_path}", flush=True)
    with log_path.open("x", encoding="utf-8") as stream:
        process = subprocess.Popen(
            command,
            cwd=REPOSITORY,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        while process.poll() is None:
            if time() >= deadline:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                raise TimeoutError("active recovery budget expired during the missing task")
            sleep(2)
        code = int(process.returncode)
    if code != 0 or not (root / "compact_tasks" / task_id / "success.json").is_file():
        raise RuntimeError(f"recovery task failed with exit={code}; inspect {log_path}")
    print(f"[recovery] scan baseline is now complete: {task_id}", flush=True)


def _incomplete_no_tes_tasks(root: Path, plan: dict[str, Any]) -> list[str]:
    no_tes_ids = (
        *family_task_ids(plan, "no_tes", phase="endpoint"),
        *family_task_ids(plan, "no_tes", phase="epsilon"),
    )
    states = {
        row["task_id"]: row["status"]
        for row in collect_run_status(root)["tasks"]
    }
    return [
        task_id
        for task_id in no_tes_ids
        if states.get(task_id) not in {"success", "skipped"}
    ]


def _initial_recovery_control(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    path = root / "recovery_control.json"
    if path.is_file():
        prior = _read_json(path)
        if prior.get("status") == "complete":
            return prior
        raise RuntimeError(
            "an earlier recovery attempt exists but is not complete; inspect "
            f"{path} before retrying"
        )
    # ``run_status.json`` is refreshed by the read-only watcher, so its elapsed
    # value keeps increasing after the driver has stopped.  Reconstruct the
    # actual first-segment runtime from immutable driver artifacts instead.
    driver_control = _read_json(root / "run_control.json")
    partial_result = _read_json(root / "compact_pareto_frontiers.json")
    driver_started_epoch = float(driver_control["started_epoch_seconds"])
    partial_assembled_epoch = datetime.fromisoformat(
        str(partial_result["assembled_at"])
    ).timestamp()
    # The final run-status write and process exit followed partial assembly by
    # about one second.  Two seconds is a conservative accounting allowance.
    original_elapsed = partial_assembled_epoch - driver_started_epoch + 2.0
    hard_budget = float(plan["wallclock_budget_seconds"])
    allowance = max(0.0, hard_budget - original_elapsed)
    if allowance <= FINALIZATION_RESERVE_SECONDS:
        raise RuntimeError("R3 has no recorded active-compute recovery allowance")
    payload = {
        "schema": "road_joint_v2_compact_recovery_control_1",
        "status": "running",
        "reason": (
            "resume after the scan driver terminated an accepted optimal worker "
            "before checkpoint and success-marker finalization"
        ),
        "budget_accounting": (
            "original driver elapsed plus recovery active elapsed; excludes the "
            "user-requested pause after the controlled driver exit"
        ),
        "original_driver_elapsed_seconds": original_elapsed,
        "original_driver_elapsed_basis": (
            "compact_pareto_frontiers.assembled_at minus "
            "run_control.started_epoch_seconds plus 2 seconds"
        ),
        "recovery_allowance_seconds": allowance,
        "hard_budget_seconds": hard_budget,
        "expected_missing_task_id": EXPECTED_MISSING_TASK,
        "plan_git_sha": plan["git_sha"],
        "task_plan_sha256": file_hash(root / "compact_task_plan.json"),
        "started_at": _utcnow(),
    }
    _write_json(path, payload)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.run_root.expanduser().resolve()
    reservation = None
    recovery_started = time()
    control: dict[str, Any] | None = None
    try:
        reservation = _acquire_driver_reservation(root)
        plan = verify_compact_plan(root)
        control = _initial_recovery_control(root, plan)
        if control.get("status") == "complete":
            print("[recovery] R3 recovery is already complete", flush=True)
            return 0
        deadline = recovery_started + float(control["recovery_allowance_seconds"])
        incomplete = _incomplete_no_tes_tasks(root, plan)
        if incomplete not in ([EXPECTED_MISSING_TASK], []):
            raise RuntimeError(
                "unexpected incomplete no-TES tasks: " + ", ".join(incomplete)
            )
        if incomplete:
            _run_missing_task(root, EXPECTED_MISSING_TASK, deadline)
        remaining = _incomplete_no_tes_tasks(root, plan)
        if remaining:
            raise RuntimeError("no-TES recovery did not close: " + ", ".join(remaining))

        assemble_compact_run(root, allow_partial=False, replay_final=False)
        assembled = _read_json(root / "compact_pareto_frontiers.json")
        replay_ids = tuple(str(item) for item in assembled["final_replay_target_point_ids"])
        if not replay_ids:
            raise RuntimeError("recovery assembly selected no final replay points")
        print(
            f"[recovery] starting {len(replay_ids)} final replays with "
            f"{plan['solver']['max_parallel_tasks']} workers",
            flush=True,
        )
        replay_deadline = deadline - FINALIZATION_RESERVE_SECONDS
        if time() >= replay_deadline:
            raise TimeoutError("no active recovery budget remains for final replay")
        replay_ok = _run_replay_stage(
            root,
            replay_ids,
            deadline=replay_deadline,
            max_parallel=int(plan["solver"]["max_parallel_tasks"]),
        )
        if not replay_ok:
            raise RuntimeError("one or more final replay tasks did not complete")

        result = assemble_compact_run(root, allow_partial=False, replay_final=True)
        final_status = collect_run_status(root)
        manifest_path = root / "completion_manifest.json"
        manifest = _read_json(manifest_path)
        if manifest.get("status") != "complete" or final_status["result_ready"] is not True:
            raise RuntimeError("R3 recovery finished computation but final certification failed")
        recovery_elapsed = time() - recovery_started
        completed_control = {
            **control,
            "status": "complete",
            "finished_at": _utcnow(),
            "recovery_active_elapsed_seconds": recovery_elapsed,
            "combined_active_elapsed_seconds": (
                float(control["original_driver_elapsed_seconds"]) + recovery_elapsed
            ),
            "within_active_3000_second_budget": (
                float(control["original_driver_elapsed_seconds"]) + recovery_elapsed
                <= float(control["hard_budget_seconds"])
            ),
            "final_replay_target_point_ids": list(replay_ids),
            "combined_frontier_point_count": len(result.combined_frontier),
            "completion_manifest_sha256": file_hash(manifest_path),
        }
        _write_json(root / "recovery_control.json", completed_control)
        print(
            "[recovery complete] "
            f"frontier={len(result.combined_frontier)}, replays={len(replay_ids)}, "
            f"active_total={completed_control['combined_active_elapsed_seconds']:.1f}s",
            flush=True,
        )
        return 0
    except BaseException as exc:
        if control is not None:
            failure = {
                **control,
                "status": "failed",
                "failed_at": _utcnow(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "recovery_active_elapsed_seconds": time() - recovery_started,
            }
            _write_json(root / "recovery_control.json", failure)
        print(f"[recovery failed] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2
    finally:
        if reservation is not None:
            _release_driver_reservation(root, reservation)


if __name__ == "__main__":
    raise SystemExit(main())
