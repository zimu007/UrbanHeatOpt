"""Run ONE unchanged full-size task with a memory safety monitor, no new deps."""
import argparse
import ctypes
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from urbanheatopt.optimization.full_season_server import _available_memory_bytes
from urbanheatopt.optimization.reference_tasks import verify_plan, write_json


def process_memory(pid):
    if sys.platform == 'win32':
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb',ctypes.c_ulong),('faults',ctypes.c_ulong)]+[(n,ctypes.c_size_t) for n in
                ('peak_rss','rss','peak_pool','pool','peak_nonpool','nonpool','pagefile','peak_pagefile','private')]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
        if not handle:
            return None
        try:
            counters= Counters(); counters.cb=ctypes.sizeof(counters)
            if not psapi.GetProcessMemoryInfo(handle,ctypes.byref(counters),counters.cb):
                return None
            return dict(rss_bytes=int(counters.rss),private_bytes=int(counters.private),peak_rss_bytes=int(counters.peak_rss))
        finally:
            kernel.CloseHandle(handle)
    path=Path(f'/proc/{pid}/status')
    if not path.exists():
        return None
    fields={line.split(':')[0]:line.split(':')[1].strip() for line in path.read_text().splitlines() if ':' in line}
    rss=int(fields.get('VmRSS','0 kB').split()[0])*1024
    return dict(rss_bytes=rss,private_bytes=rss,peak_rss_bytes=int(fields.get('VmHWM','0 kB').split()[0])*1024)


def memory_budget(available, requested_gib=None):
    if available is None or available <= 0:
        raise ValueError('无法确定可用内存；停止，不盲目启动全季任务')
    if requested_gib is not None and (not math.isfinite(requested_gib) or requested_gib <= 0):
        raise ValueError('内存预算必须有限且大于0')
    return int(min(available*.75, requested_gib*1024**3 if requested_gib is not None else available*.75))


def run_guarded(root, task_id, requested_gib=None, max_wall_seconds=None):
    root=Path(root).resolve()
    plan=verify_plan(root)
    if task_id not in {t['task_id'] for t in plan['tasks']}:
        raise ValueError('未知任务')
    available=_available_memory_bytes()
    limit=memory_budget(available,requested_gib)
    wall=max_wall_seconds if max_wall_seconds is not None else plan['time_limit_seconds']+1800
    if not math.isfinite(wall) or wall <= 0:
        raise ValueError('监控最长时间必须有限且大于0')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out=root/'resource_monitor'/task_id/stamp
    out.mkdir(parents=True,exist_ok=False)
    command=[sys.executable, str(Path(__file__).with_name('run_road_v2_task.py')), '--run-root',str(root),'--task-id',task_id]
    request=dict(command=command,cwd=str(Path(__file__).resolve().parents[2]),started_at=datetime.now(timezone.utc).isoformat(),
        memory_limit_bytes=limit,available_memory_bytes_at_start=available,wall_limit_seconds=wall,
        note='仅安全终止本监控启动的子进程；不更改小时、约束、gap或求解器时间设置')
    write_json(out/'request.json',request)
    print(json.dumps(request,ensure_ascii=False),flush=True)
    start=monotonic(); reason=None; peak=0; samples=0
    with (out/'command.log').open('x',encoding='utf-8') as log, (out/'memory.jsonl').open('x',encoding='utf-8') as records:
        child=subprocess.Popen(command,cwd=request['cwd'],stdout=log,stderr=subprocess.STDOUT)
        while child.poll() is None:
            memory=process_memory(child.pid)
            free=_available_memory_bytes()
            if memory:
                used=max(memory['rss_bytes'],memory['private_bytes'])
                peak=max(peak,used); samples+=1
                records.write(json.dumps(dict(seconds=monotonic()-start,pid=child.pid,available_bytes=free,**memory))+'\n')
                records.flush()
                if used>limit:
                    reason='memory_budget_exceeded'
                elif free is not None and free<available*.25:
                    reason='system_memory_reserve_reached'
            elif monotonic()-start>15:
                reason='memory_monitor_unavailable'
            if monotonic()-start>wall:
                reason='wall_time_limit_exceeded'
            if reason:
                child.terminate()
                child.wait(timeout=30)
                break
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
    task_root=root/'tasks'/task_id
    if reason and (task_root/'worker_reservation.json').exists():
        reservation=json.loads((task_root/'worker_reservation.json').read_text(encoding='utf-8'))
        if reservation['pid']==child.pid:
            attempt=task_root/reservation['attempt']
            if not (attempt/'failure.json').exists():
                write_json(attempt/'failure.json',dict(error=reason,error_type='ResourceSafetyStop',solver_success=False,
                    model_build_completed=(attempt/'model_build_completed.json').exists(),
                    monitor_directory=str(out),peak_process_memory_bytes=peak))
    input_check=True
    try:
        verify_plan(root)
    except (OSError,ValueError):
        input_check=False
    summary=dict(finished_at=datetime.now(timezone.utc).isoformat(),seconds=monotonic()-start,
        child_exit_code=child.returncode,safety_stop_reason=reason,peak_process_memory_bytes=peak,
        samples=samples,source_and_model_hashes_unchanged=input_check,task_success=(task_root/'success.json').exists())
    write_json(out/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    return 0 if child.returncode==0 and not reason and input_check and summary['task_success'] else 2


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root',required=True)
    parser.add_argument('--task-id',default='central-cost')
    parser.add_argument('--memory-budget-gib',type=float)
    parser.add_argument('--max-wall-seconds',type=float)
    args=parser.parse_args()
    return run_guarded(args.run_root,args.task_id,args.memory_budget_gib,args.max_wall_seconds)


if __name__=='__main__':
    raise SystemExit(main())
