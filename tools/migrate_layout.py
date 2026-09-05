"""One-time, manifest-first mechanical migration; refuses conflicting targets.

This tool moves only git-tracked files. Original data, runs, submodules and
untracked files are never moved. Formula bodies are checked separately.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "aebdeada611a1c4048aa28ce3bcabeb8015a4913"
MODULES = {
    "competition.road_joint_v2.compact_tasks": "urbanheatopt.optimization.compact_tasks",
    "competition.road_joint_v2.economic_package": "urbanheatopt.parameters.legacy_economics",
    "competition.road_joint_v2.planning_network": "urbanheatopt.spatial.planning_network",
    "competition.road_joint_v2.compact": "urbanheatopt.model.compact",
    "competition.road_joint_v2.builder": "urbanheatopt.data.road_builder",
    "competition.road_joint_v2.core": "urbanheatopt.model.road_core",
    "competition.road_joint_v2.results": "urbanheatopt.qa.road_results",
    "competition.road_joint_v2.network": "urbanheatopt.spatial.atomic_network",
    "competition.road_joint_v2.pipeline": "urbanheatopt.data.reference_road_pipeline",
    "competition.road_joint_v2.tasks": "urbanheatopt.optimization.reference_tasks",
    "competition.road_joint_v2.budget": "urbanheatopt.optimization.reference_budget",
    "competition.road_joint_v2.figures": "urbanheatopt.reporting.reference_figures",
    "competition.road_joint_v2": "urbanheatopt.model.road_version",
    "competition.core_model": "urbanheatopt.model.reference_core",
    "competition.canonical": "urbanheatopt.data.canonical",
    "competition.physical_interfaces": "urbanheatopt.model.physical_interfaces",
    "competition.full_season_server": "urbanheatopt.optimization.full_season_server",
    "competition.provisional_spatial": "urbanheatopt.spatial.provisional",
    "competition.osm_corridor": "urbanheatopt.spatial.osm_corridor",
    "competition.economics": "urbanheatopt.parameters.energy_units",
    "competition.solvers": "urbanheatopt.optimization.solvers",
    "competition.pareto": "urbanheatopt.optimization.pareto",
    "competition.readiness": "urbanheatopt.data.readiness",
    "competition.release_gate": "urbanheatopt.qa.release_gate",
    "competition.adapters": "urbanheatopt.data.adapters",
    "competition.intake": "urbanheatopt.data.intake",
    "competition.validation": "urbanheatopt.data.validation",
    "competition.costing": "urbanheatopt.model.costing",
    "competition.pipelines": "urbanheatopt.optimization.pipelines",
    "competition.results": "urbanheatopt.reporting.results",
    "competition.schemas": "urbanheatopt.data.schemas",
    "competition.configs": "urbanheatopt.data.profile_resources",
    "competition.reports": "urbanheatopt.reporting",
}
UPSTREAM = {"clustering.py", "data.py", "model.py", "utils.py", "prepare_geodata.py",
            "visualisation.py", "hd_time_series_generator.py", "main.ipynb"}
MAIN_TOOLS = {"check_environment.py", "check_core_model_frozen.py", "verify_source_hashes.py"}


def replace_modules(text: str) -> str:
    for old, new in sorted(MODULES.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(re.escape(old) + r"(?=[.\s\"'(),:]|$)", new, text)
    return text


def destination(path: str) -> str:
    if path == "competition/__init__.py":
        return "src/urbanheatopt/__init__.py"
    if path.startswith("competition/"):
        suffix = Path(path).suffix
        if suffix == ".py":
            module = path[:-3].replace("/", ".")
            initializer = module.endswith(".__init__")
            if initializer:
                module = module[:-9]
            mapped = replace_modules(module)
            if module == "competition.road_joint_v2":
                initializer = False
            return "src/" + mapped.replace(".", "/") + ("/__init__.py" if initializer else ".py")
        if path.startswith("competition/configs/"):
            return path.replace("competition/configs/", "src/urbanheatopt/data/profile_resources/", 1)
        if path.startswith("competition/schemas/"):
            return path.replace("competition/schemas/", "src/urbanheatopt/data/schemas/", 1)
        raise ValueError(path)
    if path in UPSTREAM:
        return "legacy/upstream/" + path
    if path == "_config.yaml":
        return "configs/legacy/_config.yaml"
    if path in {"PROJECT_REQUIREMENTS_CN.md", "CODE_TEAM_ROADMAP.md"}:
        return "docs/decisions/history/" + path
    if path == "README.md":
        return "legacy/upstream/README_original.md"
    if path.startswith("scripts/"):
        name = path.removeprefix("scripts/")
        return ("tools/" if name in MAIN_TOOLS else "tools/legacy_cli/") + name
    if path.startswith("docs_src/"):
        return "legacy/documentation/source/" + path.removeprefix("docs_src/")
    if path.startswith("docs/"):
        if path == "docs/CHANGELOG_COMPETITION.md":
            return path
        return "legacy/documentation/prior/" + path.removeprefix("docs/")
    return path


def safe_path(relative: str) -> Path:
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT) or path == ROOT:
        raise ValueError(f"outside workspace: {path}")
    return path


def main() -> None:
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    rows = []
    for relative in filter(None, tracked):
        source = safe_path(relative)
        if not source.is_file():
            continue  # includes the unmodified Conda submodule
        target = destination(relative)
        if target != relative and safe_path(target).exists():
            raise FileExistsError(target)
        contents = source.read_bytes()
        rows.append({"old_path": relative, "new_path": target,
                     "sha256_before": sha256(contents).hexdigest(),
                     "classification": "历史参考" if target.startswith("legacy/") else "运行依赖或项目资产",
                     "reason": "保留旧回归/来源，不进入默认主线" if target.startswith("legacy/") else "单一源码入口与显式资源定位",
                     "include_in_competition_package": not target.startswith("legacy/"),
                     "verification": "全量回归及核心函数AST比对"})
    manifest_path = safe_path("baselines/layout_migration.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("x", encoding="utf-8") as out:
        json.dump({"baseline_git_sha": BASELINE, "created_at": datetime.now(timezone.utc).isoformat(),
                   "rows": rows, "protected_directories": ["runs", "default", "Fehring", "Conda-Activation-Scripts", "../IN_DATA", "../OUT_RESULT"],
                   "unrelated_generated_exclusions": ["__pycache__", ".pytest_cache", ".ipynb_checkpoints"]}, out, ensure_ascii=False, indent=2)
    path_map = {row["old_path"]: row["new_path"] for row in rows}
    for row in rows:
        if row["old_path"] != row["new_path"]:
            source, target = safe_path(row["old_path"]), safe_path(row["new_path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)  # all exact targets preflighted above; no overwrite
    for row in rows:
        target = safe_path(row["new_path"])
        if target.suffix != ".py":
            continue
        text = target.read_text(encoding="utf-8-sig")
        text = replace_modules(text)
        text = text.replace("from scripts.", "from tools.legacy_cli.")
        text = text.replace('"scripts.', '"tools.legacy_cli.').replace("'scripts.", "'tools.legacy_cli.")
        for old, new in sorted(path_map.items(), key=lambda kv: -len(kv[0])):
            if old != new and old not in {"_config.yaml", "README.md"}:
                text = text.replace(old, new)
        # Source resources stay package-local; repository paths use one resolver.
        if row["new_path"].startswith("src/"):
            text = re.sub(r'Path\(__file__\)\.resolve\(\)\.parents\[\d+\] / "configs"', 'PACKAGE_ROOT / "data" / "profile_resources"', text)
            text = text.replace('Path(__file__).resolve().parent / "configs"', 'PACKAGE_ROOT / "data" / "profile_resources"')
            text = re.sub(r'Path\(__file__\)\.resolve\(\)\.parents\[\d+\]', 'REPOSITORY_ROOT_PATH', text)
            text = text.replace('/ "competition" / "schemas"', '/ "src" / "urbanheatopt" / "data" / "schemas"')
            text = text.replace("REPOSITORY/'competition'", "REPOSITORY/'src/urbanheatopt'")
            text = text.replace('REPOSITORY / "competition"', 'REPOSITORY / "src/urbanheatopt"')
            if "REPOSITORY_ROOT_PATH" in text or "PACKAGE_ROOT" in text:
                tree = ast.parse(text)
                anchor = 0
                for node in tree.body:
                    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) or isinstance(node, ast.ImportFrom) and node.module == "__future__":
                        anchor = node.end_lineno
                    else:
                        break
                lines = text.splitlines(keepends=True)
                lines.insert(anchor, "\nfrom urbanheatopt.paths import REPOSITORY_ROOT as REPOSITORY_ROOT_PATH, PACKAGE_ROOT\n")
                text = "".join(lines)
        if row["new_path"].startswith("tools/legacy_cli/"):
            text = re.sub(r'Path\(__file__\)\.resolve\(\)\.parents\[1\]', 'Path(__file__).resolve().parents[2]', text)
        # Existing direct command shims need only the source path, never old modules.
        text = re.sub(r'sys\.path\.insert\(0,\s*str\((Path\(__file__\)\.resolve\(\)\.parents\[\d+\])\)\)', r'sys.path.insert(0, str(\1 / "src"))', text)
        text = text.replace('sys.path.insert(0, str(REPOSITORY))', 'sys.path.insert(0, str(REPOSITORY / "src"))')
        if row["old_path"] in UPSTREAM:
            text = re.sub(r'^import (data|utils)\s*$', r'from legacy.upstream import \1', text, flags=re.M)
            text = re.sub(r'^from data import ', 'from legacy.upstream.data import ', text, flags=re.M)
        else:
            text = text.replace('import model as model_module', 'from legacy.upstream import model as model_module')
            text = text.replace('from model import ', 'from legacy.upstream.model import ')
            text = text.replace('from clustering import ', 'from legacy.upstream.clustering import ')
            text = re.sub(r'^(\s*)import clustering\s*$', r'\1from legacy.upstream import clustering', text, flags=re.M)
            text = re.sub(r'^(\s*)import model\s*$', r'\1from legacy.upstream import model', text, flags=re.M)
        text = text.replace('/ "competition" / "schemas"', '/ "src" / "urbanheatopt" / "data" / "schemas"')
        text = text.replace('/ "scripts" / "check_environment.py"', '/ "tools" / "check_environment.py"')
        text = text.replace('/ "scripts" /', '/ "tools" / "legacy_cli" /')
        if row["old_path"] == "scripts/run_legacy_case.py":
            text = text.replace('original_cwd / "_config.yaml"', 'Path(__file__).resolve().parents[2] / "configs/legacy/_config.yaml"')
        if row["old_path"] == "tests/test_solver_interface.py":
            text = text.replace('PROJECT_ROOT / "_config.yaml"', 'PROJECT_ROOT / "configs/legacy/_config.yaml"')
        target.write_text(text, encoding="utf-8", newline="\n")
    print(f"Manifest: {manifest_path}; moved {sum(r['old_path'] != r['new_path'] for r in rows)} tracked files")


if __name__ == "__main__":
    main()
