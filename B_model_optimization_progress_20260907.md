# UrbanHeatOpt B模型与求解阶段进度

- 日期：2026-09-07
- 负责人：B
- 当前本地开发分支：`b-capacity-margin-basis-wip`
- B原始开发基线：`bc0931e41e2e5d7e1882e6d078aa27a779265bed`
- B代码提交：`a21056fa27855bf31ec285d31675ca12de89ca30`
- C远端提交：`4e91be99428828987ca8dff21e8fda0f6a225e1b`
- 本地merge提交：`a81e4d12bee529f2da3861d241d5e73220ab2be9`
- 当前本地HEAD：`a81e4d12bee529f2da3861d241d5e73220ab2be9`
- 当前远端 `origin/WH_heatOPT_li`：`4e91be99428828987ca8dff21e8fda0f6a225e1b`
- Git关系：本地 ahead 2 / behind 0；C提交已合入本地B分支，当前尚未push
- 本文性质：B模型与求解阶段的实现、测试和阻塞记录，不代表正式V2全季结果已经完成

## 1. B当前任务边界

B负责模型与求解消费层，主要范围包括：

- 消费A侧标准化的新经济字段，不另建输入体系；
- 月度园区虚拟总表最大需量费；
- 站址、技术允许性、设备容量、能源接入和管网容量约束；
- central / distributed / hybrid模式实现与实际模式诊断；
- 单栋边际接网和matched comparison诊断；
- TES模型、24小时研究单例和正式全季配对任务结构；
- V2经济端点、低碳端点、少量epsilon点及折中点所需的求解能力；
- ResultBundle所需模型输出、求解证据及C侧可复算的QA信息。

B不负责：

- 另建或绕过A输入体系；
- 私自修改`CaseBundle`、`SolveRequest`等公共schema；
- 为得到hybrid结果而倒推或调节参数；
- 替老师或参数组批准最终capacity-margin口径；
- 替C组给出最终图表结论；
- 修改V1冻结tag、结果、snapshot或历史口径。

## 2. 当前代码基础能力

当前仓库已经具备以下代码能力：

- 完整道路节点、道路边、建筑接入路径等网络产物；
- `CaseBundle`到`RoadCase`的正式builder；
- `SolveRequest`到solver再到`ResultBundle`的执行链；
- B1 revised_20260831经济字段的typed projection和模型消费接线；
- 2160小时绝对电价、LHV天然气价格/碳因子、CAPEX/FOM和TES经济参数接线；
- 月度园区虚拟总表最大需量费；
- B2站址、技术、设备、能源接入及管网容量接口与fail-closed门禁；
- B3 requested/realized mode分类、模式诊断和matched comparison结构；
- B4 2节点×24小时TES OFF/ON研究单例；
- B5 economic / knee / low-carbon三组TES OFF/ON严格配对任务结构；
- capacity-margin双口径的B侧显式模型能力。

上述内容表示“代码能力已经具备”。正式A输入链、版本证据和工程口径尚未全部闭合，因此不能写成“正式V2已经完成”。

## 3. 0907参数冻结补丁审计

### 3.1 当前已完成

- 对仓库根目录的0907补丁进行了只读解析和契约审计；
- 使用补丁中的真实冻结值完成B侧专项模型和回归验证；
- 未移动补丁、未修改CSV，也未修改delivery root。

### 3.2 正式输入链尚未完成

仓库根目录中的0907补丁尚未进入A侧正式：

`prepare → parameter snapshot → CaseBundle → RoadCase`

因此，“补丁已解析并用于B侧测试”不等于“正式prepare已经消费该补丁”。

### 3.3 补丁文件及正式语义

- `scenario_parameter_manifest.csv`：场景、状态和来源证据入口。
- `pipe_types_v2_debug.csv`：仅用于程序联调，不得通过正式经济结果门禁。
- `pipe_types_v2_expansion_check.csv`：用于正式经济比较；扩容投资和扩容热损必须进入模型。
- `pipe_capacity_limits.csv`：V2正式三档规划容量硬约束，分别为`39680.34`、`79360.69`、`158721.38 kW_th`。
- `pipe_capacity_engineering_reference.csv`：DN物理工程参考，不进入V2规划硬约束，也不用于正式可行性QA。
- `tes_limits.csv`：energy不超过`793606.88 kWh_th`，charge和discharge均不超过`132267.81 kW_th`。
- `station_cost_scenarios.csv`：baseline为`3,000,000 CNY/site`，stress为`18,000,000 CNY/site`；两个场景相互替换、不叠加，无中央站时费用为0。

其他边界：

- expansion pipe heat loss继续进入物理热平衡、源侧出力、能源费用、碳排和QA；
- 泵耗继续沿用20260831正式参数体系；
- 原v0.3.1的62栋、2160小时负荷与技术路线没有被0907补丁替换。

## 4. 两种管网容量语义必须隔离

| 文件 | 含义 | 在正式V2中的用途 |
| --- | --- | --- |
| `pipe_capacity_limits.csv` | V2规划容量 | 数学模型真实硬约束及正式可行性QA |
| `pipe_capacity_engineering_reference.csv` | DN物理容量 | 仅工程参考，不进入V2求解或正式可行性QA |

当前compact、road和QA的B侧专项验证使用前者。规划容量与DN物理工程参考不得混用。

## 5. 最新中央容量冲突诊断

冻结总容量上限为：

```text
central_hp_capacity + central_boiler_capacity
<= 158721.376739411 kW_th
```

road/compact旧口径同时要求：

```text
available_central_capacity
>= 1.20 × (connected_building_demand + network_heat_loss)
```

这组口径导致5个central候选站全部infeasible。根因是capacity-margin basis与冻结总容量定义发生冲突，而不是：

- 内存不足；
- HiGHS超时或solver能力不足；
- CaseBundle断链；
- builder缺失；
- executor缺失。

## 6. 本轮capacity_margin_basis实现

B没有代替老师决定最终口径，而是在B模型构造层显式支持：

1. `connected_building_useful_heat_demand`
2. `source_side_heat_demand_including_network_loss`

统一计算逻辑覆盖：

- `reference_core`；
- `road_core`；
- compact主容量裕度约束；
- compact capacity-hour筛选；
- compact dominance/pre-screen；
- capacity witness；
- compact audit；
- `road_results` QA。

门禁语义：

- `margin > 0`：basis缺失或未知均fail-closed；
- `margin = 0`：不建立专项capacity-margin约束，basis可以为`None`；普通逐时设备容量约束和热平衡仍然存在；
- 不提供会静默改变正式V2语义的默认basis。

计算示例：

| useful demand | network loss | margin | building-useful basis | source-side basis |
| ---: | ---: | ---: | ---: | ---: |
| 100 | 0 | 20% | 120 kW | 120 kW |
| 100 | 10 | 20% | 120 kW | 132 kW |

## 7. Network loss仍完整保留

无论最终选用哪一种capacity-margin basis，network loss仍进入：

- 网络逐时热平衡；
- 中央源侧热需求；
- HP和boiler出力；
- 燃气输入与中央购电；
- 管段规划容量及expansion heat loss；
- 泵耗、能源费用和运行碳排；
- QA与结果复核。

“改变capacity-margin basis”不等于“删除network loss”。两者属于不同数学边界。

## 8. TES当前能力与边界

已经验证：

- TES energy、charge power和discharge power三个上限；
- SOC首末循环；
- 0.95 / 0.95充放效率；
- 小时静置损失；
- 不允许免费初始能量；
- TES容量为0是合法优化结果；
- 正式executor不存在TES失败后自动回退为无TES的路径；
- TES不能抵扣中央设备20% capacity margin。

B4是2节点×24小时synthetic research case，显式使用source-side basis。这是研究假设，并有`formal_engineering_result=False`证据；它不是正式V2默认，也不能替代老师对最终basis的确认。

## 9. 本轮B代码修改

本地B提交：`a21056fa27855bf31ec285d31675ca12de89ca30`

| 文件 | 修改目的 |
| --- | --- |
| `src/urbanheatopt/model/reference_core.py` | 定义双口径常量和统一capacity-margin投影/校验函数 |
| `src/urbanheatopt/model/road_core.py` | 复用统一投影，保持road约束与reference语义一致 |
| `src/urbanheatopt/model/compact.py` | 统一主约束、筛选、dominance、witness和audit语义 |
| `src/urbanheatopt/qa/road_results.py` | 使QA按模型同一basis复核容量裕度 |
| `src/urbanheatopt/optimization/b4_tes_research.py` | 明确记录B4 source-side research assumption及非正式工程证据 |
| `tests/test_capacity_margin_basis.py` | 验证双口径、fail-closed、跨核心一致性和TES边界 |
| `tests/test_competition_core_model.py` | 验证reference核心正裕度口径 |
| `tests/test_road_v2_core.py` | 验证road核心、热损和容量裕度 |
| `tests/test_v2_freeze_0907.py` | 验证0907管容、TES、站房及场景冻结语义 |
| `tests/test_b4_tes_research.py` | 验证B4研究假设证据及TES研究案例 |

本轮没有修改：

- A-owned `CaseBundle` schema；
- `SolveRequest` schema；
- `road_builder`正式公共接口；
- `case.json`公共序列化；
- CSV或0907补丁；
- 冻结hash、tag或snapshot。

## 10. C提交与本地合并

C提交：`4e91be99428828987ca8dff21e8fda0f6a225e1b`，主要新增`docs/c_qa_report/`下8个独立QA、展示文档和Python脚本。

本地merge提交：`a81e4d12bee529f2da3861d241d5e73220ab2be9`。

合并结果：

- 无文本冲突，B/C没有修改同一个文件；
- C的8个文件和B的10个源码/测试文件均完整保留；
- 合并过程没有修改C文件内容；
- 合并后没有出现新的第三类测试失败。

协作待办：C的`V1_C_QA_DELIVERABLE.md`仍将`1283.2 / 4268.9 / 7593.3 kW_th`表述为V2规划热容量。0907当前语义应为：正式V2规划容量是`39680.34 / 79360.69 / 158721.38 kW_th`，旧DN物理容量仅作工程参考。这不是本次Git冲突，但C正式V2 QA前应更新。C当前正式V2 QA也尚未校验`capacity_margin_basis`证据，这是C后续适配项，不是B模型错误。

## 11. 测试证据

### 11.1 历史B专项阶段

结果：`237 passed`。

### 11.2 B-owned核心判断

排除明确A接口阻塞后：`214 passed`。该集合仅用于判断B-owned failure，不能称为完整测试。结论：`B-owned failures = 0`。

### 11.3 合并后B专项

- 270 passed
- 2 failed
- 16 warnings

两项失败为`test_b2_boundary_survives_case_round_trip`和`test_monthly_demand_charge_survives_case_round_trip`，均属于A接口尚未序列化`capacity_margin_basis`的既有阻塞。B-owned failures仍为0。

### 11.4 合并后完整pytest

命令：`python -m pytest -q`

- 683 passed
- 27 failed
- 0 skipped
- 0 xfailed
- 43 warnings
- 43.66秒

完整pytest状态是`BLOCKED / NOT PASS`，不能将237、214、270或683描述为完整测试通过。

## 12. 27项完整pytest失败分类

### 12.1 A接口/序列化阻塞：8项

1. `test_b2_boundary_survives_case_round_trip`
2. `test_monthly_demand_charge_survives_case_round_trip`
3. `test_real_small_distributed_task_closes_solver_export_and_qa_loop`
4. `test_immutable_serialization_and_old_hash_rejected`
5. `test_full_shape_builder_uses_all_synthetic_62_by_2160_rows`
6. `test_multi_candidate_generator_runs_tiny_end_to_end`
7. `test_v3_smoke_pipeline_is_read_only_complete_and_deterministic`
8. `test_wuhan_v02_pipeline_exports_snapshot_pareto_and_qa`

共同原因：builder未传`capacity_margin_basis`，或`case.json`保存/加载过程丢失basis；正裕度输入按设计fail-closed。

### 12.2 版本/frozen hash阻塞：19项

主要涉及`full_season_server`、`road_v2_tasks`、layout mathematical drift、reference/compact frozen mathematical hash及其他task/version guard。B修改了数学核心，冻结门禁按设计检测到变化；没有更新旧hash、tag或snapshot让测试假通过。

## 13. V1/V2隔离

- V1历史tag、result和snapshot未修改；
- 最新B代码不能冒充V1冻结实现；
- V1继续使用冻结tag或对应commit回放；
- V2需要新的model、builder/request、evidence及hash/snapshot版本；
- 不通过隐式basis默认兼容V1。

## 14. A侧当前缺口

1. 在正式公共接口中提供`capacity_margin_basis`；
2. 完成`CaseBundle → RoadCase/CoreModelInput`接线；
3. 如需由`SolveRequest`记录口径，完成正式契约；
4. 完成`case.json`序列化与反序列化；
5. readiness/evidence记录basis值、来源、hash和version；
6. 将0907补丁纳入正式`prepare → snapshot → CaseBundle`链；
7. 让正式builder和tiny pipeline显式传递basis；
8. 制定新V2 model/hash冻结策略。

## 15. 参数组/老师仍待确认

已冻结或验证：V2规划pipe capacity、DN capacity仅作工程参考、TES limits、station costs、formal/debug pipe场景以及expansion cost/loss。

仍未正式确认的关键事项是20%中央设备capacity-margin basis最终采用：

- A：`connected_building_useful_heat_demand`；或
- B：`source_side_heat_demand_including_network_loss`。

0907补丁的Q_design来源强烈显示`1.20 × building peak`，但没有正式机器字段明确说明network loss是否进入basis，因此B没有自行拍板。

## 16. 当前Git状态

- 当前本地分支：`b-capacity-margin-basis-wip`
- 当前本地HEAD：`a81e4d12bee529f2da3861d241d5e73220ab2be9`
- 当前最近确认远端`origin/WH_heatOPT_li`：`4e91be99428828987ca8dff21e8fda0f6a225e1b`
- 当前关系：ahead 2 / behind 0
- 本地比远端多B提交`a21056f`和merge提交`a81e4d1`
- 当前尚未push，B成果尚未进入远端`WH_heatOPT_li`

## 17. 当前GO / NO-GO

| 事项 | 状态 | 原因 |
| --- | --- | --- |
| B双口径模型 | GO | 双口径及统一计算链已实现 |
| B-owned tests | GO | B-owned failures为0 |
| 本地B/C整合 | GO | 无冲突，双方内容完整保留 |
| push `WH_heatOPT_li` | PENDING FINAL SAFETY CHECK | 文档尚未提交，需先完成最终安全检查 |
| 正式2160h | NO-GO | A接口、正式输入和最终basis未闭合 |
| central五站 | NO-GO | 同上 |
| 三模式成本端点 | NO-GO | 同上 |
| epsilon/Pareto | NO-GO | 正式端点尚未完成 |
| 三组TES正式全季 | NO-GO | 正式代表点及门禁尚未完成 |

## 18. 下一步计划

1. 更新并核查本进度MD；
2. 完成push前安全检查；
3. 如安全，提交本MD并fast-forward push本地B+C结果到`WH_heatOPT_li`；
4. A正式接入`capacity_margin_basis`；
5. A正式接入0907 prepare/snapshot；
6. 老师确认最终basis；
7. B完成integration和小案例等价测试；
8. 求解central五站；
9. 求解三模式成本端点；
10. 求解碳端点；
11. 运行少量epsilon；
12. 去重形成Pareto并识别knee；
13. 对economic/knee/low-carbon执行TES OFF/ON严格配对；
14. 交付C独立QA和展示。

不扩大研究范围，不做全部站点×全部参数×大量epsilon扫描，不为得到hybrid倒推参数。

## 19. 当前阶段结论

- B双口径能力已完成，B-owned failures为0；
- C提交已经安全合入本地，merge没有引入新失败；
- 0907补丁已用于B侧专项回归，但正式A链尚未消费仓库根目录补丁；
- 完整pytest仍有27项integration/version blocker；
- 老师尚未确认最终capacity-margin basis；
- 当前尚未push；
- 尚未运行新的2160小时正式三模式、Pareto或TES全季任务。
