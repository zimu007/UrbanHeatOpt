"""Demo payloads for the Phase-2 scaffold (软著截图用).

All numbers are invented placeholders with realistic magnitudes; Phase 3/4
replace them with real production artifacts (ResultBundle, ParetoPoint, etc.).
"""
from __future__ import annotations

from typing import Any

from urbanheatopt.gui.contracts import KpiCardData

DEMO_BUNDLE_ID = "3f2a9c81d7e44b05a6f2c9b8e0d3a5174c9d2e01"
DEMO_RUN_ID = "B_COST_20260907_R1"
DEMO_OUTPUT_DIR = "runs/v2/B_COST_20260907_R1"
DEMO_MODE = "central"
DEMO_SELECTED_SITE = "G02"

# point_id, mode, labels, epsilon_tco2e, cost_wan_per_year, carbon_tco2e_per_year, is_knee
DEMO_PARETO_POINTS: tuple[tuple[Any, ...], ...] = (
    ("central-cost", "central", ("cost_endpoint",), None, 416.8, 2612.0, False),
    ("central-epsilon-001", "central", ("epsilon",), 2.05, 435.2, 1985.0, False),
    ("central-epsilon-002", "central", ("epsilon",), 1.52, 462.9, 1460.0, False),
    ("central-epsilon-003", "central", ("epsilon",), 1.10, 492.3, 1045.0, True),
    ("central-epsilon-004", "central", ("epsilon",), 0.88, 531.6, 862.0, False),
    ("central-epsilon-005", "central", ("epsilon",), 0.81, 588.4, 798.0, False),
    ("central-carbon", "central", ("carbon_endpoint",), None, 679.5, 782.0, False),
)

DEMO_KPIS: dict[str, Any] = {
    "best_cost_wan": 416.8,
    "min_carbon_tco2e": 782.4,
    "knee_point_id": "central-epsilon-003",
    "knee_cost_wan": 492.3,
    "knee_carbon_tco2e": 1045.0,
    "selected_site": DEMO_SELECTED_SITE,
    "certified_gap": 0.0082,
    "incumbent_wan": 492.3,
    "best_bound_wan": 496.4,
    "termination": "optimal",
    "qa_passed": True,
    "mode": DEMO_MODE,
}

DEMO_TABLE_HEADERS = ("候选站", "状态", "年化成本(万元)", "运行碳排(tCO2e)", "求解gap", "选中")

DEMO_TABLE_ROWS: tuple[tuple[str, ...], ...] = (
    ("G01", "qualified", "427.5", "1132.0", "0.94%", ""),
    ("G02", "qualified", "416.8", "1045.0", "0.82%", "★ 选中"),
    ("G03", "qualified", "449.2", "996.0", "1.12%", ""),
    ("G04", "qualified", "505.9", "861.0", "1.87%", ""),
    ("G05", "qualified", "588.4", "798.0", "3.02%", ""),
)

DEMO_VALIDATION_LOG: tuple[str, ...] = (
    "[09:12:03] 数据源复验：62 栋 × 2160 小时，SHA-256 一致",
    "[09:12:03] 参数有效性：0907 冻结参数通过（v2_freeze_0907）",
    "[09:12:04] 标准数据复验通过：无 NaN / Inf",
    "[09:12:04] 快照完整：case_bundle.json SHA-256 3f2a9c81d7e4…",
    "[09:12:04] 能力清单 5/5：需量费 / 参数映射 / TES / 站容量 / 管容量",
)

DEMO_SOLVER_LOG: tuple[str, ...] = (
    "[09:13:01] HiGHS 启动：threads=8, mip_gap=0.01, seed=0",
    "[09:13:41] G01: optimal (gap 0.94%)",
    "[09:14:22] G02: optimal (gap 0.82%)",
    "[09:15:07] G03: optimal (gap 1.12%)",
    "[09:15:51] G04: optimal (gap 1.87%)",
    "[09:16:36] G05: optimal (gap 3.02%)",
    "[09:16:36] 五树合成 gap 0.82% ≤ 阈值 1.00%：合格",
    "[09:16:37] ResultBundle 已写入 runs/v2/B_COST_20260907_R1/",
)

DEMO_QA_LOG: tuple[str, ...] = (
    "[09:17:02] 独立复算：成本 / 碳排逐项重算通过",
    "[09:17:02] 节点热平衡残差 ≤ 1e-6 kW",
    "[09:17:03] 无未供热负荷，模式约束通过",
    "[09:17:03] QA 结论：passed=true",
)

DEMO_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("input_snapshot", "pass", "输入快照校验通过"),
    ("parameter_valid", "pass", "0907 冻结参数通过"),
    ("canonical_valid", "pass", "标准数据复验通过"),
    ("snapshot_complete", "pass", "快照完整（4/4）"),
    ("capabilities", "pass", "能力清单 5/5"),
)

# DEMO_KPIS 的 KpiCardData 视图（演示态与 Mock 后端共用；单位换算：万元×1e4、t×1e3）。
DEMO_KPIS_CARD = KpiCardData(
    best_cost_cny_per_year=DEMO_KPIS["best_cost_wan"] * 1e4,
    min_carbon_kgco2e_per_year=DEMO_KPIS["min_carbon_tco2e"] * 1e3,
    knee_point_id=DEMO_KPIS["knee_point_id"],
    knee_cost_cny_per_year=DEMO_KPIS["knee_cost_wan"] * 1e4,
    knee_carbon_kgco2e_per_year=DEMO_KPIS["knee_carbon_tco2e"] * 1e3,
    selected_site_id=DEMO_KPIS["selected_site"],
    certified_gap=DEMO_KPIS["certified_gap"],
    termination_condition=DEMO_KPIS["termination"],
    qa_passed=True,
    solver_executed=True,
)

__all__ = [
    "DEMO_BUNDLE_ID",
    "DEMO_RUN_ID",
    "DEMO_OUTPUT_DIR",
    "DEMO_MODE",
    "DEMO_SELECTED_SITE",
    "DEMO_PARETO_POINTS",
    "DEMO_KPIS",
    "DEMO_TABLE_HEADERS",
    "DEMO_TABLE_ROWS",
    "DEMO_VALIDATION_LOG",
    "DEMO_SOLVER_LOG",
    "DEMO_QA_LOG",
    "DEMO_CHECKS",
    "DEMO_KPIS_CARD",
]
