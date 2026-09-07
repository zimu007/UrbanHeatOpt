# C · 独立 QA 与成果展示 —— 交付目录

作者：C（独立 QA/展示）· 2026-09-07 · 数据 guanggu-v0.3-20260823 · 基线 compact-fullseason-v1-r3（62栋×2160h，无TES）

> 状态说明：**当前仅 V1 完成**；V2（revised_20260831 正式结果）尚未产生，
> 故本目录只含 V1 复核/图结论与"读 ResultBundle 的消费适配器（自检通过）"。
> 正式 V2 的 QA 与图、TES 配对，待 B 交付真实 `result_bundle.json` 后执行。

## 目录内容
| 文件 | 说明 |
|---|---|
| `V1_C_QA_DELIVERABLE.md` | V1 交付小结：复核结论、7/8 图、口径陷阱、请 A/B 确认清单、0906 对接状态与 V2 分界 |
| `qa_v1_layer1.md` | 独立复算(Layer-1+2)方法与结果：8 前沿点成本/碳/平衡可浮点精度重建，未发现计算错误 |
| `schema_v1_inventory.md` | V1(R3)结果目录字段级 schema 盘点与口径陷阱 |
| `TES_matched_plan.md` | TES OFF/ON matched 对比复算契约（待 V2 有/无 TES 数据执行） |
| `scripts/qa_engine.py` | 独立复算引擎（Layer-1+2），接受任意 road_joint_v2 replay 根 |
| `scripts/carbon_decomposition.py` | 碳按 central/local HP + pump + gas 独立分解（对拍 B 的 carbon_breakdown 导出） |
| `scripts/resultbundle_consumer.py` | C 消费 B 的 ResultBundle：信封/哈希/qualified 校验 + 独立复算（在冻结接口上自检通过） |

## 用法
```bash
# 独立复算（结果根 = 含 case.json + compact_pareto_frontiers.json + final_replay/ 的目录）
python scripts/qa_engine.py <RESULT_ROOT> --label <名字>
# 碳分解
python scripts/carbon_decomposition.py [RESULT_ROOT]
# 消费 B 的 ResultBundle（真实 V2 交付后）
python scripts/resultbundle_consumer.py <result_bundle.json> [--params <case.json>]
```

## 复核结论（V1）
V1(R3) 8 个非支配前沿点的成本/碳排/热平衡/设备·管网·站房·接入投资，
可由原始输出独立重建到浮点精度，未发现计算错误。
图册（Pareto 合并前沿/成本碳分项/逐时调度/2160h热图/QA证据/能流/GIS）与 QA 产物
为本机生成件（位于维护者本地 `c_role_work/`，未纳入本仓库，符合仓库"不提交运行产物"约定）。

## 对 V2 的接口说明
- B 的 `solve_executor` 产 `runs/<run_id>/result_bundle.json`，artifacts 登记标准文件子集，
  完整标准文件在 run 根目录；C 从 artifact 父目录读取全量文件做独立复算。
- C 消费适配器已按冻结 schema 自检通过（用 V1 文件合成 run），真实 V2 bundle 落地即可接入。
