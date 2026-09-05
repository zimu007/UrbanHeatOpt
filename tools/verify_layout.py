"""Verify migrated mathematical functions against the frozen Git objects."""
from __future__ import annotations

import ast
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.migrate_layout import BASELINE, MODULES, replace_modules

TARGETS = {
    "competition/core_model.py": "src/urbanheatopt/model/reference_core.py",
    "competition/road_joint_v2/core.py": "src/urbanheatopt/model/road_core.py",
    "competition/road_joint_v2/compact.py": "src/urbanheatopt/model/compact.py",
    "competition/costing/annualized.py": "src/urbanheatopt/model/costing/annualized.py",
    "competition/physical_interfaces.py": "src/urbanheatopt/model/physical_interfaces.py",
}


class Normalize(ast.NodeTransformer):
    def visit_Import(self, node):
        return None

    def visit_ImportFrom(self, node):
        return None

    def visit_Expr(self, node):
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return None
        return self.generic_visit(node)


def bodies(text):
    tree = Normalize().visit(ast.parse(replace_modules(text)))
    return {node.name: ast.dump(node, include_attributes=False) for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def verify():
    evidence = []
    for old, new in TARGETS.items():
        original = subprocess.check_output(["git", "show", f"{BASELINE}:{old}"], cwd=ROOT).decode("utf-8-sig")
        current = (ROOT / new).read_text(encoding="utf-8-sig")
        left, right = bodies(original), bodies(current)
        changed = [name for name in left.keys() | right.keys() if left.get(name) != right.get(name)]
        evidence.append({"old_path": old, "new_path": new, "verified_body_count": len(left),
                         "changed_bodies": changed, "sha256_before": sha256(original.encode()).hexdigest(),
                         "sha256_after": sha256((ROOT / new).read_bytes()).hexdigest()})
    return {"baseline_git_sha": BASELINE, "passed": all(not r["changed_bodies"] for r in evidence), "checks": evidence}


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 2)
