"""Real-delivery V2 gate: validate -> economics -> spatial -> builder -> tasks."""
from pathlib import Path
from datetime import datetime, timezone
import json


from urbanheatopt.data.readiness import run_guanggu_v03_input_validation
from urbanheatopt.data.intake.guanggu_v03 import resolve_guanggu_v03_source_roots
from urbanheatopt.parameters.legacy_economics import PACKAGE_DIRECTORY, read_package, effective_parameters, write_gap_report, describe_energy_input
from urbanheatopt.spatial.atomic_network import atomize_snapshot, write_network, RoadNetworkError, read_optional_layers
from urbanheatopt.data.road_builder import build_season_case
from urbanheatopt.optimization.reference_tasks import create_plan, run_task, write_json


def prepare_delivery(delivery_root, osm_snapshot, output_root, run_id, *, profile='v0-full-season',desktop_report=None,
                     allowed_corridors=None, forbidden_areas=None, obstacle_buildings=None):
    if not run_id or Path(run_id).name!=run_id or run_id in {'.','..'}:
        raise ValueError('必须提供唯一、不含目录分隔的run-id')
    root=Path(output_root)/run_id
    root.mkdir(parents=True,exist_ok=False)
    state=dict(core_version='road_joint_v2',source_validation_passed=False,canonical_validation_passed=False,
        spatial_validation_passed=False,model_ready=False,solver_executed=False,
        started_at=datetime.now(timezone.utc).isoformat(),profile=profile)
    snapshot=None
    try:
        gate=run_guanggu_v03_input_validation(delivery_root,root/'validation',root/'input_guidance_CN.md',full_audit=True)
        data=gate.adaptation.canonical_data
        state.update(source_validation_passed=gate.adaptation.source_report.valid,canonical_validation_passed=gate.adaptation.canonical_report.valid,
                     building_count=data.building_count,hour_count=data.hour_count,load_row_count=data.load_row_count)
        if profile!='v0-full-season':
            raise ValueError('当前V2真实入口只允许明确标注假设的v0-full-season；正式报价/道路/TES未签认，v1-full停止')
        roots=resolve_guanggu_v03_source_roots(delivery_root)
        package=read_package(roots.delivery_root/PACKAGE_DIRECTORY)
        peak=float(data.loads.groupby('hour').heating_kW.sum().max())
        snapshot=effective_parameters(package,peak,energy_context=describe_energy_input(data.external_timeseries,float(data.adaptation_metadata['natural_gas_lhv_MJ_per_Nm3'])))
        write_json(root/'effective_parameters.json',snapshot)
        write_gap_report(snapshot,desktop_report or Path.home()/'Desktop'/'缺失数据清单.md')
        layers, extra_paths=read_optional_layers(roots.delivery_root, allowed_corridors=allowed_corridors,
            forbidden_areas=forbidden_areas, obstacle_buildings=obstacle_buildings)
        network=atomize_snapshot(json.loads(Path(osm_snapshot).read_text(encoding='utf-8')),data.buildings,
            data.loads.groupby('building_id').heating_kW.sum().to_dict(), **layers)
        write_network(network,root/'spatial')
        state['spatial_validation_passed']=True
        state.update(network_policy_version=network['metadata']['network_policy_version'],
                     node_count=len(network['nodes']), edge_count=len(network['edges']),
                     access_option_count=len(network['access_options']),
                     execution_purpose='source_load_matching_and_optimization_validation',
                     construction_feasibility_required=False)
        case=build_season_case(gate.adaptation,network,snapshot)
        input_paths=[]
        for relative in gate.adaptation.source_report.file_sha256:
            if relative.startswith('equipment_patch/'):
                input_paths.append(roots.equipment_patch_root/relative.removeprefix('equipment_patch/'))
            else:
                input_paths.append(roots.delivery_root/relative)
        input_paths.append(Path(osm_snapshot))
        input_paths.extend(extra_paths)
        create_plan(case,root,input_paths=input_paths)
        state['model_ready']=True
        write_json(root/'preparation_status.json',state)
        return root
    except Exception as exc:
        state.update(error=str(exc),error_type=type(exc).__name__)
        if isinstance(exc,RoadNetworkError):
            state['geometry_failures']=exc.failures
            if snapshot is not None:
                # Separate gap extension; don't pretend the mathematical parameter hash changed.
                gap_snapshot={**snapshot,'geometry_failures':exc.failures}
                write_gap_report(gap_snapshot,desktop_report or Path.home()/'Desktop'/'缺失数据清单.md')
        write_json(root/'preparation_status.json',state)
        raise
