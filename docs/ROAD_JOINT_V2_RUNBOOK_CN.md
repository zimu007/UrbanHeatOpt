# 道路原子边V2运行与验收手册

本页适用于`road_joint_v2`测试数学模型，不代表正式工程版。旧`competition/core_model.py`保留不变；旧结果与旧MPS不能作为V2成功标记。

## 1. 当前能够做什么

代码自动读取v0.2范围中的有效v0.3交付、设备LHV补丁和经济扩展包，依次执行：

```text
426文件库存/哈希 → 源校验 → 62栋×2160小时标准化复验
→ 经济参数ID/状态/单位/来源校验 → 有效参数快照和桌面缺口清单
→ 道路原子化与建筑支线几何校验 → 唯一V2 Builder
→ 新核心、独立端点与ε任务 → 决策导出 → 独立QA
```

本次真实流程在支线几何校验停止：12栋建筑的最近直线候选均未满足约束。未删除建筑、未放宽45°或穿楼规则、未退回欧氏最短路径边，也没有启动真实全季求解。这是当前生成器搜索能力的限制，不等于12栋不存在可行折线接入。

源数据通过、标准数据通过、空间通过、模型就绪、求解执行是不同状态。`preparation_status.json`只描述准备阶段；其中`solver_executed=false`不会被某个后续任务的成功改写。实际求解状态应看该任务的`success.json`、`solver_evidence.json`及`qa_summary.json`。

## 2. VS Code操作

打开`D:\co_WH_heatOPT\UrbanHeatOpt`文件夹，在PowerShell终端执行。不要在旧Codex worktree中运行，不需要编辑Python源文件。

```powershell
Set-Location 'D:\co_WH_heatOPT\UrbanHeatOpt'
conda run --no-capture-output -n urbanheatopt_env python scripts/check_environment.py
conda run --no-capture-output -n urbanheatopt_env python scripts/run_case.py --help
```

每次用新的运行ID，禁止复用已有目录：

```powershell
$roadRunId = 'ROAD_V2_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
conda run --no-capture-output -n urbanheatopt_env python scripts/run_case.py `
  --delivery-root 'D:\co_WH_heatOPT\IN_DATA\原始输入数据\v0.2' `
  --source-profile guanggu_v03 `
  --assumption-profile provisional_v0 `
  --core-version road_joint_v2 `
  --profile v0-full-season `
  --osm-snapshot 'runs/guanggu_v03/v0_full_season/LOCAL_FULL_TRIAL_20260829T091000/spatial/osm_overpass_snapshot.json' `
  --output-root 'runs/road_joint_v2' `
  --run-id $roadRunId `
  --prepare-only
```

OSM路径是本地已有、已批准的道路快照，不是旧核心MPS。该命令不联网、不上传数据、不运行优化。没有此快照的电脑必须先获得同一授权文件；不要用旧任务成功标记代替道路输入。

当前正确结果是Python返回`2`，并输出具体12栋ID/失败原因；不会产生`case.json`和全季任务。Conda某些版本会把子进程的`2`包装成自身退出码`1`，应核对子进程记录。Python的`1`代表程序异常，`0`仅代表本命令相应阶段通过，不等于全季完成。

`--core-version`不能省略：默认保留旧冻结竞赛核心的兼容入口，但**显式V2路径没有旧核心回退**。`v1-full`仍会阻止暂行经济/空间参数成为正式结论。

## 3. 输出在哪里

真实输入审计与几何阻塞的最新完整证据：

```text
D:\co_WH_heatOPT\UrbanHeatOpt\runs\road_joint_v2\IMPLEMENT_20260830\verification_signed\
  environment.log                    环境检查原始日志
  tests.log                          本次全量测试日志
  verification_evidence.json         命令、时间、退出码、输入完整性、测试产物路径
  source_hashes_before.json           426源文件SHA-256
  v2_input_gate.log                   真实公共入口的直接Python退出证据
  real_input_gate\
    preparation_status.json          各阶段真实状态与12栋失败清单
    effective_parameters.json        本次有效参数、来源、单位与版本哈希
    input_guidance_CN.md              源/标准数据校验详细说明
    validation\                     全季标准化快照、输入清单和复验报告
```

小案例图表、21个端点/ε任务、代表解复核、各任务MPS/日志/QA位于该次pytest产物的`test_task_guard_and_small_thre0/v2/`。精确路径以`verification_evidence.json`的`pytest_artifacts`为准，避免把旧测试结果冒充新证据。该案例是**2栋×2小时合成数据**，并非光谷62栋全季；另有62×2160合成数据的Builder形状测试，只验证没有丢行，不进行该规模优化。

桌面`C:\Users\leonl\Desktop\缺失数据清单.md`只列缺口。由代码依据本次有效参数快照和几何失败生成，已有同名文件先备份成时间戳`.bak.md`。目前含原25项待确认、7项额外字段/口径、12栋接入支线；不把日志和项目总结混在里面。

## 4. 几何通过之后如何运行

以下命令现在不应强行执行：当前真实准备未通过，没有合法V2任务计划。待确认接入办法、实现并通过空间测试后，重新准备生成新的运行目录。

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/run_road_v2_task.py `
  --run-root "runs/road_joint_v2/$roadRunId" --task-id central-cost
```

首先仅执行一个集中式成本端点，默认8线程、6小时、扫描gap≤1%。尚未实测新V2全季内存，不能预先宣称32核可以同时跑多少任务。只有此端点通过，其余任务才解锁。进程保留自己的工作预留记录，不能同时重复执行同一个逻辑任务。崩溃留下未收口预留时应检查进程和证据，不要手改成功状态。

其他端点ID：`central-carbon`、`distributed-cost`、`distributed-carbon`、`hybrid-cost`、`hybrid-carbon`。全部六端点通过后才允许每模式`epsilon-000`至`epsilon-010`，例如`hybrid-epsilon-010`。全季任务总计39个，未减少小时、建筑、储热变量或模式。任务CLI是显式单任务执行器；自动32核并发调度仍需在首任务内存测量后接入和验收，不承诺当前已有安全的多进程批跑。

每个任务有独立MPS、SHA-256、求解日志、gap/最优界/内存峰值、决策文件与独立QA。完成任务按输出哈希校验后跳过；失败重试保留旧`attempt_XXXX`。代码、参数、网络、原始输入或case哈希变化时拒绝旧计划，必须重建新目录。

扫描全部完成后：

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/run_road_v2_task.py `
  --run-root "runs/road_joint_v2/$roadRunId" --task-id assemble
conda run --no-capture-output -n urbanheatopt_env python scripts/run_road_v2_task.py `
  --run-root "runs/road_joint_v2/$roadRunId" --task-id refine
```

`assemble`形成三模式和合并非支配前沿、图表、暂选代表规则，不能代替0.1%复核。`refine`重新求解至gap≤0.1%，不复用旧解作为新证明。最低碳端点先求碳界，再在该界下最小化成本，避免同碳排下任意昂贵容量被当作有效代表。

尚未提供政策碳上限，因此真实运行的政策代表规则保持“未指定”，不编造第四个方案。获得上限后应在新的完整运行配置中使用`--policy-carbon-cap`，单位为`kgCO2e/year`（不是吨）；代码对该上限分别求三模式最低成本，再比较，不使用最近网格点替代。若某模式政策任务不可行会保留失败证据并停止认证，不能强行补数凑方案。

0.1%认证仅针对对应MILP目标，不意味着连续Pareto前沿或全局膝点具有0.1%误差。候选网络和经济假设仍是软件验证场景。

## 5. 结果与QA口径

每个`tasks/<task-id>/attempt_XXXX/solution/`输出：设备容量与逐时热出力/用能、建筑互斥接入、站点、TES容量/逐时SOC、原子管段静态表及GeoJSON、逐时两端热功率/方向/热损/泵耗/利用率、成本分项、碳排汇总、独立复算和节点热平衡。

- 一条物理边按双管路由米只计费一次，接入设施费用不重复包含支管。
- 测试容量档不是DN；`dn_mm`为空、`pipe_design_status=synthetic_capacity_only`。
- 中点有符号流与两端热功率分别导出；发送端含管损的功率受容量约束。
- 独立QA读导出明细与不可变输入，重算CRF、成本、碳排、路口守恒、容量、连通、方向、未供热、TES循环及20%峰值裕度。
- 正式输入仍62栋、2160个连续小时、每行1h；不做代表日、不减少TES。
- 价格沿用v0.3时序；新增8月价格/冬季倍率/3.712元m³和35.544MJ/Nm³只登记，不覆盖。LHV锅炉效率0.94。
- 新经济包采用8项已允许的设备参数，其余费用/管损/泵耗测试值显式存入快照。基本电费未纳入，不能说实际费用为零。

## 6. 复验与故障排查

```powershell
$checkId = 'CHECK_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
conda run --no-capture-output -n urbanheatopt_env python scripts/record_road_v2_checks.py `
  --delivery-root 'D:\co_WH_heatOPT\IN_DATA\原始输入数据\v0.2' `
  --output-root "runs/road_joint_v2/$checkId" `
  --osm-snapshot 'runs/guanggu_v03/v0_full_season/LOCAL_FULL_TRIAL_20260829T091000/spatial/osm_overpass_snapshot.json'
```

此命令记录环境、全量pytest、真实输入门禁、源哈希前后比对。测试含小模型求解；真实入口仅prepare-only。检查器返回0可能意味着“正确阻止未就绪真实网络”，务必阅读`v2_input_gate`子记录及`preparation_status.json`。

- WinError206/路径过长：本次首次深目录复验确实出现3项失败，原日志保留于`verification_final/`；现在测试使用短且唯一的`runs/q_<ID>`，不修改Windows注册表，不覆盖旧证据。
- 文件缺失/ID、单位、状态非法：修正交付，保留稳定参数ID，不直接改代码默认值或把空白补0。
- 几何失败：查看建筑ID/原因，提供或确认合法接入办法；不要删除建筑和关键道路来换成功。
- 求解未达到gap/独立QA失败：该任务不写success，不生成全季成功声明；保留MPS、日志、最优界与失败报告。
- 新参数/代码后旧任务被拒绝：这是预期哈希保护，生成新RUN_ID，不强行更新旧成功标记。

## 7. 待用户确认的唯一当前空间问题

是否允许建筑支线使用确定性避障折线（接入主次干路、入口≥45°、不穿楼、建筑保持叶节点）？建议允许并新增几何回归；另一途径是提供12栋的人工接入GeoJSON。确认前不会自行放宽规则。站点用地仍是走廊吸附测试点，空间与经济结果不得称为施工方案。
