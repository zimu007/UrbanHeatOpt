"""Prepare, run, monitor, and assemble the unlimited compact full-season run.

Examples
--------
Prepare a fresh hash-locked root from the most recent 62 x 2160 case::

    python tools/legacy_cli/run_compact_fullseason.py --run-root runs/road_joint_v2/COMPACT_50M --action prepare

Run/resume the dependency-safe four-worker schedule::

    python tools/legacy_cli/run_compact_fullseason.py --run-root runs/road_joint_v2/COMPACT_50M --action run

Watch evidence-based progress from another terminal::

    python tools/legacy_cli/run_compact_fullseason.py --run-root runs/road_joint_v2/COMPACT_50M --action status --watch
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from time import sleep, time
from typing import Any
from uuid import uuid4


REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from urbanheatopt.data.road_builder import load_case
from urbanheatopt.parameters.legacy_economics import file_hash
from urbanheatopt.optimization.compact_tasks import (
    assemble_compact_run,
    collect_run_status,
    create_compact_plan,
    family_task_ids,
    mark_driver_abort,
    mark_tes_fallback,
    mark_tes_qualified,
    replay_compact_point,
    run_compact_task,
    verify_compact_plan,
    write_run_status,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: Any, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    if exclusive:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(data)
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(data, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@dataclass
class _DriverReservation:
    """An OS-held single-driver lock plus its diagnostic ownership token."""

    stream: Any
    token: str


def _lock_driver_stream(stream: Any) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_driver_stream(stream: Any) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _acquire_driver_reservation(root: Path) -> _DriverReservation:
    """Allow one mutating schedule driver per run root using an OS lock."""

    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "driver.lock"
    metadata_path = root / "driver_reservation.json"
    stream = lock_path.open("a+b")
    locked = False
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        try:
            _lock_driver_stream(stream)
            locked = True
        except (OSError, BlockingIOError) as exc:
            try:
                prior = json.loads(metadata_path.read_text(encoding="utf-8"))
                owner = (
                    f"pid={prior.get('pid')}, "
                    f"created_at={prior.get('created_at')}"
                )
            except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
                owner = "owner metadata is not yet available"
            raise RuntimeError(
                "another compact driver is already running for this run root: "
                f"{owner}"
            ) from exc

        token = uuid4().hex
        _write_json(
            metadata_path,
            {
                "schema": "road_joint_v2_compact_driver_reservation_2",
                "pid": os.getpid(),
                "token": token,
                "created_at": _utcnow(),
                "lock_file": lock_path.name,
            },
        )
        return _DriverReservation(stream=stream, token=token)
    except Exception:
        if locked:
            try:
                _unlock_driver_stream(stream)
            except OSError:
                pass
        stream.close()
        raise


def _release_driver_reservation(root: Path, reservation: _DriverReservation) -> None:
    metadata_path = root / "driver_reservation.json"
    try:
        try:
            current = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            current = {}
        if not isinstance(current, dict):
            current = {}
        if (
            current.get("pid") == os.getpid()
            and current.get("token") == reservation.token
        ):
            try:
                metadata_path.unlink()
            except OSError:
                pass
    finally:
        try:
            _unlock_driver_stream(reservation.stream)
        except OSError:
            # Closing the descriptor below also releases the kernel lock.  A
            # best-effort cleanup must not mask the driver's real exit status.
            pass
        finally:
            try:
                reservation.stream.close()
            except OSError:
                pass


def _find_existing_full_case() -> Path:
    run_parent = REPOSITORY / "runs" / "road_joint_v2"
    preferred = run_parent / "FULL_ACCELERATED_20260831" / "case.json"
    candidates = [preferred] if preferred.is_file() else []
    candidates.extend(
        sorted(
            (path for path in run_parent.glob("*/case.json") if path != preferred),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
    )
    for path in candidates:
        try:
            case = load_case(path)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if len(case.common.demand_nodes) == 62 and len(case.common.hours) == 2160:
            return path.resolve()
    raise FileNotFoundError(
        "no existing serialized 62-building x 2160-hour RoadCase was found; pass --case-json"
    )


def _task_status_map(root: Path) -> dict[str, str]:
    status = collect_run_status(root)
    return {row["task_id"]: row["status"] for row in status["tasks"]}


def _progress_line(status: dict[str, Any]) -> str:
    counts = status["counts"]
    active = status["active_solver_progress"]
    solver = ""
    if active:
        row = active[0]
        gap = row.get("reported_mip_gap")
        gap_text = "?" if gap is None else f"{100 * gap:.2f}%"
        solver = (
            f" | {row['task_id']} gap={gap_text} "
            f"bound={row.get('best_objective_bound')} incumbent={row.get('incumbent_objective')}"
        )
    replay = ""
    if status.get("final_replay_point_count"):
        replay = (
            f" replay={status['final_replay_complete_count']}/"
            f"{status['final_replay_point_count']}"
        )
    remaining = status.get("remaining_budget_seconds")
    remaining_text = "unlimited" if remaining is None else f"{float(remaining):.0f}s"
    return (
        f"[{status['progress_percent']:6.2f}%] qualified={status['qualified_task_count']}/"
        f"{status['total_tasks']} running={counts['running']} failed={counts['failed']} "
        f"remaining={remaining_text}{replay}{solver}"
    )


def _optional_deadline(value: Any) -> float | None:
    return float(value) if value is not None else None


def _deadline_reached(deadline: float | None, *, now: float | None = None) -> bool:
    return deadline is not None and (time() if now is None else now) >= deadline


def _has_start_window(deadline: float | None, seconds: float) -> bool:
    return deadline is None or deadline - time() > seconds


def _remaining_text(deadline: float | None) -> str:
    return "unlimited" if deadline is None else f"{max(0.0, deadline - time()):.0f}s"


def _start_control(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    path = root / "run_control.json"
    hard_limit = plan.get("wallclock_budget_seconds")
    if path.is_file():
        control = json.loads(path.read_text(encoding="utf-8"))
        if control.get("hard_budget_seconds") != hard_limit:
            raise ValueError("existing run control does not match the frozen wallclock policy")
        return control
    started = time()
    control = {
        "schema": "road_joint_v2_compact_fullseason_control_2",
        "started_at": _utcnow(),
        "started_epoch_seconds": started,
        "deadline_epoch_seconds": (
            started + float(hard_limit) if hard_limit is not None else None
        ),
        "hard_budget_seconds": hard_limit,
        "memory_limit_bytes": None,
        "memory_guard_enabled": False,
        "max_parallel_tasks": plan["solver"]["max_parallel_tasks"],
        "threads_per_task": plan["solver"]["threads_per_task"],
    }
    _write_json(path, control, exclusive=True)
    return control


def _verified_completion(root: Path, plan: dict[str, Any]) -> dict[str, Any] | None:
    manifest_path = root / "completion_manifest.json"
    result_path = root / "compact_pareto_frontiers.json"
    if not manifest_path.is_file() or not result_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if (
        manifest.get("status") != "complete"
        or manifest.get("final_replay_complete") is not True
        or manifest.get("task_plan_sha256") != file_hash(root / "compact_task_plan.json")
        or manifest.get("case_sha256") != plan["case_sha256"]
        or manifest.get("result_file_sha256") != file_hash(result_path)
    ):
        return None
    return manifest


def _next_driver_log(root: Path, task_id: str) -> Path:
    directory = root / "driver_logs"
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(1, 10000):
        path = directory / f"{task_id}.attempt_{index:04d}.log"
        if not path.exists():
            return path
    raise RuntimeError(f"too many driver log attempts for {task_id}")


def _terminate_workers(root: Path, workers: dict[str, tuple[subprocess.Popen, Any, Path]], reason: str) -> None:
    for process, _, _ in workers.values():
        if process.poll() is None:
            process.terminate()
    for task_id, (process, stream, _) in list(workers.items()):
        if process.poll() is None:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        stream.close()
        mark_driver_abort(root, task_id, reason)
        workers.pop(task_id, None)


def _run_stage(
    root: Path,
    task_ids: tuple[str, ...],
    *,
    deadline: float | None,
    max_parallel: int,
    retry_limit: int = 2,
    deadline_reason: str = "compact solve-stage deadline reached",
) -> bool:
    status_map = _task_status_map(root)
    queue = [task_id for task_id in task_ids if status_map.get(task_id) not in {"success", "skipped"}]
    attempts = {task_id: 0 for task_id in queue}
    workers: dict[str, tuple[subprocess.Popen, Any, Path]] = {}
    last_report = 0.0
    try:
        while queue or workers:
            now = time()
            if _deadline_reached(deadline, now=now):
                _terminate_workers(root, workers, deadline_reason)
                write_run_status(root)
                return False
            while queue and len(workers) < max_parallel and _has_start_window(deadline, 5):
                task_id = queue.pop(0)
                attempts[task_id] += 1
                log_path = _next_driver_log(root, task_id)
                stream = log_path.open("x", encoding="utf-8")
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--run-root",
                    str(root),
                    "--action",
                    "run-task",
                    "--task-id",
                    task_id,
                ]
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
                process = subprocess.Popen(
                    command,
                    cwd=REPOSITORY,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
                workers[task_id] = (process, stream, log_path)
            sleep(1)
            for task_id, (process, stream, log_path) in list(workers.items()):
                code = process.poll()
                if code is None:
                    continue
                stream.close()
                workers.pop(task_id)
                current = _task_status_map(root).get(task_id)
                if current not in {"success", "skipped"}:
                    if attempts[task_id] < retry_limit and _has_start_window(deadline, 10):
                        queue.append(task_id)
                    else:
                        print(
                            f"[failed] {task_id}: exit={code}, log={log_path}",
                            file=sys.stderr,
                            flush=True,
                        )
            if time() - last_report >= 15:
                status = write_run_status(root)
                print(_progress_line(status), flush=True)
                last_report = time()
    except BaseException:
        _terminate_workers(root, workers, "compact driver interrupted")
        write_run_status(root)
        raise
    final = write_run_status(root)
    final_map = {row["task_id"]: row["status"] for row in final["tasks"]}
    return all(final_map.get(task_id) in {"success", "skipped"} for task_id in task_ids)


def run_schedule(root: str | Path) -> bool:
    root = Path(root).expanduser().resolve()
    plan = verify_compact_plan(root)
    control = _start_control(root, plan)
    deadline = _optional_deadline(control.get("deadline_epoch_seconds"))
    max_parallel = int(plan["solver"]["max_parallel_tasks"])
    started = float(control["started_epoch_seconds"])
    # A finite deadline remains available as an explicit opt-in for controlled
    # experiments.  The production default is ``None``: neither scan nor final
    # replay is stopped by elapsed wallclock time.
    scan_deadline = (
        min(deadline - 720.0, started + 2280.0)
        if deadline is not None
        else None
    )
    deadline_label = (
        datetime.fromtimestamp(deadline, timezone.utc).isoformat()
        if deadline is not None
        else "unlimited"
    )
    print(
        f"Compact full-season run: {len(plan['tasks'])} tasks, "
        f"{max_parallel} parallel x {plan['solver']['threads_per_task']} HiGHS threads, "
        f"deadline={deadline_label}, memory_guard=disabled",
        flush=True,
    )
    no_tes_endpoints = family_task_ids(plan, "no_tes", phase="endpoint")
    no_tes_epsilons = family_task_ids(plan, "no_tes", phase="epsilon")
    baseline_ids = (*no_tes_endpoints, *no_tes_epsilons)
    state_map = _task_status_map(root)
    baseline_complete = all(
        state_map.get(task_id) in {"success", "skipped"}
        for task_id in baseline_ids
    )
    if not baseline_complete and _deadline_reached(scan_deadline):
        if plan.get("storage_policy", {}).get("enable_tes"):
            mark_tes_fallback(root, "no-TES baseline did not complete")
        write_run_status(root)
        return False
    endpoints_ok = _run_stage(
        root,
        no_tes_endpoints,
        deadline=scan_deadline,
        max_parallel=max_parallel,
        deadline_reason="optional wallclock deadline reached during no-TES endpoints",
    )
    if not endpoints_ok:
        if plan.get("storage_policy", {}).get("enable_tes"):
            mark_tes_fallback(root, "no-TES baseline did not complete")
        return False
    epsilons_ok = _run_stage(
        root,
        no_tes_epsilons,
        deadline=scan_deadline,
        max_parallel=max_parallel,
        deadline_reason="optional wallclock deadline reached during no-TES Pareto scan",
    )
    if not epsilons_ok:
        if plan.get("storage_policy", {}).get("enable_tes"):
            mark_tes_fallback(root, "no-TES baseline did not complete")
        return False

    if not plan.get("storage_policy", {}).get("enable_tes"):
        return True

    tes_status_path = root / "tes_upgrade_status.json"
    if tes_status_path.is_file():
        tes_status = json.loads(tes_status_path.read_text(encoding="utf-8"))
        if tes_status.get("qualified") is True:
            return True
        if tes_status.get("status") == "fallback_to_no_tes":
            return True

    gate_id = str(plan["storage_policy"]["tes_gate_task_id"])
    gate_deadline = (
        min(started + 1320.0, scan_deadline)
        if scan_deadline is not None
        else None
    )
    gate_ok = _task_status_map(root).get(gate_id) == "success"
    if not gate_ok and not _deadline_reached(gate_deadline):
        gate_ok = _run_stage(
            root,
            (gate_id,),
            deadline=gate_deadline,
            max_parallel=1,
            retry_limit=1,
            deadline_reason="optional wallclock deadline reached during TES qualification",
        )
    if not gate_ok:
        mark_tes_fallback(root, "TES qualification gate did not pass")
        return True

    tes_endpoints = tuple(
        task_id
        for task_id in family_task_ids(plan, "tes", phase="endpoint")
        if task_id != gate_id
    )
    tes_epsilons = family_task_ids(plan, "tes", phase="epsilon")
    tes_endpoints_ok = _run_stage(
        root,
        tes_endpoints,
        deadline=scan_deadline,
        max_parallel=max_parallel,
        deadline_reason="optional wallclock deadline reached during TES endpoints",
    )
    tes_epsilons_ok = tes_endpoints_ok and _run_stage(
        root,
        tes_epsilons,
        deadline=scan_deadline,
        max_parallel=max_parallel,
        deadline_reason="optional wallclock deadline reached during TES Pareto scan",
    )
    if tes_endpoints_ok and tes_epsilons_ok:
        mark_tes_qualified(root)
    else:
        mark_tes_fallback(root, "TES Pareto family did not complete")
    # A failed optional TES enhancement never invalidates the complete no-TES
    # full-season result.
    return True


def _next_replay_log(root: Path, point_id: str) -> Path:
    directory = root / "final_replay_driver_logs"
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(1, 10000):
        path = directory / f"{point_id}.attempt_{index:04d}.log"
        if not path.exists():
            return path
    raise RuntimeError(f"too many replay driver attempts for {point_id}")


def _run_replay_stage(
    root: Path,
    point_ids: tuple[str, ...],
    *,
    deadline: float | None,
    max_parallel: int,
    retry_limit: int = 2,
) -> bool:
    queue = [
        point_id
        for point_id in point_ids
        if not (root / "final_replay" / point_id / "success.json").is_file()
    ]
    attempts = {point_id: 0 for point_id in queue}
    workers: dict[str, tuple[subprocess.Popen, Any, Path]] = {}
    failed: list[str] = []
    last_report = 0.0
    try:
        while queue or workers:
            now = time()
            if _deadline_reached(deadline, now=now):
                failed.extend(queue)
                queue.clear()
                break
            while queue and len(workers) < max_parallel and _has_start_window(deadline, 5):
                point_id = queue.pop(0)
                attempts[point_id] += 1
                log_path = _next_replay_log(root, point_id)
                stream = log_path.open("x", encoding="utf-8")
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--run-root",
                    str(root),
                    "--action",
                    "replay-task",
                    "--task-id",
                    point_id,
                ]
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
                process = subprocess.Popen(
                    command,
                    cwd=REPOSITORY,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
                workers[point_id] = (process, stream, log_path)
            sleep(2)
            for point_id, (process, stream, log_path) in list(workers.items()):
                code = process.poll()
                if code is None:
                    continue
                stream.close()
                workers.pop(point_id)
                success = root / "final_replay" / point_id / "success.json"
                if code != 0 or not success.is_file():
                    if attempts[point_id] < retry_limit and _has_start_window(deadline, 10):
                        queue.append(point_id)
                    else:
                        failed.append(point_id)
                        print(f"[replay failed] {point_id}: exit={code}, log={log_path}", file=sys.stderr, flush=True)
            if time() - last_report >= 30:
                complete = sum(
                    (root / "final_replay" / point_id / "success.json").is_file()
                    for point_id in point_ids
                )
                print(
                    f"[final replay] {complete}/{len(point_ids)} complete; "
                    f"remaining={_remaining_text(deadline)}",
                    flush=True,
                )
                last_report = time()
    finally:
        for process, _, _ in workers.values():
            if process.poll() is None:
                process.terminate()
        for point_id, (process, stream, _) in workers.items():
            if process.poll() is None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            stream.close()
            failed.append(point_id)
    return not failed and all(
        (root / "final_replay" / point_id / "success.json").is_file()
        for point_id in point_ids
    )


def _print_status(root: Path, *, watch: bool, interval: float) -> int:
    while True:
        status = write_run_status(root)
        print(_progress_line(status), flush=True)
        if not watch:
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0
        if status["result_ready"]:
            return 0
        remaining = status.get("remaining_budget_seconds")
        if remaining is not None and remaining <= 0 and not status["counts"]["running"]:
            return 2
        sleep(interval)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--case-json", type=Path)
    parser.add_argument(
        "--action",
        choices=(
            "prepare",
            "run-task",
            "replay-task",
            "run",
            "assemble",
            "status",
            "all",
        ),
        default="all",
    )
    parser.add_argument("--task-id")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument(
        "--task-time-limit-seconds",
        type=float,
        default=None,
        help="optional per-solve limit; omitted means unlimited",
    )
    parser.add_argument(
        "--wallclock-budget-seconds",
        type=float,
        default=None,
        help="optional whole-run limit; omitted means unlimited",
    )
    parser.add_argument("--enable-tes-upgrade", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.run_root.expanduser().resolve()
    driver_reservation: _DriverReservation | None = None
    try:
        if args.action in {"prepare", "run", "assemble", "all"}:
            driver_reservation = _acquire_driver_reservation(root)
        if args.action in {"prepare", "all"} and not (root / "compact_task_plan.json").is_file():
            case_path = args.case_json.expanduser().resolve() if args.case_json else _find_existing_full_case()
            plan = create_compact_plan(
                case_path,
                root,
                wallclock_budget_seconds=args.wallclock_budget_seconds,
                task_time_limit_seconds=args.task_time_limit_seconds,
                enable_tes=args.enable_tes_upgrade,
            )
            print(
                json.dumps(
                    {
                        "prepared": str(root),
                        "case_source": str(case_path),
                        "task_count": len(plan["tasks"]),
                        "case_sha256": plan["case_sha256"],
                        "git_sha": plan["git_sha"],
                        "remote_upload_performed": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                flush=True,
            )
        if args.action == "prepare":
            if (root / "compact_task_plan.json").is_file():
                verify_compact_plan(root)
            return 0
        if args.action == "run-task":
            if not args.task_id:
                raise ValueError("--task-id is required for --action run-task")
            point = run_compact_task(root, args.task_id)
            print(json.dumps(asdict(point) if point is not None else {"status": "skipped"}, ensure_ascii=False, default=str))
            return 0
        if args.action == "replay-task":
            if not args.task_id:
                raise ValueError("--task-id is required for --action replay-task")
            payload = replay_compact_point(root, args.task_id)
            print(json.dumps(payload, ensure_ascii=False, default=str))
            return 0
        if args.action == "status":
            if args.interval <= 0:
                raise ValueError("--interval must be positive")
            return _print_status(root, watch=args.watch, interval=args.interval)
        if args.action == "assemble":
            result = assemble_compact_run(root, allow_partial=args.allow_partial)
            print(f"assembled {len(result.combined_frontier)} globally nondominated points")
            return 0
        if args.action in {"run", "all"}:
            plan = verify_compact_plan(root)
            completed_manifest = _verified_completion(root, plan)
            if completed_manifest is not None:
                print(
                    "compact run is already complete: "
                    f"{completed_manifest['combined_frontier_point_count']} "
                    "globally nondominated points",
                    flush=True,
                )
                return 0
            completed = run_schedule(root)
            if not completed:
                try:
                    assemble_compact_run(
                        root,
                        allow_partial=True,
                        replay_final=False,
                    )
                except (ValueError, OSError, RuntimeError):
                    pass
                write_run_status(root)
                print(
                    "no-TES full-season baseline did not complete; inspect failed task evidence",
                    file=sys.stderr,
                    flush=True,
                )
                return 2
            preliminary = assemble_compact_run(
                root,
                allow_partial=False,
                replay_final=False,
            )
            preliminary_payload = json.loads(
                (root / "compact_pareto_frontiers.json").read_text(encoding="utf-8")
            )
            replay_ids = tuple(preliminary_payload["final_replay_target_point_ids"])
            baseline_replay_ids = tuple(
                point["point_id"]
                for point in preliminary_payload["no_tes_baseline_frontier"]
            )
            preferred_replay_ids = tuple(
                point_id for point_id in replay_ids if point_id not in baseline_replay_ids
            )
            control = _start_control(root, plan)
            baseline_replay_ok = _run_replay_stage(
                root,
                baseline_replay_ids,
                deadline=_optional_deadline(control.get("deadline_epoch_seconds")),
                max_parallel=int(control["max_parallel_tasks"]),
            )
            if not baseline_replay_ok:
                write_run_status(root)
                print(
                    "no-TES fallback replay or independent QA did not finish",
                    file=sys.stderr,
                    flush=True,
                )
                return 2
            replay_ok = _run_replay_stage(
                root,
                preferred_replay_ids,
                deadline=_optional_deadline(control.get("deadline_epoch_seconds")),
                max_parallel=int(control["max_parallel_tasks"]),
            )
            tes_by_id = {
                task["task_id"]: bool(task.get("enable_tes"))
                for task in plan["tasks"]
            }
            if not replay_ok and any(
                tes_by_id.get(point_id) for point_id in preferred_replay_ids
            ):
                mark_tes_fallback(
                    root,
                    "TES fixed-decision replay or independent QA did not pass",
                )
                preliminary = assemble_compact_run(
                    root,
                    allow_partial=False,
                    replay_final=False,
                )
                preliminary_payload = json.loads(
                    (root / "compact_pareto_frontiers.json").read_text(encoding="utf-8")
                )
                replay_ids = tuple(preliminary_payload["final_replay_target_point_ids"])
                replay_ok = _run_replay_stage(
                    root,
                    replay_ids,
                    deadline=_optional_deadline(control.get("deadline_epoch_seconds")),
                    max_parallel=int(control["max_parallel_tasks"]),
                )
            if not replay_ok:
                write_run_status(root)
                print(
                    "compact scan completed, but final fixed-decision replay or QA did not complete",
                    file=sys.stderr,
                    flush=True,
                )
                return 2
            result = assemble_compact_run(
                root,
                allow_partial=False,
                replay_final=True,
            )
            print(
                f"compact run {'completed' if completed else 'ended with a partial result'}: "
                f"{len(result.combined_frontier)} globally nondominated points",
                flush=True,
            )
            return 0
        raise AssertionError(args.action)
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"[compact stopped] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2
    except Exception as exc:
        print(f"[compact failed] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        if driver_reservation is not None:
            _release_driver_reservation(root, driver_reservation)


if __name__ == "__main__":
    raise SystemExit(main())
