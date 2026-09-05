"""Publish versioned Markdown copies; never lose an existing desktop edition."""
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import argparse
import json
import shutil

ROOT = Path(__file__).resolve().parents[1]
NAMES = (
    "UrbanHeatOpt_总体方向与三人分工.md",
    "UrbanHeatOpt_A_主线参数与集成任务书.md",
    "UrbanHeatOpt_B_模型诊断站址与储热任务书.md",
    "UrbanHeatOpt_C_独立QA与成果展示任务书.md",
)


def publish(destination: Path) -> list[dict]:
    destination = destination.resolve(strict=True)
    records = []
    for name in NAMES:
        source = ROOT / "docs" / "team" / name
        content = source.read_bytes()
        target = destination / name
        backup = None
        if target.exists() and target.read_bytes() != content:
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
            backup = target.with_name(f"{target.stem}.backup_{stamp}.md")
            with backup.open("xb") as stream:
                stream.write(target.read_bytes())
        shutil.copyfile(source, target)
        assert target.read_bytes() == content
        records.append({"source": str(source), "desktop": str(target),
                        "sha256": sha256(content).hexdigest(),
                        "backup": str(backup) if backup else None})
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desktop", type=Path, default=Path.home() / "Desktop")
    print(json.dumps(publish(parser.parse_args().desktop), ensure_ascii=False, indent=2))
