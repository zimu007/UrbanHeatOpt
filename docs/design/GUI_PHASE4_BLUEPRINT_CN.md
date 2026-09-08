# 阶段四设计说明：真实结果可视化与导出

- 状态：已实现并验证（2026-09-07）
- 依赖：PySide6 6.11.1 + Matplotlib（PDF/PNG）+ openpyxl 3.1.5（Excel）
- 范围确认：**先可视化（真实结果直接上屏），Pareto 扫描流程后续再做**；Excel 用 openpyxl（用户选定）

## 1. 数据流

```
ResultBundle + runs/gui_solves/<run_id>/ 产物
  → backends.build_outcome()
  → SolveOutcome{ kpis, pareto(站点散点), table_rows, summary, artifacts }
      ├─ MainWindow._draw_outcome() → VisualCanvas.draw_sites()/KPI/明细表（界面）
      └─ exporters.export_pdf/export_excel/export_png（文件导出）
```

## 2. 真实结果可视化

- 单次求解只产出**一个真实数据点**（distributed 1 站；central 5 站），严格
  Pareto 前沿需多次求解合成 → 本期按用户决策落地**候选站方案对比散点**：
  - x = 年运行碳排 (tCO2e/年)，y = 年化真实成本 (万元/年)
  - 每个 qualified 站点一个蓝点 + 直接标注「站 NN」
  - 选中站（selected_site_id）以 ★ 高亮 + 「★ 选中站」标注（即膝点语义）
  - 单点/多点通用边距：跨度为零时按数值 10% 保底，避免 xlim 退化
- `ParetoPointView` 复用既有契约结构：labels=("site",) 标记真实站点；
  Mock 演示端点 labels=("cost_endpoint"|"carbon_endpoint"|"epsilon",) 走
  draw_pareto —— `MainWindow._draw_outcome` 按 labels 自动分流
- KPI 卡统一消费 `KpiCardData`（kpi.py load_kpis 改造）；真实路径
  min_carbon/knee_* 由 qualified 站点最值/选中站值填充
- 图头文案随态切换：演示「Pareto 最优前沿 · 成本/碳端点/ε 点 · 膝点高亮」；
  真实「候选站方案对比 · 已认证候选站 · 选中站」

## 3. 导出三件套（exporters.py，零 GUI 依赖，agg 后端）

| 导出 | 实现 | 内容 | 验证 |
|---|---|---|---|
| QA 报告 PDF | matplotlib PdfPages | 3 页：关键指标与证据 / 站点对比图 / 方案明细表（精选 8 列） | 页数=3、大小>10KB |
| 方案明细 Excel | openpyxl | 3 工作表：方案明细（CSV 透传）/ 求解摘要（嵌套展平）/ QA 结果（independent_qa.json，passed 行状态色） | sheet 名、表头、行数、passed 行 |
| Pareto 图 PNG | matplotlib savefig | 候选站对比散点，150 dpi，约 1500×900 | PNG 头签名 + IHDR 尺寸 |

- 文件名约定 `{run_id}_qa_report.pdf / _site_detail.xlsx / _pareto.png`
- 默认落盘 `runs/gui_exports/`（文件对话框可改）
- 导出菜单仅求解完成后启用；失败仅 QMessageBox 提示不崩界面
- openpyxl 颜色要求 aRGB 无 `#` 前缀（`_rgb()` 适配 dataviz 色板 token）

## 4. 验证记录（2026-09-07）

- verify_phase4（27 项）：build_outcome 数据断言 8 项；三导出产物结构/内容
  校验 11 项；GUI 集成（表格/KPI/菜单/三导出对话框路径/demo 回归）8 项 —— 全部通过
- verify_layout（18 项）：全部通过（无回归）
- verify_phase3（24 项）：全链路回归重跑通过（真实 validate + Mock + 终止 + 真实分布式求解）
- 已知限制：真实数据为单点对比（distributed），central 模式 5 站点对比待
  central 实跑验证；Pareto 扫描（多目标/ε 序列合成前沿）为后续迭代

## 5. 修订记录

| 日期 | 内容 |
|---|---|
| 2026-09-07 | 阶段四落地：真实结果可视化 + 三件套导出 + 27 项冒烟 |
