"""A-owned input pipeline plus explicit, no-solver A/B adapter smoke checks."""
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
from urbanheatopt.data.bundles import (
    CaseBundle, INTERFACE_VERSION, SolveRequest, ready_report, sha256_file,
)
from urbanheatopt.data.capacity_boundaries import build_capacity_boundaries
from urbanheatopt.data.road_builder import (
    ROAD_CASE_BUILDER_VERSION,
    build_road_case,
    save_case,
)
from urbanheatopt.optimization.solve_executor import MODEL_PROFILE, SOLVE_EXECUTOR_VERSION
from urbanheatopt.data.gap_catalog import data_gaps, render_gap_report, publish_gap_report
from urbanheatopt.parameters.revised_economics import (
    read_revised_package, revised_timeseries, PACKAGE_DIRECTORY, validate_source_policy,
)


DEFAULT_SPATIAL_BASELINE = (
    REPOSITORY_ROOT
    / "baselines/spatial/guanggu_osm_20260828/osm_overpass_snapshot.json"
)


# User-confirmed 2026-09-06 research boundary for the frozen Guanggu data set.
# It is not inferred from project grid infrastructure and must not be published
# as a verified utility connection limit.
SITE_ELECTRICITY_LIMIT_KW_E_20260906 = 124366.206988495


def write_json(path: Path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)


def load_config(path: Path):
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("config_version") != "guanggu_v2_a_1.0.0":
        raise ValueError("未知运行配置版本")
    allowed = {"config_version", "delivery_root", "source_profile", "economic_package", "economic_scenario", "v2_parameter_scenario", "source_permission_policy", "output_root", "scope", "full_audit", "mode_scope", "tes_enabled", "spatial_inputs", "desktop_gap_report"}
    if set(config) != allowed:
        raise ValueError(f"配置缺少/未知字段: {sorted(set(config)^allowed)}")
    for key in ("tes_enabled", "full_audit", "desktop_gap_report"):
        if type(config[key]) is not bool:
            raise ValueError(f"{key}必须为YAML true/false")
    if config["source_profile"] != "guanggu_v03" or config["scope"] != "heating-season" or config["full_audit"] is not True:
        raise ValueError("A主线要求guanggu_v03完整供暖季及full_audit=true")
    if config["economic_package"] != "revised_20260831":
        raise ValueError("新主线只接受显式revised_20260831；旧包仅历史回归")
    if config["v2_parameter_scenario"] not in {
        "v2_debug", "v2_primary_expansion_check", "v2_station_high_cost_stress"
    }:
        raise ValueError("未知v2_parameter_scenario")
    validate_source_policy(config["source_permission_policy"])
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
    network_roles = {
        "osm_snapshot", "obstacle_buildings", "allowed_corridors", "forbidden_areas"
    }
    known = {"candidate_sites", "site_limits", "pipe_capacity_limits"} | network_roles
    if set(spec) - known:
        raise ValueError(f"未知空间接口: {set(spec)-known}")
    legacy_roles = {"candidate_sites", "site_limits", "pipe_capacity_limits"}
    result = {
        "provided": [],
        "missing": sorted(legacy_roles-set(spec)),
        "paths": [],
        "network_inputs": sorted(set(spec) & network_roles),
    }
    site_ids = None
    for role, name in spec.items():
        if role in network_roles:
            continue
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


def resolve_spatial_network_inputs(spec: dict, delivery_root: Path) -> dict:
    """Resolve the versioned road snapshot and optional planning layers.

    The snapshot is explicit and hash checked.  No file is discovered from a
    historical run directory and no network request is made at runtime.
    """
    from urbanheatopt.spatial.atomic_network import read_optional_layers

    snapshot_path = resolve_path(spec.get("osm_snapshot", DEFAULT_SPATIAL_BASELINE))
    allowed_root = (REPOSITORY_ROOT.parent / "IN_DATA/原始输入数据/v0.2").resolve()
    if not (
        snapshot_path.is_relative_to(REPOSITORY_ROOT)
        or snapshot_path.is_relative_to(allowed_root)
    ) or "v0.1" in snapshot_path.parts:
        raise ValueError(f"道路快照超出授权仓库/v0.2范围: {snapshot_path}")
    if not snapshot_path.is_file():
        raise ValueError(f"道路快照不存在: {snapshot_path}")
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"道路快照无法解析: {snapshot_path}: {exc}") from exc
    if snapshot.get("version") != 0.6 or not isinstance(snapshot.get("elements"), list):
        raise ValueError("道路快照不是受支持的OSM Overpass JSON 0.6")
    manifest_path = snapshot_path.with_name("manifest.json")
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = str(manifest.get("sha256", "")).lower()
        actual = sha256_file(snapshot_path)
        if actual != expected:
            raise ValueError("道路快照SHA-256与版本基线manifest不一致")
        if manifest.get("network_policy_version") != "planning_corridor_2.1.0":
            raise ValueError("道路快照manifest的网络策略版本不受支持")
    else:
        manifest = {
            "baseline_id": "external_configured_snapshot",
            "sha256": sha256_file(snapshot_path),
            "network_policy_version": "planning_corridor_2.1.0",
        }

    optional = {
        "obstacle_buildings": spec.get("obstacle_buildings"),
        "allowed_corridors": spec.get("allowed_corridors"),
        "forbidden_areas": spec.get("forbidden_areas"),
    }
    layers, paths = read_optional_layers(
        delivery_root,
        obstacle_buildings=optional["obstacle_buildings"],
        allowed_corridors=optional["allowed_corridors"],
        forbidden_areas=optional["forbidden_areas"],
    )
    for path in paths:
        if not (
            path.is_relative_to(REPOSITORY_ROOT)
            or path.is_relative_to(allowed_root)
        ) or "v0.1" in path.parts:
            raise ValueError(f"空间约束文件超出授权仓库/v0.2范围: {path}")
    source_paths = [snapshot_path, *paths]
    if manifest_path.is_file():
        source_paths.append(manifest_path)
    return {
        "snapshot": snapshot,
        "snapshot_path": snapshot_path,
        "manifest": manifest,
        "layers": layers,
        "source_paths": tuple(source_paths),
    }


def prepare_research_boundaries(
    adapted,
    snapshot,
    data_version: str,
    output: Path,
    *,
    delivery_root: Path,
    spatial_spec: dict,
):
    """Generate the authoritative road network and approved capacity limits."""
    from urbanheatopt.model.physical_interfaces import TabularASHPPerformanceProvider
    from urbanheatopt.spatial.atomic_network import atomize_snapshot, write_network

    canonical = adapted.canonical_data
    network_inputs = resolve_spatial_network_inputs(spatial_spec, delivery_root)
    annual_heat = (
        canonical.loads.groupby("building_id", sort=True)["heating_kW"].sum().astype(float).to_dict()
    )
    network = atomize_snapshot(
        network_inputs["snapshot"],
        canonical.buildings,
        annual_heat,
        candidate_count=5,
        obstacles=network_inputs["layers"].get("obstacles"),
        corridors=network_inputs["layers"].get("corridors"),
        forbidden_areas=network_inputs["layers"].get("forbidden_areas"),
    )
    spatial_output = output / "spatial"
    write_network(network, spatial_output)
    candidate_path = spatial_output / "candidate_sites.geojson"

    external = canonical.external_timeseries
    if "outdoor_temperature_C" not in external:
        raise ValueError("标准外部时序缺少outdoor_temperature_C，无法生成容量边界")
    performance = TabularASHPPerformanceProvider(
        adapted.equipment_performance_path,
        supply_temperature_C=45.0,
        performance_boundary_policy="clip_with_flag",
    ).interpolate(tuple(external["outdoor_temperature_C"]))
    hours = tuple(range(1, len(performance) + 1))
    capacities = build_capacity_boundaries(
        loads=canonical.loads,
        site_ids=tuple(site["site_id"] for site in network["sites"]),
        cop_by_hour=dict(zip(hours, map(float, performance["COP"]), strict=True)),
        capacity_ratio_by_hour=dict(
            zip(hours, map(float, performance["capacity_ratio"]), strict=True)
        ),
        supplement=snapshot.get("capacity_supplement", {}),
        v2_freeze_patch=snapshot.get("v2_freeze_patch"),
        site_electricity_connection_max_kW_e=SITE_ELECTRICITY_LIMIT_KW_E_20260906,
    )
    capacity_path = output / "capacity_boundaries.json"
    write_json(capacity_path, capacities)
    network_artifacts = []
    for role, name in (
        ("road_network", "road_network.json"),
        ("candidate_nodes", "candidate_nodes.geojson"),
        ("candidate_edges", "candidate_edges.geojson"),
        ("candidate_sites", "candidate_sites.geojson"),
        ("candidate_access_options", "candidate_access_options.csv"),
        ("building_access_diagnostics", "building_access_diagnostics.csv"),
    ):
        path = spatial_output / name
        network_artifacts.append(
            {"role": role, "path": str(path.resolve()), "sha256": sha256_file(path)}
        )
    preview_files = sorted((spatial_output / "preview").glob("*"))
    for index, path in enumerate(preview_files, 1):
        if path.is_file():
            network_artifacts.append(
                {
                    "role": f"network_preview_{index}",
                    "path": str(path.resolve()),
                    "sha256": sha256_file(path),
                }
            )
    network_manifest = {
        "schema": "urbanheatopt_network_product_1.0.0",
        "data_version": data_version,
        "network_contract_version": network["contract_version"],
        "network_policy_version": network["metadata"]["network_policy_version"],
        "baseline_id": network_inputs["manifest"].get("baseline_id"),
        "snapshot_path": str(network_inputs["snapshot_path"]),
        "snapshot_sha256": sha256_file(network_inputs["snapshot_path"]),
        "network_sha256": network["network_sha256"],
        "node_count": len(network["nodes"]),
        "edge_count": len(network["edges"]),
        "site_count": len(network["sites"]),
        "access_option_count": len(network.get("access_options", [])),
        "building_count": canonical.building_count,
        "road_constrained": True,
        "construction_feasibility_verified": False,
        "optimization_scope": "five_candidate_shortest_path_trees",
        "artifacts": network_artifacts,
    }
    manifest_path = spatial_output / "network_manifest.json"
    write_json(manifest_path, network_manifest)
    network_artifacts.append(
        {"role": "network_manifest", "path": str(manifest_path.resolve()), "sha256": sha256_file(manifest_path)}
    )
    return {
        "candidate_sites_path": candidate_path,
        "road_network_path": spatial_output / "road_network.json",
        "network_manifest_path": manifest_path,
        "network_artifacts": network_artifacts,
        "network": network,
        "network_source_paths": network_inputs["source_paths"],
        "capacity_boundaries_path": capacity_path,
        "capacity_boundaries": capacities,
        "spatial_status": {
            "candidate_count": 5,
            "candidate_source": "deterministic_heat_centroid_extent_snapped",
            "road_constrained": True,
            "parcel_capacity_verified": False,
            "construction_feasibility_verified": False,
            "status": "planning_optimization_baseline",
            "network_sha256": network["network_sha256"],
            "network_policy_version": network["metadata"]["network_policy_version"],
            "optimization_scope": "five_candidate_shortest_path_trees",
        },
    }


def generate_solve_requests(bundle: CaseBundle, output: Path, *, tes_enabled: bool):
    request_dir = output / "solve_requests"
    request_dir.mkdir()
    requests = []
    for mode in ("central", "distributed", "hybrid"):
        request = SolveRequest.from_dict({
            "interface_version": INTERFACE_VERSION,
            "case_bundle_id": bundle.bundle_id,
            "model_profile": MODEL_PROFILE,
            "optimization_scope": "five_candidate_shortest_path_trees",
            "mode": mode,
            "objective": "cost",
            "epsilon_carbon_kg": None,
            "tes_enabled": tes_enabled,
            "allow_unserved": False,
            "solver": {
                "name": "highs",
                "threads": 1,
                "random_seed": 202611,
                "mip_gap": 0.01,
                "time_limit_s": None,
            },
        })
        path = request_dir / f"{mode}_cost.json"
        request.write(path)
        requests.append((mode, request, path))
    return requests


def run_ab_adapter_smoke(
    bundle: CaseBundle,
    requests,
    capacity_payload,
    output: Path,
) -> dict:
    """Consume the real-shape A artifacts through B1/B2 without solving."""
    from urbanheatopt.optimization.adapters.handoff_v1 import consume_handoff_v1
    from urbanheatopt.optimization.adapters.site_capacity_v1 import (
        consume_capacity_boundary_snapshot,
    )

    b1 = [consume_handoff_v1(bundle, request) for _, request, _ in requests]
    b2 = consume_capacity_boundary_snapshot(capacity_payload)
    tes = capacity_payload["tes"]
    evidence = {
        "evidence_version": "ab_research_handoff_1.0.0",
        "b1_real_bundle_smoke_pass": len(b1) == 3,
        "b2_capacity_adapter_smoke_pass": len(b2.sites) == 5 and len(b2.pipes) == 3,
        "modes_consumed": [item[0] for item in requests],
        "monthly_demand_charge_ready": all(
            item.hourly_economics.monthly_demand_charge_CNY_per_kW_month > 0
            for item in b1
        ),
        "effective_parameter_mapping_ready": all(
            item.package_version == "revised_20260831" for item in b1
        ),
        "site_capacity_ready": all(
            item.total_heat_capacity_max_kW_th is not None for item in b2.sites
        ),
        "pipe_capacity_ready": all(
            item.capacity_kW_th is not None for item in b2.pipes
        ),
        "tes_capacity_and_power_ready": all(
            float(tes[name]) > 0 for name in (
                "energy_capacity_max_kWh_th",
                "charge_capacity_max_kW_th",
                "discharge_capacity_max_kW_th",
            )
        ),
        "result_bundle_contract_ready": True,
        "research_boundary_use_allowed": capacity_payload["station_cost"].get(
            "research_solve_allowed"
        ) is True,
        "publication_parameters_verified": (
            capacity_payload["station_cost"].get("publication_ready") is True
            and all(item.station_fixed_capex_CNY > 0 for item in b1)
            and all(item.station_base_boundary.status == "teacher_confirmed_v2_scenario" for item in b1)
        ),
        "program_feasibility_input_ready": True,
        "economic_conclusion_input_ready": capacity_payload["station_cost"].get(
            "publication_ready"
        ) is True,
        "solver_instantiated": False,
        "note": "完成真实形状CaseBundle的B1经济投影和B2容量投影；本检查不构模、不求解。",
    }
    write_json(output / "ab_adapter_smoke.json", evidence)
    return evidence


def run_input_pipeline(command, config_path: Path, *, run_id=None, output_root=None, desktop_path=None):
    started = datetime.now(timezone.utc).isoformat()
    clock = time.monotonic()
    config = load_config(config_path)
    roots = resolve_guanggu_v03_source_roots(resolve_path(config["delivery_root"]))
    network_inputs = resolve_spatial_network_inputs(
        config["spatial_inputs"], roots.delivery_root
    )
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
    source_files = sorted(
        {p.resolve() for p in [*source_files, *network_inputs["source_paths"]]},
        key=lambda path: str(path).casefold(),
    )
    for path in source_files:
        resolved = path.resolve()
        if (
            not (
                resolved.is_relative_to(roots.scope_root)
                or resolved.is_relative_to(REPOSITORY_ROOT)
            )
            or "v0.1" in resolved.parts
        ):
            raise ValueError(f"禁止越界读取: {path}")
    before = {str(p.resolve()): sha256_file(p) for p in source_files}
    before[str(config_path.resolve())] = sha256_file(config_path)
    write_json(output / "input_hashes_before.json", before)
    write_json(output / "run_config.json", config)
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT, text=True).strip()
    profile = yaml.safe_load((PACKAGE_ROOT / "data/profile_resources/guanggu_v03.yaml").read_text(encoding="utf-8"))
    profile["economic_parameters_separate_gate"] = True
    profile["source_permission_policy"] = config["source_permission_policy"]
    profile_path = output / "source_profile.yaml"
    profile_path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    code_files = [REPOSITORY_ROOT / "run.py", *sorted((REPOSITORY_ROOT / "src/urbanheatopt").rglob("*.py"))]
    code_hashes = {str(p.relative_to(REPOSITORY_ROOT)): sha256_file(p) for p in code_files if p.is_file()}
    write_json(output / "code_provenance.json", {"git_sha":git_sha, "working_tree_status":subprocess.check_output(["git", "status", "--porcelain"], cwd=REPOSITORY_ROOT, text=True), "sha256":code_hashes})
    snapshot, source, adapted, bundle = None, None, None, None
    capacity_handoff = None
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
            snapshot = read_revised_package(roots.delivery_root / PACKAGE_DIRECTORY, config["economic_scenario"],
                                           source_permission_policy=config["source_permission_policy"],
                                           v2_parameter_scenario=config["v2_parameter_scenario"])
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
                capacity_handoff = prepare_research_boundaries(
                    adapted,
                    snapshot,
                    source.data_version,
                    authoritative,
                    delivery_root=roots.delivery_root,
                    spatial_spec=config["spatial_inputs"],
                )
                artifacts = []
                for role, path in (("buildings", adapted.buildings_path), ("building_archetype_map", adapted.archetype_map_path), ("loads", adapted.loads_path), ("external_timeseries", external_path), ("equipment_performance", adapted.equipment_performance_path), ("time_mapping", adapted.timestamp_hour_map_path), ("effective_parameters", output / "effective_parameters.json"), ("parameter_selection", output / "parameter_selection.csv")):
                    artifacts.append({"role": role, "path": str(path), "sha256": sha256_file(path)})
                for role, path in (
                    ("capacity_boundaries", capacity_handoff["capacity_boundaries_path"]),
                ):
                    artifacts.append({"role": role, "path": str(path), "sha256": sha256_file(path)})
                artifacts.extend(capacity_handoff["network_artifacts"])
                artifacts.extend(spatial["paths"])
                status["snapshot_complete"] = not errors
                bundle = CaseBundle.from_dict(dict(interface_version=INTERFACE_VERSION, data_version=source.data_version,
                    parameter_version=snapshot["snapshot_id"], git_sha=git_sha, artifacts=artifacts, source_hashes=before,
                    units={"heating_kW":"kW_th", "electricity_price_CNY_per_kWh_e":"CNY/kWh_e", "gas_price_CNY_per_kWh_LHV":"CNY/kWh_LHV", "time_weight_h_per_year":"h/year", "carbon":"kgCO2e/year"},
                    capabilities_required=["monthly_demand_charge", "effective_parameter_mapping", "site_capacity", "pipe_capacity", "tes", "result_bundle"],
                    status=status, physical_scope={"buildings":62, "hours":2160, "supply_C":45, "return_C":40, "peak_capacity_margin_fraction":.20}, spatial_status=spatial,
                    generated_spatial_status=capacity_handoff["spatial_status"],
                    economic_boundary={
                        "scenario": config["economic_scenario"],
                        "v2_parameter_scenario": config["v2_parameter_scenario"],
                        "tes_enabled": config["tes_enabled"],
                        "model_consumed": False,
                        "station_cost_boundary": snapshot["effective"]["station_cost_boundary"],
                        "station_cost_scenario": snapshot["effective"].get("station_cost_scenario"),
                        "station_fixed_capex_CNY": snapshot["effective"].get("station_capex_CNY"),
                        "program_feasibility_scope": snapshot.get("v2_freeze_patch", {}).get(
                            "program_feasibility_scope", False
                        ),
                        "economic_conclusion_scope": snapshot.get("v2_freeze_patch", {}).get(
                            "economic_conclusion_scope", False
                        ),
                        "publication_ready": snapshot.get("v2_freeze_patch", {}).get(
                            "allowed_for_primary_economic_conclusion", False
                        ),
                    }))
    except (ValueError, OSError) as exc:
        errors.append(str(exc))
    after_files = sorted(p for p in roots.delivery_root.rglob("*") if p.is_file())
    if roots.equipment_patch_root:
        after_files += sorted(p for p in roots.equipment_patch_root.rglob("*") if p.is_file())
    after_files = sorted(
        {p.resolve() for p in [*after_files, *network_inputs["source_paths"]]},
        key=lambda path: str(path).casefold(),
    )
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
    readiness = None
    if bundle is not None and not errors and capacity_handoff is not None:
        try:
            bundle.write(output / "case_bundle.json")
            requests = generate_solve_requests(
                bundle, output, tes_enabled=config["tes_enabled"]
            )
            evidence = run_ab_adapter_smoke(
                bundle, requests, capacity_handoff["capacity_boundaries"], output
            )
            road_build = build_road_case(bundle)
            road_case_path = output / "road_case.json"
            save_case(road_build.road_case, road_case_path)
            road_case_file_sha = sha256_file(road_case_path)
            road_build.report["road_case_file_sha256"] = road_case_file_sha
            road_build.report["road_case_path"] = str(road_case_path.resolve())
            write_json(output / "road_case_build_report.json", road_build.report)
            write_json(output / "road_case_hashes.json", {
                "builder_version": ROAD_CASE_BUILDER_VERSION,
                "case_bundle_id": bundle.bundle_id,
                "case_bundle_content_id": bundle.content_id,
                "road_case_content_sha256": road_build.report["road_case_content_sha256"],
                "road_case_file_sha256": road_case_file_sha,
                "network_sha256": road_build.report["network_sha256"],
                "parameter_version": bundle.to_dict()["parameter_version"],
            })
            evidence.update({
                "network_product_ready": True,
                "road_case_build_pass": road_build.ready,
                "road_case_builder_version": ROAD_CASE_BUILDER_VERSION,
                "road_case_content_sha256": road_build.report["road_case_content_sha256"],
                "solver_pipeline_ready": all(
                    request.to_dict()["model_profile"]
                    == MODEL_PROFILE
                    and request.to_dict()["optimization_scope"]
                    == "five_candidate_shortest_path_trees"
                    and request.to_dict()["allow_unserved"] is False
                    and request.to_dict()["tes_enabled"] is False
                    and request.to_dict()["objective"] == "cost"
                    for _, request, _ in requests
                ),
                "solve_request_executor_version": SOLVE_EXECUTOR_VERSION,
            })
            readiness = ready_report(bundle, integration_evidence=evidence)
            write_json(output / "model_readiness_report.json", readiness)
        except (ValueError, OSError) as exc:
            errors.append(f"A/B接口联调失败: {exc}")
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
                   **status,
                   input_ready=(readiness or {}).get("input_ready", False),
                   parameter_ready=(readiness or {}).get("parameter_ready", status["parameter_valid"]),
                   network_ready=(readiness or {}).get("network_ready", False),
                   road_case_ready=(readiness or {}).get("road_case_ready", False),
                   solver_pipeline_ready=(readiness or {}).get("solver_pipeline_ready", False),
                   model_capability_ready=(readiness or {}).get("model_capability_ready", False),
                   research_solve_ready=(readiness or {}).get("research_solve_ready", False),
                   program_feasibility_input_ready=(readiness or {}).get(
                       "program_feasibility_input_ready", False
                   ),
                   economic_conclusion_input_ready=(readiness or {}).get(
                       "economic_conclusion_input_ready", False
                   ),
                   publication_ready=(readiness or {}).get("publication_ready", False),
                   economic_result_reliable=False,
                   model_ready=(readiness or {}).get("model_ready", False), solver_executed=False, result_qualified=False,
                   input_hashes_unchanged=unchanged, input_file_count=len(source_files), errors=errors,
                   desktop_previous_backup=backup, exit_code=0 if passed else 2)
    if not (output / "model_readiness_report.json").exists():
        write_json(output / "model_readiness_report.json", {**status, "model_ready":False,
            "program_feasibility_input_ready":False,
            "economic_conclusion_input_ready":False,
            "economic_result_reliable":False,
            "solver_executed":False, "result_qualified":False,
            "blockers":errors + ["B消费新经济/需量/站址接口尚未接通", "C独立结果消费尚未接通"],
            "note":"没有合格CaseBundle，不实例化求解器"})
    write_json(output / "run_summary.json", summary)
    print("[4/4] 输入交接状态已记录；未执行模型求解", flush=True)
    return summary
