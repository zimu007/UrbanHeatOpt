"""Build, but never solve, Guanggu v0.3 models to estimate full-season scale."""

from __future__ import annotations

import argparse
import ctypes
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from urbanheatopt.data.adapters.guanggu_v03 import adapt_guanggu_v03_sources
from urbanheatopt.data.adapters.guanggu_v03_case import prepare_guanggu_v03_v0_case
from urbanheatopt.model.reference_core import build_core_model


def _working_set_bytes() -> int | None:
    if sys.platform != "win32":
        return None

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    process = ctypes.windll.kernel32.GetCurrentProcess()
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(
        process, ctypes.byref(counters), counters.cb
    )
    if ok:
        return int(counters.WorkingSetSize)
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"(Get-Process -Id {os.getpid()}).WorkingSet64",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        return int(completed.stdout.strip()) if completed.returncode == 0 else None
    except ValueError:
        return None


def _system_memory() -> dict[str, int] | None:
    if sys.platform != "win32":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return {
        "total_physical_bytes": int(status.ullTotalPhys),
        "available_physical_bytes": int(status.ullAvailPhys),
        "memory_load_percent": int(status.dwMemoryLoad),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery-root", required=True, type=Path)
    parser.add_argument(
        "--profile",
        choices=("v0-smoke", "v0-168h", "v0-full-season"),
        default="v0-full-season",
    )
    parser.add_argument(
        "--mode",
        action="append",
        choices=("central", "distributed", "hybrid"),
        help="只构建指定模式；可重复。默认构建三种模式。",
    )
    args = parser.parse_args()
    rows: list[dict[str, object]] = []
    with TemporaryDirectory(prefix="urbanheatopt-size-estimate-") as temporary:
        root = Path(temporary)
        adaptation = adapt_guanggu_v03_sources(
            args.delivery_root, root / "validation", full_audit=True
        )
        prepared = prepare_guanggu_v03_v0_case(
            adaptation, root / "prepared", profile=args.profile
        )
        for mode in (tuple(args.mode) if args.mode else prepared.canonical_case.modes):
            before = _working_set_bytes()
            started = perf_counter()
            model = build_core_model(prepared.canonical_case.to_core_input(mode))
            after = _working_set_bytes()
            rows.append(
                {
                    "mode": mode,
                    "hours": len(model.HOURS),
                    "demand_nodes": len(model.DEMAND_NODES),
                    "candidate_stations": len(model.STATIONS),
                    "candidate_segments": len(model.SEGMENTS),
                    "variables": model.nvariables(),
                    "constraints": model.nconstraints(),
                    "objectives": model.nobjectives(),
                    "build_elapsed_seconds": perf_counter() - started,
                    "working_set_before_bytes": before,
                    "working_set_after_bytes": after,
                    "working_set_increment_bytes": (
                        after - before if before is not None and after is not None else None
                    ),
                }
            )
            del model
            gc.collect()
    print(
        json.dumps(
            {
                "solver_instantiated": False,
                "profile": args.profile,
                "system_memory": _system_memory(),
                "models": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
