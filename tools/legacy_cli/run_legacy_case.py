"""Explicit historical regression entry for the original UrbanHeatOpt chain."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from urbanheatopt.data.adapters.legacy_case import adapt_case_to_legacy_case_directory
from urbanheatopt.reporting.results.standard import export_standard_results
from urbanheatopt.data.validation.inputs import InputValidationError, validate_case_inputs


def _write_summary(path: Path, summary: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the explicit legacy regression chain; not the competition V0 mainline.")
    parser.add_argument("--case", required=True)
    parser.add_argument("--legacy-root", default="runs/legacy_cases")
    parser.add_argument("--skip-model", action="store_true")
    args = parser.parse_args()
    started = time.time()
    legacy_root = Path(args.legacy_root).resolve()
    summary: dict[str, object] = {
        "case_input": str(Path(args.case).resolve()),
        "legacy_root": str(legacy_root),
        "execution_chain": "explicit_legacy_regression",
        "steps": {},
    }
    try:
        inputs = validate_case_inputs(args.case)
        summary.update(case_id=inputs.config["case_id"], scenario_id=inputs.config["scenario_id"])
        summary["steps"]["validate_inputs"] = "ok"
        adapted = adapt_case_to_legacy_case_directory(args.case, legacy_root)
        summary["steps"]["adapt_case_to_legacy"] = "ok"
        summary["legacy_case_dir"] = str(adapted.output_dir)
    except InputValidationError as exc:
        summary["steps"]["validate_inputs"] = "failed"
        summary["validation_errors"] = exc.errors
        _write_summary(legacy_root / "last_run_summary.json", summary)
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        summary["steps"]["adapt_case_to_legacy"] = "failed"
        summary["error"] = repr(exc)
        _write_summary(legacy_root / "last_run_summary.json", summary)
        print(f"[失败] legacy 适配失败：{exc}", file=sys.stderr)
        return 1

    case_id = str(summary["case_id"])
    scenario_id = str(summary["scenario_id"])
    original_cwd = Path.cwd()
    legacy_root.mkdir(parents=True, exist_ok=True)
    config_target = legacy_root / "_config.yaml"
    if not config_target.exists():
        shutil.copy2(Path(__file__).resolve().parents[2] / "configs/legacy/_config.yaml", config_target)
    try:
        from legacy.upstream import clustering
        os.chdir(legacy_root)
        clustering.perform_complete_clustering(case_id, scenario_id)
        summary["steps"]["clustering"] = "ok"
        if not args.skip_model:
            from legacy.upstream import model
            model.run_model(case_id, scenario_id)
            summary["steps"]["model_run"] = "ok"
        else:
            summary["steps"]["model_run"] = "skipped"
    except Exception as exc:
        summary["steps"]["legacy_execution"] = "failed"
        summary["error"] = repr(exc)
        _write_summary(legacy_root / case_id / "run_summary.json", summary)
        print(f"[失败] legacy 执行失败：{exc}", file=sys.stderr)
        return 1
    finally:
        os.chdir(original_cwd)

    if not args.skip_model:
        exported = export_standard_results(args.case, legacy_root / case_id)
        summary["steps"]["standard_result_export"] = "ok"
        summary["standard_results_dir"] = str(exported.output_dir)
    else:
        summary["steps"]["standard_result_export"] = "skipped"
    summary["elapsed_seconds"] = round(time.time() - started, 3)
    _write_summary(legacy_root / case_id / "run_summary.json", summary)
    print("[通过] legacy 回归完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
