"""Run a non-destructive check and preserve a fresh command/log record."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("missing command")
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    before = perf_counter()
    with (out / "command.log").open("w", encoding="utf-8") as log:
        environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=environment)
    payload = {"command": command, "cwd": str(ROOT), "started_at": started,
               "finished_at": datetime.now(timezone.utc).isoformat(),
               "elapsed_seconds": perf_counter() - before, "exit_code": result.returncode,
               "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
    (out / "evidence.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    print((out / "command.log").read_text(encoding="utf-8", errors="replace")[-7000:])
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
