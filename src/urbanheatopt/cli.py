"""A-owned entry shell. Model and reporting capabilities are explicit gates."""
import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(description="UrbanHeatOpt V2 输入与集成入口；V1仅显式回放")
    parser.add_argument("command", choices=("validate", "prepare", "solve", "report", "diagnose", "tes-check"))
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    print(f"[未就绪] {args.command}: 目录迁移节点只提供入口；参数接口及B/C功能尚未交接，未执行求解。")
    return 2
