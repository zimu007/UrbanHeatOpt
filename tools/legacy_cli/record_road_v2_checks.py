"""Record fresh environment/tests, timings and input integrity without solving real cases."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import json
from uuid import uuid4
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from urbanheatopt.data.intake.guanggu_v03 import resolve_guanggu_v03_source_roots
from urbanheatopt.parameters.legacy_economics import file_hash


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delivery-root',required=True)
    parser.add_argument('--output-root',required=True)
    parser.add_argument('--osm-snapshot',help='可选：额外实跑道路V2全季输入准备门禁，不启动求解')
    args=parser.parse_args()
    root=Path(args.output_root).resolve()
    root.mkdir(parents=True,exist_ok=False)
    repo=Path(__file__).resolve().parents[2]
    roots=resolve_guanggu_v03_source_roots(args.delivery_root)
    inputs=[p for p in roots.delivery_root.rglob('*') if p.is_file()]
    if roots.equipment_patch_root:
        inputs += [p for p in roots.equipment_patch_root.rglob('*') if p.is_file()]
    before={str(p):file_hash(p) for p in sorted(inputs)}
    (root/'source_hashes_before.json').write_text(json.dumps(before,ensure_ascii=False,indent=2),encoding='utf-8')
    records=[]
    # Keep test-generated nested paths below Windows MAX_PATH without changing
    # machine settings or deleting any previous pytest evidence directory.
    pytest_root=repo/'runs'/('q_'+uuid4().hex[:8])
    commands=[('environment',[sys.executable,'tools/check_environment.py'],[0]),
              ('tests',[sys.executable,'-m','pytest','-q','--basetemp',str(pytest_root)],[0])]
    if args.osm_snapshot:
        commands.append(('v2_input_gate',[sys.executable,'tools/legacy_cli/run_case.py',
            '--delivery-root',args.delivery_root,'--source-profile','guanggu_v03',
            '--assumption-profile','provisional_v0','--core-version','road_joint_v2',
            '--profile','v0-full-season','--osm-snapshot',str(Path(args.osm_snapshot).resolve()),
            '--output-root',str(root),'--run-id','real_input_gate','--prepare-only'],[0,2]))
    for name,command,expected in commands:
        start=datetime.now(timezone.utc).isoformat(); clock=perf_counter()
        with (root/f'{name}.log').open('w',encoding='utf-8') as stream:
            completed=subprocess.run(command,cwd=repo,stdout=stream,stderr=subprocess.STDOUT)
        record=dict(name=name,command=command,cwd=str(repo),started=start,
                    finished=datetime.now(timezone.utc).isoformat(),seconds=perf_counter()-clock,exit_code=completed.returncode,
                    accepted_exit_codes=expected)
        records.append(record)
        print(json.dumps(record,ensure_ascii=False),flush=True)
        if completed.returncode not in expected:
            break
    after={str(p):file_hash(p) for p in sorted(inputs)}
    evidence=dict(git_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
        git_status=subprocess.check_output(['git','status','--short'],cwd=repo,text=True).strip(),
        core_sha256=file_hash(repo/'src/urbanheatopt/model/reference_core.py'),commands=records,
        pytest_artifacts=str(pytest_root),
        source_file_count=len(before),source_hashes_unchanged=before==after,real_solver_executed=False)
    (root/'verification_evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if before==after and all(x['exit_code'] in x['accepted_exit_codes'] for x in records) else 1


if __name__=='__main__':
    raise SystemExit(main())
