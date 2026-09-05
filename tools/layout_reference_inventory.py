"""Read-only migration referencer inventory; runtime output is not source input."""
import ast
from hashlib import sha256
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def collect():
    inventory = json.loads((ROOT / "baselines/layout_migration.json").read_text(encoding="utf-8"))
    code = {}
    for folder in ("src", "tests", "tools", "legacy/upstream"):
        for path in (ROOT / folder).rglob("*.py"):
            imports = set()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
                if isinstance(node, ast.Import):
                    imports.update(item.name for item in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module)
                    imports.update(f"{node.module}.{item.name}" for item in node.names)
            code[path.relative_to(ROOT).as_posix()] = (imports, path.read_text(encoding="utf-8-sig"))
    for row in inventory["rows"]:
        path = ROOT / row["new_path"]
        module = row["new_path"].removeprefix("src/").removesuffix(".py").replace("/", ".").removesuffix(".__init__")
        refs = [name for name, (imports, text) in code.items()
                if name != row["new_path"] and (module in imports or row["new_path"] in text)]
        row["current_referencers_static"] = sorted(refs)
        row["reference_limit"] = "静态import/完整路径检索；动态加载与外部脚本需单独确认，无引用不等于可删除"
        row["sha256_after"] = sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    inventory["current_git_sha"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    return inventory


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.resolve()
    if not output.is_relative_to(ROOT):
        parser.error("output must remain in repository")
    with output.open("x", encoding="utf-8") as stream:
        json.dump(collect(), stream, ensure_ascii=False, indent=2)
