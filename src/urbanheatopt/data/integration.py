"""A-only input pipeline. Never import an optimization backend."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import time

import geopandas as gpd
import pandas as pd
import yaml

from urbanheatopt.paths import PACKAGE_ROOT, REPOSITORY_ROOT
from urbanheatopt.data.intake.guanggu_v03 import resolve_guanggu_v03_source_roots, validate_guanggu_v03_delivery
from urbanheatopt.data.adapters.guanggu_v03 import adapt_guanggu_v03_sources
from urbanheatopt.data.bundles import CaseBundle, INTERFACE_VERSION, ready_report, sha256_file
from urbanheatopt.data.gap_catalog import data_gaps, render_gap_report, publish_gap_report
from urbanheatopt.parameters.revised_economics import read_revised_package, revised_timeseries, PACKAGE_DIRECTORY


def write_json(path: Path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)


def load_config(path: Path):
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("config_version") != "guanggu_v2_a_1.0.0":
        raise ValueError("未知运行配置版本")
    allowed = {"config_version", "delivery_root", "source_profile", "economic_package", "economic_scenario", "source_permission_policy", "output_root", "scope", "full_audit", "mode_scope", "tes_enabled", "spatial_inputs", "desktop_gap_report"}
    if set(config) != allowed:
        raise ValueError(f"配置缺少/未知字段: {sorted(set(config)^allowed)}")
    for key in ("tes_enabled", "full_audit", "desktop_gap_report"):
        if type(config[key]) is not bool:
            raise ValueError(f"{key}必须为YAML true/false")
    if config["source_profile"] != "guanggu_v03" or config["scope"] != "heating-season" or config["full_audit"] is not True:
        raise ValueError("A主线要求guanggu_v03完整供暖季及full_audit=true")
    if config["economic_package"] != "revised_20260831":
        raise ValueError("新主线只接受显式revised_20260831；旧包仅历史回归")
    if config["source_permission_policy"] != "require_consistent":
        raise ValueError("来源许可冲突尚待用户确认，不得自行放行")
    if config["mode_scope"] != ["central", "distributed", "hybrid"]:
        raise ValueError("三模式必须共用一个输入边界")
    if not isinstance(config["spatial_inputs"], dict):
        raise ValueError("spatial_inputs必须为映射")
    return config


def resolve_path(value):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (REPOSITORY_ROOT / path).resolve()


def spatial_interfaces(spec):
    """Validate supplied handoff files, without inventing sites or capacities."""
    known = {"candidate_sites", "site_limits", "pipe_capacity_limits"}
    if set(spec) - known:
        raise ValueError(f"未知空间接口: {set(spec)-known}")
    result = {"provided": [], "missing": sorted(known-set(spec)), "paths": []}
    site_ids = None
    for role, name in spec.items():
        path = resolve_path(name)
        allowed_source = REPOSITORY_ROOT.parent / "IN_DATA/原始输入数据/v0.2"
        if "v0.1" in path.parts or not (path.is_relative_to(REPOSITORY_ROOT) or path.is_relative_to(allowed_source)):
            raise ValueError(f"空间输入超出授权仓库/v0.2范围: {path}")
        if not path.is_file():
            raise ValueError(f"空间文件不存在: {path}")
        if role == "candidate_sites":
            frame = gpd.read_file(path)
            if frame.crs is None or frame.crs.to_epsg() != 32650:
                raise ValueError("candidate_sites须EPSG:32650")
            required = {"site_id", "parcel_id", "attachment_node_id", "source_id", "parameter_status"}
            if not frame.geometry.is_valid.all() or frame.geometry.is_empty.any() or not frame.geom_type.eq("Point").all():
                raise ValueError("候选站须有效非空Point")
        else:
            frame = pd.read_csv(path, encoding="utf-8-sig")
            required = {"source_id", "parameter_status"} | ({"site_id", "allowed_technology_ids", "heat_capacity_limit_kW_th", "electricity_limit_kW_e", "gas_limit_kW_LHV"} if role == "site_limits" else {"pipe_type_id", "capacity_kW_th"})
            for column in required & {"heat_capacity_limit_kW_th", "electricity_limit_kW_e", "gas_limit_kW_LHV", "capacity_kW_th"}:
                if column not in frame:
                    continue
                values = pd.to_numeric(frame[column], errors="coerce")
                if values.isna().any() or not values.map(lambda x: 0 <= x < float("inf")).all():
                    raise ValueError(f"{role}.{column}: 容量缺失/非法")
                if column != "gas_limit_kW_LHV" and values.le(0).any():
                    raise ValueError(f"{role}.{column}: 容量必须大于0")
        if not required <= set(frame) or frame.empty or frame[list(required)].isna().any().any():
            raise ValueError(f"{role}: 缺字段/空表/空值 {sorted(required-set(frame))}")
        for column in required:
            if frame[column].astype(str).str.strip().eq("").any():
                raise ValueError(f"{role}.{column}: 空字段")
        key = "pipe_type_id" if role == "pipe_capacity_limits" else "site_id"
        if frame[key].astype(str).str.strip().eq("").any() or frame[key].duplicated().any():
            raise ValueError(f"{role}: 空/重复主键")
        if not frame.parameter_status.isin(["verified", "research_assumption", "pending"]).all():
            raise ValueError(f"{role}: 未知参数状态")
        if key == "site_id":
            if site_ids is not None and site_ids != set(frame.site_id):
                raise ValueError("候选站与容量表site_id不一致")
            site_ids = set(frame.site_id)
        result["provided"].append(role)
        result["paths"].append({"role": role, "path": str(path), "sha256": sha256_file(path)})
    result["model_consumed"] = False
    return result


def run_input_pipeline(command, config_path: Path, *, run_id=None, output_root=None, desktop_path=None):
    started = datetime.now(timezone.utc).isoformat()
    clock = time.monotonic()
    config = load_config(config_path)
    roots = resolve_guanggu_v03_source_roots(resolve_path(config["delivery_root"]))
    output_base = resolve_path(output_root or config["output_root"])
    # All generated artifacts stay in this repo's work/runs, never IN_DATA/OUT_RESULT.
    if not any(output_base.is_relative_to(REPOSITORY_ROOT / folder) for folder in ("work", "runs")):
        raise ValueError("输出必须在仓库work或runs的新目录，不写原始输入或历史OUT_RESULT")
    run_id = run_id or datetime.now().strftime("A_%Y%m%dT%H%M%S%f")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id):
        raise ValueError("run_id只能使用英文字母/数字/下划线/短横线")
    output = output_base / run_id
    output.mkdir(parents=True, exist_ok=False)
    source_files = sorted(p for p in roots.delivery_root.rglob("*") if p.is_file())
    if roots.equipment_patch_root:
        source_files += sorted(p for p in roots.equipment_patch_root.rglob("*") if p.is_file())
    for path in source_files:
        if not path.resolve().is_relative_to(roots.scope_root) or "v0.1" in path.resolve().parts:
            raise ValueError(f"禁止越界读取: {path}")
    before = {str(p.resolve()): sha256_file(p) for p in source_files}
    before[str(config_path.resolve())] = sha256_file(config_path)
    write_json(output / "input_hashes_before.json", before)
    write_json(output / "run_config.json", config)
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT, text=True).strip()
    profile = yaml.safe_load((PACKAGE_ROOT / "data/profile_resources/guanggu_v03.yaml").read_text(encoding="utf-8"))
    profile["economic_parameters_separate_gate"] = True
    profile_path = output / "source_profile.yaml"
    profile_path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    code_files = [REPOSITORY_ROOT / "run.py", *sorted((REPOSITORY_ROOT / "src/urbanheatopt").rglob("*.py"))]
    code_hashes = {str(p.relative_to(REPOSITORY_ROOT)): sha256_file(p) for p in code_files if p.is_file()}
    write_json(output / "code_provenance.json", {"git_sha":git_sha, "working_tree_status":subprocess.check_output(["git", "status", "--porcelain"], cwd=REPOSITORY_ROOT, text=True), "sha256":code_hashes})
    snapshot, source, adapted, bundle = None, None, None, None
    errors = []
    status = dict(input_valid=False, parameter_valid=False, canonical_valid=False, snapshot_complete=False)
    spatial = None
    try:
        print("[1/4] 自动发现与完整源校验（不构模）", flush=True)
        source = validate_guanggu_v03_delivery(roots.requested_root, profile_path=profile_path, full_audit=True)
        write_json(output / "source_validation_report.json", source.to_dict())
        status["input_valid"] = source.valid
        errors.extend(i.message for i in source.issues if i.severity == "error")
        print("[2/4] 新经济包逐参数校验与选择", flush=True)
        try:
            snapshot = read_revised_package(roots.delivery_root / PACKAGE_DIRECTORY, config["economic_scenario"])
            write_json(output / "effective_parameters.json", snapshot)
            pd.DataFrame(snapshot["registry"].values()).to_csv(output / "parameter_selection.csv", index=False, encoding="utf-8-sig")
            status["parameter_valid"] = True
        except (ValueError, OSError) as exc:
            errors.extend(str(exc).splitlines())
        try:
            spatial = spatial_interfaces(config["spatial_inputs"])
            if snapshot:
                for item in spatial["paths"]:
                    if item["role"] == "pipe_capacity_limits":
                        supplied = set(pd.read_csv(item["path"], encoding="utf-8-sig").pipe_type_id)
                        if supplied != {p["pipe_type_id"] for p in snapshot["pipes"]}:
                            raise ValueError("pipe_capacity_limits与新经济包pipe_type_id不一致")
                    if item["role"] == "site_limits":
                        ids = set(pd.read_csv(roots.delivery_root / "technologies.csv", encoding="utf-8-sig").technology_id)
                        for value in pd.read_csv(item["path"], encoding="utf-8-sig").allowed_technology_ids:
                            tokens = str(value).split(";")
                            if any(not token or token not in ids for token in tokens) or len(tokens) != len(set(tokens)):
                                raise ValueError("site_limits.allowed_technology_ids须与交付技术登记ID对应，且无空/重复值")
            write_json(output / "spatial_interface_report.json", spatial)
        except (ValueError, OSError) as exc:
            errors.append(str(exc))
        if command == "prepare" and source.valid:
            print("[3/4] 62栋供暖季适配与标准复验", flush=True)
            adapted = adapt_guanggu_v03_sources(roots.requested_root, output / "source_accepted", profile_path=profile_path, full_audit=True)
            status["canonical_valid"] = adapted.canonical_report.valid
            if snapshot and spatial is not None:
                authoritative = output / "inputs"
                authoritative.mkdir()
                external_path = authoritative / "external_timeseries.parquet"
                revised_timeseries(adapted.canonical_data.external_timeseries, snapshot).to_parquet(external_path, index=False)
                if adapted.canonical_data.building_count != 62 or adapted.canonical_data.load_row_count != 133920:
                    raise ValueError("新主线要求62栋×2160小时，不能把合成/子集当本次标准输入")
                artifacts = []
                for role, path in (("buildings", adapted.buildings_path), ("building_archetype_map", adapted.archetype_map_path), ("loads", adapted.loads_path), ("external_timeseries", external_path), ("equipment_performance", adapted.equipment_performance_path), ("time_mapping", adapted.timestamp_hour_map_path), ("effective_parameters", output / "effective_parameters.json"), ("parameter_selection", output / "parameter_selection.csv")):
                    artifacts.append({"role": role, "path": str(path), "sha256": sha256_file(path)})
                artifacts.extend(spatial["paths"])
                status["snapshot_complete"] = not errors
                bundle = CaseBundle.from_dict(dict(interface_version=INTERFACE_VERSION, data_version=source.data_version,
                    parameter_version=snapshot["snapshot_id"], git_sha=git_sha, artifacts=artifacts, source_hashes=before,
                    units={"heating_kW":"kW_th", "electricity_price_CNY_per_kWh_e":"CNY/kWh_e", "gas_price_CNY_per_kWh_LHV":"CNY/kWh_LHV", "time_weight_h_per_year":"h/year", "carbon":"kgCO2e/year"},
                    capabilities_required=["monthly_demand_charge", "effective_parameter_mapping", "site_capacity", "result_bundle"] + (["tes"] if config["tes_enabled"] else []),
                    status=status, physical_scope={"buildings":62, "hours":2160, "supply_C":45, "return_C":40, "peak_capacity_margin_fraction":.20}, spatial_status=spatial,
                    economic_boundary={"scenario":config["economic_scenario"], "tes_enabled":config["tes_enabled"], "model_consumed":False}))
    except (ValueError, OSError) as exc:
        errors.append(str(exc))
    after_files = sorted(p for p in roots.delivery_root.rglob("*") if p.is_file())
    if roots.equipment_patch_root:
        after_files += sorted(p for p in roots.equipment_patch_root.rglob("*") if p.is_file())
    after = {}
    for p in [*after_files, config_path]:
        try:
            after[str(p.resolve())] = sha256_file(p)
        except OSError as exc:
            errors.append(f"结束时输入不可读: {p}: {exc}")
    unchanged = before == after
    if not unchanged:
        errors.append("输入哈希在读取前后不一致，拒绝交付")
        status["snapshot_complete"] = False
    if errors:
        status["snapshot_complete"] = False
    if bundle is not None and not errors:
        bundle.write(output / "case_bundle.json")
        write_json(output / "model_readiness_report.json", ready_report(bundle))
    write_json(output / "input_hashes_after.json", after)
    gaps = data_gaps(snapshot, errors, spatial)
    write_json(output / "input_gaps.json", gaps)
    text = render_gap_report(gaps, snapshot)
    (output / "缺失数据清单.md").write_text(text, encoding="utf-8")
    if config["desktop_gap_report"]:
        backup = publish_gap_report(desktop_path or Path.home() / "Desktop/缺失数据清单.md", text)
    else:
        backup = None
    passed = status["input_valid"] and status["parameter_valid"] and not errors and unchanged
    if command == "prepare":
        passed = passed and status["snapshot_complete"]
    summary = dict(command=command, output_dir=str(output), git_sha=git_sha, started_at=started, finished_at=datetime.now(timezone.utc).isoformat(), elapsed_seconds=time.monotonic()-clock,
                   **status, model_ready=False, solver_executed=False, result_qualified=False,
                   input_hashes_unchanged=unchanged, input_file_count=len(source_files), errors=errors,
                   desktop_previous_backup=backup, exit_code=0 if passed else 2)
    if not (output / "model_readiness_report.json").exists():
        write_json(output / "model_readiness_report.json", {**status, "model_ready":False,
            "solver_executed":False, "result_qualified":False,
            "blockers":errors + ["B消费新经济/需量/站址接口尚未接通", "C独立结果消费尚未接通"],
            "note":"没有合格CaseBundle，不实例化求解器"})
    write_json(output / "run_summary.json", summary)
    print("[4/4] 输入交接状态已记录；未执行模型求解", flush=True)
    return summary
