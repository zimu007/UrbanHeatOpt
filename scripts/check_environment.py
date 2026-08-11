"""Verify the pinned UrbanHeatOpt competition development environment.

This command is deliberately read-only with respect to the repository.  Its
Parquet test uses an operating-system temporary directory that is removed when
the check finishes.
"""

from __future__ import annotations

import json
import os
import sys
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory


EXPECTED_ENVIRONMENT = "urbanheatopt_env"
EXPECTED_VERSIONS = {
    "Python": "3.12.2",
    "fiona": "1.10.1",
    "geopandas": "1.0.1",
    "highspy": "1.11.0",
    "jsonschema": "4.26.0",
    "networkx": "3.6.1",
    "numpy": "2.1.3",
    "pandas": "2.2.2",
    "pyarrow": "25.0.0",
    "pyomo": "6.8.2",
    "pyproj": "3.7.2",
    "pytest": "9.1.1",
    "scikit-learn": "1.9.0",
    "scipy": "1.18.0",
    "shapely": "2.1.2",
}
IMPORT_NAMES = {
    "fiona": "fiona",
    "geopandas": "geopandas",
    "highspy": "highspy",
    "jsonschema": "jsonschema",
    "networkx": "networkx",
    "numpy": "numpy",
    "pandas": "pandas",
    "pyarrow": "pyarrow",
    "pyomo": "pyomo",
    "pyproj": "pyproj",
    "pytest": "pytest",
    "scikit-learn": "sklearn",
    "scipy": "scipy",
    "shapely": "shapely",
}
CPU_CONDA_PACKAGES = {
    "libarrow": "25.0.0",
    "libparquet": "25.0.0",
    "pyarrow-core": "25.0.0",
}
CONDA_FORGE_PACKAGES = {
    "openssl": "3.6.3",
    "pyarrow": "25.0.0",
    "pytest": "9.1.1",
    "jsonschema": "4.26.0",
    **CPU_CONDA_PACKAGES,
}


def _pass(message: str) -> None:
    print(f"[通过] {message}")


def _fail(errors: list[str], message: str) -> None:
    errors.append(message)
    print(f"[失败] {message}")


def _check_versions(errors: list[str]) -> None:
    actual_python = ".".join(map(str, sys.version_info[:3]))
    if actual_python == EXPECTED_VERSIONS["Python"]:
        _pass(f"Python={actual_python}")
    else:
        _fail(
            errors,
            f"Python 版本应为 {EXPECTED_VERSIONS['Python']}，实际为 {actual_python}",
        )

    for distribution, expected in EXPECTED_VERSIONS.items():
        if distribution == "Python":
            continue
        try:
            actual = version(distribution)
            import_module(IMPORT_NAMES[distribution])
        except Exception as exc:  # pragma: no cover - diagnostic boundary
            _fail(errors, f"{distribution} 无法导入或缺失：{exc}")
            continue
        if actual == expected:
            _pass(f"{distribution}={actual}")
        else:
            _fail(errors, f"{distribution} 应为 {expected}，实际为 {actual}")


def _conda_record(package_name: str) -> dict[str, object] | None:
    metadata_dir = Path(sys.prefix) / "conda-meta"
    if not metadata_dir.is_dir():
        return None
    for record_path in metadata_dir.glob("*.json"):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if record.get("name") == package_name:
            return record
    return None


def _check_cpu_arrow(errors: list[str]) -> None:
    for package_name, expected_version in CPU_CONDA_PACKAGES.items():
        record = _conda_record(package_name)
        if record is None:
            _fail(errors, f"缺少 {package_name} 的 Conda 元数据，无法确认 CPU 构建")
            continue
        actual_version = str(record.get("version", ""))
        build = str(record.get("build", ""))
        if actual_version != expected_version or not build.endswith("_cpu"):
            _fail(
                errors,
                f"{package_name} 应为 {expected_version} 的 *_cpu 构建，实际为 "
                f"{actual_version}={build}",
            )
            continue
        _pass(f"{package_name}={actual_version}={build}（CPU）")


def _check_conda_sources(errors: list[str]) -> None:
    for package_name, expected_version in CONDA_FORGE_PACKAGES.items():
        record = _conda_record(package_name)
        if record is None:
            _fail(errors, f"缺少 {package_name} 的 Conda 元数据")
            continue
        actual_version = str(record.get("version", ""))
        channel = str(record.get("channel", ""))
        if actual_version != expected_version or "conda-forge" not in channel:
            _fail(
                errors,
                f"{package_name} 应来自 conda-forge 且版本为 {expected_version}，"
                f"实际为 {actual_version}（{channel or '未知来源'}）",
            )
            continue
        _pass(f"{package_name} 来源=conda-forge")

    metadata_dir = Path(sys.prefix) / "conda-meta"
    cuda_arrow_records: list[str] = []
    for record_path in metadata_dir.glob("*.json"):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        name = str(record.get("name", ""))
        build = str(record.get("build", ""))
        if (name.startswith("libarrow") or name.startswith("pyarrow")) and (
            "cuda" in name.lower() or "cuda" in build.lower()
        ):
            cuda_arrow_records.append(f"{name}={build}")
    if cuda_arrow_records:
        _fail(errors, f"发现 Arrow CUDA 构建：{', '.join(cuda_arrow_records)}")
    else:
        _pass("未发现 Arrow CUDA 构建")


def _check_activation(errors: list[str]) -> None:
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix and Path(conda_prefix).resolve() == Path(sys.prefix).resolve():
        _pass("CONDA_PREFIX 与当前解释器前缀一致")
    else:
        _fail(
            errors,
            "环境未通过 conda activate 或 conda run 正确激活；"
            "请勿直接调用环境目录内的 python.exe",
        )

    for variable in ("GDAL_DATA", "PROJ_DATA"):
        configured = os.environ.get(variable)
        if configured and Path(configured).is_dir():
            _pass(f"{variable}={configured}")
        else:
            _fail(errors, f"{variable} 未设置为有效目录，GIS 读取可能不完整")


def _check_parquet_roundtrip(errors: list[str]) -> None:
    try:
        import pandas as pd

        source = pd.DataFrame(
            {
                "timestamp": pd.date_range(
                    "2026-01-01 00:00:00",
                    periods=3,
                    freq="h",
                    tz="Asia/Shanghai",
                ),
                "building_id": ["B001", "B001", "B001"],
                "heating_kW": [10.25, 11.5, 9.75],
            }
        )
        with TemporaryDirectory(prefix="urbanheatopt_parquet_") as temporary:
            output_path = Path(temporary) / "roundtrip.parquet"
            source.to_parquet(output_path, engine="pyarrow", index=False)
            restored = pd.read_parquet(output_path, engine="pyarrow")
        pd.testing.assert_frame_equal(
            source,
            restored,
            check_dtype=True,
            check_exact=True,
        )
    except Exception as exc:  # pragma: no cover - diagnostic boundary
        _fail(errors, f"带时区 Parquet 往返失败：{exc}")
        return
    _pass("带 Asia/Shanghai 时区的 Parquet 精确往返")


def _check_highs(errors: list[str]) -> None:
    try:
        from pyomo.contrib.appsi.base import TerminationCondition
        from pyomo.contrib.appsi.solvers import Highs
        from pyomo.environ import (
            ConcreteModel,
            Constraint,
            NonNegativeReals,
            Objective,
            Var,
            value,
        )

        model = ConcreteModel()
        model.x = Var(domain=NonNegativeReals)
        model.minimum = Constraint(expr=model.x >= 1.0)
        model.objective = Objective(expr=model.x)
        solver = Highs()
        availability = solver.available()
        if not bool(availability):
            _fail(errors, f"APPSI HiGHS 不可用：{availability}")
            return
        results = solver.solve(model)
        objective = float(value(model.objective))
    except Exception as exc:  # pragma: no cover - diagnostic boundary
        _fail(errors, f"APPSI HiGHS 最小模型求解失败：{exc}")
        return
    if results.termination_condition != TerminationCondition.optimal:
        _fail(errors, f"HiGHS 未返回 optimal：{results.termination_condition}")
        return
    if abs(objective - 1.0) > 1e-12:
        _fail(errors, f"HiGHS 最小模型目标值应为 1，实际为 {objective}")
        return
    _pass(f"APPSI HiGHS 可用并返回 optimal（目标值={objective:.12f}）")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    errors: list[str] = []
    print(f"Python 可执行文件：{sys.executable}")
    if Path(sys.prefix).name == EXPECTED_ENVIRONMENT:
        _pass(f"Conda 环境={EXPECTED_ENVIRONMENT}")
    else:
        _fail(
            errors,
            f"应在 {EXPECTED_ENVIRONMENT} 中运行，当前前缀为 {sys.prefix}",
        )

    _check_activation(errors)
    _check_versions(errors)
    _check_cpu_arrow(errors)
    _check_conda_sources(errors)
    _check_parquet_roundtrip(errors)
    _check_highs(errors)

    if errors:
        print(f"环境检查失败：共 {len(errors)} 项。", file=sys.stderr)
        return 1
    print("环境检查通过：版本、CPU PyArrow、Parquet 和 HiGHS 均符合当前竞赛基线。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
