# 光谷 62 栋×2160 h 原规模全季服务器运行手册

## 1. 当前状态

截至 2026-08-28，代码已完成服务器运行前准备，但尚未在 60 核服务器执行全季求解，因此不得宣称已有全季三模式或 Pareto 结果。

本地准备证据：

- 运行目录：`runs/guanggu_v03/v0_full_season/PREP_LOCAL_20260828T171500/`
- 源数据独立校验：通过，检查有效 v0.2 范围内 422 个文件；
- 标准数据：62 栋、2160 连续小时、133920 行负荷；
- 空间输入：5 个候选能源站、79 条 OSM 路侧概念候选管段；
- 任务计划：3 个线程基准、6 个端点、33 个 epsilon 点，共 39 个主任务；
- 求解器状态：未实例化，未产生全季优化结果；
- 核心模型冻结 SHA-256：`da9a29273e79a7c361fd8cd38b109023177500594168530942049a9812f8194d`。

空间输入固定标记：

```yaml
road_constrained: true
greenbelt_alignment_assumed: true
construction_feasibility_verified: false
spatial_status: PROVISIONAL_OSM_ROADSIDE_CORRIDOR
```

该空间网络用于验证站址、接网、管径和热流优化，不代表施工可实施方案。核心目前只允许建筑或站点作为管网节点，因此 OSM 交叉口采用确定性的“终端 Voronoi 商图”表达；共用走廊的造价和容量精度低于显式道路节点模型。

## 2. 服务器所需文件

以下内容必须复制到服务器，不能只克隆 Git 仓库：

1. 完整代码仓库及分支 `WH_heatOPT_li`；
2. `IN_DATA/原始输入数据/v0.2/` 全部有效数据；
3. 推荐复用冻结的 `osm_overpass_snapshot.json`，当前本地文件位于：
   `runs/guanggu_v03/v0_full_season/PREP_LOCAL_20260828T171500/spatial/osm_overpass_snapshot.json`。

OSM 快照没有提交 Git。若不复制快照，`prepare` 会只发送园区包围框和道路等级，从 Overpass 新下载一次，并记录新哈希。为了保证本地与服务器使用同一空间输入，推荐复制冻结快照。

## 3. 新环境检查

以下示例假设服务器仓库根目录记为 `<REPO>`，有效数据根目录记为 `<V02_ROOT>`，本次输出目录记为 `<RUN_ROOT>`。

```powershell
Set-Location <REPO>
git branch --show-current
git rev-parse HEAD
git status --short
conda run --no-capture-output -n urbanheatopt_env python scripts/check_environment.py
conda run --no-capture-output -n urbanheatopt_env python scripts/check_core_model_frozen.py
conda run --no-capture-output -n urbanheatopt_env python -m pytest -q
```

期望：分支正确、工作树无意外修改、环境检查通过、全量测试通过、核心哈希与冻结记录一致。若依赖缺失或核心哈希变化，停止，不启动全季任务。

## 4. 建立唯一全季运行目录

RUN_ID 必须唯一，已存在目录会被拒绝且不会覆盖。60 核服务器应显式传 `--physical-cores 60`。

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/server_full_season.py prepare `
  --delivery-root <V02_ROOT> `
  --run-root <RUN_ROOT> `
  --physical-cores 60 `
  --osm-snapshot <OSM_SNAPSHOT_JSON>
```

成功后核对：

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/server_full_season.py status `
  --run-root <RUN_ROOT>
```

此时应为 9 个待运行任务（3 基准+6 端点）和 33 个等待端点的 epsilon 任务。`task_plan.json` 必须记录 62 栋、2160 小时、输入哈希、Git 环境、Python/Pyomo/HighsPy 版本和核心模型哈希。

## 5. 串行执行 1/4/8 线程基准

三个基准必须串行，避免互相争抢 CPU 后得出错误结论：

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/server_full_season.py run `
  --run-root <RUN_ROOT> `
  --phase benchmark `
  --physical-cores 60 `
  --max-workers 1

conda run --no-capture-output -n urbanheatopt_env python scripts/server_full_season.py summarize-benchmarks `
  --run-root <RUN_ROOT>
```

`benchmark_comparison.json` 会验证三个任务的 MPS SHA-256 完全一致，并列出 incumbent、best bound、relative gap、耗时和内存峰值。应按同样 30 分钟内 gap/bound 改善速度选择 1、4 或 8 线程，不能只比较退出码。

将选定线程写入尚未开始的全部正式任务，例如选择 8 线程：

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/server_full_season.py select-threads `
  --run-root <RUN_ROOT> `
  --threads 8
```

## 6. 运行六个端点

根据基准记录的单任务内存峰值计算 `MEMORY_PER_TASK_GIB`，建议取实测峰值的 1.25 倍并向上取整。调度器使用：

```text
min(6, 物理核心数÷每任务线程数, 可用内存×70%÷单任务内存预算)
```

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/server_full_season.py run `
  --run-root <RUN_ROOT> `
  --phase endpoint `
  --physical-cores 60 `
  --memory-per-task-gib <MEMORY_PER_TASK_GIB>
```

初次每任务最长 6 小时、认证 gap 不超过 1%。若有任务未通过，可保留原 attempt、MPS、日志和证据，将未完成端点延长到 12 小时：

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/server_full_season.py extend-time `
  --run-root <RUN_ROOT> `
  --phase endpoint `
  --hours 12
```

随后重新执行同一 `run --phase endpoint` 命令。成功任务会跳过，失败任务会创建新的 attempt，不覆盖旧证据。

## 7. 运行 33 个 epsilon 点

只有六个端点全部通过后才能生成 epsilon：

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/server_full_season.py resolve-epsilon `
  --run-root <RUN_ROOT>

conda run --no-capture-output -n urbanheatopt_env python scripts/server_full_season.py run `
  --run-root <RUN_ROOT> `
  --phase epsilon `
  --physical-cores 60 `
  --memory-per-task-gib <MEMORY_PER_TASK_GIB>
```

未完成点可用 `extend-time --phase epsilon --hours 12` 延长，再重跑同一阶段。

39 个主任务全部通过后汇总：

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/server_full_season.py assemble `
  --run-root <RUN_ROOT>
```

## 8. 四类代表解的 0.1% 认证

政策碳约束值目前没有被擅自假定。应先由老师或项目组给出单位为 `kgCO2e/year` 的约束值，再执行：

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/server_full_season.py resolve-representatives `
  --run-root <RUN_ROOT> `
  --policy-carbon-constraint-kgco2e-per-year <CONFIRMED_VALUE>

conda run --no-capture-output -n urbanheatopt_env python scripts/server_full_season.py run `
  --run-root <RUN_ROOT> `
  --phase representative `
  --physical-cores 60 `
  --memory-per-task-gib <MEMORY_PER_TASK_GIB>

conda run --no-capture-output -n urbanheatopt_env python scripts/server_full_season.py verify-source-integrity `
  --run-root <RUN_ROOT> `
  --delivery-root <V02_ROOT>

conda run --no-capture-output -n urbanheatopt_env python scripts/server_full_season.py finalize-representatives `
  --run-root <RUN_ROOT>
```

最低成本、最低碳、归一化膝点和政策约束点均要求 gap 不超过 0.1%。若多条规则选中同一方案，只重新认证一次，并在四类映射中标记重合。
最终认证前会重新运行全部源输入校验并逐项比较准备阶段记录的422个SHA-256；不一致时最终认证会停止。

## 9. 任务证据和结果位置

每个任务目录：

```text
<RUN_ROOT>/tasks/<TASK_ID>/attempt_NNN/
```

至少包含：

- `model.mps`：独立数学模型；
- `solver.log`：HiGHS 完整日志；
- `solver_evidence.json`：incumbent、best bound、gap、耗时、线程、随机种子、内存峰值和MPS哈希；
- `point.json`：通过门槛后的成本、碳排和未供热摘要；
- `<RUN_ROOT>/solutions/<POINT_ID>/`：容量、逐时调度、站点、接网、管段、TES、成本、碳排和QA结果。

总表包括 `pareto_points.csv`、`combined_nondominated_frontier.csv`、`representative_solutions.json` 和 `certified_representative_solutions.json`。

## 10. 完成判定

只有以下内容全部成立，才能称为“光谷62栋×2160h全季源荷匹配与优化能力验证完成”：

- 39 个主任务全部达到 1% gap 门槛；
- 四类代表解达到 0.1% gap，重合规则已记录；
- 每个任务均为62栋×2160小时且核心哈希一致；
- 热平衡、未供热、TES循环、峰值容量裕度、管网连通/容量、成本和碳排独立复算全部通过；
- 输入运行前后哈希一致，未调用旧 `model.run_model()`；
- 图表可打开且与决策明细一致。

即使以上全部通过，由于能源价格、管损、泵耗和工程造价仍含暂定或零值，成果仍只能称为“优化能力验证结果”，不能作为最终工程投资或施工方案。

## 11. 常见失败解释

- `核心模型哈希不一致`：代码边界被改变，停止并审查差异；
- `任务目录已存在`：换新 RUN_ID，不删除或覆盖旧目录；
- `blocked_waiting_endpoints`：先完成全部六个端点；
- `failed_unaccepted`：求解未达到所需 gap，结果不会被加载或导出；
- `任务已被其他进程锁定`：确认对应进程是否仍运行，不要直接删除锁；
- `模型内存不足`：降低并行任务数或提高服务器内存，不改变数学模型；
- `OSM道路服务分量冲突`：补充或确认道路等级/走廊，不补欧氏捷径；
- 全部任务完成但缺政策代表解：补充经确认的政策碳约束值。

OpenStreetMap 数据署名与许可见 [OpenStreetMap Copyright and License](https://www.openstreetmap.org/copyright)；公共 Overpass 服务应单次下载后缓存，不应并行重复请求。
