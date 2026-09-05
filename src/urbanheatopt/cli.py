"""A-owned entry shell. Model and reporting capabilities are explicit gates."""
import argparse
import json
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="UrbanHeatOpt V2 输入与集成入口；V1仅显式回放")
    parser.add_argument("command", choices=("validate", "prepare", "solve", "report", "diagnose", "tes-check"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args(argv)
    try:
        from urbanheatopt.data.integration import load_config, run_input_pipeline
        if args.command in {"validate", "prepare"}:
            result = run_input_pipeline(args.command, args.config, run_id=args.run_id, output_root=args.output_root)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return result["exit_code"]
        load_config(args.config)
        if args.bundle:
            from urbanheatopt.data.bundles import CaseBundle, ready_report
            result = ready_report(CaseBundle.read(args.bundle))
        else:
            result = {"model_ready": False, "solver_executed": False,
                      "reason": "B/C消费适配器尚未交接；请先validate/prepare。没有旧模型回退。"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    except (ValueError, OSError) as exc:
        print(f"[输入/接口错误] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[程序异常] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
