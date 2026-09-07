# C · V1(R3)独立 QA 与成果交付小结

日期 2026-09-06 · C(独立 QA/展示)· 数据 guanggu-v0.3-20260823 · 基线 compact-fullseason-v1-r3(62栋×2160h,无TES)
只读源:`cw/release_assets/R3_extracted/COMPACT_FULLSEASON_V1_20260903_R3`
全部产物:`cw/c_role_work/`(qa/、figures/)

## 1. 一句话结论
V1(R3)报告的成本/碳排/热平衡/设备·管网·站房·接入投资,**可由原始输出独立重建到浮点精度,未发现计算错误**;图册 **7/8 类**已出且逐张打开验收(剩 TES 配对,待 B 的 V2)。

## 2. 独立复算(机器 JSON 见 qa/out/v1_r3_qa.json,中文小结 qa/qa_v1_layer1.md)
方法:不 import 模型代码;仅读回放解 + 归档 case.json 参数。8 个非支配前沿点全部通过:
- 成本分项重加总=报告年化成本(差 0);设备投资年化/固定运维/管网(长度×双管单价,未×2)/站房(10000×CRF×启用数)/接入(1000×CRF×接网栋)复算均 0.00%
- 燃气/电费(含管网泵耗)/运行物理碳复算均 ~0.0000%
- 节点/小时热平衡 max 残差 ≤4.4e-11 kW(阈值 1e-6);未供热=0;MIP gap~1e-12 全 optimal

## 3. 已核实的口径陷阱(报告给 A/B)
1. **账单电费 = 热泵耗电 + 管网泵耗(`network_hourly.pump_kW_e`)**——仅用 dispatch 复算会偏低 1.4%~60%,C 复算必含泵耗。
2. 管网投资 `unit_cost_CNY_per_pair_route_m` 已含双管,**不得再×2**(已核对初始投资=长度×单价)。
3. R3 无 TES(`fallback_to_no_tes`),任何图不画储热收益;不能解读为"储热不经济"。
4. "per year"=冬季 2160h 运行量(权重和 2160),非 8760h。

## 4. 图册(7/8 类,PNG 300dpi + PDF/SVG 矢量;manifest: figures/figure_manifest.json)
| 图 | 覆盖 §3 类别 | 关键观测 |
|---|---|---|
| fig_pareto_combined_frontier | 三模式 Pareto 合并前沿 | 8 非支配点;成本端 17.7M↔碳端 4,483 tCO2e;膝点 hybrid ε-025 |
| fig_cost_carbon_breakdown | 成本/碳排分项 | 成本升→碳降;燃气份额让位电力 |
| fig_dispatch_representative_day | 逐时调度 | 峰值日(规则已标)成本端烧气 vs 碳端热泵抬升 |
| fig_heatmap_2160h | 2160h 热图 | 12→1→2月;热泵基底+锅炉峰值备用 |
| fig_qa_evidence | QA 证据 | 残差/gap/容量裕度≥1.2/复算差~浮点噪声,阈值均在图上 |
| fig_energy_flow | 能流 | COP≈2.97,环境取热 21,940 MWh 显式画出;供热守恒残差 0 |
| fig_gis_network | GIS 候选站/管网/接网 | 膝点 ε-025:62 栋 61 接网+1 本地;候选 389 边灰虚线 vs 建成 189 实线;4 候选站+1 选中站;EPSG:32650,附指北针/比例尺 |

样式:模式固定配色(集中蓝/分布橙/混合绿/膝点紫,已通过 CVD 色盲校验),Noto Sans CJK,高分辨率+矢量,每图脚注版本/单位/限制,同名字 .meta.json 记输入哈希/GitSHA/单位换算。
> 待补 1/8:**TES 配对**(等 B V2 有/无 TES 对照);GIS 已按"五固定站/固定树基线,非自由拓扑最优、无审批不称可施工"标注。

## 5. 请 A/B 确认(编号)
1. [A] V1 上报以 R3 归档还是复现目录(COMPACT_REPRO_20260905)为准?二者数值一致(0.000%)。
2. [A] ResultBundle 冻结时点/schema;新代码布局(src/urbanheatopt/{reporting,qa}/、configs/)归属。
3. [A] 数据缺口登记:IN_DATA 比冻结基线多 17 文件(20260831 经济包未接线)。
4. [B] 确认成本口径含泵耗;每点是否可补导出 carbon_breakdown.csv;distributed 仅 1 前沿点是否为预期。
5. [B] V2 交付时点(便于 C 排复算+绘图窗口);V2 需 TES 开启版本以做配对。

## 6. 复现工具(供后续)
- **`run_all.py [RESULT_ROOT]` —— 一条命令端到端**:QA 复算 → 碳分解 → 全部图(6 脚本/7 图)→ 重建 manifest/INDEX(R3 默认全量;任意 road_joint_v2 根跑 QA+碳分解)
- `qa/qa_engine.py <RESULT_ROOT> --label x` —— 任意 road_joint_v2 replay 根一键独立复算(Layer-1+2)
- `qa/carbon_decomposition.py` —— 碳按 central HP / local HP / pump / gas 独立分解(对应 B 可补导出的 carbon_breakdown)
- `qa/TES_matched_plan.md` —— TES OFF/ON matched 对比契约(待 V2 三组数据执行)
- `figures/make_prototypes.py` / `fig_dispatch.py` / `fig_heatmap_2160.py` / `fig_qa_evidence.py` / `fig_energy_flow.py` / `fig_gis_network.py` —— 各图再生成
- 建筑底图来源:`IN_DATA/原始输入数据/v0.2/0823代码组交付_光谷软件园_v0.3/03_buildings.geojson`(EPSG:4326→32650 投影),管网/站为结果内 geojson(EPSG:32650)

## 7. 0906 对接更新与 V2 分界(重要)
- **B 侧已确认**:①泵耗已计入总购电/电费/月度需量费/碳(与 C 复算的"HP+泵耗"口径一致;central-site-02-cost HP 电≈13.98万kWh、泵耗≈15.37万kWh 两边数字吻合);②carbon_breakdown 属低风险结果导出增强,模型/QA 已具 electricity/gas/total 分项,可拆至 central/local HP 与 pump;C 侧已独立产出分解(carbon_decomposition_v1.csv,8点与报告差~0),将来对拍 B 导出;③distributed 仅 1 点是任务设计(distributed-unique,0接网/62本地,被支配不入 global Pareto),非求解异常。
- **0906 参数已明确(属 V2 新口径,勿与上表 V1 数值混用)**:DN250/400/500 规划热容量 1283.2/4268.9/7593.3 kW_th;站房基础 300万/座、压力 1800万/座;0.026 不作 variable O&M;沿用 5 虚拟候选站。
- **当前仍不能正式跑 V2**:正式输入未闭合——每站 central HP/boiler 技术级独立容量上限(现仅站点总热容量公式)、62 栋 local HP 正式上限、electricity connection 的正式 limit 与 scope(是否含 pump/aux)、gas connection 逐站 kW_LHV 字段、TES 正式 max energy/charge/discharge、若干工程边界缺机读 source/status/evidence;另有已给数据需 **A 接进标准 CaseBundle/SolveRequest**(三档管径容量 research_reference 状态不符 schema、站房 300万 仍按 excluded_unseparated)。
- **C 分界**:上表全部为 **V1/历史旧参数**证据;**正式新版图与结论一律以 B 交付的 revised_20260831 三组(economic/knee/low-carbon)TES OFF/ON 2160h 为准**,数据未到前不提前定稿。
