"""Hash-locked orchestration for the 62-building compact full-season model.

This module deliberately uses a new plan schema and a new task namespace.  It
cannot consume success markers produced by either ``strict_v2`` or
``budget_50m_v1``.  The compact mathematical model lives in
``competition.road_joint_v2.compact``; this file is responsible only for
evidence, task dependencies, gap qualification, and Pareto assembly.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
import gc
import json
from math import isfinite
import os
from pathlib import Path
import platform
import re
import subprocess
from time import time
from typing import Any, Iterable
from uuid import uuid4

from pyomo.environ import Var, value

from competition.pareto import ParetoPoint, ParetoSpec, assemble_pareto_run
from competition.road_joint_v2.builder import load_case, save_case
from competition.road_joint_v2.economic_package import file_hash
from competition.solvers import SolverSettings, get_solver_evidence, solve_pyomo_model


REPOSITORY = Path(__file__).resolve().parents[2]
PLAN_SCHEMA = "road_joint_v2_compact_fullseason_tasks_2"
STATUS_SCHEMA = "road_joint_v2_compact_fullseason_status_2"
RESULT_SCHEMA = "road_joint_v2_compact_fullseason_result_1"
PROFILE = "compact_fullseason_unlimited_v1"
EPSILON_FRACTIONS = (0.25, 0.50, 0.75)
EPSILON_TOLERANCE = 1e-6


def _strictly_exceeds_with_roundoff(left: float, right: float) -> bool:
    """Compare certified boundaries without rejecting float serialization noise."""

    scale = max(1.0, abs(float(left)), abs(float(right)))
    margin = max(1e-9, 1e-12 * scale)
    return float(left) > float(right) + margin


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _atomic_json(path: str | Path, payload: Any, *, exclusive: bool = False) -> None:
    """Write valid JSON atomically; ``exclusive`` protects immutable evidence."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    )
    if exclusive:
        with target.open("x", encoding="utf-8") as stream:
            stream.write(serialized)
        return
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(serialized, encoding="utf-8")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _normalized_json(value_: Any) -> Any:
    return json.loads(json.dumps(value_, ensure_ascii=False, allow_nan=False))


def compact_mathematics_hashes() -> dict[str, str]:
    """Hash only code that can alter compact mathematics or its certification."""

    relative_paths = (
        "competition/core_model.py",
        "competition/pareto.py",
        "competition/solvers.py",
        "competition/road_joint_v2/builder.py",
        "competition/road_joint_v2/compact.py",
        "competition/road_joint_v2/compact_tasks.py",
        "competition/road_joint_v2/results.py",
        "scripts/run_compact_fullseason.py",
    )
    result: dict[str, str] = {}
    for relative in relative_paths:
        path = REPOSITORY / relative
        if not path.is_file():
            raise FileNotFoundError(f"compact model source is missing: {path}")
        result[relative] = file_hash(path)
    return result


def _git_identity() -> dict[str, Any]:
    def git(*arguments: str) -> str:
        return subprocess.check_output(
            ["git", *arguments], cwd=REPOSITORY, text=True, encoding="utf-8"
        ).strip()

    status = git("status", "--porcelain=v1", "--untracked-files=all")
    return {
        "git_sha": git("rev-parse", "HEAD"),
        "git_worktree_dirty": bool(status),
        "git_status_sha256": __import__("hashlib").sha256(
            status.encode("utf-8")
        ).hexdigest(),
        "remote_upload_performed": False,
    }


def _validate_plan_settings(
    *,
    max_parallel: int,
    threads: int,
    wallclock_budget_seconds: float | None,
    task_time_limit_seconds: float | None,
    scan_gap: float,
    acceptance_gap: float,
) -> None:
    if isinstance(max_parallel, bool) or max_parallel not in range(1, 5):
        raise ValueError("max_parallel must be an integer from 1 to 4")
    if isinstance(threads, bool) or threads not in (1, 4, 8):
        raise ValueError("threads must be one of 1, 4, or 8")
    for name, number in (
        ("wallclock_budget_seconds", wallclock_budget_seconds),
        ("task_time_limit_seconds", task_time_limit_seconds),
    ):
        if number is None:
            continue
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ValueError(f"{name} must be null or finite and positive")
        if not isfinite(float(number)) or float(number) <= 0:
            raise ValueError(f"{name} must be null or finite and positive")
    for name, gap in (("scan_gap", scan_gap), ("acceptance_gap", acceptance_gap)):
        if isinstance(gap, bool) or not isinstance(gap, (int, float)):
            raise ValueError(f"{name} must be a finite fraction")
        if not isfinite(float(gap)) or not 0 <= float(gap) < 1:
            raise ValueError(f"{name} must be a finite fraction in [0, 1)")
    if scan_gap > acceptance_gap:
        raise ValueError("scan_gap cannot be wider than the 3% acceptance gate")
    if acceptance_gap > 0.03 + 1e-15:
        raise ValueError("compact results with a MIP gap above 3% are forbidden")


def _design_metadata(design: Any) -> dict[str, Any]:
    if not hasattr(design, "site_id"):
        raise TypeError("compact tree design is missing site_id")
    metadata = design.as_metadata() if hasattr(design, "as_metadata") else asdict(design)
    normalized = _normalized_json(metadata)
    normalized.setdefault("site_id", str(design.site_id))
    return normalized


def _design_summary(design: Any) -> dict[str, Any]:
    """Return compact task evidence while the full certificate stays in the plan."""

    metadata = _design_metadata(design)
    canonical = json.dumps(
        metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        key: metadata[key]
        for key in (
            "schema",
            "model_version",
            "site_id",
            "root_node_id",
            "source_edge_count",
            "hour_count",
            "selected_edge_count",
            "chain_count",
            "variable_grade_edge_count",
            "retained_pipe_capacity_row_count",
            "full_season_qualified",
            "root_peak_including_losses_kW",
            "total_route_length_m",
        )
    } | {
        "design_certificate_sha256": __import__("hashlib").sha256(canonical).hexdigest()
    }


def _make_tasks(
    designs: list[dict[str, Any]], *, enable_tes_upgrade: bool = False
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = [
        {
            "task_id": "distributed-unique",
            "mode": "distributed",
            "phase": "endpoint",
            "objective": "cost",
            "topology_id": None,
            "site_id": None,
            "epsilon_fraction": None,
            "lexicographic_carbon": False,
            "result_family": "no_tes",
            "enable_tes": False,
            "tes_gate": False,
        }
    ]
    families = [("no_tes", False, "")]
    if enable_tes_upgrade:
        families.append(("tes", True, "tes-"))
    for family, enable_tes, prefix in families:
        # Endpoints form a dependency barrier before any family-specific
        # global epsilon is known.
        for objective in ("cost", "carbon"):
            for topology_index, design in enumerate(designs, 1):
                topology_id = f"site-{topology_index:02d}"
                for mode in ("central", "hybrid"):
                    task_id = f"{prefix}{mode}-{topology_id}-{objective}"
                    tasks.append(
                        {
                            "task_id": task_id,
                            "mode": mode,
                            "phase": "endpoint",
                            "objective": objective,
                            "topology_id": topology_id,
                            "site_id": design["site_id"],
                            "epsilon_fraction": None,
                            "lexicographic_carbon": objective == "carbon",
                            "result_family": family,
                            "enable_tes": enable_tes,
                            "tes_gate": (
                                enable_tes
                                and topology_index == 1
                                and mode == "hybrid"
                                and objective == "cost"
                            ),
                        }
                    )
        for topology_index, design in enumerate(designs, 1):
            topology_id = f"site-{topology_index:02d}"
            for mode in ("central", "hybrid"):
                for fraction in EPSILON_FRACTIONS:
                    tag = f"{round(100 * fraction):03d}"
                    tasks.append(
                        {
                            "task_id": f"{prefix}{mode}-{topology_id}-epsilon-{tag}",
                            "mode": mode,
                            "phase": "epsilon",
                            "objective": "cost",
                            "topology_id": topology_id,
                            "site_id": design["site_id"],
                            "epsilon_fraction": fraction,
                            "lexicographic_carbon": False,
                            "result_family": family,
                            "enable_tes": enable_tes,
                            "tes_gate": False,
                        }
                    )
    return tasks


def create_compact_plan(
    case_source: str | Path | Any,
    root: str | Path,
    *,
    full_scale: bool = True,
    expected_site_count: int | None = None,
    max_parallel: int = 4,
    threads: int = 4,
    wallclock_budget_seconds: float | None = None,
    task_time_limit_seconds: float | None = None,
    scan_gap: float = 0.01,
    acceptance_gap: float = 0.03,
    enable_tes: bool = False,
) -> dict[str, Any]:
    """Create a fresh immutable compact plan from an existing serialized case."""

    _validate_plan_settings(
        max_parallel=max_parallel,
        threads=threads,
        wallclock_budget_seconds=wallclock_budget_seconds,
        task_time_limit_seconds=task_time_limit_seconds,
        scan_gap=scan_gap,
        acceptance_gap=acceptance_gap,
    )
    source_path: Path | None = None
    if isinstance(case_source, (str, Path)):
        source_path = Path(case_source).expanduser().resolve()
        case = load_case(source_path)
    else:
        case = case_source
    if enable_tes and case.common.storage is None:
        raise ValueError("TES upgrade requires case.common.storage; no-TES baseline remains available")
    building_count = len(case.common.demand_nodes)
    hour_count = len(case.common.hours)
    site_count = len(case.common.candidate_station_nodes or ())
    if full_scale and (building_count != 62 or hour_count != 2160):
        raise ValueError("compact full-season plan requires exactly 62 buildings x 2160 hours")
    required_sites = 5 if expected_site_count is None and full_scale else expected_site_count
    if required_sites is not None and site_count != required_sites:
        raise ValueError(
            f"compact plan requires {required_sites} candidate sites; case has {site_count}"
        )

    # Import lazily so serialization/status tests do not build Pyomo models.
    from competition.road_joint_v2.compact import build_compact_tree_designs

    designs = sorted(build_compact_tree_designs(case), key=lambda item: str(item.site_id))
    design_rows = [_design_metadata(design) for design in designs]
    if len(design_rows) != site_count or len({row["site_id"] for row in design_rows}) != site_count:
        raise ValueError("compact tree builder did not return exactly one design per candidate site")
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    case_target = root / "case.json"
    plan_target = root / "compact_task_plan.json"
    if case_target.exists() or plan_target.exists() or (root / "compact_tasks").exists():
        raise FileExistsError(
            "compact run root already contains a plan, case, or task evidence; use a fresh root"
        )
    save_case(case, case_target)
    plan = {
        "schema": PLAN_SCHEMA,
        "execution_profile": PROFILE,
        "created_at": _utcnow(),
        "python_version": platform.python_version(),
        **_git_identity(),
        "case_sha256": file_hash(case_target),
        "source_case_path": str(source_path) if source_path is not None else None,
        "source_case_sha256": file_hash(source_path) if source_path is not None else None,
        "mathematics_sha256": compact_mathematics_hashes(),
        "full_scale": bool(full_scale),
        "building_count": building_count,
        "hour_count": hour_count,
        "site_count": site_count,
        "tree_designs": design_rows,
        "tasks": _make_tasks(design_rows, enable_tes_upgrade=enable_tes),
        "epsilon_fractions": list(EPSILON_FRACTIONS),
        "epsilon_tolerance_kgCO2e_per_year": EPSILON_TOLERANCE,
        "global_epsilon_definition": (
            "minimum certified carbon endpoint to carbon of the globally cheapest "
            "certified cost endpoint; fixed-site caps below that site's carbon endpoint "
            "are certified skipped"
        ),
        "solver": {
            "name": "highs",
            "threads_per_task": int(threads),
            "max_parallel_tasks": int(max_parallel),
            "scan_mip_gap": float(scan_gap),
            "acceptance_mip_gap": float(acceptance_gap),
            "final_target_mip_gap": 0.005,
            "task_time_limit_seconds": (
                float(task_time_limit_seconds)
                if task_time_limit_seconds is not None
                else None
            ),
            "feasibility_tolerance": 1e-7,
            "scan_model_snapshot_policy": (
                "omit_redundant_mps; immutable case/source/tree hashes plus model_build "
                "metadata certify scans; every final replay stores one numeric-label MPS"
            ),
        },
        "wallclock_budget_seconds": (
            float(wallclock_budget_seconds)
            if wallclock_budget_seconds is not None
            else None
        ),
        "resource_limits": {
            "wallclock_time_limit_seconds": (
                float(wallclock_budget_seconds)
                if wallclock_budget_seconds is not None
                else None
            ),
            "solver_time_limit_seconds": (
                float(task_time_limit_seconds)
                if task_time_limit_seconds is not None
                else None
            ),
            "memory_limit_bytes": None,
            "memory_guard_enabled": False,
        },
        "storage_policy": {
            "baseline": "no_tes",
            "tes_hook": (
                "single_task_validation_gate_then_full_family_or_fallback"
                if enable_tes else "disabled_by_plan"
            ),
            "enable_tes": bool(enable_tes),
            "tes_gate_task_id": "tes-hybrid-site-01-cost" if enable_tes else None,
            "tes_gate_time_limit_seconds": None,
        },
        "result_qualification": (
            "certified_within_five_fixed-root_shortest-path-tree_candidates; "
            "not a global optimum over the cyclic 578-edge graph"
        ),
        "remote_upload_performed": False,
    }
    _atomic_json(plan_target, plan, exclusive=True)
    return plan


def verify_compact_plan(root: str | Path) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    plan_path = root / "compact_task_plan.json"
    plan = _read_json(plan_path)
    if plan.get("schema") != PLAN_SCHEMA or plan.get("execution_profile") != PROFILE:
        raise ValueError("run root is not a compact_fullseason_unlimited_v1 plan")
    if plan.get("case_sha256") != file_hash(root / "case.json"):
        raise ValueError("compact case hash changed; create a fresh run root")
    if plan.get("mathematics_sha256") != compact_mathematics_hashes():
        raise ValueError("compact mathematics/source hash changed; create a fresh run root")
    if plan.get("remote_upload_performed") is not False:
        raise ValueError("compact plan must not claim or perform a remote upload")
    _validate_plan_settings(
        max_parallel=plan["solver"]["max_parallel_tasks"],
        threads=plan["solver"]["threads_per_task"],
        wallclock_budget_seconds=plan["wallclock_budget_seconds"],
        task_time_limit_seconds=plan["solver"]["task_time_limit_seconds"],
        scan_gap=plan["solver"]["scan_mip_gap"],
        acceptance_gap=plan["solver"]["acceptance_mip_gap"],
    )
    expected_resource_limits = {
        "wallclock_time_limit_seconds": plan["wallclock_budget_seconds"],
        "solver_time_limit_seconds": plan["solver"]["task_time_limit_seconds"],
        "memory_limit_bytes": None,
        "memory_guard_enabled": False,
    }
    if plan.get("resource_limits") != expected_resource_limits:
        raise ValueError("compact resource-limit declaration is inconsistent")
    if plan.get("storage_policy", {}).get("enable_tes") not in {False, True}:
        raise ValueError("compact storage policy has an invalid enable_tes flag")
    return plan


def _task_map(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tasks = {task["task_id"]: task for task in plan["tasks"]}
    if len(tasks) != len(plan["tasks"]):
        raise ValueError("compact task plan contains duplicate task IDs")
    return tasks


def _success_path(root: Path, task_id: str) -> Path:
    return root / "compact_tasks" / task_id / "success.json"


def _skipped_path(root: Path, task_id: str) -> Path:
    return root / "compact_tasks" / task_id / "skipped.json"


def _failure_paths(root: Path, task_id: str) -> list[Path]:
    return sorted((root / "compact_tasks" / task_id).glob("attempt_*/failure.json"))


def _load_point(
    root: Path, task_id: str, *, verify_outputs: bool = False
) -> ParetoPoint | None:
    path = _success_path(root, task_id)
    if not path.is_file():
        return None
    payload = _read_json(path)
    if payload.get("schema") != RESULT_SCHEMA or payload.get("qa", {}).get("passed") is not True:
        raise ValueError(f"compact task success is not QA-certified: {task_id}")
    point_data = dict(payload["point"])
    point_data["labels"] = tuple(point_data["labels"])
    point = ParetoPoint(**point_data)
    gap = point.reported_mip_gap
    if gap is None or gap > payload["acceptance_mip_gap"] + 1e-12:
        raise ValueError(f"compact task exceeds its certified MIP gap: {task_id}")
    if verify_outputs:
        for relative, digest in payload.get("output_sha256", {}).items():
            output = (root / relative).resolve()
            try:
                output.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"compact task output escaped run root: {relative}") from exc
            if file_hash(output) != digest:
                raise ValueError(f"compact task output hash changed: {relative}")
    return point


def _endpoint_tasks(
    plan: dict[str, Any], *, family: str | None = None
) -> list[dict[str, Any]]:
    rows = [task for task in plan["tasks"] if task["phase"] == "endpoint"]
    if family is None:
        return rows
    return [
        task for task in rows
        if task.get("result_family", "no_tes") == family
        or task["mode"] == "distributed"
    ]


def _global_epsilon_range(
    root: Path, plan: dict[str, Any], *, family: str = "no_tes"
) -> tuple[float, float]:
    endpoint_rows: list[tuple[dict[str, Any], ParetoPoint]] = []
    for task in _endpoint_tasks(plan, family=family):
        point = _load_point(root, task["task_id"])
        if point is None:
            raise ValueError("all compact endpoints must finish before global epsilon tasks")
        endpoint_rows.append((task, point))
    carbon_candidates = [
        point
        for task, point in endpoint_rows
        if task["objective"] == "carbon" or task["mode"] == "distributed"
    ]
    cost_candidates = [
        point
        for task, point in endpoint_rows
        if task["objective"] == "cost"
    ]
    low = min(point.annual_operating_carbon_kgCO2e_per_year for point in carbon_candidates)
    cheapest = min(
        cost_candidates,
        key=lambda point: (
            point.annual_real_cost_CNY_per_year,
            point.annual_operating_carbon_kgCO2e_per_year,
            point.point_id,
        ),
    )
    high = cheapest.annual_operating_carbon_kgCO2e_per_year
    if _strictly_exceeds_with_roundoff(low, high + EPSILON_TOLERANCE):
        raise ValueError("global compact cost/carbon endpoint range is contradictory")
    return low, max(low, high)


def epsilon_for_task(
    root: str | Path, task_id: str
) -> tuple[float | None, str | None]:
    """Return an epsilon and an optional proof-based skip reason."""

    root = Path(root).expanduser().resolve()
    plan = verify_compact_plan(root)
    task = _task_map(plan).get(task_id)
    if task is None:
        raise ValueError(f"unknown compact task ID: {task_id}")
    if task["phase"] != "epsilon":
        return None, None
    family = task.get("result_family", "no_tes")
    low, high = _global_epsilon_range(root, plan, family=family)
    epsilon = low + (high - low) * float(task["epsilon_fraction"])
    prefix = "tes-" if task.get("enable_tes") else ""
    carbon_task_id = f"{prefix}{task['mode']}-{task['topology_id']}-carbon"
    site_minimum = _load_point(root, carbon_task_id)
    if site_minimum is None:
        raise ValueError(f"missing fixed-site carbon certificate: {carbon_task_id}")
    certificate_payload = _read_json(_success_path(root, carbon_task_id)).get(
        "carbon_certificate"
    ) or {}
    site_lower_bound = (certificate_payload.get("solver_evidence") or {}).get(
        "best_objective_bound"
    )
    if site_lower_bound is None and (
        site_minimum.reported_mip_gap == 0
        and site_minimum.termination_condition.lower() == "optimal"
    ):
        site_lower_bound = site_minimum.annual_operating_carbon_kgCO2e_per_year
    if site_lower_bound is not None and _strictly_exceeds_with_roundoff(
        float(site_lower_bound), epsilon + EPSILON_TOLERANCE
    ):
        return epsilon, (
            "global epsilon is below this fixed-site certified carbon lower bound: "
            f"epsilon={epsilon:.12g}, site_certified_lower_bound="
            f"{float(site_lower_bound):.12g}, site_incumbent="
            f"{site_minimum.annual_operating_carbon_kgCO2e_per_year:.12g}"
        )
    return epsilon, None


def process_is_alive(pid: int) -> bool:
    """Check a PID without sending a signal that can terminate it on Windows."""

    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        # On Windows ``os.kill(pid, 0)`` is not a POSIX existence probe: Python
        # delegates non-console signals to TerminateProcess, so signal 0 can
        # actually terminate the worker being checked.  Wait on a process
        # synchronization handle with a zero timeout instead.  Unlike checking
        # for STILL_ACTIVE, this remains correct if a process really exits with
        # status code 259.
        import ctypes
        from ctypes import wintypes

        synchronize = 0x00100000
        wait_object_0 = 0x00000000
        wait_timeout = 0x00000102
        error_access_denied = 5
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(synchronize, False, int(pid))
        if not handle:
            # A protected process that denies synchronization access still
            # exists.  All normal same-user workers are queryable; other errors
            # mean that the PID is absent or invalid.
            return ctypes.get_last_error() == error_access_denied
        try:
            wait_result = int(kernel32.WaitForSingleObject(handle, 0))
            if wait_result == wait_timeout:
                return True
            if wait_result == wait_object_0:
                return False
            return False
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _reserve_attempt(task_root: Path) -> Path:
    task_root.mkdir(parents=True, exist_ok=True)
    reservation = task_root / "worker_reservation.json"
    if reservation.exists():
        prior = _read_json(reservation)
        prior_attempt = task_root / str(prior.get("attempt", ""))
        failure = prior_attempt / "failure.json"
        if process_is_alive(int(prior.get("pid", -1))) and not failure.exists():
            raise RuntimeError(
                f"compact task already has a live worker: pid={prior.get('pid')}"
            )
        if not failure.exists():
            _atomic_json(
                failure,
                {
                    "error": "stale compact worker reservation was closed automatically",
                    "error_type": "StaleWorkerReservation",
                    "failed_at": _utcnow(),
                },
                exclusive=True,
            )
        archived = prior_attempt / "worker_reservation.json"
        if not archived.exists():
            os.replace(reservation, archived)
        elif reservation.exists():
            reservation.unlink()
    for index in range(1, 10000):
        attempt = task_root / f"attempt_{index:04d}"
        try:
            attempt.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise RuntimeError("compact task has exhausted attempt IDs")
    _atomic_json(
        reservation,
        {"pid": os.getpid(), "attempt": attempt.name, "created_at": _utcnow()},
        exclusive=True,
    )
    return attempt


def _relative(root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")


def _attempt_hashes(root: Path, attempt: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted((item for item in attempt.rglob("*") if item.is_file())):
        relative = _relative(root, path)
        hashes[relative] = file_hash(path)
    return hashes


def _model_value(model: Any, name: str, default: float = 0.0) -> float:
    component = getattr(model, name, None)
    return default if component is None else float(value(component))


def _unserved_heat(case: Any, model: Any) -> float:
    component = getattr(model, "unserved_heat_kW", None)
    if component is None:
        return 0.0
    return float(
        sum(
            value(component[building, hour])
            * case.common.economics.time_weight_h_per_year[hour]
            for building in case.common.demand_nodes
            for hour in case.common.hours
        )
    )


def _point_from_model(
    *,
    task: dict[str, Any],
    case: Any,
    model: Any,
    evidence: dict[str, Any],
    epsilon: float | None,
    started_at: str,
    finished_at: str,
    elapsed_seconds: float,
    reported_gap: float | None = None,
) -> ParetoPoint:
    labels = (
        ("cost_endpoint", "unique_mode_point", "carbon_endpoint")
        if task["mode"] == "distributed"
        else ("carbon_endpoint", "lexicographic_cost_tiebreak")
        if task["objective"] == "carbon"
        else ("epsilon",)
        if task["phase"] == "epsilon"
        else ("cost_endpoint",)
    )
    if task.get("enable_tes"):
        labels = (*labels, "tes_enabled")
    return ParetoPoint(
        point_id=task["task_id"],
        mode=task["mode"],
        labels=labels,
        epsilon_kgCO2e_per_year=epsilon,
        annual_real_cost_CNY_per_year=_model_value(
            model, "annual_real_cost_CNY_per_year"
        ),
        annual_operating_carbon_kgCO2e_per_year=_model_value(
            model, "annual_operating_physical_carbon_kgCO2e_per_year"
        ),
        annual_hns_penalty_CNY_per_year=_model_value(
            model, "annual_hns_penalty_CNY_per_year"
        ),
        unserved_heat_kWh=_unserved_heat(case, model),
        solve_elapsed_seconds=float(elapsed_seconds),
        solve_started_at_utc=started_at,
        solve_finished_at_utc=finished_at,
        solver_status=str(evidence.get("solver_status", "unknown")),
        termination_condition=str(evidence.get("termination_condition", "unknown")),
        reported_mip_gap=(
            reported_gap
            if reported_gap is not None
            else evidence.get("relative_mip_gap")
        ),
        incumbent_objective=evidence.get("incumbent_objective"),
        best_objective_bound=evidence.get("best_objective_bound"),
        reported_wallclock_seconds=evidence.get("elapsed_seconds"),
        model_sha256=evidence.get("model_sha256"),
        solver_log_file=evidence.get("solver_log_file"),
        solver_evidence_file=evidence.get("solver_evidence_file"),
    )


def _design_for_task(case: Any, plan: dict[str, Any], task: dict[str, Any]) -> Any:
    from competition.road_joint_v2.compact import CompactChain, CompactTreeDesign

    selected_site = task["site_id"] or plan["tree_designs"][0]["site_id"]
    try:
        frozen = next(row for row in plan["tree_designs"] if row["site_id"] == selected_site)
    except StopIteration as exc:
        raise ValueError(f"compact plan has no frozen design for site {selected_site}") from exc
    payload = dict(frozen)
    for derived in (
        "schema",
        "model_version",
        "selected_edge_count",
        "chain_count",
        "variable_grade_edge_count",
        "retained_pipe_capacity_row_count",
        "full_season_qualified",
    ):
        payload.pop(derived, None)
    payload["selected_edge_ids"] = tuple(payload["selected_edge_ids"])
    payload["variable_grade_edge_ids"] = tuple(payload["variable_grade_edge_ids"])
    payload["chains"] = tuple(
        CompactChain(
            **{
                **row,
                "original_edge_ids": tuple(row["original_edge_ids"]),
                "downstream_buildings": tuple(row["downstream_buildings"]),
            }
        )
        for row in payload["chains"]
    )
    for field in ("downstream_buildings_by_edge", "subtree_edges_by_edge"):
        payload[field] = {
            key: tuple(items) for key, items in payload[field].items()
        }
    payload["capacity_hours_by_edge"] = {
        key: tuple(int(hour) for hour in hours)
        for key, hours in payload["capacity_hours_by_edge"].items()
    }
    payload["capacity_hour_witness_by_edge"] = {
        key: {int(hour): int(witness) for hour, witness in rows.items()}
        for key, rows in payload["capacity_hour_witness_by_edge"].items()
    }
    payload["central_capacity_hours"] = tuple(
        int(hour) for hour in payload["central_capacity_hours"]
    )
    payload["central_capacity_hour_witness"] = {
        int(hour): int(witness)
        for hour, witness in payload["central_capacity_hour_witness"].items()
    }
    design = CompactTreeDesign(**payload)
    if _design_metadata(design) != frozen:
        raise ValueError(f"failed to losslessly restore compact design for site {selected_site}")
    if design.hour_count != len(case.common.hours):
        raise ValueError("frozen compact design horizon differs from the case")
    return design


def _connected_values_from_success(
    root: Path, task: dict[str, Any], epsilon: float | None
) -> tuple[
    dict[str, float] | None,
    dict[str, float] | None,
    dict[str, float] | None,
    str | None,
]:
    candidates: list[tuple[float, str, Path]] = []
    task_directory = root / "compact_tasks"
    if not task_directory.is_dir():
        return None, None, None, None
    for success in task_directory.glob("*/success.json"):
        payload = _read_json(success)
        if payload.get("mode") != task["mode"] or payload.get("site_id") != task["site_id"]:
            continue
        checkpoint_value = payload.get("checkpoint_file")
        if not checkpoint_value:
            continue
        checkpoint = root / checkpoint_value
        if not checkpoint.is_file():
            continue
        point = payload["point"]
        target = epsilon if epsilon is not None else point["annual_operating_carbon_kgCO2e_per_year"]
        distance = abs(point["annual_operating_carbon_kgCO2e_per_year"] - target)
        candidates.append((distance, payload["task_id"], checkpoint))
    if not candidates:
        return None, None, None, None
    _, source_task, path = min(candidates)
    checkpoint = _read_json(path)
    return (
        {
            str(building): float(selected)
            for building, selected in checkpoint["connected"].items()
        },
        {
            str(pair): float(selected)
            for pair, selected in checkpoint.get("variable_grade_selected", {}).items()
        },
        {
            str(site): float(selected)
            for site, selected in checkpoint.get("tes_built_selected", {}).items()
        },
        source_task,
    )


def _apply_mip_start(
    model: Any,
    case: Any,
    root: Path,
    task: dict[str, Any],
    epsilon: float | None,
    *,
    explicit_values: dict[str, float] | None = None,
    explicit_grade_values: dict[str, float] | None = None,
    explicit_tes_values: dict[str, float] | None = None,
) -> dict[str, Any]:
    connected = getattr(model, "connected", None)
    candidates = ["all_central", "all_distributed", "nearest_completed_epsilon"]
    values = explicit_values
    grade_values = explicit_grade_values
    tes_values = explicit_tes_values
    source_task: str | None = None
    if values is None:
        values, grade_values, tes_values, source_task = _connected_values_from_success(
            root, task, epsilon
        )
    if values is None:
        selected = (
            "all_distributed"
            if task["mode"] == "distributed"
            or (task["mode"] == "hybrid" and task["objective"] == "carbon")
            else "all_central"
        )
        fill = 0.0 if selected == "all_distributed" else 1.0
        values = {str(building): fill for building in case.common.demand_nodes}
    else:
        selected = "previous_solution"
    count = 0
    if connected is not None:
        for building in case.common.demand_nodes:
            variable = connected[building]
            if (
                hasattr(variable, "is_variable_type")
                and variable.is_variable_type()
                and not variable.fixed
                and variable.is_integer()
            ):
                variable.set_value(round(values[str(building)]), skip_validation=False)
                count += 1
    grade_count = 0
    grade_component = getattr(model, "_grade_selected", None)
    design = getattr(model, "_compact_design", None)
    if grade_component is not None and design is not None:
        for edge in getattr(model, "VARIABLE_GRADE_E", ()):
            edge_active = any(
                round(values[str(building)])
                for building in design.downstream_buildings_by_edge[edge]
            )
            for grade in model.K:
                key = f"{edge}|{grade}"
                start = (
                    grade_values[key]
                    if grade_values is not None and key in grade_values
                    else float(edge_active and grade == design.pipe_type_by_edge[edge])
                )
                variable = grade_component[edge, grade]
                if (
                    hasattr(variable, "is_variable_type")
                    and variable.is_variable_type()
                    and not variable.fixed
                    and variable.is_integer()
                ):
                    variable.set_value(round(start), skip_validation=False)
                    grade_count += 1
    tes_count = 0
    tes_component = getattr(model, "_tes_built", None)
    if tes_component is not None:
        for site in getattr(model, "TES_S", ()):
            start = tes_values.get(str(site), 0.0) if tes_values is not None else 0.0
            variable = tes_component[site]
            variable.set_value(round(start), skip_validation=False)
            tes_count += 1
    if count + grade_count + tes_count:
        model._urbanheatopt_mip_start_policy = (
            f"compact:{selected}" + (f":{source_task}" if source_task else "")
        )
    return {
        "candidate_policies": candidates,
        "selected_policy": selected if count + grade_count + tes_count else None,
        "source_task_id": source_task,
        "connection_value_count": count,
        "pipe_grade_value_count": grade_count,
        "tes_install_value_count": tes_count,
        "value_count": count + grade_count + tes_count,
    }


def _copy_connected_values(case: Any, model: Any) -> dict[str, float]:
    connected = getattr(model, "connected", None)
    if connected is None:
        return {}
    return {
        str(building): float(value(connected[building]))
        for building in case.common.demand_nodes
    }


def _copy_variable_grade_values(model: Any) -> dict[str, float]:
    """Serialize every retained static pipe-grade binary for exact replay."""

    edges = getattr(model, "VARIABLE_GRADE_E", ())
    grades = getattr(model, "K", ())
    selected = getattr(model, "_grade_selected", None)
    if selected is None:
        return {}
    return {
        f"{edge}|{grade}": float(value(selected[edge, grade]))
        for edge in edges
        for grade in grades
    }


def _copy_tes_built_values(model: Any) -> dict[str, float]:
    component = getattr(model, "_tes_built", None)
    if component is None:
        return {}
    return {
        str(site): float(value(component[site]))
        for site in getattr(model, "TES_S", ())
    }


def _settings_for_attempt(
    plan: dict[str, Any],
    attempt: Path,
    *,
    task: dict[str, Any] | None = None,
    carbon_certificate: bool = False,
) -> SolverSettings:
    prefix = "carbon_certificate_" if carbon_certificate else ""
    configured_limit = plan["solver"].get("task_time_limit_seconds")
    time_limit = float(configured_limit) if configured_limit is not None else None
    if task is not None and task.get("tes_gate"):
        gate_limit = plan["storage_policy"].get("tes_gate_time_limit_seconds")
        if gate_limit is not None:
            time_limit = (
                min(float(time_limit), float(gate_limit))
                if time_limit is not None
                else float(gate_limit)
            )
    return SolverSettings(
        name="highs",
        mip_gap=float(plan["solver"]["scan_mip_gap"]),
        time_limit_acceptance_mip_gap=float(
            plan["solver"]["acceptance_mip_gap"]
        ),
        threads=int(plan["solver"]["threads_per_task"]),
        time_limit_seconds=time_limit,
        random_seed=202611,
        tee=False,
        log_file=str(attempt / f"{prefix}solver.log"),
        # Writing and hashing the same ~29 MB full-horizon MPS for every one of
        # 101 scans consumes several wallclock minutes.  The immutable case,
        # source, task plan, complete tree certificate and model metadata lock
        # scan mathematics; final selected replays still preserve their MPS.
        model_file=None,
        evidence_file=str(attempt / f"{prefix}solver_evidence.json"),
        presolve="on",
        feasibility_tolerance=float(plan["solver"]["feasibility_tolerance"]),
    )


def _assert_gap(
    evidence: dict[str, Any], *, configured_gap: float, acceptance_gap: float
) -> float:
    gap = evidence.get("relative_mip_gap")
    if gap is None or not isfinite(float(gap)):
        raise ValueError("solver did not provide a finite certified MIP gap")
    gap = float(gap)
    if gap > acceptance_gap + 1e-12:
        raise ValueError(f"solver result exceeds hard 3% acceptance gate: {gap:.6%}")
    return gap


def run_compact_task(root: str | Path, task_id: str) -> ParetoPoint | None:
    """Run one independently schedulable no-TES compact task.

    A carbon endpoint is lexicographic: first minimize carbon and preserve its
    certificate, then minimize cost subject to that certified carbon bound.
    """

    root = Path(root).expanduser().resolve()
    plan = verify_compact_plan(root)
    tasks = _task_map(plan)
    if task_id not in tasks:
        raise ValueError(f"unknown compact task ID: {task_id}")
    existing = _load_point(root, task_id)
    if existing is not None:
        return existing
    if _skipped_path(root, task_id).is_file():
        return None
    task = tasks[task_id]
    enable_tes = bool(task.get("enable_tes", False))
    epsilon, skip_reason = epsilon_for_task(root, task_id)
    task_root = root / "compact_tasks" / task_id
    if skip_reason is not None:
        task_root.mkdir(parents=True, exist_ok=True)
        _atomic_json(
            _skipped_path(root, task_id),
            {
                "schema": RESULT_SCHEMA,
                "task_id": task_id,
                "status": "certified_infeasible_for_fixed_site_cap",
                "epsilon_kgCO2e_per_year": epsilon,
                "reason": skip_reason,
                "finished_at": _utcnow(),
                "task_plan_sha256": file_hash(root / "compact_task_plan.json"),
            },
            exclusive=True,
        )
        _atomic_json(
            task_root / "state.json",
            {"task_id": task_id, "status": "skipped", "reason": skip_reason, "updated_at": _utcnow()},
        )
        return None

    attempt = _reserve_attempt(task_root)
    _atomic_json(
        task_root / "state.json",
        {
            "task_id": task_id,
            "status": "running",
            "phase": task["phase"],
            "mode": task["mode"],
            "site_id": task["site_id"],
            "attempt": attempt.name,
            "pid": os.getpid(),
            "started_at": _utcnow(),
            "updated_at": _utcnow(),
        },
    )
    case = load_case(root / "case.json").with_mode(task["mode"])
    design = _design_for_task(case, plan, task)
    from competition.road_joint_v2 import compact as compact_module
    build_compact_model = compact_module.build_compact_model
    metadata_reader = getattr(compact_module, "compact_model_metadata", lambda model: {})
    started_at = _utcnow()
    started_clock = time()
    primary_certificate: dict[str, Any] | None = None
    final_model: Any | None = None
    try:
        _atomic_json(
            attempt / "task_request.json",
            {
                "task": task,
                "epsilon_kgCO2e_per_year": epsilon,
                "epsilon_tolerance_kgCO2e_per_year": EPSILON_TOLERANCE if epsilon is not None else None,
                "settings": asdict(_settings_for_attempt(plan, attempt, task=task)),
                "building_count": len(case.common.demand_nodes),
                "hour_count": len(case.common.hours),
                "case_sha256": plan["case_sha256"],
                "execution_profile": PROFILE,
                "enable_tes": enable_tes,
            },
            exclusive=True,
        )
        if task["objective"] == "carbon":
            primary = build_compact_model(
                case,
                design,
                objective="carbon",
                epsilon_kgCO2e_per_year=None,
                epsilon_tolerance_kgCO2e_per_year=EPSILON_TOLERANCE,
                enable_tes=enable_tes,
            )
            primary_start = _apply_mip_start(primary, case, root, task, None)
            primary_results = solve_pyomo_model(
                primary,
                _settings_for_attempt(
                    plan, attempt, task=task, carbon_certificate=True
                ),
            )
            primary_evidence = get_solver_evidence(primary_results)
            primary_gap = _assert_gap(
                primary_evidence,
                configured_gap=plan["solver"]["scan_mip_gap"],
                acceptance_gap=plan["solver"]["acceptance_mip_gap"],
            )
            carbon_bound = _model_value(
                primary, "annual_operating_physical_carbon_kgCO2e_per_year"
            )
            connected_values = _copy_connected_values(case, primary)
            primary_grade_values = _copy_variable_grade_values(primary)
            primary_tes_values = _copy_tes_built_values(primary)
            primary_certificate = {
                "carbon_upper_bound_kgCO2e_per_year": carbon_bound,
                "reported_mip_gap": primary_gap,
                "solver_evidence": primary_evidence,
                "mip_start": primary_start,
            }
            final_model = build_compact_model(
                case,
                design,
                objective="cost",
                epsilon_kgCO2e_per_year=carbon_bound,
                epsilon_tolerance_kgCO2e_per_year=EPSILON_TOLERANCE,
                enable_tes=enable_tes,
            )
            mip_start = _apply_mip_start(
                final_model,
                case,
                root,
                task,
                carbon_bound,
                explicit_values=connected_values,
                explicit_grade_values=primary_grade_values,
                explicit_tes_values=primary_tes_values,
            )
            del primary_results, primary
            gc.collect()
        else:
            final_model = build_compact_model(
                case,
                design,
                objective="cost",
                epsilon_kgCO2e_per_year=epsilon,
                epsilon_tolerance_kgCO2e_per_year=EPSILON_TOLERANCE,
                enable_tes=enable_tes,
            )
            mip_start = _apply_mip_start(final_model, case, root, task, epsilon)
        model_metadata = _normalized_json(metadata_reader(final_model))
        _atomic_json(
            attempt / "model_build.json",
            {
                "variables": final_model.nvariables(),
                "constraints": final_model.nconstraints(),
                "metadata": model_metadata,
                "mip_start": mip_start,
                "built_at": _utcnow(),
            },
            exclusive=True,
        )
        results = solve_pyomo_model(
            final_model, _settings_for_attempt(plan, attempt, task=task)
        )
        evidence = get_solver_evidence(results)
        final_gap = _assert_gap(
            evidence,
            configured_gap=plan["solver"]["scan_mip_gap"],
            acceptance_gap=plan["solver"]["acceptance_mip_gap"],
        )
        certified_gap = max(
            final_gap,
            primary_certificate["reported_mip_gap"] if primary_certificate else 0.0,
        )
        actual_carbon = _model_value(
            final_model, "annual_operating_physical_carbon_kgCO2e_per_year"
        )
        enforced_bound = (
            primary_certificate["carbon_upper_bound_kgCO2e_per_year"]
            if primary_certificate
            else epsilon
        )
        if enforced_bound is not None and _strictly_exceeds_with_roundoff(
            actual_carbon, enforced_bound + EPSILON_TOLERANCE
        ):
            raise ValueError(
                "compact solution violates its certified carbon upper bound: "
                f"actual={actual_carbon}, bound={enforced_bound}"
            )
        compact_qa = compact_module.audit_compact_solution(case, final_model)
        connected_values = _copy_connected_values(case, final_model)
        grade_values = _copy_variable_grade_values(final_model)
        tes_values = _copy_tes_built_values(final_model)
        binary_residual = max(
            (
                abs(selected - round(selected))
                for selected in (
                    *connected_values.values(),
                    *grade_values.values(),
                    *tes_values.values(),
                )
            ),
            default=0.0,
        )
        unserved = _unserved_heat(case, final_model)
        qa = {
            "passed": (
                compact_qa.get("passed") is True
                and binary_residual <= 1e-6
                and unserved <= 1e-6
            ),
            "qa_basis": (
                "in-memory compact full-horizon capacity audit plus finite solved "
                "decision checkpoint; full independent export/replay is deferred to "
                "globally nondominated points"
            ),
            "compact_full_horizon_qa": compact_qa,
            "max_connection_binary_residual": binary_residual,
            "unserved_heat_kWh": unserved,
        }
        if qa["passed"] is not True:
            raise ValueError("compact independent export QA did not pass")
        finished_at = _utcnow()
        point = _point_from_model(
            task=task,
            case=case,
            model=final_model,
            evidence=evidence,
            epsilon=epsilon,
            started_at=started_at,
            finished_at=finished_at,
            elapsed_seconds=time() - started_clock,
            reported_gap=certified_gap,
        )
        checkpoint_path = attempt / "compact_solution.json"
        _atomic_json(
            checkpoint_path,
            {
                "schema": "road_joint_v2_compact_solution_checkpoint_1",
                "task_id": task_id,
                "mode": task["mode"],
                "site_id": task["site_id"],
                "topology_id": task["topology_id"],
                "connected": connected_values,
                "variable_grade_selected": grade_values,
                "tes_built_selected": tes_values,
                "central_capacity_kW": {
                    f"{site}|{technology}": float(value(final_model.capacity[site, technology]))
                    for site in final_model.S
                    for technology in final_model.T
                },
                "annual_real_cost_CNY_per_year": point.annual_real_cost_CNY_per_year,
                "annual_operating_carbon_kgCO2e_per_year": point.annual_operating_carbon_kgCO2e_per_year,
                "reported_mip_gap": point.reported_mip_gap,
                "epsilon_kgCO2e_per_year": epsilon,
                "full_hour_count": len(case.common.hours),
                "enable_tes": enable_tes,
            },
            exclusive=True,
        )
        # Verify the immutable plan immediately before admitting a success.
        verify_compact_plan(root)
        output_hashes = _attempt_hashes(root, attempt)
        success = {
            "schema": RESULT_SCHEMA,
            "task_id": task_id,
            "mode": task["mode"],
            "site_id": task["site_id"],
            "topology_id": task["topology_id"],
            "point": asdict(point),
            "qa": qa,
            "model_metadata": model_metadata,
            "tree_design_summary": _design_summary(design),
            "mip_start": mip_start,
            "carbon_certificate": primary_certificate,
            "configured_scan_mip_gap": plan["solver"]["scan_mip_gap"],
            "acceptance_mip_gap": plan["solver"]["acceptance_mip_gap"],
            "scan_target_met": certified_gap <= plan["solver"]["scan_mip_gap"] + 1e-12,
            "checkpoint_file": _relative(root, checkpoint_path),
            "output_sha256": output_hashes,
            "task_plan_sha256": file_hash(root / "compact_task_plan.json"),
            "case_sha256": plan["case_sha256"],
            "result_qualification": plan["result_qualification"],
            "enable_tes": enable_tes,
            "completed_at": finished_at,
        }
        _atomic_json(_success_path(root, task_id), success, exclusive=True)
        _atomic_json(
            task_root / "state.json",
            {
                "task_id": task_id,
                "status": "success",
                "attempt": attempt.name,
                "reported_mip_gap": certified_gap,
                "incumbent_objective": evidence.get("incumbent_objective"),
                "best_objective_bound": evidence.get("best_objective_bound"),
                "finished_at": finished_at,
                "updated_at": finished_at,
            },
        )
        return point
    except BaseException as exc:
        failure = attempt / "failure.json"
        if not failure.exists():
            _atomic_json(
                failure,
                {
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "solver_success": False,
                    "failed_at": _utcnow(),
                },
                exclusive=True,
            )
        _atomic_json(
            task_root / "state.json",
            {
                "task_id": task_id,
                "status": "failed",
                "attempt": attempt.name,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "updated_at": _utcnow(),
            },
        )
        raise
    finally:
        if final_model is not None:
            del final_model
        gc.collect()


_TABLE_PROGRESS = re.compile(
    r"^\s*(?:[A-Za-z]\s+)?\d+\s+\d+\s+\d+\s+\S+%\s+"
    r"(?P<bound>[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|[-+]?inf)\s+"
    r"(?P<incumbent>[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|[-+]?inf)\s+"
    r"(?P<gap>\d+(?:\.\d+)?)%",
    re.MULTILINE,
)


def parse_highs_progress(log_text: str) -> dict[str, float] | None:
    """Extract the latest finite HiGHS bound/incumbent/gap table row."""

    matches = list(_TABLE_PROGRESS.finditer(log_text))
    if not matches:
        return None
    row = matches[-1].groupdict()
    try:
        result = {
            "best_objective_bound": float(row["bound"]),
            "incumbent_objective": float(row["incumbent"]),
            "relative_mip_gap": float(row["gap"]) / 100.0,
        }
    except ValueError:
        return None
    return result if all(isfinite(item) for item in result.values()) else None


def _task_state(root: Path, task: dict[str, Any]) -> dict[str, Any]:
    task_id = task["task_id"]
    success = _success_path(root, task_id)
    skipped = _skipped_path(root, task_id)
    state_path = root / "compact_tasks" / task_id / "state.json"
    if success.is_file():
        payload = _read_json(success)
        point = payload["point"]
        return {
            "task_id": task_id,
            "status": "success",
            "reported_mip_gap": point.get("reported_mip_gap"),
            "incumbent_objective": point.get("incumbent_objective"),
            "best_objective_bound": point.get("best_objective_bound"),
        }
    if skipped.is_file():
        return {"task_id": task_id, "status": "skipped"}
    if state_path.is_file():
        state = _read_json(state_path)
        if state.get("status") == "running":
            attempt = root / "compact_tasks" / task_id / str(state.get("attempt", ""))
            logs = [path for path in (attempt / "solver.log", attempt / "carbon_certificate_solver.log") if path.is_file()]
            if logs:
                latest = max(logs, key=lambda path: path.stat().st_mtime_ns)
                progress = parse_highs_progress(
                    latest.read_text(encoding="utf-8", errors="replace")[-200_000:]
                )
                if progress:
                    state = {**state, **progress, "solver_log": _relative(root, latest)}
        return state
    if _failure_paths(root, task_id):
        return {"task_id": task_id, "status": "failed"}
    return {"task_id": task_id, "status": "pending"}


def collect_run_status(root: str | Path) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    plan = verify_compact_plan(root)
    states = [_task_state(root, task) for task in plan["tasks"]]
    counts = {
        name: sum(state.get("status") == name for state in states)
        for name in ("pending", "running", "success", "skipped", "failed", "aborted")
    }
    qualified = counts["success"] + counts["skipped"]
    total = len(states)
    control_path = root / "run_control.json"
    control = _read_json(control_path) if control_path.is_file() else {}
    started_epoch = control.get("started_epoch_seconds")
    elapsed = max(0.0, time() - started_epoch) if isinstance(started_epoch, (int, float)) else 0.0
    wallclock_limit = plan.get("wallclock_budget_seconds")
    if wallclock_limit is None:
        remaining = None
    elif isinstance(started_epoch, (int, float)):
        remaining = max(0.0, float(wallclock_limit) - elapsed)
    else:
        remaining = float(wallclock_limit)
    active_progress = [
        {
            "task_id": state["task_id"],
            "reported_mip_gap": state.get("relative_mip_gap", state.get("reported_mip_gap")),
            "incumbent_objective": state.get("incumbent_objective"),
            "best_objective_bound": state.get("best_objective_bound"),
        }
        for state in states
        if state.get("status") == "running"
    ]
    endpoint_ids = {task["task_id"] for task in _endpoint_tasks(plan)}
    endpoint_states = [state for state in states if state["task_id"] in endpoint_ids]
    result_path = root / "compact_pareto_frontiers.json"
    replay_point_ids: tuple[str, ...] = ()
    if result_path.is_file():
        try:
            assembled = _read_json(result_path)
            frozen_targets = assembled.get("final_replay_target_point_ids")
            replay_point_ids = (
                tuple(str(point_id) for point_id in frozen_targets)
                if isinstance(frozen_targets, list)
                else tuple(
                    str(point["point_id"])
                    for point in assembled.get("combined_frontier", ())
                )
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            replay_point_ids = ()
    replay_complete_count = sum(
        (root / "final_replay" / point_id / "success.json").is_file()
        for point_id in replay_point_ids
    )
    completion_path = root / "completion_manifest.json"
    completion = _read_json(completion_path) if completion_path.is_file() else {}
    result_ready = (
        completion.get("status") == "complete"
        and completion.get("final_replay_complete") is True
        and qualified == total
        and counts["failed"] == 0
        and counts["aborted"] == 0
        and bool(replay_point_ids)
        and replay_complete_count == len(replay_point_ids)
    )
    scan_fraction = qualified / total if total else 1.0
    replay_fraction = (
        replay_complete_count / len(replay_point_ids)
        if replay_point_ids
        else 0.0
    )
    overall_fraction = 1.0 if result_ready else 0.85 * scan_fraction + 0.15 * replay_fraction
    status = {
        "schema": STATUS_SCHEMA,
        "updated_at": _utcnow(),
        "total_tasks": total,
        "counts": counts,
        "qualified_task_count": qualified,
        "progress_percent": round(100.0 * overall_fraction, 3),
        "scan_progress_percent": round(100.0 * scan_fraction, 3),
        "final_replay_progress_percent": round(100.0 * replay_fraction, 3),
        "final_replay_complete_count": replay_complete_count,
        "final_replay_point_count": len(replay_point_ids),
        "terminal_percent": round(
            100.0
            * sum(state["status"] in {"success", "skipped", "failed", "aborted"} for state in states)
            / total,
            3,
        ) if total else 100.0,
        "endpoint_barrier_complete": all(
            state["status"] in {"success", "skipped"} for state in endpoint_states
        ),
        "active_solver_progress": active_progress,
        "elapsed_seconds": elapsed,
        "remaining_budget_seconds": remaining,
        "hard_budget_seconds": plan["wallclock_budget_seconds"],
        "result_ready": result_ready,
        "tasks": states,
    }
    return status


def write_run_status(root: str | Path) -> dict[str, Any]:
    status = collect_run_status(root)
    _atomic_json(Path(root) / "run_status.json", status)
    return status


def endpoint_task_ids(plan: dict[str, Any]) -> tuple[str, ...]:
    return tuple(task["task_id"] for task in plan["tasks"] if task["phase"] == "endpoint")


def epsilon_task_ids(plan: dict[str, Any]) -> tuple[str, ...]:
    return tuple(task["task_id"] for task in plan["tasks"] if task["phase"] == "epsilon")


def family_task_ids(
    plan: dict[str, Any], family: str, *, phase: str | None = None
) -> tuple[str, ...]:
    return tuple(
        task["task_id"]
        for task in plan["tasks"]
        if task.get("result_family", "no_tes") == family
        and (phase is None or task["phase"] == phase)
    )


def mark_tes_fallback(root: str | Path, reason: str) -> dict[str, Any]:
    """Fail closed from TES while preserving the complete no-TES baseline."""

    root = Path(root).expanduser().resolve()
    plan = verify_compact_plan(root)
    for task in plan["tasks"]:
        if not task.get("enable_tes"):
            continue
        task_id = task["task_id"]
        if _success_path(root, task_id).is_file() or _skipped_path(root, task_id).is_file():
            continue
        task_root = root / "compact_tasks" / task_id
        task_root.mkdir(parents=True, exist_ok=True)
        _atomic_json(
            _skipped_path(root, task_id),
            {
                "schema": RESULT_SCHEMA,
                "task_id": task_id,
                "status": "tes_fallback_not_admitted",
                "reason": reason,
                "finished_at": _utcnow(),
                "task_plan_sha256": file_hash(root / "compact_task_plan.json"),
            },
            exclusive=True,
        )
        _atomic_json(
            task_root / "state.json",
            {
                "task_id": task_id,
                "status": "skipped",
                "reason": reason,
                "updated_at": _utcnow(),
            },
        )
    payload = {
        "schema": RESULT_SCHEMA,
        "requested": True,
        "qualified": False,
        "status": "fallback_to_no_tes",
        "reason": reason,
        "updated_at": _utcnow(),
    }
    _atomic_json(root / "tes_upgrade_status.json", payload)
    return payload


def mark_tes_qualified(root: str | Path) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    plan = verify_compact_plan(root)
    status_path = root / "tes_upgrade_status.json"
    if status_path.is_file():
        prior = _read_json(status_path)
        if prior.get("status") == "fallback_to_no_tes":
            raise ValueError("TES fallback is terminal for this immutable run root")
    states = {
        state["task_id"]: state
        for state in (_task_state(root, task) for task in plan["tasks"])
    }
    incomplete = [
        task_id
        for task_id in family_task_ids(plan, "tes")
        if states[task_id]["status"] not in {"success", "skipped"}
    ]
    gate_id = plan.get("storage_policy", {}).get("tes_gate_task_id")
    invalid_skips = []
    for task_id in family_task_ids(plan, "tes"):
        skipped = _skipped_path(root, task_id)
        if skipped.is_file() and _read_json(skipped).get("status") != (
            "certified_infeasible_for_fixed_site_cap"
        ):
            invalid_skips.append(task_id)
    if (
        incomplete
        or invalid_skips
        or not gate_id
        or states.get(gate_id, {}).get("status") != "success"
    ):
        raise ValueError("TES family cannot be qualified before its gate and tasks complete")
    payload = {
        "schema": RESULT_SCHEMA,
        "requested": True,
        "qualified": True,
        "status": "tes_pareto_qualified",
        "gate_task_id": gate_id,
        "updated_at": _utcnow(),
    }
    _atomic_json(root / "tes_upgrade_status.json", payload)
    return payload


def mark_driver_abort(root: str | Path, task_id: str, reason: str) -> None:
    """Close evidence for a worker terminated by an interrupted/bounded driver."""

    root = Path(root).expanduser().resolve()
    task_root = root / "compact_tasks" / task_id
    state_path = task_root / "state.json"
    state = _read_json(state_path) if state_path.is_file() else {"task_id": task_id}
    attempt = task_root / str(state.get("attempt", ""))
    failure = attempt / "failure.json"
    if attempt.is_dir() and not failure.exists():
        _atomic_json(
            failure,
            {
                "error": reason,
                "error_type": "CompactDriverHardStop",
                "solver_success": False,
                "failed_at": _utcnow(),
            },
            exclusive=True,
        )
    _atomic_json(
        state_path,
        {
            **state,
            "status": "aborted",
            "error": reason,
            "updated_at": _utcnow(),
        },
    )


def _load_checkpoint(root: Path, success: dict[str, Any]) -> dict[str, Any]:
    relative = success.get("checkpoint_file")
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"compact task {success.get('task_id')} has no decision checkpoint")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("compact decision checkpoint escaped the run root") from exc
    checkpoint = _read_json(path)
    if (
        checkpoint.get("schema") != "road_joint_v2_compact_solution_checkpoint_1"
        or checkpoint.get("task_id") != success.get("task_id")
        or bool(checkpoint.get("enable_tes")) != bool(success.get("enable_tes"))
    ):
        raise ValueError("compact decision checkpoint does not match its success marker")
    return checkpoint


def _fix_replay_connections(case: Any, model: Any, checkpoint: dict[str, Any]) -> int:
    connected = getattr(model, "connected", None)
    if connected is None:
        raise ValueError("compact replay model is missing connected decisions")
    expected = {str(building) for building in case.common.demand_nodes}
    values = checkpoint.get("connected")
    if not isinstance(values, dict) or set(values) != expected:
        raise ValueError("compact replay checkpoint has incomplete building decisions")
    fixed = 0
    for building in case.common.demand_nodes:
        item = connected[building]
        selected = float(values[str(building)])
        if abs(selected - round(selected)) > 1e-6:
            raise ValueError(f"non-binary replay connection decision for {building}")
        if hasattr(item, "is_variable_type") and item.is_variable_type():
            item.fix(round(selected))
            fixed += 1
        elif abs(float(value(item)) - round(selected)) > 1e-6:
            raise ValueError(f"fixed-mode connection mismatch for {building}")
    return fixed


def _fix_replay_grades(model: Any, checkpoint: dict[str, Any]) -> int:
    """Fix all retained pipe-grade binaries recorded by the scan solve."""

    selected = getattr(model, "_grade_selected", None)
    edges = tuple(getattr(model, "VARIABLE_GRADE_E", ()))
    grades = tuple(getattr(model, "K", ()))
    expected = {f"{edge}|{grade}" for edge in edges for grade in grades}
    values = checkpoint.get("variable_grade_selected")
    if not isinstance(values, dict) or set(values) != expected:
        raise ValueError("compact replay checkpoint has incomplete pipe-grade decisions")
    fixed = 0
    for edge in edges:
        for grade in grades:
            item = selected[edge, grade]
            raw = float(values[f"{edge}|{grade}"])
            if abs(raw - round(raw)) > 1e-6:
                raise ValueError(f"non-binary replay pipe-grade decision for {edge}|{grade}")
            if not (hasattr(item, "is_variable_type") and item.is_variable_type()):
                raise ValueError(f"retained pipe-grade decision is not a variable: {edge}|{grade}")
            item.fix(round(raw))
            fixed += 1
    return fixed


def _fix_replay_tes_installation(model: Any, checkpoint: dict[str, Any]) -> int:
    component = getattr(model, "_tes_built", None)
    expected = {str(site) for site in getattr(model, "TES_S", ())}
    values = checkpoint.get("tes_built_selected")
    if not isinstance(values, dict) or set(values) != expected:
        raise ValueError("compact replay checkpoint has incomplete TES decisions")
    if component is None:
        return 0
    fixed = 0
    for site in model.TES_S:
        raw = float(values[str(site)])
        if abs(raw - round(raw)) > 1e-6:
            raise ValueError(f"non-binary replay TES installation decision for {site}")
        component[site].fix(round(raw))
        fixed += 1
    return fixed


def _verify_replay_success(root: Path, point_id: str) -> dict[str, Any] | None:
    path = root / "final_replay" / point_id / "success.json"
    if not path.is_file():
        return None
    payload = _read_json(path)
    if payload.get("schema") != RESULT_SCHEMA or payload.get("qa", {}).get("passed") is not True:
        raise ValueError(f"final replay is not QA-certified: {point_id}")
    plan = _read_json(root / "compact_task_plan.json")
    if (
        payload.get("task_plan_sha256") != file_hash(root / "compact_task_plan.json")
        or payload.get("case_sha256") != plan.get("case_sha256")
        or payload.get("mathematics_sha256") != plan.get("mathematics_sha256")
    ):
        raise ValueError(f"final replay evidence hashes do not match the plan: {point_id}")
    for relative, digest in payload.get("output_sha256", {}).items():
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("final replay output escaped run root") from exc
        if file_hash(target) != digest:
            raise ValueError(f"final replay output hash changed: {relative}")
    return payload


def _replay_final_point(
    root: Path,
    plan: dict[str, Any],
    task: dict[str, Any],
    point: ParetoPoint,
) -> dict[str, Any]:
    existing = _verify_replay_success(root, point.point_id)
    if existing is not None:
        return existing
    task_success = _read_json(_success_path(root, point.point_id))
    checkpoint = _load_checkpoint(root, task_success)
    replay_root = root / "final_replay" / point.point_id
    replay_root.mkdir(parents=True, exist_ok=True)
    for index in range(1, 10000):
        attempt = replay_root / f"attempt_{index:04d}"
        try:
            attempt.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise RuntimeError(f"final replay has exhausted attempt IDs: {point.point_id}")
    case = load_case(root / "case.json").with_mode(task["mode"])
    design = _design_for_task(case, plan, task)
    from competition.road_joint_v2.compact import (
        audit_compact_solution,
        build_compact_model,
        compact_model_metadata,
        export_compact_solution,
    )

    epsilon = point.epsilon_kgCO2e_per_year
    if task["objective"] == "carbon":
        certificate = task_success.get("carbon_certificate") or {}
        epsilon = certificate.get("carbon_upper_bound_kgCO2e_per_year")
        if epsilon is None:
            raise ValueError("lexicographic carbon endpoint has no primary carbon certificate")
    model = build_compact_model(
        case,
        design,
        objective="cost",
        epsilon_kgCO2e_per_year=epsilon,
        epsilon_tolerance_kgCO2e_per_year=EPSILON_TOLERANCE,
        enable_tes=bool(task.get("enable_tes", False)),
    )
    fixed_connection_count = _fix_replay_connections(case, model, checkpoint)
    fixed_grade_count = _fix_replay_grades(model, checkpoint)
    fixed_tes_count = _fix_replay_tes_installation(model, checkpoint)
    unfixed_integer = [
        variable.name
        for variable in model.component_data_objects(Var, active=True)
        if variable.is_integer() and not variable.fixed
    ]
    if unfixed_integer:
        raise ValueError(
            "final replay is not continuous after fixing structural decisions: "
            + ", ".join(unfixed_integer[:5])
        )
    settings = SolverSettings(
        name="highs",
        mip_gap=float(plan["solver"]["final_target_mip_gap"]),
        threads=int(plan["solver"]["threads_per_task"]),
        time_limit_seconds=(
            float(plan["solver"]["task_time_limit_seconds"])
            if plan["solver"].get("task_time_limit_seconds") is not None
            else None
        ),
        random_seed=202611,
        tee=False,
        log_file=str(attempt / "solver.log"),
        # Scan tasks omit redundant snapshots, but each selected final point
        # keeps its own exact fixed-decision LP.  Different Pareto points have
        # different fixed bounds/epsilon rows and therefore must not share MPS.
        model_file=str(attempt / "model.mps"),
        evidence_file=str(attempt / "solver_evidence.json"),
        presolve="on",
        feasibility_tolerance=float(plan["solver"]["feasibility_tolerance"]),
    )
    try:
        results = solve_pyomo_model(model, settings)
        evidence = get_solver_evidence(results)
        gap = _assert_gap(
            evidence,
            configured_gap=plan["solver"]["final_target_mip_gap"],
            acceptance_gap=plan["solver"]["acceptance_mip_gap"],
        )
        compact_qa = audit_compact_solution(case, model)
        export_qa = export_compact_solution(case, model, attempt / "solution")
        replay_cost = _model_value(model, "annual_real_cost_CNY_per_year")
        replay_carbon = _model_value(
            model, "annual_operating_physical_carbon_kgCO2e_per_year"
        )
        cost_delta = abs(replay_cost - point.annual_real_cost_CNY_per_year)
        carbon_delta = abs(
            replay_carbon - point.annual_operating_carbon_kgCO2e_per_year
        )
        cost_tolerance = max(1e-4, 1e-7 * abs(point.annual_real_cost_CNY_per_year))
        carbon_tolerance = max(
            1e-4, 1e-7 * abs(point.annual_operating_carbon_kgCO2e_per_year)
        )
        qa = {
            "passed": (
                compact_qa.get("passed") is True
                and export_qa.get("passed") is True
                and cost_delta <= cost_tolerance
                and carbon_delta <= carbon_tolerance
            ),
            "compact_full_horizon_qa": compact_qa,
            "export_independent_qa": export_qa,
            "scan_to_replay_cost_delta_CNY_per_year": cost_delta,
            "scan_to_replay_carbon_delta_kgCO2e_per_year": carbon_delta,
            "cost_tolerance_CNY_per_year": cost_tolerance,
            "carbon_tolerance_kgCO2e_per_year": carbon_tolerance,
        }
        if not qa["passed"]:
            raise ValueError(f"fixed-decision full-season replay QA failed: {qa}")
        output_hashes = _attempt_hashes(root, attempt)
        payload = {
            "schema": RESULT_SCHEMA,
            "point_id": point.point_id,
            "task_id": task["task_id"],
            "mode": task["mode"],
            "site_id": task["site_id"],
            "topology_id": task["topology_id"],
            "fixed_connection_count": fixed_connection_count,
            "fixed_pipe_grade_binary_count": fixed_grade_count,
            "fixed_tes_install_binary_count": fixed_tes_count,
            "unfixed_integer_count": 0,
            "continuous_replay": True,
            "enable_tes": bool(task.get("enable_tes", False)),
            "reported_mip_gap": gap,
            "replay_cost_CNY_per_year": replay_cost,
            "replay_carbon_kgCO2e_per_year": replay_carbon,
            "qa": qa,
            "model_metadata": _normalized_json(compact_model_metadata(model)),
            "solution_directory": _relative(root, attempt / "solution"),
            "solver_evidence_file": _relative(root, attempt / "solver_evidence.json"),
            "output_sha256": output_hashes,
            "task_plan_sha256": file_hash(root / "compact_task_plan.json"),
            "case_sha256": plan["case_sha256"],
            "mathematics_sha256": plan["mathematics_sha256"],
            "completed_at": _utcnow(),
        }
        _atomic_json(replay_root / "success.json", payload, exclusive=True)
        return payload
    except BaseException as exc:
        failure = attempt / "failure.json"
        if not failure.exists():
            _atomic_json(
                failure,
                {
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "failed_at": _utcnow(),
                },
                exclusive=True,
            )
        raise
    finally:
        del model
        gc.collect()


def replay_compact_point(root: str | Path, point_id: str) -> dict[str, Any]:
    """Run or verify one fixed-integer full export for a certified scan point."""

    root = Path(root).expanduser().resolve()
    plan = verify_compact_plan(root)
    task = _task_map(plan).get(point_id)
    if task is None:
        raise ValueError(f"unknown compact replay point ID: {point_id}")
    point = _load_point(root, point_id, verify_outputs=True)
    if point is None:
        raise ValueError(f"compact replay point has no certified scan success: {point_id}")
    return _replay_final_point(root, plan, task, point)


def assemble_compact_run(
    root: str | Path, *, allow_partial: bool = False, replay_final: bool = True
) -> Any:
    """Assemble certified points and a global non-dominated frontier."""

    root = Path(root).expanduser().resolve()
    plan = verify_compact_plan(root)
    states = [_task_state(root, task) for task in plan["tasks"]]
    nonterminal = [
        state["task_id"]
        for state in states
        if state["status"] not in {"success", "skipped"}
    ]
    if nonterminal and not allow_partial:
        raise ValueError(
            "compact tasks are incomplete; cannot certify complete frontier: "
            + ", ".join(nonterminal[:8])
        )
    tes_requested = bool(plan.get("storage_policy", {}).get("enable_tes"))
    tes_status_path = root / "tes_upgrade_status.json"
    tes_status = (
        _read_json(tes_status_path)
        if tes_status_path.is_file()
        else {
            "requested": tes_requested,
            "qualified": False,
            "status": "pending" if tes_requested else "not_requested",
        }
    )
    tes_qualified = tes_status.get("qualified") is True
    points: list[ParetoPoint] = []
    topology: dict[str, Any] = {}
    success_hashes: dict[str, str] = {}
    for task in plan["tasks"]:
        if task.get("enable_tes") and not tes_qualified:
            continue
        point = _load_point(root, task["task_id"], verify_outputs=True)
        if point is None:
            continue
        points.append(point)
        success = _success_path(root, task["task_id"])
        payload = _read_json(success)
        topology[point.point_id] = {
            "site_id": payload.get("site_id"),
            "topology_id": payload.get("topology_id"),
            "enable_tes": payload.get("enable_tes"),
            "reported_mip_gap": point.reported_mip_gap,
        }
        success_hashes[_relative(root, success)] = file_hash(success)
    modes = tuple(mode for mode in ("central", "distributed", "hybrid") if any(point.mode == mode for point in points))
    if not modes:
        raise ValueError("no QA- and gap-certified compact points are available")
    if not allow_partial and modes != ("central", "distributed", "hybrid"):
        raise ValueError("complete compact result requires central, distributed, and hybrid points")
    task_lookup = _task_map(plan)
    no_tes_points = tuple(
        point
        for point in points
        if not task_lookup[point.point_id].get("enable_tes", False)
    )
    no_tes_modes = tuple(
        mode
        for mode in ("central", "distributed", "hybrid")
        if any(point.mode == mode for point in no_tes_points)
    )
    if not allow_partial and no_tes_modes != ("central", "distributed", "hybrid"):
        raise ValueError("complete compact result requires a full no-TES fallback")
    no_tes_result = (
        assemble_pareto_run(
            no_tes_points,
            ParetoSpec(
                point_count=3,
                cost_tolerance_CNY_per_year=1e-6,
                carbon_tolerance_kgCO2e_per_year=1e-6,
            ),
            modes=no_tes_modes,
        )
        if no_tes_modes
        else None
    )
    result = assemble_pareto_run(
        tuple(points),
        ParetoSpec(
            point_count=3,
            cost_tolerance_CNY_per_year=1e-6,
            carbon_tolerance_kgCO2e_per_year=1e-6,
        ),
        modes=modes,
    )
    replay_points: list[ParetoPoint] = []
    seen_replay_ids: set[str] = set()
    for candidate in (
        *((no_tes_result.combined_frontier if no_tes_result is not None else ())),
        *result.combined_frontier,
    ):
        if candidate.point_id not in seen_replay_ids:
            replay_points.append(candidate)
            seen_replay_ids.add(candidate.point_id)
    final_replays: dict[str, Any] = {}
    if replay_final:
        # The no-TES fallback is replayed first even when TES later becomes the
        # preferred frontier.  This guarantees a fully exported safe result.
        for point in replay_points:
            final_replays[point.point_id] = _replay_final_point(
                root, plan, task_lookup[point.point_id], point
            )
    payload = {
        "schema": RESULT_SCHEMA,
        "assembled_at": _utcnow(),
        "complete": not nonterminal,
        "missing_or_failed_task_ids": nonterminal,
        "execution_profile": PROFILE,
        "result_qualification": plan["result_qualification"],
        "storage_policy": plan["storage_policy"],
        "tes_upgrade_status": tes_status,
        "global_epsilon_range_kgCO2e_per_year": (
            list(_global_epsilon_range(root, plan, family="no_tes"))
            if all(
                _load_point(root, task["task_id"]) is not None
                for task in _endpoint_tasks(plan, family="no_tes")
            )
            else None
        ),
        "global_epsilon_ranges_kgCO2e_per_year": {
            family: (
                list(_global_epsilon_range(root, plan, family=family))
                if all(
                    _load_point(root, task["task_id"]) is not None
                    for task in _endpoint_tasks(plan, family=family)
                )
                else None
            )
            for family in (("no_tes", "tes") if tes_requested else ("no_tes",))
        },
        "mode_frontiers": {
            mode: [asdict(point) for point in frontier]
            for mode, frontier in result.mode_frontiers.items()
        },
        "mode_all_points": {
            mode: [asdict(point) for point in rows]
            for mode, rows in result.mode_all_points.items()
        },
        "combined_frontier": [asdict(point) for point in result.combined_frontier],
        "no_tes_baseline_frontier": (
            [asdict(point) for point in no_tes_result.combined_frontier]
            if no_tes_result is not None
            else []
        ),
        "point_topology": topology,
        "final_fixed_decision_replays": final_replays,
        "no_tes_baseline_fixed_decision_replays": {
            point.point_id: final_replays[point.point_id]
            for point in (
                no_tes_result.combined_frontier if no_tes_result is not None else ()
            )
            if point.point_id in final_replays
        },
        "final_replay_target_point_ids": [
            point.point_id for point in replay_points
        ],
        "final_replay_complete": bool(
            replay_final
            and len(final_replays) == len(replay_points)
        ),
        "task_plan_sha256": file_hash(root / "compact_task_plan.json"),
        "case_sha256": plan["case_sha256"],
        "success_marker_sha256": success_hashes,
        "remote_upload_performed": False,
    }
    _atomic_json(root / "compact_pareto_frontiers.json", payload)
    if replay_final:
        _atomic_json(
            root / "completion_manifest.json",
            {
                "schema": RESULT_SCHEMA,
                "status": "complete" if not nonterminal else "partial",
                "created_at": _utcnow(),
                "task_plan_sha256": payload["task_plan_sha256"],
                "case_sha256": plan["case_sha256"],
                "certified_point_count": len(points),
                "combined_frontier_point_count": len(result.combined_frontier),
                "no_tes_baseline_frontier_point_count": (
                    len(no_tes_result.combined_frontier)
                    if no_tes_result is not None
                    else 0
                ),
                "final_replay_point_count": len(replay_points),
                "final_replay_complete": payload["final_replay_complete"],
                "success_marker_sha256": success_hashes,
                "result_file_sha256": file_hash(root / "compact_pareto_frontiers.json"),
                "remote_upload_performed": False,
            },
        )
    write_run_status(root)
    return result


__all__ = [
    "PLAN_SCHEMA",
    "PROFILE",
    "assemble_compact_run",
    "collect_run_status",
    "compact_mathematics_hashes",
    "create_compact_plan",
    "endpoint_task_ids",
    "epsilon_for_task",
    "epsilon_task_ids",
    "family_task_ids",
    "mark_driver_abort",
    "mark_tes_fallback",
    "mark_tes_qualified",
    "parse_highs_progress",
    "process_is_alive",
    "replay_compact_point",
    "run_compact_task",
    "verify_compact_plan",
    "write_run_status",
]
