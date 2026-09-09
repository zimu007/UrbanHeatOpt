"""Versioned, immutable A/B/C hand-off objects; no model imports or solving."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


INTERFACE_VERSION = "handoff_1.1.0"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CASE_STATUS = ("input_valid", "parameter_valid", "canonical_valid", "snapshot_complete")


class BundleValidationError(ValueError):
    """A contract violation, to be reported as CLI exit code 2."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BundleValidationError(message)


def _nonempty(value: Any, name: str) -> None:
    _require(isinstance(value, str) and bool(value.strip()), f"{name} 必须是非空字符串")


def _number(value: Any, name: str, *, minimum: float = 0, maximum: float | None = None) -> None:
    _require(type(value) in (int, float) and math.isfinite(value), f"{name} 必须是有限数值，不能是布尔值")
    _require(value >= minimum, f"{name} 不得小于 {minimum}")
    if maximum is not None:
        _require(value <= maximum, f"{name} 不得大于 {maximum}")


def _boolean(value: Any, name: str) -> None:
    _require(type(value) is bool, f"{name} 必须是 JSON true/false，不能是字符串或数字")


def _hash(value: Any, name: str) -> None:
    _require(isinstance(value, str) and _SHA256.fullmatch(value) is not None, f"{name} 必须是小写 SHA-256")


def _absolute_file_name(value: Any, name: str) -> None:
    _nonempty(value, name)
    _require(Path(value).is_absolute(), f"{name} 必须使用绝对路径")


def _canonical_json(payload: Mapping[str, Any]) -> str:
    _require(isinstance(payload, Mapping), "交接内容必须为 JSON 对象")
    try:
        return json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise BundleValidationError(f"交接内容不是有效 JSON：{exc}") from exc


def _validate_artifacts(artifacts: Any) -> None:
    _require(isinstance(artifacts, list), "artifacts 必须是列表")
    paths: set[str] = set()
    for index, artifact in enumerate(artifacts):
        _require(isinstance(artifact, dict), f"artifacts[{index}] 必须是对象")
        _nonempty(artifact.get("role"), f"artifacts[{index}].role")
        _absolute_file_name(artifact.get("path"), f"artifacts[{index}].path")
        _hash(artifact.get("sha256"), f"artifacts[{index}].sha256")
        normalized = str(Path(artifact["path"]).resolve()).casefold()
        _require(normalized not in paths, f"重复 artifact 路径：{artifact['path']}")
        paths.add(normalized)


@dataclass(frozen=True, slots=True, init=False)
class _JsonBundle:
    """Only a canonical JSON string is retained, never caller-owned containers."""

    _json: str

    def __init__(self, payload: Mapping[str, Any]) -> None:
        serialized = _canonical_json(payload)
        normalized = json.loads(serialized)
        _require(normalized.get("interface_version") == INTERFACE_VERSION,
                 f"interface_version 必须为 {INTERFACE_VERSION}，不自动迁移旧版本")
        self._validate(normalized)
        object.__setattr__(self, "_json", serialized)

    def _validate(self, payload: dict[str, Any]) -> None:
        raise NotImplementedError

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> _JsonBundle:
        return cls(payload)

    @classmethod
    def read(cls, path: str | Path) -> _JsonBundle:
        try:
            return cls(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise BundleValidationError(f"无法读取交接文件 {path}：{exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._json)

    @property
    def payload(self) -> dict[str, Any]:
        return self.to_dict()

    @property
    def bundle_id(self) -> str:
        return hashlib.sha256(self._json.encode("utf-8")).hexdigest()

    def to_json(self) -> str:
        return self._json

    def write(self, path: str | Path, *, exclusive: bool = True) -> None:
        """Runtime artifact output; callers must choose a fresh run directory."""
        with Path(path).open("x" if exclusive else "w", encoding="utf-8", newline="\n") as stream:
            stream.write(self._json + "\n")

    def verify_artifacts(self, *, check_sources: bool = False) -> dict[str, Any]:
        payload = self.to_dict()
        files = [(item["path"], item["sha256"], item["role"])
                 for item in payload.get("artifacts", [])]
        if check_sources:
            files.extend((name, digest, "source") for name, digest in payload.get("source_hashes", {}).items())
        checks = []
        for name, expected, role in files:
            try:
                actual = sha256_file(name)
                error = None if actual == expected else "SHA-256 与冻结快照不一致"
            except OSError as exc:
                actual, error = None, str(exc)
            checks.append({"path": name, "role": role, "expected_sha256": expected,
                           "actual_sha256": actual, "passed": error is None, "error": error})
        return {"passed": all(item["passed"] for item in checks), "checked_files": len(checks), "checks": checks}


class CaseBundle(_JsonBundle):
    """A prepares this object; B consumes file bytes only after hash verification."""

    __slots__ = ()

    @property
    def content_id(self) -> str:
        """Path-independent identity for equivalent fresh prepare outputs."""
        payload = self.to_dict()
        identity = {
            "interface_version": payload["interface_version"],
            "data_version": payload["data_version"],
            "parameter_version": payload["parameter_version"],
            "artifacts": sorted(
                (row["role"], row["sha256"]) for row in payload["artifacts"]
            ),
            "source_sha256": sorted(payload["source_hashes"].values()),
            "units": payload["units"],
            "physical_scope": payload.get("physical_scope"),
            "economic_boundary": payload.get("economic_boundary"),
            "generated_spatial_status": payload.get("generated_spatial_status"),
        }
        return hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()

    def _validate(self, payload: dict[str, Any]) -> None:
        for name in ("data_version", "parameter_version", "git_sha"):
            _nonempty(payload.get(name), name)
        _validate_artifacts(payload.get("artifacts"))
        sources = payload.get("source_hashes")
        _require(isinstance(sources, dict), "source_hashes 必须是绝对路径到 SHA-256 的对象")
        for name, digest in sources.items():
            _absolute_file_name(name, "source_hashes.path")
            _hash(digest, f"source_hashes[{name}]")
        _require(isinstance(payload.get("units"), dict) and bool(payload["units"]), "units 不能为空")
        for field, unit in payload["units"].items():
            _nonempty(field, "units.field")
            _nonempty(unit, f"units[{field}]")
        required = payload.get("capabilities_required")
        _require(isinstance(required, list), "capabilities_required 必须是列表")
        for capability in required:
            _nonempty(capability, "capabilities_required.item")
        _require(len(required) == len(set(required)), "capabilities_required 不得重复")
        status = payload.get("status")
        _require(isinstance(status, dict), "status 必须是对象")
        for field in _CASE_STATUS:
            _boolean(status.get(field), f"status.{field}")
        if status["snapshot_complete"]:
            _require(bool(payload["artifacts"]) and bool(sources), "完整快照必须包含产物及源文件哈希")
            _require(all(status[field] for field in _CASE_STATUS[:-1]), "输入或参数未通过时不能标记 snapshot_complete")


class SolveRequest(_JsonBundle):
    """A describes a solve, not a formula or an implementation capability flag."""

    __slots__ = ()

    def _validate(self, payload: dict[str, Any]) -> None:
        _hash(payload.get("case_bundle_id"), "case_bundle_id")
        _require(
            payload.get("model_profile") == "compact_five_tree_fullseason_v2",
            "model_profile 必须为 compact_five_tree_fullseason_v2",
        )
        _require(
            payload.get("optimization_scope") == "five_candidate_shortest_path_trees",
            "optimization_scope 必须为 five_candidate_shortest_path_trees",
        )
        _require(payload.get("mode") in ("central", "distributed", "hybrid"), "mode 必须为 central/distributed/hybrid")
        _require(payload.get("objective") in ("cost", "carbon", "lexicographic_carbon"), "objective 非法")
        epsilon = payload.get("epsilon_carbon_kg")
        if epsilon is not None:
            _number(epsilon, "epsilon_carbon_kg")
            _require(payload["objective"] == "cost", "ε 碳上限仅用于 cost 目标请求")
        _boolean(payload.get("tes_enabled"), "tes_enabled")
        _boolean(payload.get("allow_unserved"), "allow_unserved")
        _require(payload["allow_unserved"] is False, "生产SolveRequest不允许未供热")
        solver = payload.get("solver")
        _require(isinstance(solver, dict), "solver 必须为对象")
        _require(solver.get("name") in ("highs", "gurobi", "auto"), "solver.name 非法")
        for field, minimum in (("threads", 1), ("random_seed", 0)):
            _require(type(solver.get(field)) is int and solver[field] >= minimum, f"solver.{field} 必须为整数且 ≥{minimum}")
        _require(solver["threads"] in (1, 4, 8), "solver.threads 只能为 1、4 或 8")
        _number(solver.get("mip_gap"), "solver.mip_gap", maximum=1)
        if solver.get("time_limit_s") is not None:
            _number(solver["time_limit_s"], "solver.time_limit_s")
            _require(solver["time_limit_s"] > 0, "time_limit_s 必须为正数或 null")
        sensitivity = payload.get("sensitivity")
        if sensitivity is not None:
            _require(isinstance(sensitivity, dict), "sensitivity 必须是对象")
            _require(
                set(sensitivity) == {"scenario_id", "tes_capex_multiplier"},
                "sensitivity只允许scenario_id和tes_capex_multiplier",
            )
            _nonempty(sensitivity.get("scenario_id"), "sensitivity.scenario_id")
            multiplier = sensitivity.get("tes_capex_multiplier")
            _number(multiplier, "sensitivity.tes_capex_multiplier", maximum=1.0)
            _require(multiplier > 0, "sensitivity.tes_capex_multiplier必须大于0")
            _require(payload["tes_enabled"] is True, "TES投资敏感性只允许用于tes_enabled=true")


class ResultBundle(_JsonBundle):
    """B-owned result envelope; schema validity is not independent QA evidence."""

    __slots__ = ()

    def _validate(self, payload: dict[str, Any]) -> None:
        _hash(payload.get("case_bundle_id"), "case_bundle_id")
        _hash(payload.get("solve_request_id"), "solve_request_id")
        _nonempty(payload.get("run_id"), "run_id")
        _validate_artifacts(payload.get("artifacts"))
        _boolean(payload.get("solver_executed"), "solver_executed")
        _boolean(payload.get("qualified"), "qualified")
        allowed = ("not_executed", "optimal", "feasible", "infeasible", "unbounded", "error", "interrupted", "time_limit")
        _require(payload.get("termination_condition") in allowed, "termination_condition 非法")
        if not payload["solver_executed"]:
            _require(payload["termination_condition"] == "not_executed", "未执行求解不能报告求解终止状态")
        if payload["qualified"]:
            _require(payload["solver_executed"] and payload["termination_condition"] in ("optimal", "feasible"),
                     "未执行、失败或未认证超时结果不能标记 qualified")
            proof = payload.get("solve_evidence")
            _require(isinstance(proof, dict), "qualified 结果缺少 solve_evidence")
            for field in ("incumbent", "best_bound", "certified_gap", "accepted_gap"):
                _number(proof.get(field), f"solve_evidence.{field}")
            _require(proof["best_bound"] <= proof["incumbent"] + 1e-6, "最小化下界不得超过可行解上界")
            recomputed_gap = abs(proof["incumbent"] - proof["best_bound"]) / max(1.0, abs(proof["incumbent"]))
            _require(abs(proof["certified_gap"] - recomputed_gap) <= 1e-8,
                     "certified_gap 与可行上界/下界重新计算不一致")
            _require(proof["certified_gap"] <= proof["accepted_gap"] <= 1, "结果尚未达到认证 gap")
            qa = payload.get("qa")
            _require(isinstance(qa, dict) and qa.get("passed") is True, "qualified 结果必须通过独立 QA")
            roles = {artifact["role"] for artifact in payload["artifacts"]}
            _require({"solver_log", "independent_qa"}.issubset(roles), "qualified 结果必须附求解日志和独立 QA 的文件哈希")


def ready_report(
    bundle: CaseBundle,
    *,
    verify_files: bool = True,
    integration_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute research/publication readiness from generated evidence.

    The evidence is produced by executable A/B adapter checks, not by a manual
    configuration flag.  ``solver_executed`` remains false until a ResultBundle
    with independent QA exists.
    """
    payload = bundle.to_dict()
    integrity = bundle.verify_artifacts(check_sources=True) if verify_files else {
        "passed": False, "checked_files": 0, "checks": [], "reason": "尚未执行磁盘哈希复验"}
    blockers = []
    for key in _CASE_STATUS:
        if not payload["status"][key]:
            blockers.append({"id": f"case_{key}", "owner": "A", "reason": f"{key} 未通过"})
    if not integrity["passed"]:
        blockers.append({"id": "artifact_integrity", "owner": "A", "reason": "产物/源文件完整性未通过或未验证"})
    evidence = dict(integration_evidence or {})
    known = {
        "monthly_demand_charge": "虚拟总表月度最大需量费表达式及手算测试待接通",
        "effective_parameter_mapping": "新经济参数到目标表达式的逐项映射、边界及重算待验收",
        "tes": "新参数下有/无 TES 单例及 SOC 独立验算待 B 验收",
        "site_capacity": "候选地块/容量接口到约束的消费实现待 B 接通",
        "result_bundle": "B 标准结果导出与 C 独立重算/展示消费尚未联调",
    }
    evidence_keys = {
        "monthly_demand_charge": "monthly_demand_charge_ready",
        "effective_parameter_mapping": "effective_parameter_mapping_ready",
        "site_capacity": "site_capacity_ready",
        "pipe_capacity": "pipe_capacity_ready",
        "tes": "tes_capacity_and_power_ready",
        "result_bundle": "result_bundle_contract_ready",
    }
    b1_ready = evidence.get("b1_real_bundle_smoke_pass") is True
    b2_ready = evidence.get("b2_capacity_adapter_smoke_pass") is True
    if not b1_ready or not b2_ready:
        blockers.append({
            "id": "new_case_consumer",
            "owner": "A/B",
            "reason": "CaseBundle经济投影或容量边界消费适配器未通过真实快照联调；不回退历史模型",
        })
    for capability in payload["capabilities_required"]:
        key = evidence_keys.get(capability)
        if key is None or evidence.get(key) is not True:
            blockers.append({"id": f"capability_{capability}", "owner": "B/C" if capability == "result_bundle" else "A/B",
                             "reason": known.get(capability, f"尚无经接入测试认证的能力消费者：{capability}")})
    input_ready = all(payload["status"][key] for key in ("input_valid", "canonical_valid", "snapshot_complete")) and integrity["passed"]
    parameter_ready = payload["status"]["parameter_valid"]
    required_model_capabilities = tuple(
        capability for capability in payload["capabilities_required"]
        if capability != "result_bundle"
    )
    model_capability_ready = b1_ready and b2_ready and all(
        evidence.get(evidence_keys[capability]) is True
        for capability in required_model_capabilities
    )
    network_ready = evidence.get("network_product_ready") is True
    road_case_ready = evidence.get("road_case_build_pass") is True
    solver_pipeline_ready = evidence.get("solver_pipeline_ready") is True
    if not network_ready:
        blockers.append({"id": "network_product", "owner": "A", "reason": "版本化网络产物尚未通过消费校验"})
    if not road_case_ready:
        blockers.append({"id": "road_case_builder", "owner": "A/B", "reason": "最新CaseBundle尚未构建为合格RoadCase"})
    if not solver_pipeline_ready:
        blockers.append({"id": "solve_request_executor", "owner": "B", "reason": "SolveRequest生产执行器尚未接通"})
    research_solve_ready = (
        input_ready and parameter_ready and model_capability_ready
        and network_ready and road_case_ready and solver_pipeline_ready
        and evidence.get("research_boundary_use_allowed") is True
    )
    publication_ready = (
        research_solve_ready
        and evidence.get("publication_parameters_verified") is True
    )
    if research_solve_ready and not publication_ready:
        blockers.append({
            "id": "publication_station_cost_boundary",
            "owner": "用户/老师/参数组",
            "reason": "研究求解允许，但站房固定投资边界尚未确认，禁止发布正式经济结论",
        })
    return {
        "interface_version": INTERFACE_VERSION,
        "case_bundle_id": bundle.bundle_id,
        **payload["status"],
        "artifact_integrity_passed": integrity["passed"],
        "input_ready": input_ready,
        "parameter_ready": parameter_ready,
        "network_ready": network_ready,
        "road_case_ready": road_case_ready,
        "solver_pipeline_ready": solver_pipeline_ready,
        "model_capability_ready": model_capability_ready,
        "research_solve_ready": research_solve_ready,
        "program_feasibility_input_ready": (
            research_solve_ready
            and evidence.get("program_feasibility_input_ready") is True
        ),
        "economic_conclusion_input_ready": (
            research_solve_ready
            and evidence.get("economic_conclusion_input_ready") is True
        ),
        "publication_ready": publication_ready,
        "economic_result_reliable": False,
        "model_ready": research_solve_ready,
        "solver_executed": False,
        "result_qualified": False,
        "registered_model_adapter": (
            "handoff_v1+site_capacity_v1+road_case_builder_1.0.0+solve_request_executor_1.2.0"
            if b1_ready and b2_ready and road_case_ready and solver_pipeline_ready
            else None
        ),
        "blockers": blockers,
        "artifact_integrity": integrity,
        "integration_evidence": evidence,
        "note": "输入口径就绪与结果可靠分开验收：本报告未执行求解，economic_result_reliable固定为false。",
    }
