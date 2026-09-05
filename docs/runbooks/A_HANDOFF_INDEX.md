# A部分交接索引（2026-09-05）

## 代码交付

- 目录迁移：7fba2b1；总体方向／任务书：36f7099；经济登记：b890461；接口与门禁：本索引所在提交。
- 工作期间另有外部配置提交2f53041、d742ed1及合并0a4c4be，原样保留；并非本任务执行推送或合并。
- [操作手册](A_INTEGRATION_CN.md)、[接口契约](../architecture/HANDOFF_CONTRACT_CN.md)、[目录说明](../architecture/DIRECTORY_MIGRATION_CN.md)。
- [总体方向](../team/UrbanHeatOpt_总体方向与三人分工.md)、[A任务](../team/UrbanHeatOpt_A_主线参数与集成任务书.md)、[B任务](../team/UrbanHeatOpt_B_模型诊断站址与储热任务书.md)、[C任务](../team/UrbanHeatOpt_C_独立QA与成果展示任务书.md)。桌面同名四文件为同版本副本。

## 本次证据（不提交Git）

| 内容 | 相对仓库路径 | 结论 |
|---|---|---|
| 迁移前测试 | runs/A_INTEGRATION_20260905/before | 451通过/3跳过 |
| 目录节点测试 | runs/A_INTEGRATION_20260905/layout_final | 453通过/3跳过 |
| 最终全量测试 | runs/A_INTEGRATION_20260905/acceptance_stable | 552通过/3跳过 |
| 数学主体与冻结 | runs/A_INTEGRATION_20260905/mathematics_final、freeze_final | 67主体等价；冻结通过 |
| 正确激活环境 | runs/A_INTEGRATION_20260905/conda_environment | 仅highspy1.11.0与要求1.15.1不符；其他检查及小模型通过 |
| 真输入命令证据 | runs/A_INTEGRATION_20260905/conda_prepare_final | 退出2，许可冲突未绕过 |
| 源／供暖季／缺项 | work/guanggu_v2/A_HANDOFF_FINAL_20260905 | 443文件、62×2160、源hash前后相同；参数未验收，无求解 |
| 新solve门禁 | runs/A_INTEGRATION_20260905/solve_gate | 退出2，B/C未接通，无legacy回退 |

桌面`缺失数据清单.md`由上述input_gaps.json渲染；历史同名版本已按时间备份，不删除。清单将许可冲突、真实缺字段、成本边界及B/C代码工作分开，不把已登记研究冻结参数全部当成缺数据。

## 最小剩余条件

1. 用户／参数组确认8项逐参数允许与3项来源禁用的优先级；当前严格一致策略不放行。
2. B按任务书接通新经济、月需量和站址能力接口，完成有／无TES单例；C完成ResultBundle独立QA与展示。
3. 后续真实V2求解前统一highspy版本；本轮未安装或降级要求。

A代码可交接，但新经济完整有效快照与V2求解不能宣称完成。没有改数学公式，没有实施B/C研究任务，没有运行新V2全季。
