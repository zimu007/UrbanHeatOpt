from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition.adapters.legacy_case import adapt_case_to_legacy_case_directory
from competition.results.standard import export_standard_results
from competition.validation.inputs import InputValidationError, validate_case_inputs


def _write_summary(path: Path, summary: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a P0 competition case through the legacy UrbanHeatOpt pipeline.")
    parser.add_argument("--case", required=True, help="Competition case input directory.")
    parser.add_argument(
        "--legacy-root",
        default="runs/legacy_cases",
        help="Directory where legacy UrbanHeatOpt case files are generated.",
    )
    parser.add_argument("--skip-model", action="store_true", help="Stop after validation, adaptation, and clustering.")
    args = parser.parse_args()

    started = time.time()
    summary: dict[str, object] = {
        "case_input": str(Path(args.case).resolve()),
        "legacy_root": str(Path(args.legacy_root).resolve()),
        "steps": {},
    }

    try:
        inputs = validate_case_inputs(args.case)
        summary["case_id"] = inputs.config["case_id"]
        summary["scenario_id"] = inputs.config["scenario_id"]
        summary["steps"]["validate_inputs"] = "ok"
    except InputValidationError as exc:
        summary["steps"]["validate_inputs"] = "failed"
        summary["validation_errors"] = exc.errors
        _write_summary(Path(args.legacy_root) / "last_run_summary.json", summary)
        print("[失败] 输入校验未通过：", file=sys.stderr)
        for error in exc.errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    try:
        result = adapt_case_to_legacy_case_directory(args.case, args.legacy_root)
        summary["legacy_case_dir"] = str(result.output_dir)
        summary["steps"]["adapt_case_to_legacy"] = "ok"
    except Exception as exc:
        summary["steps"]["adapt_case_to_legacy"] = "failed"
        summary["error"] = repr(exc)
        _write_summary(Path(args.legacy_root) / "last_run_summary.json", summary)
        print(f"[失败] 格式适配失败：{exc}", file=sys.stderr)
        return 1

    case_id = str(summary["case_id"])
    scenario_id = str(summary["scenario_id"])
    legacy_root = Path(args.legacy_root).resolve()
    original_cwd = Path.cwd()
    legacy_root.mkdir(parents=True, exist_ok=True)
    config_target = legacy_root / "_config.yaml"
    if not config_target.exists():
        shutil.copy2(original_cwd / "_config.yaml", config_target)

    try:
        import clustering

        # Legacy code resolves case paths relative to the current process CWD.
        # Running from legacy_root keeps generated case directories isolated.
        import os

        os.chdir(legacy_root)
        clustering.perform_complete_clustering(case_id, scenario_id)
        summary["steps"]["clustering"] = "ok"
    except Exception as exc:
        summary["steps"]["clustering"] = "failed"
        summary["error"] = repr(exc)
        _write_summary(legacy_root / case_id / "run_summary.json", summary)
        print(f"[失败] 聚类/模型输入生成失败：{exc}", file=sys.stderr)
        return 1
    finally:
        import os

        os.chdir(original_cwd)

    if not args.skip_model:
        try:
            import model
            import os

            os.chdir(legacy_root)
            model.run_model(case_id, scenario_id)
            summary["steps"]["model_run"] = "ok"
        except Exception as exc:
            summary["steps"]["model_run"] = "failed"
            summary["error"] = repr(exc)
            _write_summary(legacy_root / case_id / "run_summary.json", summary)
            print(f"[失败] 优化求解失败：{exc}", file=sys.stderr)
            return 1
        finally:
            import os

            os.chdir(original_cwd)
    else:
        summary["steps"]["model_run"] = "skipped"

    if not args.skip_model:
        try:
            standard_results = export_standard_results(args.case, legacy_root / case_id)
            summary["steps"]["standard_result_export"] = "ok"
            summary["standard_results_dir"] = str(standard_results.output_dir)
        except Exception as exc:
            summary["steps"]["standard_result_export"] = "failed"
            summary["error"] = repr(exc)
            _write_summary(legacy_root / case_id / "run_summary.json", summary)
            print(f"[失败] 标准结果整理失败：{exc}", file=sys.stderr)
            return 1
    else:
        summary["steps"]["standard_result_export"] = "skipped"

    summary["elapsed_seconds"] = round(time.time() - started, 3)
    _write_summary(legacy_root / case_id / "run_summary.json", summary)
    print("[通过] run_case 完成")
    print(legacy_root / case_id / "run_summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
