"""Mechanical correction of case resource names, which are not repo paths."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in ("src", "tests", "tools/legacy_cli"):
    for path in (ROOT / folder).rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        fixed = text.replace("caseconfigs/legacy/_config.yaml", "case_config.yaml")
        fixed = fixed.replace("legacy/upstream/README_original.md", "README.md")
        fixed = fixed.replace("import scripts.", "import tools.legacy_cli.")
        if fixed != text:
            path.write_text(fixed, encoding="utf-8", newline="\n")
