"""Hash-locked SolveRequest execution for the compact five-tree RoadCase.

The executor owns orchestration and evidence only.  It does not define cost,
carbon, storage, or network physics and it never calls a legacy model.
"""
from __future__ import annotations

from datetime import datetime, timezone
import gc
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
from time import perf_counter
from typing import Any

import pandas as pd
from pyomo.environ import value

from urbanheatopt.data.bundles import (
    CaseBundle, ResultBundle, SolveRequest, sha256_file,
)
from urbanheatopt.data.road_builder import (
    ROAD_CASE_BUILDER_VERSION, build_road_case, load_case,
    road_case_content_sha256,
)
from urbanheatopt.model.compact import (
    COMPACT_MODEL_VERSION, audit_compact_solution, build_compact_model,
    build_compact_tree_design, compact_model_metadata, export_compact_solution,
)
from urbanheatopt.model.road_core import RoadCase
from urbanheatopt.optimization.solvers import (
    SolverNotOptimalError, SolverSettings, get_solver_evidence, solve_pyomo_model,
)
from urbanheatopt.optimization.pareto import (
    ParetoPoint, ParetoSpec, assemble_pareto_run, point_to_dict,
)
from urbanheatopt.paths import REPOSITORY_ROOT


SOLVE_EXECUTOR_VERSION = "solve_request_executor_1.2.0"
MODEL_PROFILE = "compact_five_tree_fullseason_v2"
PARETO_INTERNAL_FRACTIONS = (0.25, 0.50, 0.75)
PARETO_EPSILON_TOLERANCE_KG = 1e-6
FIXED_STRUCTURE_SCHEMA = "urbanheatopt_tes_fixed_structure_1"


def _write_json(path: Path, value_: Any, *, exclusive: bool = True) -> None:
    with path.open("x" if exclusive else "w", encoding="utf-8", newline="\n") as stream:
        json.dump(value_, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def _git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT, text=True, encoding="utf-8",
    ).strip()


def load_prepared_road_case(bundle_path: str | Path) -> tuple[CaseBundle, RoadCase, dict[str, Any]]:
    """Verify the prepared RoadCase against a fresh build from CaseBundle."""
    bundle_path = Path(bundle_path).expanduser().resolve(strict=True)
    bundle = CaseBundle.read(bundle_path)
    root = bundle_path.parent
    case_path, hashes_path = root / "road_case.json", root / "road_case_hashes.json"
    if not case_path.is_file() or not hashes_path.is_file():
        raise ValueError("prepare目录缺少road_case.json或road_case_hashes.json")
    declared = json.loads(hashes_path.read_text(encoding="utf-8"))
    if declared.get("builder_version") != ROAD_CASE_BUILDER_VERSION:
        raise ValueError("RoadCase Builder版本不一致")
    if declared.get("case_bundle_id") != bundle.bundle_id:
        raise ValueError("RoadCase哈希记录引用了其他CaseBundle")
    if declared.get("case_bundle_content_id") != bundle.content_id:
        raise ValueError("CaseBundle内容ID与RoadCase哈希记录不一致")
    if sha256_file(case_path) != declared.get("road_case_file_sha256"):
        raise ValueError("road_case.json文件SHA-256不一致")
    serialized = load_case(case_path)
    serialized_hash = road_case_content_sha256(serialized)
    if serialized_hash != declared.get("road_case_content_sha256"):
        raise ValueError("road_case.json内容哈希不一致")
    rebuilt = build_road_case(bundle)
    if rebuilt.report["road_case_content_sha256"] != serialized_hash:
        raise ValueError("RoadCase重新构建结果与prepare冻结结果不一致")
    return bundle, serialized, {
        **declared,
        "road_case_rebuild_verified": True,
        "road_case_builder_report": rebuilt.report,
    }


def _request_settings(payload: dict, task_root: Path) -> SolverSettings:
    solver = payload["solver"]
    name = solver["name"]
    if name == "auto":
        name = "highs"
    return SolverSettings(
        name=name,
        mip_gap=float(solver["mip_gap"]),
        time_limit_acceptance_mip_gap=float(solver["mip_gap"]),
        threads=int(solver["threads"]),
        time_limit_seconds=(
            float(solver["time_limit_s"]) if solver["time_limit_s"] is not None else None
        ),
        random_seed=int(solver["random_seed"]),
        tee=False,
        log_file=str(task_root / "solver.log"),
        evidence_file=str(task_root / "solver_evidence.json"),
        model_file=None,
        presolve="on",
        feasibility_tolerance=1e-7,
    )


def _objective_value(model, objective: str) -> float:
    component = (
        model.annual_real_cost_CNY_per_year
        if objective == "cost"
        else model.annual_operating_physical_carbon_kgCO2e_per_year
    )
    result = float(value(component))
    if not math.isfinite(result):
        raise ValueError("求解目标值不是有限数值")
    return result


def _carbon_breakdown(solution_root: Path) -> None:
    data = json.loads((solution_root / "independent_recalculation.json").read_text(encoding="utf-8"))
    pd.DataFrame([
        {"component": "purchased_electricity", "annual_kgCO2e": data["carbon_electricity_kgCO2e"]},
        {"component": "natural_gas_LHV", "annual_kgCO2e": data["carbon_gas_kgCO2e"]},
    ]).to_csv(solution_root / "carbon_breakdown.csv", index=False, encoding="utf-8-sig")


def _canonical_sha256(payload: Any) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _candidate_ids(
    case: RoadCase,
    mode: str,
    fixed_structure: dict[str, Any] | None = None,
) -> tuple[str, ...]:
    sites = tuple(sorted(str(row["site_id"]) for row in case.network["sites"]))
    if len(case.common.demand_nodes) == 62 and len(case.common.hours) == 2160 and len(sites) != 5:
        raise ValueError("62栋×2160小时生产执行固定要求5个候选站")
    if fixed_structure is not None:
        site_id = str(fixed_structure["site_id"])
        if site_id not in sites:
            raise ValueError(f"TES固定结构引用未知候选站：{site_id}")
        return (site_id,)
    return (sites[0],) if mode == "distributed" else sites


def _load_fixed_structure(solution_root: Path) -> dict[str, Any]:
    """Read the exact site, connection and pipe design of one qualified run."""
    required = (
        "selected_site.json",
        "building_connection.csv",
        "network_decisions.csv",
        "solution_summary.json",
        "solver_evidence.json",
        "independent_qa.json",
    )
    missing = [name for name in required if not (solution_root / name).is_file()]
    if missing:
        raise ValueError("TES配对源点缺少结果文件：" + ", ".join(missing))
    site_payload = json.loads(
        (solution_root / "selected_site.json").read_text(encoding="utf-8")
    )
    summary = json.loads(
        (solution_root / "solution_summary.json").read_text(encoding="utf-8")
    )
    qa = json.loads(
        (solution_root / "independent_qa.json").read_text(encoding="utf-8")
    )
    if qa.get("passed") is not True:
        raise ValueError("TES配对源点独立QA未通过")
    connections = pd.read_csv(solution_root / "building_connection.csv")
    if connections["building_id"].duplicated().any():
        raise ValueError("TES配对源点存在重复building_id")
    connection_vector: dict[str, int] = {}
    for row in connections.itertuples(index=False):
        raw = float(row.connected)
        selected = int(round(raw))
        if selected not in (0, 1) or abs(raw - selected) > 1e-7:
            raise ValueError(f"TES配对源点接网决策不是二元值：{row.building_id}")
        connection_vector[str(row.building_id)] = selected
    pipes = pd.read_csv(
        solution_root / "network_decisions.csv", keep_default_na=False
    )
    if pipes["edge_id"].duplicated().any():
        raise ValueError("TES配对源点存在重复edge_id")
    pipe_grade_by_edge: dict[str, str | None] = {}
    for row in pipes.itertuples(index=False):
        built = float(row.built)
        grade = str(row.pipe_type_id).strip()
        if built > 0.5 and not grade:
            raise ValueError(f"已建边{row.edge_id}缺少pipe_type_id")
        pipe_grade_by_edge[str(row.edge_id)] = grade if built > 0.5 else None
    payload = {
        "schema": FIXED_STRUCTURE_SCHEMA,
        "source_result": str(solution_root.resolve()),
        "source_artifact_sha256": {
            name: sha256_file(solution_root / name) for name in required
        },
        "mode": str(summary["mode"]),
        "site_id": str(site_payload["selected_site_id"]),
        "connection_vector": connection_vector,
        "pipe_grade_by_edge": pipe_grade_by_edge,
    }
    payload["fixed_structure_sha256"] = _canonical_sha256(payload)
    return payload


def _apply_fixed_structure(model, case: RoadCase, fixed: dict[str, Any]) -> None:
    if fixed.get("schema") != FIXED_STRUCTURE_SCHEMA:
        raise ValueError("TES固定结构schema不受支持")
    declared_hash = fixed.get("fixed_structure_sha256")
    hash_payload = {
        key: value_
        for key, value_ in fixed.items()
        if key != "fixed_structure_sha256"
    }
    if declared_hash != _canonical_sha256(hash_payload):
        raise ValueError("TES固定结构内容哈希不一致")
    if fixed.get("mode") != case.common.mode:
        raise ValueError("TES固定结构与SolveRequest模式不一致")
    connection = dict(fixed.get("connection_vector", {}))
    if set(connection) != set(case.common.demand_nodes):
        raise ValueError("TES固定结构的建筑集合与RoadCase不一致")
    if case.common.mode == "central" and any(
        selected != 1 for selected in connection.values()
    ):
        raise ValueError("集中式TES源点必须全部接网")
    if case.common.mode == "distributed":
        raise ValueError("纯分布式没有区域站TES，不执行TES固定结构配对")
    if case.common.mode == "hybrid":
        for building, selected in connection.items():
            model.connected[building].fix(int(selected))
    grades = dict(fixed.get("pipe_grade_by_edge", {}))
    if set(grades) != {str(edge) for edge in model.E}:
        raise ValueError("TES固定结构的管段集合与RoadCase不一致")
    for edge in model.VARIABLE_GRADE_E:
        selected_grade = grades[str(edge)]
        if selected_grade is not None and selected_grade not in model.K:
            raise ValueError(f"TES固定结构管型未知：{edge}/{selected_grade}")
        for grade in model.K:
            model._grade_selected[edge, grade].fix(int(selected_grade == grade))
    model._tes_fixed_structure_sha256 = fixed["fixed_structure_sha256"]


def _assert_fixed_structure_solution(model, fixed: dict[str, Any]) -> None:
    for building, selected in fixed["connection_vector"].items():
        if abs(float(value(model.connected[building])) - int(selected)) > 1e-7:
            raise ValueError(f"TES配对改变了建筑接网决策：{building}")
    for edge, selected_grade in fixed["pipe_grade_by_edge"].items():
        chosen = [
            str(grade)
            for grade in model.K
            if float(value(model.grade[edge, grade])) > 0.5
        ]
        expected = [] if selected_grade is None else [selected_grade]
        if chosen != expected:
            raise ValueError(f"TES配对改变了管段/管型决策：{edge}")


def execute_request(
    bundle: CaseBundle,
    road_case: RoadCase,
    request: SolveRequest,
    output_dir: str | Path,
    *,
    road_case_evidence: dict[str, Any] | None = None,
    fixed_structure: dict[str, Any] | None = None,
) -> ResultBundle:
    """Execute one request and emit a qualified ResultBundle or fail closed."""
    request_payload = request.to_dict()
    if request_payload["case_bundle_id"] != bundle.bundle_id:
        raise ValueError("SolveRequest引用的CaseBundle不一致")
    if request_payload.get("allow_unserved", False) is not False:
        raise ValueError("生产SolveRequest不允许未供热")
    if request_payload.get("model_profile", MODEL_PROFILE) != MODEL_PROFILE:
        raise ValueError(f"model_profile必须为{MODEL_PROFILE}")
    if request_payload.get("optimization_scope") != "five_candidate_shortest_path_trees":
        raise ValueError("optimization_scope必须为five_candidate_shortest_path_trees")
    if request_payload["objective"] == "lexicographic_carbon":
        raise ValueError("词典序碳端点尚未交接；请使用carbon请求或等待B组后续节点")
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    started_at = datetime.now(timezone.utc)
    started_clock = perf_counter()
    mode = request_payload["mode"]
    case = road_case.with_mode(mode)
    if fixed_structure is not None and request_payload["tes_enabled"] is not True:
        raise ValueError("fixed_structure只允许用于TES ON配对求解")
    candidate_ids = _candidate_ids(case, mode, fixed_structure)
    candidate_rows: list[dict[str, Any]] = []
    candidates: list[tuple[float, str, Path, dict, dict]] = []
    certified_infeasible = 0

    for site_id in candidate_ids:
        task_root = output / "candidate_tasks" / site_id
        task_root.mkdir(parents=True, exist_ok=False)
        model = None
        status: dict[str, Any] = {
            "site_id": site_id, "mode": mode, "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            design = build_compact_tree_design(case, site_id)
            model = build_compact_model(
                case, design,
                objective=request_payload["objective"],
                epsilon_kgCO2e_per_year=request_payload["epsilon_carbon_kg"],
                enable_tes=request_payload["tes_enabled"],
            )
            if fixed_structure is not None:
                _apply_fixed_structure(model, case, fixed_structure)
            metadata = compact_model_metadata(model)
            metadata["fixed_structure_sha256"] = (
                fixed_structure["fixed_structure_sha256"]
                if fixed_structure is not None else None
            )
            _write_json(task_root / "model_metadata.json", metadata)
            results = solve_pyomo_model(model, _request_settings(request_payload, task_root))
            evidence = get_solver_evidence(results)
            if fixed_structure is not None:
                _assert_fixed_structure_solution(model, fixed_structure)
            compact_qa = audit_compact_solution(case, model)
            if compact_qa.get("passed") is not True:
                raise ValueError(f"紧凑模型全时域QA失败: {compact_qa}")
            solution_root = task_root / "solution"
            independent_qa = export_compact_solution(case, model, solution_root)
            if independent_qa.get("passed") is not True:
                raise ValueError(f"独立导出QA失败: {independent_qa}")
            _carbon_breakdown(solution_root)
            objective = _objective_value(model, request_payload["objective"])
            cost = float(value(model.annual_real_cost_CNY_per_year))
            carbon = float(value(model.annual_operating_physical_carbon_kgCO2e_per_year))
            status.update({
                "status": "qualified", "objective_value": objective,
                "annual_real_cost_CNY_per_year": cost,
                "annual_operating_carbon_kgCO2e_per_year": carbon,
                "incumbent": evidence["incumbent_objective"],
                "best_bound": evidence["best_objective_bound"],
                "reported_gap": evidence["relative_mip_gap"],
                "termination_condition": evidence["termination_condition"],
                "compact_qa": compact_qa,
                "independent_qa_passed": independent_qa.get("passed") is True,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            })
            candidates.append((objective, site_id, solution_root, evidence, independent_qa))
        except SolverNotOptimalError as exc:
            is_certified_infeasible = (
                "infeasible" in exc.termination_condition.lower()
                and not exc.has_feasible_solution
            )
            status.update({
                "status": (
                    "certified_infeasible"
                    if is_certified_infeasible else "not_qualified"
                ),
                "error": None if is_certified_infeasible else str(exc),
                "termination_condition": exc.termination_condition,
                "incumbent": exc.incumbent_objective, "best_bound": exc.best_objective_bound,
                "reported_gap": exc.reported_mip_gap,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            })
            if is_certified_infeasible:
                certified_infeasible += 1
        except Exception as exc:
            status.update({
                "status": "error", "error": f"{type(exc).__name__}: {exc}",
                "finished_at": datetime.now(timezone.utc).isoformat(),
            })
        finally:
            _write_json(task_root / "task_status.json", status)
            candidate_rows.append({
                key: status.get(key) for key in (
                    "site_id", "mode", "status", "objective_value",
                    "annual_real_cost_CNY_per_year", "annual_operating_carbon_kgCO2e_per_year",
                    "incumbent", "best_bound", "reported_gap", "termination_condition", "error",
                )
            })
            del model
            gc.collect()

    comparison = pd.DataFrame(candidate_rows)
    if len(candidates) + certified_infeasible != len(candidate_ids) or not candidates:
        comparison["selected"] = False
        comparison.to_csv(output / "candidate_site_comparison.csv", index=False, encoding="utf-8-sig")
        _write_json(output / "run_manifest.json", {
            "executor_version": SOLVE_EXECUTOR_VERSION, "model_profile": MODEL_PROFILE,
            "mode": mode, "candidate_count": len(candidate_ids),
            "qualified_candidate_count": len(candidates),
            "certified_infeasible_candidate_count": certified_infeasible,
            "result_qualified": False,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "legacy_fallback_used": False,
        })
        raise RuntimeError(
            "候选站子任务存在未认证失败，或全部候选均不可行，不能认证五树范围全局结果"
        )

    selected = min(candidates, key=lambda row: (row[0], row[1]))
    selected_objective, selected_site, selected_root, selected_evidence, selected_qa = selected
    comparison["selected"] = comparison["site_id"].eq(selected_site)
    comparison.to_csv(output / "candidate_site_comparison.csv", index=False, encoding="utf-8-sig")
    global_bound = min(float(item[3]["best_objective_bound"]) for item in candidates)
    global_gap = abs(selected_objective - global_bound) / max(1.0, abs(selected_objective))
    accepted_gap = float(request_payload["solver"]["mip_gap"])
    if global_gap > accepted_gap + 1e-12:
        raise RuntimeError(f"五树合成gap={global_gap:.6%}超过{accepted_gap:.6%}")

    selected_target = output / "selected_solution"
    shutil.copytree(selected_root, selected_target)
    standard_files = (
        "capacity_decisions.csv", "building_connection.csv", "network_decisions.geojson",
        "dispatch_hourly.parquet", "cost_breakdown.csv", "carbon_breakdown.csv",
        "qa_summary.json", "independent_recalculation.json", "solution_summary.json",
        "storage_decisions.csv", "storage_hourly.parquet", "network_decisions.csv",
        "network_hourly.parquet", "building_hourly.parquet", "access_decisions.csv",
        "access_decisions.geojson", "station_decisions.csv", "node_balance_check.parquet",
    )
    for name in standard_files:
        shutil.copy2(selected_target / name, output / name)
    shutil.copy2(output / "qa_summary.json", output / "independent_qa.json")
    realized_scope = (
        "fixed_structure_tes_pair"
        if fixed_structure is not None
        else "five_candidate_shortest_path_trees"
    )
    _write_json(output / "selected_site.json", {
        "selected_site_id": selected_site,
        "optimization_scope": realized_scope,
        "all_candidate_sites_certified": fixed_structure is None,
        "qualified_candidate_site_count": len(candidates),
        "certified_infeasible_candidate_site_count": certified_infeasible,
        "road_constrained": True,
        "construction_feasibility_verified": False,
        "fixed_structure_sha256": (
            fixed_structure["fixed_structure_sha256"]
            if fixed_structure is not None else None
        ),
    })
    global_evidence = {
        "executor_version": SOLVE_EXECUTOR_VERSION,
        "model_version": COMPACT_MODEL_VERSION,
        "incumbent": selected_objective,
        "best_bound": global_bound,
        "certified_gap": global_gap,
        "accepted_gap": accepted_gap,
        "selected_site_id": selected_site,
        "optimization_scope": realized_scope,
        "fixed_structure_sha256": (
            fixed_structure["fixed_structure_sha256"]
            if fixed_structure is not None else None
        ),
        "candidate_evidence": {item[1]: item[3] for item in candidates},
    }
    _write_json(output / "solver_evidence.json", global_evidence)
    manifest = {
        "executor_version": SOLVE_EXECUTOR_VERSION,
        "model_profile": MODEL_PROFILE,
        "model_version": COMPACT_MODEL_VERSION,
        "case_bundle_id": bundle.bundle_id,
        "case_bundle_content_id": bundle.content_id,
        "solve_request_id": request.bundle_id,
        "road_case_content_sha256": road_case_content_sha256(road_case),
        "road_case_evidence": road_case_evidence or {},
        "run_id": output.name,
        "mode": mode,
        "objective": request_payload["objective"],
        "tes_enabled": request_payload["tes_enabled"],
        "building_count": len(case.common.demand_nodes),
        "hour_count": len(case.common.hours),
        "candidate_count": len(candidate_ids),
        "qualified_candidate_count": len(candidates),
        "certified_infeasible_candidate_count": certified_infeasible,
        "selected_site_id": selected_site,
        "optimization_scope": realized_scope,
        "legacy_fallback_used": False,
        "fixed_structure_sha256": (
            fixed_structure["fixed_structure_sha256"]
            if fixed_structure is not None else None
        ),
        "git_sha": _git_sha(),
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": perf_counter() - started_clock,
        "solver_executed": True,
        "result_qualified": True,
    }
    _write_json(output / "run_manifest.json", manifest)
    if fixed_structure is not None:
        _write_json(output / "fixed_structure.json", fixed_structure)

    artifacts = []
    role_by_name = {
        "independent_qa.json": "independent_qa",
        "solver_evidence.json": "solver_evidence",
        "capacity_decisions.csv": "capacity_decisions",
        "building_connection.csv": "building_connection",
        "network_decisions.geojson": "network_decisions",
        "dispatch_hourly.parquet": "dispatch_hourly",
        "cost_breakdown.csv": "cost_breakdown",
        "carbon_breakdown.csv": "carbon_breakdown",
        "run_manifest.json": "run_manifest",
    }
    if fixed_structure is not None:
        role_by_name["fixed_structure.json"] = "fixed_structure"
    for name, role in role_by_name.items():
        path = output / name
        artifacts.append({"role": role, "path": str(path), "sha256": sha256_file(path)})
    selected_log = output / "candidate_tasks" / selected_site / "solver.log"
    artifacts.append({"role": "solver_log", "path": str(selected_log), "sha256": sha256_file(selected_log)})
    termination = (
        "optimal"
        if all(str(item[3]["termination_condition"]).lower() == "optimal" for item in candidates)
        else "feasible"
    )
    result = ResultBundle.from_dict({
        "interface_version": bundle.to_dict()["interface_version"],
        "case_bundle_id": bundle.bundle_id,
        "solve_request_id": request.bundle_id,
        "run_id": output.name,
        "artifacts": artifacts,
        "solver_executed": True,
        "qualified": True,
        "termination_condition": termination,
        "solve_evidence": global_evidence,
        "qa": selected_qa,
    })
    result.write(output / "result_bundle.json")
    return result


def execute_prepared_request(
    bundle_path: str | Path,
    request_path: str | Path,
    output_dir: str | Path,
) -> ResultBundle:
    bundle, road_case, evidence = load_prepared_road_case(bundle_path)
    request = SolveRequest.read(request_path)
    return execute_request(bundle, road_case, request, output_dir, road_case_evidence=evidence)


def execute_cost_endpoint_set(
    bundle_path: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    """Execute the three prepared no-TES cost requests through one loaded case."""
    bundle_path = Path(bundle_path).expanduser().resolve(strict=True)
    bundle, road_case, evidence = load_prepared_road_case(bundle_path)
    output = Path(output_root).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = {}
    for mode in ("central", "distributed", "hybrid"):
        request_path = bundle_path.parent / "solve_requests" / f"{mode}_cost.json"
        request = SolveRequest.read(request_path)
        result = execute_request(
            bundle, road_case, request, output / mode, road_case_evidence=evidence,
        )
        results[mode] = {
            "result_bundle_id": result.bundle_id,
            "path": str((output / mode / "result_bundle.json").resolve()),
            "qualified": result.to_dict()["qualified"],
        }
    summary = {
        "executor_version": SOLVE_EXECUTOR_VERSION,
        "request_set": "cost-endpoints", "case_bundle_id": bundle.bundle_id,
        "road_case_content_sha256": road_case_content_sha256(road_case),
        "results": results, "all_qualified": all(row["qualified"] for row in results.values()),
    }
    _write_json(output / "request_set_summary.json", summary)
    return summary


def _request_from_base(
    base: SolveRequest,
    *,
    objective: str,
    epsilon: float | None = None,
    tes_enabled: bool = False,
) -> SolveRequest:
    payload = base.to_dict()
    payload.update({
        "objective": objective,
        "epsilon_carbon_kg": epsilon,
        "tes_enabled": tes_enabled,
    })
    return SolveRequest.from_dict(payload)


def _base_request(bundle_path: Path, mode: str) -> SolveRequest:
    request = SolveRequest.read(
        bundle_path.parent / "solve_requests" / f"{mode}_cost.json"
    )
    if request.to_dict()["mode"] != mode:
        raise ValueError(f"基础SolveRequest模式错位：{mode}")
    return request


def _point_from_result(
    point_id: str,
    labels: tuple[str, ...],
    epsilon: float | None,
    output: Path,
) -> ParetoPoint:
    summary = json.loads(
        (output / "solution_summary.json").read_text(encoding="utf-8")
    )
    evidence = json.loads(
        (output / "solver_evidence.json").read_text(encoding="utf-8")
    )
    qa = json.loads((output / "independent_qa.json").read_text(encoding="utf-8"))
    if qa.get("passed") is not True:
        raise ValueError(f"Pareto点{point_id}独立QA未通过")
    return ParetoPoint(
        point_id=point_id,
        mode=str(summary["mode"]),
        labels=labels,
        epsilon_kgCO2e_per_year=epsilon,
        annual_real_cost_CNY_per_year=float(summary["real_cost_CNY"]),
        annual_operating_carbon_kgCO2e_per_year=float(summary["carbon_kgCO2e"]),
        annual_hns_penalty_CNY_per_year=float(summary["hns_penalty_CNY"]),
        unserved_heat_kWh=float(qa["unserved_kWh"]),
        solver_status="ok",
        termination_condition=(
            "optimal" if float(evidence["certified_gap"]) <= 1e-12 else "feasible"
        ),
        reported_mip_gap=float(evidence["certified_gap"]),
        incumbent_objective=float(evidence["incumbent"]),
        best_objective_bound=float(evidence["best_bound"]),
        solver_evidence_file=str((output / "solver_evidence.json").resolve()),
    )


def _execute_pareto_point(
    *,
    bundle: CaseBundle,
    road_case: RoadCase,
    road_case_evidence: dict[str, Any],
    base_request: SolveRequest,
    root: Path,
    point_id: str,
    objective: str,
    labels: tuple[str, ...],
    epsilon: float | None = None,
) -> ParetoPoint:
    request = _request_from_base(
        base_request, objective=objective, epsilon=epsilon, tes_enabled=False
    )
    requests = root / "requests"
    requests.mkdir(parents=True, exist_ok=True)
    _write_json(requests / f"{point_id}.json", request.to_dict())
    point_root = root / "points" / point_id
    execute_request(
        bundle,
        road_case,
        request,
        point_root,
        road_case_evidence=road_case_evidence,
    )
    return _point_from_result(point_id, labels, epsilon, point_root)


def _write_pareto_outputs(
    root: Path,
    points: list[ParetoPoint],
    collapsed_modes: dict[str, str],
) -> dict[str, Any]:
    run = assemble_pareto_run(tuple(points), ParetoSpec(point_count=5))
    rows = []
    for point in points:
        row = point_to_dict(point)
        row["labels"] = "|".join(point.labels)
        rows.append(row)
    pd.DataFrame(rows).to_csv(
        root / "pareto_points.csv", index=False, encoding="utf-8-sig"
    )
    mode_frontiers = {
        mode: [point_to_dict(point) for point in frontier]
        for mode, frontier in run.mode_frontiers.items()
    }
    combined = [point_to_dict(point) for point in run.combined_frontier]
    _write_json(root / "pareto_frontiers.json", {
        "method": "epsilon_constraint",
        "internal_fractions": list(PARETO_INTERNAL_FRACTIONS),
        "mode_frontiers": mode_frontiers,
        "combined_frontier": combined,
        "collapsed_modes": collapsed_modes,
    })
    knees = {
        "modes": {
            mode: next(
                (point_to_dict(point) for point in frontier if point.is_knee),
                None,
            )
            for mode, frontier in run.mode_frontiers.items()
        },
        "combined": next(
            (point_to_dict(point) for point in run.combined_frontier if point.is_knee),
            None,
        ),
        "rule": "maximum_distance_in_normalized_cost_carbon_space",
        "no_fabricated_knee_when_fewer_than_three_distinct_points": True,
    }
    _write_json(root / "knee_points.json", knees)
    return {
        "point_count": len(points),
        "mode_frontier_counts": {
            mode: len(frontier) for mode, frontier in run.mode_frontiers.items()
        },
        "combined_frontier_count": len(run.combined_frontier),
        "knees": knees,
        "collapsed_modes": collapsed_modes,
    }


def execute_carbon_endpoint_set(
    bundle_path: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    """Solve carbon first, then minimize cost at the certified carbon floor."""
    bundle_path = Path(bundle_path).expanduser().resolve(strict=True)
    bundle, road_case, evidence = load_prepared_road_case(bundle_path)
    output = Path(output_root).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    points: list[ParetoPoint] = []
    for mode in ("central", "distributed", "hybrid"):
        base = _base_request(bundle_path, mode)
        primary = _execute_pareto_point(
            bundle=bundle, road_case=road_case, road_case_evidence=evidence,
            base_request=base, root=output,
            point_id=f"{mode}_carbon_primary", objective="carbon",
            labels=("carbon_primary_certificate",),
        )
        endpoint = _execute_pareto_point(
            bundle=bundle, road_case=road_case, road_case_evidence=evidence,
            base_request=base, root=output,
            point_id=f"{mode}_carbon_endpoint", objective="cost",
            epsilon=primary.annual_operating_carbon_kgCO2e_per_year,
            labels=("carbon_endpoint", "lexicographic_cost_tiebreak"),
        )
        points.append(endpoint)
    payload = {
        "request_set": "carbon-endpoints",
        "method": "lexicographic_carbon_then_cost",
        "case_bundle_id": bundle.bundle_id,
        "points": [point_to_dict(point) for point in points],
        "qualified": len(points) == 3,
    }
    _write_json(output / "request_set_summary.json", payload)
    return payload


def execute_pareto_knee_set(
    bundle_path: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    """Run three no-TES mode frontiers and expose knee points, not four labels."""
    bundle_path = Path(bundle_path).expanduser().resolve(strict=True)
    bundle, road_case, evidence = load_prepared_road_case(bundle_path)
    output = Path(output_root).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    points: list[ParetoPoint] = []
    collapsed_modes: dict[str, str] = {}
    for mode in ("central", "distributed", "hybrid"):
        base = _base_request(bundle_path, mode)
        cost = _execute_pareto_point(
            bundle=bundle, road_case=road_case, road_case_evidence=evidence,
            base_request=base, root=output,
            point_id=f"{mode}_cost_endpoint", objective="cost",
            labels=("cost_endpoint",),
        )
        primary = _execute_pareto_point(
            bundle=bundle, road_case=road_case, road_case_evidence=evidence,
            base_request=base, root=output,
            point_id=f"{mode}_carbon_primary", objective="carbon",
            labels=("carbon_primary_certificate",),
        )
        carbon = _execute_pareto_point(
            bundle=bundle, road_case=road_case, road_case_evidence=evidence,
            base_request=base, root=output,
            point_id=f"{mode}_carbon_endpoint", objective="cost",
            epsilon=primary.annual_operating_carbon_kgCO2e_per_year,
            labels=("carbon_endpoint", "lexicographic_cost_tiebreak"),
        )
        points.extend((cost, carbon))
        low = carbon.annual_operating_carbon_kgCO2e_per_year
        high = cost.annual_operating_carbon_kgCO2e_per_year
        if high < low - PARETO_EPSILON_TOLERANCE_KG:
            raise ValueError(f"模式{mode}成本端点碳排低于认证最低碳端点")
        if high - low <= PARETO_EPSILON_TOLERANCE_KG:
            collapsed_modes[mode] = "成本端点与最低碳端点重合，无需伪造内部ε点"
            continue
        for fraction in PARETO_INTERNAL_FRACTIONS:
            epsilon = low + (high - low) * fraction
            tag = int(round(fraction * 100))
            points.append(_execute_pareto_point(
                bundle=bundle, road_case=road_case, road_case_evidence=evidence,
                base_request=base, root=output,
                point_id=f"{mode}_epsilon_{tag:03d}", objective="cost",
                epsilon=epsilon, labels=("epsilon", f"fraction_{fraction:.2f}"),
            ))
    summary = _write_pareto_outputs(output, points, collapsed_modes)
    payload = {
        "request_set": "pareto-knee",
        "method": "epsilon_constraint",
        "case_bundle_id": bundle.bundle_id,
        "tes_enabled": False,
        "qualified": True,
        **summary,
    }
    _write_json(output / "request_set_summary.json", payload)
    return payload


def _frontier_representative(
    pareto_root: Path, mode: str
) -> tuple[dict[str, Any], str]:
    frontiers = json.loads(
        (pareto_root / "pareto_frontiers.json").read_text(encoding="utf-8")
    )
    points = list(frontiers["mode_frontiers"].get(mode, ()))
    if not points:
        raise ValueError(f"模式{mode}没有合格Pareto前沿，无法进行TES配对")
    knee = next((point for point in points if point.get("is_knee") is True), None)
    if knee is not None:
        return knee, "normalized_knee"
    # A two-point or collapsed frontier has no mathematical interior knee.  Use
    # the minimum-cost endpoint as the documented representative, without
    # relabelling it as a fabricated knee.
    endpoint = min(
        points,
        key=lambda point: (
            float(point["annual_real_cost_CNY_per_year"]),
            float(point["annual_operating_carbon_kgCO2e_per_year"]),
            str(point["point_id"]),
        ),
    )
    return endpoint, "cost_endpoint_fallback_no_distinct_knee"


def _read_costs(root: Path) -> dict[str, float]:
    table = pd.read_csv(root / "cost_breakdown.csv")
    if table["component"].duplicated().any():
        raise ValueError("成本分项存在重复component")
    return {
        str(row.component): float(row.annual_CNY)
        for row in table.itertuples(index=False)
    }


def _compare_tes_pair(
    off_root: Path,
    on_root: Path,
    fixed: dict[str, Any],
    role: str,
) -> dict[str, Any]:
    off_summary = json.loads(
        (off_root / "solution_summary.json").read_text(encoding="utf-8")
    )
    on_summary = json.loads(
        (on_root / "solution_summary.json").read_text(encoding="utf-8")
    )
    on_manifest = json.loads(
        (on_root / "run_manifest.json").read_text(encoding="utf-8")
    )
    on_qa = json.loads(
        (on_root / "independent_qa.json").read_text(encoding="utf-8")
    )
    realized = _load_fixed_structure(on_root)
    structure_match = (
        realized["site_id"] == fixed["site_id"]
        and realized["connection_vector"] == fixed["connection_vector"]
        and realized["pipe_grade_by_edge"] == fixed["pipe_grade_by_edge"]
    )
    off_costs, on_costs = _read_costs(off_root), _read_costs(on_root)
    fixed_cost_components = ("pipe_investment", "connection_investment")
    fixed_cost_residual = max(
        abs(on_costs[name] - off_costs[name]) for name in fixed_cost_components
    )
    storage = pd.read_csv(on_root / "storage_decisions.csv")
    active = storage.loc[storage["built"].astype(float) > 0.5]
    if active.empty:
        tes = {
            "built": False,
            "energy_capacity_kWh_th": 0.0,
            "charge_capacity_kW_th": 0.0,
            "discharge_capacity_kW_th": 0.0,
            "actual_peak_charge_kW_th": 0.0,
            "actual_peak_discharge_kW_th": 0.0,
            "energy_upper_bound_binding": False,
            "charge_upper_bound_binding": False,
            "discharge_upper_bound_binding": False,
        }
    else:
        row = active.iloc[0]
        tes = {
            "built": True,
            "energy_capacity_kWh_th": float(row["energy_capacity_kWh"]),
            "charge_capacity_kW_th": float(row["charge_capacity_kW"]),
            "discharge_capacity_kW_th": float(row["discharge_capacity_kW"]),
            "actual_peak_charge_kW_th": float(row["actual_peak_charge_kW_th"]),
            "actual_peak_discharge_kW_th": float(row["actual_peak_discharge_kW_th"]),
            "energy_upper_bound_binding": bool(row["energy_upper_bound_binding"]),
            "charge_upper_bound_binding": bool(row["charge_upper_bound_binding"]),
            "discharge_upper_bound_binding": bool(row["discharge_upper_bound_binding"]),
        }
    passed = (
        structure_match
        and fixed_cost_residual <= 1e-6
        and on_qa.get("passed") is True
        and on_manifest.get("result_qualified") is True
    )
    return {
        "representative_role": role,
        "mode": off_summary["mode"],
        "fixed_structure_sha256": fixed["fixed_structure_sha256"],
        "structure_match": structure_match,
        "fixed_pipe_connection_cost_residual_CNY_per_year": fixed_cost_residual,
        "tes": tes,
        "tes_off": {
            "result_path": str(off_root.resolve()),
            "annual_real_cost_CNY_per_year": float(off_summary["real_cost_CNY"]),
            "annual_operating_carbon_kgCO2e_per_year": float(off_summary["carbon_kgCO2e"]),
        },
        "tes_on": {
            "result_path": str(on_root.resolve()),
            "annual_real_cost_CNY_per_year": float(on_summary["real_cost_CNY"]),
            "annual_operating_carbon_kgCO2e_per_year": float(on_summary["carbon_kgCO2e"]),
        },
        "delta_cost_CNY_per_year": (
            float(on_summary["real_cost_CNY"]) - float(off_summary["real_cost_CNY"])
        ),
        "delta_carbon_kgCO2e_per_year": (
            float(on_summary["carbon_kgCO2e"]) - float(off_summary["carbon_kgCO2e"])
        ),
        "independent_qa_passed": on_qa.get("passed") is True,
        "passed": passed,
    }


def execute_full_study_set(
    bundle_path: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    """Run no-TES Pareto and fixed-structure TES comparisons for regional modes."""
    bundle_path = Path(bundle_path).expanduser().resolve(strict=True)
    bundle, road_case, evidence = load_prepared_road_case(bundle_path)
    output = Path(output_root).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    pareto_root = output / "pareto_no_tes"
    pareto_summary = execute_pareto_knee_set(bundle_path, pareto_root)
    pairs: dict[str, dict[str, Any]] = {}
    for mode in ("central", "hybrid"):
        point, role = _frontier_representative(pareto_root, mode)
        point_id = str(point["point_id"])
        off_root = pareto_root / "points" / point_id
        fixed = _load_fixed_structure(off_root)
        source_request = SolveRequest.read(pareto_root / "requests" / f"{point_id}.json")
        tes_request = _request_from_base(
            source_request,
            objective=source_request.to_dict()["objective"],
            epsilon=source_request.to_dict()["epsilon_carbon_kg"],
            tes_enabled=True,
        )
        pair_root = output / "tes_pairs" / mode
        pair_root.mkdir(parents=True, exist_ok=False)
        _write_json(pair_root / "tes_on_request.json", tes_request.to_dict())
        _write_json(pair_root / "fixed_structure.json", fixed)
        on_root = pair_root / "tes_on"
        execute_request(
            bundle,
            road_case,
            tes_request,
            on_root,
            road_case_evidence=evidence,
            fixed_structure=fixed,
        )
        comparison = _compare_tes_pair(off_root, on_root, fixed, role)
        _write_json(pair_root / "tes_pair_qa.json", comparison)
        pairs[mode] = comparison
    payload = {
        "request_set": "full-study",
        "method": "epsilon_constraint_with_fixed_structure_tes_pair",
        "case_bundle_id": bundle.bundle_id,
        "pareto": pareto_summary,
        "tes_pairs": pairs,
        "distributed_tes_status": "not_applicable_no_regional_station",
        "qualified": (
            pareto_summary.get("qualified") is True
            and all(pair["passed"] for pair in pairs.values())
        ),
    }
    _write_json(output / "request_set_summary.json", payload)
    return payload


def execute_request_set(
    bundle_path: str | Path,
    output_root: str | Path,
    request_set: str,
) -> dict[str, Any]:
    if request_set == "cost-endpoints":
        return execute_cost_endpoint_set(bundle_path, output_root)
    if request_set == "carbon-endpoints":
        return execute_carbon_endpoint_set(bundle_path, output_root)
    if request_set == "pareto-knee":
        return execute_pareto_knee_set(bundle_path, output_root)
    if request_set == "full-study":
        return execute_full_study_set(bundle_path, output_root)
    raise ValueError(f"未知request-set：{request_set}")


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id) is None:
        raise ValueError("run_id只能使用1..100位英文字母、数字、下划线或短横线")
    return run_id
