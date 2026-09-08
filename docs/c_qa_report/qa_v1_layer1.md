# C 独立复算 · V1(R3 冻结)Layer-1 小结

日期：2026-09-05 · 复算人：C · 数据版本：guanggu-v0.3-20260823（R3 冻结，62 栋 × 2160h，无 TES）
只读源：`release_assets/R3_extracted/COMPACT_FULLSEASON_V1_20260903_R3`
代码：`c_role_work/qa/recompute_v1.py`（仅读结果字节 + 归档 `case.json` 参数，不 import 任何模型代码）
机器 JSON：`c_role_work/qa/qa_v1_summary.json`

## 复算方法（独立于求解器）
- **运行期成本/碳**：从每个前沿点回放的 `dispatch_hourly.parquet` 重算逐时燃气(LHV)×气价 0.351391 CNY/kWh_LHV、气碳 0.201957 kgCO2e/kWh_LHV；热泵耗电与**管网泵耗(`network_hourly.parquet` 的 `pump_kW_e`)** 逐时 × 归档 TOU 电价、电碳 0.4044 kgCO2e/kWh_e。
- **投资年化**：`capacity_decisions.csv` 装机容量 × capex × CRF(discount=5%, lifetime)；固定运维 = 装机投资 × fixed_om_fraction。
- **平衡**：读 `node_balance_check.parquet` 逐节点/小时残差，max|·| ≤ 1e-6 kW。
- **交叉核对**：重算运行成本/碳 vs `compact_pareto_frontiers.json` 报告的 annual_real_cost / annual_operating_carbon。

## 结果（8 个合并前沿点全过）
| 检查 | 结果 |
|---|---|
| cost_breakdown 逐项加总 == 报告年化成本 | ✅ 差 = 0 |
| 设备投资年化复算(容量×capex×CRF) | ✅ 8/8 差 0.00% |
| 固定运维复算 | ✅ 8/8 差 0.00% |
| 燃气成本复算(dispatch×气价) | ✅ 8/8 差 0.00% |
| 电费复算(热泵+**泵耗**) | ✅ 8/8 差 ≤1e-6 |
| 运行物理碳复算(电+气) | ✅ 8/8 差 0.00% |
| 节点/小时热平衡 | ✅ max ≤ 4.4e-11 kW(阈值 1e-6) |

**判定：V1(R3)报告的 8 个前沿点成本/碳排/平衡,可用原始输出独立重建到浮点精度,未发现计算错误。**

## 发现与缺口(供 A/B / 后续层)
1. **电费口径**:账单电费 = 热泵耗电 + **管网泵耗电**。仅用 dispatch 复算会系统性偏低(本项目 cost 端约 -56~-60%),必须把 `network_hourly.pump_kW_e` 计入——这是 C 复算必含项,已在引擎内修正确认。纯分布式端点(h-site-03-carbon)无网泵,故 pump=0。
2. **管/站/接入投资** 暂未独立重算(Layer-2:需核对管线路由长度×双管单价"不重复×2"、站房固定 capex、接入 capex 口径);当前仅有"分项加总=总值"的自洽证据。
3. **每点未单独导出 carbon_breakdown.csv**(只有汇总碳):本次由 dispatch+碳因子独立推出;建议 A/B 后续在 `solution/` 增补 carbon_breakdown 便于审计。
4. **TES**:R3 无储热(`fallback_to_no_tes`),相关检查 N/A;待 B 的 V2 有/无 TES 对照再做配对。

---

## 更新:Engine 参数化 + Layer-2(管/站/接入投资)已并入

代码已升级为**参数化引擎** `c_role_work/qa/qa_engine.py`(接受任意符合 road_joint_v2 compact replay 布局的 run-root;不 import 模型代码):

```bash
python qa_engine.py <RESULT_ROOT> --label <名字>   # 输出 <out>/<名字>_qa.json
```

已在 **两个源** 上跑通(8 个合并前沿点,全部 0.0000% 一致):
- R3 冻结源:`release_assets/R3_extracted/COMPACT_FULLSEASON_V1_20260903_R3` → `out/v1_r3_qa.json`
- 复现目录:`runs/road_joint_v2/COMPACT_REPRO_20260905` → `out/v1_repro_qa.json`

**Layer-2 口径与结论**:
| 投资项 | 复算公式 | 结果(8/8) |
|---|---|---|
| 设备投资年化 | 装机容量×capex×CRF(discount 5%,寿命) | ✅ 0.00% |
| 固定运维 | 装机投资×fixed_om_fraction | ✅ 0.00% |
| **管网** | Σ建成边 `length_m × unit_cost_CNY_per_pair_route_m × CRF`(**双管已含,未再×2**) | ✅ 0.00% |
| **站房** | 启用站数×10000×CRF(30) | ✅ 0.00% |
| **接入** | Σ接网栋×每栋 1000×CRF(30)(与 access/管网不双算) | ✅ 0.00% |
| 储热 | 0(R3 无 TES) | ✅ |
| 总成本/总碳 | 各分项重加总 vs cost_breakdown/report | ✅ 差≈0 |

附加自洽:我复算的管网年化 vs 边表自带 `annualized_investment_CNY`(模型侧)逐点一致(0.000%),且**初始投资 = 长度×单价**核对无 ×2 错误;纯分布式点无管边(N/A)。

**V1(R3)完整复算结论:8 个前沿点的成本/碳排/平衡/四类投资,全部可用原始输出独立重建到浮点精度,未发现计算错误。**
