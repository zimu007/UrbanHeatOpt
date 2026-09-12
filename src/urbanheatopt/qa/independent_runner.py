"""Standalone C-side QA for an immutable prepared case and result bundle."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from urbanheatopt.data.bundles import ResultBundle
from urbanheatopt.optimization.solve_executor import load_prepared_road_case
from urbanheatopt.qa.road_results import audit_export


def _write_json(path: Path, payload: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def run_independent_qa(
    bundle_path: str | Path,
    result_root: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Verify frozen inputs/results and write QA evidence outside the solver run."""
    bundle, road_case, road_evidence = load_prepared_road_case(bundle_path)
    result_directory = Path(result_root).expanduser().resolve(strict=True)
    result = ResultBundle.read(result_directory / "result_bundle.json")
    result_payload = result.to_dict()
    if result_payload["case_bundle_id"] != bundle.bundle_id:
        raise ValueError("ResultBundle引用的CaseBundle与独立QA输入不一致")
    if result_payload["qualified"] is not True:
        raise ValueError("独立QA只接受qualified=true的正式ResultBundle")

    case_integrity = bundle.verify_artifacts(check_sources=True)
    result_integrity = result.verify_artifacts()
    if not case_integrity["passed"]:
        raise ValueError("CaseBundle源文件或标准快照哈希验证失败")
    if not result_integrity["passed"]:
        raise ValueError("ResultBundle产物缺失或哈希验证失败")

    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    qa = audit_export(road_case, result_directory, audit_output=output)
    _write_json(output / "independent_qa.json", qa)
    provenance = {
        "schema": "urbanheatopt_standalone_independent_qa_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_bundle_id": bundle.bundle_id,
        "result_bundle_id": result.bundle_id,
        "result_run_id": result_payload["run_id"],
        "result_root": str(result_directory),
        "qa_output": str(output),
        "solver_executed_by_qa": False,
        "qa_reads_model_expressions": False,
        "case_integrity": case_integrity,
        "result_integrity": result_integrity,
        "road_case_evidence": road_evidence,
        "passed": qa["passed"],
    }
    _write_json(output / "qa_provenance.json", provenance)
    return {
        "schema": provenance["schema"],
        "passed": provenance["passed"],
        "case_bundle_id": provenance["case_bundle_id"],
        "result_bundle_id": provenance["result_bundle_id"],
        "result_run_id": provenance["result_run_id"],
        "qa_output": provenance["qa_output"],
        "solver_executed_by_qa": False,
        "qa_reads_model_expressions": False,
        "case_files_verified": case_integrity["checked_files"],
        "result_files_verified": result_integrity["checked_files"],
        "independent_qa": str(output / "independent_qa.json"),
        "provenance": str(output / "qa_provenance.json"),
    }
