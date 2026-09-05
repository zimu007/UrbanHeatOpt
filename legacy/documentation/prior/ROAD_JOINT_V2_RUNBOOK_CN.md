# 道路原子边V2：规划优化运行与验收手册

本轮定位是**源荷匹配与规划优化能力验证，不是施工图设计**。缺少施工审查、埋深、沟槽、产权等资料不阻止本轮运行。经济假设仍须显式记录；热平衡、容量、共享管段、TES、成本与碳排必须严格验算。

## 1. 当前主线和新规则

```text
v0.2自动发现 → v0.3/设备LHV补丁/经济包校验
→ 62栋×2160h标准数据复验 → planning_corridor_2.1.0候选网络
→ 道路V2唯一Builder → 三模式/ε-constraint → 导出和独立QA
```

- 允许现有主次干路、三级和普通道路，也可提供允许敷设走廊。取消45°和禁止跨路限制，不新增道路等级费用。
- 每栋最多3个边界接入候选，优化器只选其中一个；已建建筑保持叶节点，不能为其他建筑转接。共享路由先原子拆分，一次建设、一次双管路由计费。
- 62栋是负荷；自动读取的上游63栋GIS包含不供暖的数据中心，只用于障碍检查。缺少禁建区图层仅标注覆盖未知。
- 5个站是代码生成的**候选**，优化器最多建设1个；地图上的候选线不是已建结果。
- 沿道路的地下管网是规划概念；不模拟施工细节。geometry、路口流量和容量仍是真实数学约束，不可用“非工程比赛”绕过。
- 旧冻结核心不变，显式V2入口没有旧模型回退。历史12栋接入阻塞属于已废止几何政策，不能作为当前状态。
- 源校验、标准校验、空间校验、模型就绪、构模完成、求解完成和QA通过必须分开报告。准备通过不等于完成运行。

## 2. VS Code中准备完整62栋供暖季

打开 `D:\co_WH_heatOPT\UrbanHeatOpt`，在该目录的PowerShell终端执行：

```powershell
Set-Location 'D:\co_WH_heatOPT\UrbanHeatOpt'
conda run --no-capture-output -n urbanheatopt_env python scripts/check_environment.py

$roadRunId = 'ROAD_PLANNING_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
conda run --no-capture-output -n urbanheatopt_env python scripts/run_case.py `
  --delivery-root 'D:\co_WH_heatOPT\IN_DATA\原始输入数据\v0.2' `
  --source-profile guanggu_v03 --assumption-profile provisional_v0 `
  --core-version road_joint_v2 --profile v0-full-season `
  --osm-snapshot 'runs/guanggu_v03/v0_full_season/LOCAL_FULL_TRIAL_20260829T091000/spatial/osm_overpass_snapshot.json' `
  --output-root 'runs/road_joint_v2' --run-id $roadRunId --prepare-only
```

只读复用已有OSM快照，不联网，不读v0.1，不修改输入。其他电脑需复制同一授权输入和道路快照。运行ID必须全新。

可选参数：`--allowed-corridors <GeoJSON>`、`--forbidden-areas <GeoJSON>`、`--obstacle-buildings <GeoJSON>`。格式见 `docs/ROAD_ATOMIC_NETWORK_CONTRACT_CN.md`，不需要改Python字段。显式提供的文件不存在/格式错误会失败；没有禁建区资料不阻止本轮验证。

预期准备成功：Python退出0，`preparation_status.json`的source/canonical/spatial/model_ready为true，solver_executed仍为false。输出包含：
- validation：426个交付/补丁文件的库存、哈希、标准化输入与复验。
- effective_parameters.json：全部实际参数及来源、单位、暂定状态。
- spatial：原子节点/边/站GIS、candidate_access_options.csv、building_access_diagnostics.csv及preview候选地图。
- case.json：62栋×2160小时不可变模型输入。
- task_plan.json：6个端点+三模式各11个ε点，数学代码、参数、输入哈希冻结。

`v0-full-season`在这里表示全规模规划验证，不是缩减时段。`v1-full`名称未在这条任务链发布，不能借改标志冒称完成；无需为本轮补齐施工图资料。

## 3. 先运行一个完整端点，再运行其他任务

推荐使用带内存保护的单进程命令：

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/run_road_v2_guarded.py `
  --run-root "runs/road_joint_v2/$roadRunId" --task-id central-cost
```

- 所有62栋、2160小时、TES和管段决策保留；默认8线程，求解器6小时上限，扫描gap≤1%。
- 内存保护默认预算为启动时可用内存的75%，同时保留系统余量；`--memory-budget-gib`只可进一步降低本次预算，不会放宽75%上限。
- 监控只终止它自己启动的计算进程，不修改模型。保护触发属于资源未通过，不属于模型不可行，更不属于求解完成。
- 保存resource_monitor中的精确命令、内存时序、日志、退出码和停止原因；构模开始/完成标记在任务attempt目录。没有model_build_completed就不能说完整模型已建成；没有求解证据不能说求解器已开始。
- 可选`--max-wall-seconds`只控制本次安全监控最长时间，不改变求解器gap和模型。默认比求解器上限多1800秒，留给构模及MPS导出。
- 首个集中式成本端点通过后才解锁其他端点；此前不启动多进程。服务器并发数必须依据实测内存决定。

其他端点：central-carbon、distributed-cost、distributed-carbon、hybrid-cost、hybrid-carbon。六端点全部通过后才解锁central/distributed/hybrid的epsilon-000至epsilon-010，仍是39个完整模型任务。逐个将task-id替换即可。

全部扫描通过后：

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/run_road_v2_task.py `
  --run-root "runs/road_joint_v2/$roadRunId" --task-id assemble
conda run --no-capture-output -n urbanheatopt_env python scripts/run_road_v2_task.py `
  --run-root "runs/road_joint_v2/$roadRunId" --task-id refine
```

代表解按0.1%重新认证；政策碳上限未给定就标注缺失，不捏造第四个方案。提供上限使用`--policy-carbon-cap`，单位kgCO2e/year。规则重合明确记录，不人为凑四个不同方案。

## 4. 怎样确认结果可信

每个任务位于`tasks/<task-id>/attempt_XXXX/`：

- task_request.json记录62栋、2160小时、模式、ε及求解配置。
- model_build_started/completed.json分别证明构模尝试和完整构模；model.mps及SHA证明求解问题。
- solver.log、solver_evidence.json记录实际gap、上下界、时间和内存。
- solution/access_decisions.csv及GeoJSON记录每栋候选与实际选择；network_decisions及network_hourly记录唯一管段、容量、方向、端口热量、损失和泵耗。
- capacity、dispatch、building、station、storage记录真实决策变量；成本、碳排和节点平衡由导出明细独立复算。
- qa_summary.json须通过热平衡≤1e-6kW、成本/碳重算≤1e-6、连接/容量/储热/峰值裕度/未供热检查。
- 只有success.json及其哈希校验有效，才是该任务通过；单个任务通过不等于全季三模式Pareto完成。

已完成任务会校验后跳过；失败可用同命令产生新attempt，旧证据不删除。未收口进程的reservation不可手改，先检查实际进程。代码或输入变化必须重新准备RUN_ID，不能修改旧哈希“放行”。

费用仍沿用已批准经济包和显式测试值；LHV、45/40℃、20%裕度和公共时间权重不变。真实报价可后续补充以提高经济解释力，不影响现在检验数学和程序是否正确。

## 5. 测试、最新输出和排错

```powershell
$checkId = 'CHECK_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
conda run --no-capture-output -n urbanheatopt_env python scripts/record_road_v2_checks.py `
  --delivery-root 'D:\co_WH_heatOPT\IN_DATA\原始输入数据\v0.2' `
  --output-root "runs/road_joint_v2/$checkId" `
  --osm-snapshot 'runs/guanggu_v03/v0_full_season/LOCAL_FULL_TRIAL_20260829T091000/spatial/osm_overpass_snapshot.json'
```

检查器执行环境、全量测试、真实prepare-only和前后输入哈希。以该次verification_evidence.json和子命令退出码为准，不能用旧pytest缓存或历史结果替代。

本轮工作输出根目录：`D:\co_WH_heatOPT\UrbanHeatOpt\runs\road_joint_v2\PLANNING_20260830\`。其中baseline是修改前证据，其他子目录见本轮INDEX.md；它们不提交Git。桌面`缺失数据清单.md`由有效参数快照自动更新，原文件先保留时间戳备份；已解除的12栋几何缺口不再作为当前待补数据。

排错：
- 退出2：阅读具体输入/空间/资源/任务门禁证据；不是一概“模型错误”。
- 文件、字段、ID、时间、单位异常：纠正交付，不把空值补0。
- 内存保护：保留日志，关闭其他高占用程序或换更多内存机器；不删除建筑、小时或储热变量。
- 求解达到时限但无认证gap：保留可行解信息/上下界，延长资源需要新明确配置，不宣称最优。
- 独立QA失败：该结果不合格，不能仅凭optimal通过。
- 路径过长：使用已有短且唯一的runs/q_<ID>测试目录，不修改注册表。
