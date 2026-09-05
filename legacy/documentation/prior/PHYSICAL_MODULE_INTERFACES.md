# V3 物理模块接入与验收接口

## 目的

候选生成、温度性能等算法由相应负责人开发，但不得直接改动主程序字段或把
公式散落到 `run_case.py`。主线只接收本文件定义的标准对象，并在进入 Pyomo
前完成范围、覆盖关系和单位校验。

## 热泵性能 Provider

实现者应遵守 `HeatPumpPerformanceProvider`，根据：

- `technology_id`；
- 带时区的逐时 `timestamp`；
- `outdoor_temperature_C`；
- `leaving_water_temperature_C`；

返回每个空气源热泵、每个小时唯一一组：

- `cop`：无量纲，必须大于 0；
- `capacity_ratio`：无量纲，必须在 `(0, 1]`。

Core 使用 `heat_output <= installed_capacity × capacity_ratio`，并按
`electricity_input = heat_output / cop` 核算购电。插值和外推必须在 Provider
中完成并记录 `provider_name`、`parameter_version`；不得在 Pyomo 中临时插值。

`FixedV0PerformanceProvider` 只供合成调试：使用技术表固定 COP，容量修正为
1。`v1-full` 明确拒绝该 Provider，也不允许未提供 Provider 时自动回退。

## 管损和泵耗

三档管径的每档接口包括：

- `capacity_max_kW_th`；
- `heat_loss_kW_per_m`；
- `pumping_kWh_e_per_kWh_th_transferred`。

当前 V1 简化线性式为：

```text
管段逐时热损 = 建设长度 × 选中管径 heat_loss_kW_per_m
管段逐时泵耗功率 = |热流功率| × 选中管径 pumping_kWh_e_per_kWh_th_transferred
```

热损进入能源站逐时热平衡；泵耗进入购电、电费和购电碳排。同一物理管段只
选择一档、建设和计费一次。该线性式不是复杂水力模型；正式参数仍须由专业
负责人给出来源、适用供回水温度和版本。

## 候选站与候选管网 Provider

实现者应遵守 `CandidateNetworkProvider`，只返回：

- 候选站 `GeoDataFrame`；
- 物理无向候选管段 `GeoDataFrame`；
- provider 名称与参数版本。

模块可以使用道路/可建设空间、建筑空间数据和固定随机种子，但不得自行决定
最终建设方案。最终站点、管段和管径由统一 Pyomo Core 决定。

当前主线仅验收一个给定候选站和给定候选物理管网；`candidate_source=generate`
仍明确失败。原因是自动算法和多候选站到单站选址决策尚未由负责人交付并通过
联合测试，不能用直线网络伪装道路可实施结果。

## 合并门槛

物理模块提交合并前必须同时具备：

1. 完整覆盖与非法值测试；
2. 至少一组可手算物理测试；
3. 参数来源和版本；
4. 与 `CanonicalCaseData` 的适配测试；
5. 三种模式联合回归；
6. 成本、碳排和独立 QA 对新增能流的重算覆盖。

未满足以上门槛时，`v0-smoke` 可继续使用明确的测试简化，但不得输出或命名为
正式 V1.0 结果。
