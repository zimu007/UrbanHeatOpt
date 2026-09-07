# B组交接书：三层生产链后的端点、TES与膝点任务

版本：2026-09-07  
接口：`handoff_1.1.0`  
生产模型档案：`compact_five_tree_fullseason_v2`

## 1. 已交接、尚未交接

A侧已交接唯一生产链：版本化道路网络产物 → CaseBundle → RoadCase Builder → SolveRequest执行器 → 紧凑Pyomo核心 → HiGHS → 独立QA → ResultBundle。`run.py solve`不会读取历史`runs`补数据，也不会调用旧`model.run_model()`。

已由自动测试证明：三种模式可通过同一执行器构模、实解和导出；哈希不一致、输出目录已存在、候选站失败、gap超限或QA失败都会封闭失败。真实62栋×2160小时RoadCase已经过哈希重建，但“接线通过”不等于真实端点已经求完。

当前生产优化范围固定为5个候选站各自对应的确定性最短路树，集中式和混合式必须完成5个站点子任务才能认证该范围内的站点选择；它不是完整自由拓扑或施工级管网最优。

## 2. B组不得改变的输入边界

- 只读CaseBundle、RoadCase和自动生成的SolveRequest；不得手工拼RoadCase。
- 62栋、2160连续小时、同一负荷/气象/能源价格/碳因子和哈希。
- 设备角色固定为`central_hp`、`central_boiler`、`local_hp`、`tes`；锅炉统一LHV。
- 0907扩容校核管型表用于经济比较；DN物理容量只作审计。
- 站房基准300万元/座；1800万元是替换式压力情景，不叠加。
- 20%峰值容量裕度不含TES，不解释为N-1。
- 不调整参数来制造混合方案；全集中或全分布可以是正确最优结果。
- 不改字段、单位、ID、小时含义或接口版本；确需变更先交A重新编号。

## 3. 先完成成本端点验收

使用新prepare目录执行：

```powershell
conda run --no-capture-output -n urbanheatopt_env python run.py solve `
  --config configs/cases/guanggu_v2.yaml `
  --bundle "<prepare目录>\case_bundle.json" `
  --request-set cost-endpoints `
  --run-id "B_COST_YYYYMMDD_R1" `
  --output-root runs/v2
```

验收三模式各自`result_bundle.json`：有incumbent和best bound，合成gap≤1%，独立QA通过，输入/网络/参数/RoadCase/请求/核心均可追溯。集中式全接网，分布式不建设区域站网，混合式逐栋互斥。任一候选站失败时，该模式只能记“部分结果”，不得用4/5站结果称为范围最优。

## 4. 再生成碳端点与ε点

先在`src/urbanheatopt/data/integration.py`的请求工厂中增加版本化的碳请求生成，不手改JSON。碳端点保持同一RoadCase，设置：

```text
objective=carbon
epsilon_carbon_kg=null
tes_enabled=false
allow_unserved=false
```

成本、碳端点均合格后，每种模式只生成少量内部ε请求。每个ε点仍必须保留完整ResultBundle，而不是只保存成本/碳两个数。完成去重、非支配筛选后选择归一化膝点；端点只作边界证据。低碳区间是否加密由结果而不是预设点数决定。

## 5. TES配对

RoadCase已保存0907 TES能量、充热功率、放热功率、效率、损失和成本接口。首轮成本端点固定`tes_enabled=false`；完成端点后，在冻结相同站点、接网和树的条件下分别运行无TES/有TES配对。

必须导出并独立核验：TES实际能量容量、实际最大充/放功率、是否触及三个上限、2160小时SOC、首末循环、效率与损失、成本和碳排差额。TES不能抵扣峰值容量裕度。TES优化为零是允许结果，但请求未真正启用或变量未进入模型不算配对完成。

## 6. 结果资格与交给C的内容

每点至少交付：

- ResultBundle、run manifest、solver evidence及每候选站日志；
- 设备容量、逐时热/电/气调度；
- 站点、建筑接网、管网与容量结果；
- 成本、运行碳排和月度需量分项；
- TES容量/SOC（启用时）；
- 独立QA、gap、输入和代码哈希。

C组只能从导出明细复算并制作图表，不能读取目标总值冒充复算，也不能在绘图阶段改变决策。真实全季未求完前，状态只能是“三层软件接线完成”。

## 7. 推荐提交顺序

1. `完成新版三模式成本端点验收`
2. `增加同一RoadCase碳端点请求`
3. `完成少量epsilon点与膝点筛选`
4. `完成有无TES全季配对与独立QA`

每个节点记录命令、运行目录、Git SHA、输入/网络/RoadCase/请求哈希、求解状态、gap、QA和剩余阻塞；运行结果不提交Git。
