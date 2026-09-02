"""Fresh, hash-locked V2 tasks. Never reuses old-core MPS or success flags."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import gc
import json
from pathlib import Path
import platform
import subprocess
from math import isfinite

from competition.pareto import ParetoPoint, ParetoSpec, solve_pareto_task, assemble_pareto_run, select_representative_points
from competition.solvers import SolverSettings, get_solver_evidence
from competition.road_joint_v2 import FROZEN_LEGACY_CORE_SHA256
from competition.road_joint_v2.economic_package import file_hash
from competition.road_joint_v2.builder import save_case, load_case
from competition.road_joint_v2.core import build_road_model, classify_direction_solution
from competition.road_joint_v2.budget import (
    apply_budget_network_design,
    build_budget_network_design,
    initialize_budget_hybrid_mip_start,
)
from competition.road_joint_v2.results import export_solution

REPOSITORY=Path(__file__).resolve().parents[2]
COMPLETION_INTEGRITY_SCHEMA='road_joint_v2_completion_integrity_1'
SCAN_INTEGRITY_SCHEMA='road_joint_v2_scan_integrity_audit_1'
FINAL_INTEGRITY_SCHEMA='road_joint_v2_final_integrity_audit_1'


def write_json(path, data):
    with Path(path).open('x',encoding='utf-8') as stream:
        json.dump(data,stream,ensure_ascii=False,indent=2,allow_nan=False)


def mathematics_hashes():
    result={str(p.relative_to(REPOSITORY)).replace('\\','/'):file_hash(p)
            for p in sorted((REPOSITORY/'competition').rglob('*.py'))}
    if result['competition/core_model.py']!=FROZEN_LEGACY_CORE_SHA256:
        raise ValueError('旧核心冻结哈希被改变')
    return result


def create_plan(
    case,
    root,
    *,
    input_paths=None,
    point_count=11,
    full_scale=True,
    budget_metadata=None,
):
    root=Path(root)
    root.mkdir(parents=True,exist_ok=True)
    if full_scale and (len(case.common.demand_nodes)!=62 or len(case.common.hours)!=2160):
        raise ValueError('全季任务必须62栋×2160h')
    if point_count not in (5,11):
        raise ValueError('测试5点；全季11点')
    if full_scale and point_count!=11:
        raise ValueError('全季不得减少11个epsilon点')
    budget_profile=budget_metadata is not None
    if budget_profile and (full_scale or point_count!=5):
        raise ValueError('budget_50m profile requires full_scale=False and point_count=5')
    if budget_profile and len(case.common.hours)>168:
        raise ValueError('budget_50m profile accepts at most 168 representative hours')
    if (root/'task_plan.json').exists() or (root/'case.json').exists():
        raise FileExistsError('V2计划已存在，禁止覆盖')
    save_case(case,root/'case.json')
    tasks=[dict(task_id=f'{mode}-{obj}',mode=mode,objective=obj,phase='endpoint',epsilon_index=None)
           for mode in ('central','distributed','hybrid') for obj in ('cost','carbon')]
    tasks += [dict(task_id=f'{mode}-epsilon-{i:03d}',mode=mode,objective='cost',phase='epsilon',epsilon_index=i)
              for mode in ('central','distributed','hybrid') for i in range(point_count)]
    input_hashes={str(Path(p).resolve()):file_hash(Path(p)) for p in (input_paths or [])}
    budget_design=build_budget_network_design(case) if budget_profile else None
    plan=dict(schema='road_joint_v2_tasks_1',core_version='road_joint_v2',created_at=datetime.now(timezone.utc).isoformat(),
              git_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPOSITORY,text=True).strip(),
              python_version=platform.python_version(),case_sha256=file_hash(root/'case.json'),
              mathematics_sha256=mathematics_hashes(),source_sha256=input_hashes,tasks=tasks,
              point_count=point_count,full_scale=full_scale,solver_executed=False,
              scan_gap=.03 if budget_profile else .01,
              representative_gap=.03 if budget_profile else .001,
              threads=4 if budget_profile else (8 if full_scale else 1),
              time_limit_seconds=150 if budget_profile else (21600 if full_scale else 60),
              execution_profile='budget_50m_v1' if budget_profile else 'strict_v2',
              result_qualification=('budgeted_approximate_representative_horizon'
                                    if budget_profile else 'strict_model_horizon'),
              wallclock_budget_seconds=3000 if budget_profile else None,
              feasibility_tolerance=1e-7 if budget_profile else 1e-9,
              direction_relaxation=budget_profile,
              direction_lift_required=budget_profile,
              budget_metadata=dict(budget_metadata) if budget_profile else None,
              budget_network_design=budget_design,
              engineering_result=False)
    write_json(root/'task_plan.json',plan)
    return plan


def verify_plan(root):
    root=Path(root)
    plan=json.loads((root/'task_plan.json').read_text(encoding='utf-8'))
    if plan['schema']!='road_joint_v2_tasks_1' or plan['mathematics_sha256']!=mathematics_hashes() or plan['case_sha256']!=file_hash(root/'case.json'):
        raise ValueError('模型/案例哈希改变；必须生成全新V2任务，禁止复用旧成功标记')
    for name,digest in plan['source_sha256'].items():
        if file_hash(Path(name))!=digest:
            raise ValueError(f'原始输入哈希改变: {name}')
    return plan


def _relative_output(root, path):
    root=Path(root).resolve()
    resolved=Path(path).resolve()
    try:
        return str(resolved.relative_to(root)).replace('\\','/')
    except ValueError as exc:
        raise ValueError(f'任务证据路径越出运行目录: {resolved}') from exc


def _output_path(root, relative):
    if not isinstance(relative,str) or not relative:
        raise ValueError('任务证据路径必须为非空相对路径')
    root=Path(root).resolve()
    target=(root/relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f'任务证据路径越出运行目录: {relative}') from exc
    return target


def _file_stat(path):
    stat=Path(path).stat()
    return dict(size_bytes=int(stat.st_size),mtime_ns=int(stat.st_mtime_ns))


def _completion_outputs(root, attempt, evidence):
    """Hash attempt outputs once, reusing the solver's fresh MPS digest.

    The MPS is immutable after ``solve_pyomo_model`` writes and hashes it.  Its
    size and nanosecond mtime are checked again here before that digest is
    admitted to the success manifest, so success creation never performs a
    redundant multi-gigabyte read.
    """
    root=Path(root).resolve()
    attempt=Path(attempt).resolve()
    expected_model=(attempt/'model.mps').resolve()
    model_value=evidence.get('model_file')
    model_digest=evidence.get('model_sha256')
    if not model_value or Path(model_value).resolve()!=expected_model:
        raise ValueError('求解证据的MPS路径与当前attempt不一致')
    if not isinstance(model_digest,str) or len(model_digest)!=64:
        raise ValueError('求解证据缺少有效MPS SHA-256')
    expected_stat=dict(size_bytes=evidence.get('model_size_bytes'),mtime_ns=evidence.get('model_mtime_ns'))
    actual_stat=_file_stat(expected_model)
    if actual_stat!=expected_stat:
        raise ValueError('MPS在求解器哈希后被修改，拒绝生成成功标记')
    evidence_value=evidence.get('solver_evidence_file')
    if not evidence_value:
        raise ValueError('求解证据缺少solver_evidence_file')
    evidence_path=Path(evidence_value).resolve()
    if evidence_path.parent!=attempt:
        raise ValueError('solver_evidence_file不在当前attempt目录')
    model_relative=_relative_output(root,expected_model)
    evidence_relative=_relative_output(root,evidence_path)
    output_hashes={}
    for path in sorted((p for p in attempt.rglob('*') if p.is_file()),key=lambda p:str(p)):
        relative=_relative_output(root,path)
        output_hashes[relative]=(model_digest if path.resolve()==expected_model else file_hash(path))
    if model_relative not in output_hashes or evidence_relative not in output_hashes:
        raise ValueError('成功证据缺少MPS或solver_evidence.json')
    integrity=dict(schema=COMPLETION_INTEGRITY_SCHEMA,model_snapshot=dict(
        path=model_relative,sha256=model_digest,**actual_stat,
        solver_evidence_path=evidence_relative,label_style=evidence.get('model_label_style')))
    return output_hashes,integrity


def _verify_model_snapshot(root, payload, *, model_verification, audited_record):
    integrity=payload.get('completion_integrity')
    if not isinstance(integrity,dict) or integrity.get('schema')!=COMPLETION_INTEGRITY_SCHEMA:
        raise ValueError('成功标记缺少可验证的MPS完整性记录')
    snapshot=integrity.get('model_snapshot')
    if not isinstance(snapshot,dict):
        raise ValueError('成功标记缺少model_snapshot')
    relative=snapshot.get('path')
    digest=snapshot.get('sha256')
    outputs=payload.get('output_sha256')
    if not isinstance(outputs,dict) or outputs.get(relative)!=digest:
        raise ValueError('MPS哈希与成功输出清单不一致')
    target=_output_path(root,relative)
    expected_stat=dict(size_bytes=snapshot.get('size_bytes'),mtime_ns=snapshot.get('mtime_ns'))
    try:
        actual_stat=_file_stat(target)
    except FileNotFoundError as exc:
        raise ValueError(f'已完成结果缺少MPS: {relative}') from exc
    if actual_stat!=expected_stat:
        raise ValueError(f'MPS元数据在最终全量哈希前已改变: {relative}')
    if model_verification=='full':
        if file_hash(target)!=digest:
            raise ValueError(f'已完成结果被修改: {relative}')
    elif model_verification=='audited':
        if not isinstance(audited_record,dict):
            raise ValueError('缺少已通过全量SHA-256的扫描审计记录')
        audited_snapshot=audited_record.get('model_snapshot')
        if (audited_record.get('verification')!='full_sha256'
                or not isinstance(audited_snapshot,dict)
                or audited_snapshot.get('path')!=relative
                or audited_snapshot.get('sha256')!=digest
                or audited_snapshot.get('size_bytes')!=actual_stat['size_bytes']
                or audited_snapshot.get('mtime_ns')!=actual_stat['mtime_ns']):
            raise ValueError('MPS与已通过全量SHA-256的扫描审计记录不一致')
    elif model_verification!='deferred':
        raise ValueError(f'未知MPS验证模式: {model_verification}')
    return snapshot


def _completed(root, task_id, namespace='tasks', *, model_verification='full',
               audited_record=None, audit_records=None):
    root=Path(root)
    marker=root/namespace/task_id/'success.json'
    if not marker.exists():
        return None
    payload=json.loads(marker.read_text(encoding='utf-8'))
    snapshot=_verify_model_snapshot(root,payload,model_verification=model_verification,
                                    audited_record=audited_record)
    for path,digest in payload['output_sha256'].items():
        if path==snapshot['path']:
            continue
        if file_hash(_output_path(root,path))!=digest:
            raise ValueError(f'已完成结果被修改: {path}')
    evidence_path=_output_path(root,snapshot['solver_evidence_path'])
    evidence=json.loads(evidence_path.read_text(encoding='utf-8'))
    if (Path(evidence.get('model_file','')).resolve()!=_output_path(root,snapshot['path'])
            or evidence.get('model_sha256')!=snapshot['sha256']
            or evidence.get('model_size_bytes')!=snapshot['size_bytes']
            or evidence.get('model_mtime_ns')!=snapshot['mtime_ns']
            or evidence.get('model_label_style')!=snapshot.get('label_style')
            or payload.get('point',{}).get('model_sha256')!=snapshot['sha256']):
        raise ValueError('MPS、solver_evidence与point之间的证据链不一致')
    if payload.get('qa',{}).get('passed') is not True:
        raise ValueError(f'已完成任务缺少通过的QA: {task_id}')
    if model_verification=='audited':
        if file_hash(marker)!=audited_record.get('success_sha256'):
            raise ValueError(f'成功标记在全量审计后被修改: {marker}')
    if audit_records is not None:
        if model_verification!='full':
            raise ValueError('只能记录已通过完整SHA-256的审计项')
        audit_records.append(dict(task_id=task_id,namespace=namespace,
            success_path=_relative_output(root,marker),success_sha256=file_hash(marker),
            verification='full_sha256',model_snapshot=dict(snapshot),
            output_count=len(payload['output_sha256']),qa_passed=bool(payload.get('qa',{}).get('passed'))))
    fields=dict(payload['point'])
    fields['labels']=tuple(fields['labels'])
    return ParetoPoint(**fields)


def _integrity_audit_payload(root, plan, records, *, schema, scope, **extra):
    return dict(schema=schema,status='passed',scope=scope,
        verification='every listed output was checked against success.json; every listed MPS was read and SHA-256 verified',
        verified_at=datetime.now(timezone.utc).isoformat(),
        task_plan_sha256=file_hash(Path(root)/'task_plan.json'),case_sha256=plan['case_sha256'],
        task_count=len(records),tasks=records,**extra)


def _load_scan_audit(root, plan):
    root=Path(root)
    path=root/'scan_integrity_audit.json'
    if not path.is_file():
        raise ValueError('缺少扫描任务全量完整性审计；必须先执行assemble')
    payload=json.loads(path.read_text(encoding='utf-8'))
    expected_ids=[row['task_id'] for row in plan['tasks']]
    rows=payload.get('tasks')
    if (payload.get('schema')!=SCAN_INTEGRITY_SCHEMA or payload.get('status')!='passed'
            or payload.get('task_plan_sha256')!=file_hash(root/'task_plan.json')
            or payload.get('case_sha256')!=plan['case_sha256']
            or not isinstance(rows,list) or [row.get('task_id') for row in rows]!=expected_ids
            or payload.get('task_count')!=len(expected_ids)):
        raise ValueError('扫描任务完整性审计与当前计划不一致')
    return path,payload,{row['task_id']:row for row in rows}


def run_task(root, task_id, *, settings: SolverSettings | None=None):
    root=Path(root)
    plan=verify_plan(root)
    tasks={t['task_id']:t for t in plan['tasks']}
    if task_id not in tasks:
        raise ValueError('未知V2任务ID')
    existing=_completed(root,task_id)
    if existing is not None:
        return existing
    task=tasks[task_id]
    # The MPS is evidence, not a downstream solver input.  Dependency checks
    # hash every smaller output and validate the MPS metadata/evidence chain;
    # the expensive MPS content hash is deliberately deferred to assemble.
    if (plan['full_scale'] and task_id!='central-cost'
            and _completed(root,'central-cost',model_verification='deferred') is None):
        raise ValueError('首次全季必须仅运行集中式成本端点并测量内存，其余任务尚未解锁')
    epsilon=None
    if task['phase']=='epsilon':
        endpoints={t['task_id']:_completed(root,t['task_id'],model_verification='deferred')
                   for t in plan['tasks'] if t['phase']=='endpoint'}
        if not all(endpoints.values()):
            raise ValueError('六端点尚未全部通过，禁止epsilon扫描')
        lower=endpoints[task['mode']+'-carbon'].annual_operating_carbon_kgCO2e_per_year
        upper=endpoints[task['mode']+'-cost'].annual_operating_carbon_kgCO2e_per_year
        if lower>upper+1e-6:
            raise ValueError('成本/碳端点范围矛盾，需复核最优性')
        epsilon=lower+(upper-lower)*task['epsilon_index']/(plan['point_count']-1)
    return _execute(root,plan,task,epsilon,settings,namespace='tasks',gap_limit=plan['scan_gap'])


def _execute(root,plan,task,epsilon,settings,*,namespace,gap_limit):
    task_id=task['task_id']
    task_root=root/namespace/task_id
    task_root.mkdir(parents=True,exist_ok=True)
    # Reserve the logical task, not just an attempt. Never remove old attempt
    # evidence. A crashed worker leaves its reservation for explicit inspection.
    reservation=task_root/'worker_reservation.json'
    if reservation.exists():
        prior=json.loads(reservation.read_text(encoding='utf-8'))
        if not (task_root/prior['attempt']/'failure.json').exists():
            raise ValueError('同一任务已有工作进程或未收口尝试，禁止并发重复求解')
        archived=task_root/prior['attempt']/'worker_reservation.json'
        reservation.rename(archived)
    # Atomic mkdir reserves an attempt even when two independent workers race.
    for index in range(1,10000):
        attempt=task_root/f'attempt_{index:04d}'
        try:
            attempt.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise RuntimeError('任务尝试次数过多')
    import os
    write_json(reservation,dict(pid=os.getpid(),attempt=attempt.name,created_at=datetime.now(timezone.utc).isoformat()))
    case=load_case(root/'case.json').with_mode(task['mode'])
    resolved=settings or SolverSettings(threads=plan['threads'],mip_gap=gap_limit,time_limit_seconds=plan['time_limit_seconds'])
    if plan.get('execution_profile')=='budget_50m_v1':
        resolved=replace(resolved,feasibility_tolerance=plan['feasibility_tolerance'])
    if resolved.mip_gap>gap_limit:
        write_json(attempt/'failure.json',dict(error='gap过宽',solver_success=False))
        raise ValueError(f'任务gap不得宽于冻结门槛 {gap_limit}')
    resolved=replace(resolved,log_file=str(attempt/'solver.log'),model_file=str(attempt/'model.mps'),evidence_file=str(attempt/'solver_evidence.json'))
    epsilon_constraint_tolerance=1e-6
    write_json(attempt/'task_request.json',dict(task=task,settings=asdict(resolved),epsilon=epsilon,
        epsilon_constraint_tolerance_kgCO2e_per_year=(epsilon_constraint_tolerance if epsilon is not None else None),
        enforced_epsilon_upper_bound_kgCO2e_per_year=(epsilon+epsilon_constraint_tolerance if epsilon is not None else None),
        building_count=len(case.common.demand_nodes),hour_count=len(case.common.hours),case_sha256=plan['case_sha256'],
        execution_profile=plan.get('execution_profile','strict_v2'),
        result_qualification=plan.get('result_qualification','strict_model_horizon')))
    try:
        def measured_builder(_):
            from time import perf_counter
            write_json(attempt/'model_build_started.json',dict(
                started_at=datetime.now(timezone.utc).isoformat(), building_count=len(case.common.demand_nodes),
                hour_count=len(case.common.hours), edge_count=len(case.network['edges']),
                core_version='road_joint_v2', solver_instantiated=False))
            clock=perf_counter()
            direction_relaxation=bool(plan.get('direction_relaxation',False))
            model=build_road_model(case,direction_relaxation=direction_relaxation)
            if plan.get('execution_profile')=='budget_50m_v1':
                apply_budget_network_design(model,case,plan['budget_network_design'])
                if case.common.mode=='hybrid' and resolved.name=='highs':
                    central_carbon=_completed(root,'central-carbon',model_verification='deferred')
                    use_distributed=(task['objective']=='carbon' or (
                        epsilon is not None and central_carbon is not None
                        and epsilon<central_carbon.annual_operating_carbon_kgCO2e_per_year))
                    initialize_budget_hybrid_mip_start(
                        model,case,plan['budget_network_design'],
                        policy=('all_distributed' if use_distributed else 'all_central'))
            write_json(attempt/'model_build_completed.json',dict(
                seconds=perf_counter()-clock, variables=model.nvariables(), constraints=model.nconstraints(),
                flow_formulation=('aggregate_uniform_pumping' if model.uniform_pumping_flow.value
                                  else 'grade_indexed_pumping'),
                service_leaf_flow_elimination=bool(model.service_leaf_flow_elimination.value),
                eliminated_service_leaf_edge_count=len(model.SERVICE_LEAF_E),
                deterministic_demand_dispatch=bool(model.deterministic_demand_dispatch.value),
                distributed_fastpath=bool(model.distributed_fastpath.value),
                direction_relaxation=direction_relaxation,
                exact_direction_pair_count=len(model.EXACT_DIRECTION_PAIRS),
                relaxed_direction_pair_count=len(model.RELAXED_DIRECTION_PAIRS),
                budget_network_design_applied=(plan.get('execution_profile')=='budget_50m_v1'),
                mip_start_policy=getattr(model,'_urbanheatopt_mip_start_policy',None),
                completed_at=datetime.now(timezone.utc).isoformat(), solver_instantiated=False))
            return model
        # Keep the requested epsilon in the result while giving only the
        # mathematical constraint its QA-scale numerical margin. Keeping this
        # separate from the frontier tolerance preserves endpoint dominance.
        point,solution=solve_pareto_task(case.common,resolved,ParetoSpec(
            plan['point_count'],carbon_tolerance_kgCO2e_per_year=0.,
            epsilon_constraint_tolerance_kgCO2e_per_year=epsilon_constraint_tolerance),
            point_id=task_id,objective=task['objective'],epsilon_kgCO2e_per_year=epsilon,
            model_builder=measured_builder)
        direction_audit=None
        if bool(plan.get('direction_relaxation',False)) and not bool(solution.model.distributed_fastpath.value):
            direction_audit=classify_direction_solution(solution.model,tolerance_kW=1e-6)
            if plan.get('direction_lift_required') and not direction_audit['liftable']:
                raise ValueError(
                    'budget direction relaxation is not liftable: '
                    f"violations={direction_audit['metrics']['violation_count']}, "
                    f"max_counterflow_kW={direction_audit['metrics']['max_counterflow_kW']}"
                )
        evidence=get_solver_evidence(solution.solver_results)
        gap=evidence.get('relative_mip_gap')
        if gap is None or gap>resolved.mip_gap+1e-12:
            raise ValueError('缺少可认证gap，不能导出成功结果')
        if epsilon is not None and point.annual_operating_carbon_kgCO2e_per_year>epsilon+epsilon_constraint_tolerance:
            raise ValueError('实际碳排超过epsilon上限及1e-6校核容差')
        qa=export_solution(case,solution.model,attempt/'solution')
        verify_plan(root)
        output_hashes,completion_integrity=_completion_outputs(root,attempt,evidence)
        write_json(task_root/'success.json',dict(point=asdict(point),qa=qa,
            model_core='road_joint_v2',output_sha256=output_hashes,
            completion_integrity=completion_integrity,
            execution_profile=plan.get('execution_profile','strict_v2'),
            result_qualification=plan.get('result_qualification','strict_model_horizon'),
            direction_audit=(direction_audit['metrics'] if direction_audit else None)))
        del solution
        gc.collect()
        return point
    except Exception as exc:
        write_json(attempt/'failure.json',dict(error=str(exc),error_type=type(exc).__name__,solver_success=False))
        raise


def assemble(root, *, policy_carbon_cap=None):
    root=Path(root)
    plan=verify_plan(root)
    audit_records=[]
    points=[_completed(root,t['task_id'],audit_records=audit_records) for t in plan['tasks']]
    if any(p is None for p in points):
        raise ValueError('全部端点与epsilon任务尚未完成，不能生成全季完成报告')
    result=assemble_pareto_run(tuple(points),ParetoSpec(plan['point_count']))
    write_json(root/'pareto_frontiers.json',dict(modes={mode:[asdict(p) for p in row] for mode,row in result.mode_frontiers.items()},
        combined=[asdict(p) for p in result.combined_frontier],method='epsilon_constraint',
        execution_profile=plan.get('execution_profile','strict_v2'),
        result_qualification=plan.get('result_qualification','strict_model_horizon')))
    representatives=select_representative_points(result.combined_frontier,policy_carbon_constraint_kgCO2e_per_year=policy_carbon_cap)
    write_json(root/'representatives_provisional.json',dict(rules=representatives,certified_at_0_1_percent=False,
        note=(f"仅选择；尚未执行{plan['representative_gap']:.3%} gap复核求解，"
              "不宣称最终代表解认证完成")))
    from competition.road_joint_v2.figures import render_figures
    render_figures(root)
    write_json(root/'scan_integrity_audit.json',_integrity_audit_payload(
        root,plan,audit_records,schema=SCAN_INTEGRITY_SCHEMA,
        scope='all endpoint and epsilon task outputs before Pareto assembly'))
    return result


def refine_representatives(root, *, policy_carbon_cap=None, settings=None):
    """Fresh gap-certified solves; missing policy cap stays missing, never invented.

    A policy-cap recommendation is solved separately in all three modes, not
    substituted with the nearest grid point. Other rules refine their selected
    mode; this is not a proof of a continuous/global Pareto knee.
    """
    root=Path(root)
    plan=verify_plan(root)
    if policy_carbon_cap is not None and (isinstance(policy_carbon_cap,bool) or not isfinite(policy_carbon_cap) or policy_carbon_cap<0):
        raise ValueError('政策碳上限必须为有限非负kgCO2e/year')
    scan_audit_path,scan_audit,scan_records=_load_scan_audit(root,plan)
    points=[_completed(root,t['task_id'],model_verification='audited',
                       audited_record=scan_records[t['task_id']]) for t in plan['tasks']]
    if any(p is None for p in points):
        raise ValueError('扫描未完成，不得提前认证代表解')
    result=assemble_pareto_run(tuple(points),ParetoSpec(plan['point_count']))
    selected=select_representative_points(result.combined_frontier,policy_carbon_constraint_kgCO2e_per_year=policy_carbon_cap)
    recipes={}
    rule_jobs={}
    for rule,row in selected.items():
        if rule=='policy_constraint':
            if policy_carbon_cap is not None:
                rule_jobs[rule]=[]
                for mode in ('central','distributed','hybrid'):
                    job=f'policy-{mode}'
                    recipes[job]=dict(task_id=job,mode=mode,objective='cost',epsilon=policy_carbon_cap)
                    rule_jobs[rule].append(job)
            continue
        if not row['point_id']:
            continue
        original=next(p for p in result.combined_frontier if p.point_id==row['point_id'])
        objective='carbon' if rule=='minimum_carbon' else 'cost'
        epsilon=original.annual_operating_carbon_kgCO2e_per_year if rule=='normalized_knee' else None
        job=rule
        recipes[job]=dict(task_id=job,mode=original.mode,objective=objective,epsilon=epsilon)
        rule_jobs[rule]=[job]
    request=dict(policy_carbon_cap=policy_carbon_cap,recipes=recipes,rule_jobs=rule_jobs,selected=selected)
    directory=root/'refinements'
    directory.mkdir(exist_ok=True)
    request_path=directory/'refinement_plan.json'
    if request_path.exists():
        if json.loads(request_path.read_text(encoding='utf-8'))!=request:
            raise ValueError('代表规则或政策上限改变，必须使用新的运行目录')
    else:
        write_json(request_path,request)
    solved={}
    for job,recipe in recipes.items():
        point=_completed(root,job,'refinements',model_verification='deferred')
        if point is None:
            point=_execute(root,plan,recipe,recipe['epsilon'],settings,namespace='refinements',gap_limit=plan['representative_gap'])
        solved[job]=point
    # Carbon alone can leave arbitrary expensive capacities at an equally
    # optimal carbon value. Resolve that degeneracy with the already-approved
    # epsilon-constraint form; preserve BOTH solves and their gap certificates.
    carbon_bound=solved.get('minimum_carbon')
    if carbon_bound is not None:
        tie_id='minimum_carbon-cost-tiebreak'
        tie=_completed(root,tie_id,'refinements',model_verification='deferred')
        if tie is None:
            recipe=dict(task_id=tie_id,mode=carbon_bound.mode,objective='cost')
            tie=_execute(root,plan,recipe,carbon_bound.annual_operating_carbon_kgCO2e_per_year,settings,
                namespace='refinements',gap_limit=plan['representative_gap'])
        solved[tie_id]=tie
        rule_jobs['minimum_carbon']=[tie_id]
    final={}
    for rule,row in selected.items():
        jobs=rule_jobs.get(rule,[])
        if not jobs:
            final[rule]=dict(row,certified_at_0_1_percent=False)
            continue
        winner=min((solved[j] for j in jobs),key=lambda p:(p.annual_real_cost_CNY_per_year,p.point_id)) if rule=='policy_constraint' else solved[jobs[0]]
        overlaps=[other for other in solved.values() if other.point_id!=winner.point_id and other.mode==winner.mode
            and abs(other.annual_real_cost_CNY_per_year-winner.annual_real_cost_CNY_per_year)<=1e-6
            and abs(other.annual_operating_carbon_kgCO2e_per_year-winner.annual_operating_carbon_kgCO2e_per_year)<=1e-6]
        final[rule]=dict(point=asdict(winner),
            certified_at_0_1_percent=plan['representative_gap']<=.001+1e-15,
            certified_mip_gap=plan['representative_gap'],
            objective_overlap_with=[p.point_id for p in overlaps])
    refinement_audit_records=[]
    for job in solved:
        verified=_completed(root,job,'refinements',audit_records=refinement_audit_records)
        if verified is None:
            raise ValueError(f'代表解复核任务缺少成功证据: {job}')
        solved[job]=verified
    final_audit=root/'integrity_audit.json'
    if not final_audit.exists():
        write_json(final_audit,_integrity_audit_payload(root,plan,
            [*scan_audit['tasks'],*refinement_audit_records],
            schema=FINAL_INTEGRITY_SCHEMA,
            scope='all scan and representative-refinement task outputs',
            scan_integrity_audit_path=_relative_output(root,scan_audit_path),
            scan_integrity_audit_sha256=file_hash(scan_audit_path),
            scan_task_count=scan_audit['task_count'],
            refinement_task_count=len(refinement_audit_records)))
    target=directory/'representatives_certified.json'
    if not target.exists():
        write_json(target,dict(rules=final,policy_cap_unit='kgCO2e/year',
            carbon_endpoint_certificate=asdict(carbon_bound) if carbon_bound else None,
            qualification=(plan.get('result_qualification','strict_model_horizon')
                if plan.get('execution_profile')=='budget_50m_v1'
                else '规则来源于已计算离散前沿；0.1%是各复核MILP的gap，不是连续前沿/全局膝点误差。'),
            execution_profile=plan.get('execution_profile','strict_v2'),
            certified_mip_gap=plan['representative_gap'],
            engineering_result=False))
    return final
