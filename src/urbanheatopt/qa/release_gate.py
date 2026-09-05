"""Read-only V1.0 release-gate assessment."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


FINAL_CONTRACT = "competition_input_3.0.0"
FORMAL_RELEASE = "UrbanHeatOpt-V1.0"
FULL_SEASON_HOURS = 2160


@dataclass(frozen=True, slots=True)
class ReleaseGateReport:
    passed: bool
    blockers: tuple[str, ...]
    checks: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "blockers": list(self.blockers),
            "checks": list(self.checks),
        }


def _read_json(path: Path, label: str, blockers: list[str]) -> dict[str, Any]:
    if not path.is_file():
        blockers.append(f"缺少 {label}：{path}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        blockers.append(f"{label} 无法解析：{exc}")
        return {}
    if not isinstance(payload, dict):
        blockers.append(f"{label} 顶层必须是对象")
        return {}
    return payload


def assess_v1_release(case_dir: str | Path, run_dir: str | Path) -> ReleaseGateReport:
    """Assess existing inputs/results without running a solver or modifying files."""

    case_root = Path(case_dir).resolve()
    run_root = Path(run_dir).resolve()
    blockers: list[str] = []
    checks: list[str] = []

    config_path = case_root / "case_config.yaml"
    if not config_path.is_file():
        blockers.append(f"缺少 case_config.yaml：{config_path}")
        config: dict[str, Any] = {}
    else:
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            blockers.append(f"case_config.yaml 无法解析：{exc}")
            config = {}
    if not isinstance(config, dict):
        blockers.append("case_config.yaml 顶层必须是对象")
        config = {}

    if config.get("contract_version") != FINAL_CONTRACT:
        blockers.append(
            f"输入契约尚未冻结为 {FINAL_CONTRACT}（当前：{config.get('contract_version')}）"
        )
    else:
        checks.append("最终输入契约版本正确")
    if config.get("run", {}).get("profile") != "v1-full":
        blockers.append("案例 profile 必须为 v1-full")
    if config.get("time", {}).get("complete_heating_season") is not True:
        blockers.append("案例必须声明 complete_heating_season=true")

    external_name = config.get("files", {}).get("external_timeseries")
    external_path = case_root / external_name if isinstance(external_name, str) else None
    if external_path is None or not external_path.is_file():
        blockers.append("缺少正式 external_timeseries.parquet")
    else:
        try:
            external = pd.read_parquet(external_path, columns=["timestamp", "time_weight_h_per_year"])
            if len(external) != FULL_SEASON_HOURS:
                blockers.append(
                    f"完整供暖季必须为 {FULL_SEASON_HOURS} 个逐小时点（当前：{len(external)}）"
                )
            elif not (external["time_weight_h_per_year"].astype(float) == 1.0).all():
                blockers.append("完整供暖季逐小时权重必须全部为 1 h/year")
            else:
                checks.append("2160 小时完整供暖季和权重通过")
        except Exception as exc:
            blockers.append(f"完整供暖季时序无法核验：{exc}")

    manifest = _read_json(run_root / "run_manifest.json", "run_manifest.json", blockers)
    if manifest:
        if manifest.get("software_release") != FORMAL_RELEASE:
            blockers.append(
                f"结果程序版本不是 {FORMAL_RELEASE}（当前：{manifest.get('software_release')}）"
            )
        if manifest.get("contract_version") != FINAL_CONTRACT:
            blockers.append("结果清单未使用冻结的 3.0.0 输入契约")
        if manifest.get("legacy_model_used") is not False:
            blockers.append("正式结果必须明确 legacy_model_used=false")
        incomplete_tokens = ("interface", "v0", "disabled", "not_executable")
        for name, status in manifest.get("capability_status", {}).items():
            normalized = str(status).lower()
            if any(token in normalized for token in incomplete_tokens):
                blockers.append(f"正式能力未通过：{name}={status}")

    qa = _read_json(run_root / "qa_summary.json", "qa_summary.json", blockers)
    if qa and qa.get("all_points_passed") is not True:
        blockers.append("并非所有正式 Pareto 点通过独立 QA")
    elif qa:
        checks.append("全部 Pareto 点独立 QA 通过")

    return ReleaseGateReport(not blockers, tuple(blockers), tuple(checks))
