# A部分交接索引（2026-09-05）

## 代码交付

- 目录迁移：7fba2b1；总体方向／任务书：36f7099；经济登记：b890461；接口与门禁：b628107；最终报价权限与清单收口：本索引最新提交。
- 工作期间另有外部配置提交2f53041、d742ed1及合并0a4c4be，原样保留；并非本任务执行推送或合并。
- [操作手册](A_INTEGRATION_CN.md)、[接口契约](../architecture/HANDOFF_CONTRACT_CN.md)、[目录说明](../architecture/DIRECTORY_MIGRATION_CN.md)。
- [总体方向](../team/UrbanHeatOpt_总体方向与三人分工.md)、[A任务](../team/UrbanHeatOpt_A_主线参数与集成任务书.md)、[B原任务](../team/UrbanHeatOpt_B_模型诊断站址与储热任务书.md)、[B三层接线后续任务](../team/UrbanHeatOpt_B_三层接线后续任务书.md)、[C任务](../team/UrbanHeatOpt_C_独立QA与成果展示任务书.md)。

## 本次证据（不提交Git）

| 内容 | 相对仓库路径 | 结论 |
|---|---|---|
| 迁移前测试 | runs/A_INTEGRATION_20260905/before | 451通过/3跳过 |
| 目录节点测试 | runs/A_INTEGRATION_20260905/layout_final | 453通过/3跳过 |
| 上一节点全量测试 | runs/A_INTEGRATION_20260905/acceptance_stable | 552通过/3跳过 |
| 最终报价节点全量测试 | runs/A_INTEGRATION_20260905/final_quotes_tests | 603通过/3跳过，42条依赖弃用警告 |
| 数学主体与冻结 | runs/A_INTEGRATION_20260905/mathematics_final、freeze_final | 67主体等价；冻结通过 |
| 正确激活环境 | runs/A_INTEGRATION_20260905/conda_environment | 仅highspy1.11.0与要求1.15.1不符；其他检查及小模型通过 |
| 历史许可冲突证据 | runs/A_INTEGRATION_20260905/conda_prepare_final | 确认前退出2；保留历史，不作为当前结论 |
| 按最终报价准备 | runs/A_INTEGRATION_20260905/final_quotes_verified_prepare | 退出0，原来源保留、8绑定显式按用户决定处理 |
| 源／供暖季／参数 | work/guanggu_v2/A_FINAL_QUOTES_VERIFIED_20260905 | 443文件、62×2160、87参数登记、CaseBundle；源hash不变，无求解 |
| 独立快照复验 | runs/A_INTEGRATION_20260905/final_quotes_artifacts | 452文件hash、62栋/133920行、2160小时、LHV单次换算、4缺项通过 |
| 本节点数学／冻结复验 | runs/A_INTEGRATION_20260905/final_quotes_layout、final_quotes_freeze | 67数学主体等价；参考核心冻结通过 |
| 新solve门禁 | runs/A_INTEGRATION_20260905/final_quotes_solve_gate | 真CaseBundle完整性通过，仍退出2，B/C未接通，无legacy回退 |

桌面`缺失数据清单.md`由最新input_gaps.json渲染；旧版按时间备份。当前仅4类数据/语义缺口，已有经济报价、历史管价和B/C代码工作不列为缺数据。B/C未完成状态保留在model_readiness_report.json及任务书，不靠删清单使求解门禁变绿。

## 最小剩余条件

1. 候选站位置/容量及管型输热容量需后续研究输入；已有费用不重复收集。站房费用拆分与0.026单位语义为可选完善，不影响本次基础输入准备。
2. B按任务书接通新经济、月需量和站址能力接口，完成有／无TES单例；C完成ResultBundle独立QA与展示。
3. 后续真实V2求解前统一highspy版本；本轮未安装或降级要求。

A代码、新经济快照、道路网络、RoadCase Builder和SolveRequest执行器已可交接；真实V2全季成本端点仍需执行。没有改数学公式或增加模型变量；合成测试不能替代真实2160小时结果。
