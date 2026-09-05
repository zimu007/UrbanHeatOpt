"""Run one non-interactive command and persist an auditable log plus metadata."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
from time import perf_counter


def main() -> int:
    parser = argparse.ArgumentParser(
        description="执行命令并保存原始合并日志、起止时间、耗时和退出码。"
    )
    parser.add_argument("--log-dir", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--cwd", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        parser.error("必须在 -- 后提供待执行命令")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", args.label) is None:
        parser.error("label 只能包含字母、数字、点、下划线和连字符")

    cwd = args.cwd.resolve()
    if not cwd.is_dir():
        parser.error(f"工作目录不存在：{cwd}")
    log_dir = args.log_dir.resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{args.label}.log"
    metadata_path = log_dir / f"{args.label}.json"
    if log_path.exists() or metadata_path.exists():
        parser.error(f"证据文件已存在，拒绝覆盖：{args.label}")

    started = datetime.now(timezone.utc)
    clock = perf_counter()
    with log_path.open("x", encoding="utf-8", newline="") as stream:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            stream.write(line)
            stream.flush()
            print(line, end="")
        exit_code = process.wait()
    elapsed = perf_counter() - clock
    finished = datetime.now(timezone.utc)
    metadata = {
        "label": args.label,
        "command": command,
        "cwd": str(cwd),
        "started_at_utc": started.isoformat(),
        "finished_at_utc": finished.isoformat(),
        "elapsed_seconds": elapsed,
        "exit_code": exit_code,
        "log_file": str(log_path),
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
