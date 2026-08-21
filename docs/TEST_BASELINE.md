# 测试版主线基线

## 2026-08-21：合并后回归基线

- 分支：`WH_heatOPT_li`
- 起始提交：`296cc5b`
- 环境：`urbanheatopt_env`
- 修复前结果：`190 passed, 6 failed, 2 warnings`
- 修复后结果：`196 passed, 13 warnings`
- 六个失败的共同原因：旧的 `competition_input_v1` 合成案例被错误套用
  `competition_input_v2_1` Schema，尚未进入模型求解就被拒绝。

本节点只恢复历史回归能力：

- `competition_input_v1` 继续作为固定合成热源的 legacy smoke 测试输入；
- `competition_input_v2_1` 继续使用当前机器 Schema；
- 两者按 `contract_version` 显式分流，不自动转换、不共享字段语义；
- 正式竞赛入口迁移到新 Pyomo 核心属于后续节点。

此处的 legacy 通过不代表测试版 V0 主线已经闭环，也不代表正式版 V1.0
已经具备运行条件。

13 条 warning 均来自 GeoPandas、Shapely、PyProj、GDAL 或 Pandas 的既有第三方
兼容提示，本节点没有将 warning 当作功能通过证据，也没有修改相关依赖。
