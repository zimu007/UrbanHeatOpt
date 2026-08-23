# 光谷软件园 v0.2 接收说明

## 自动读取边界

`wuhan_v02` 是源数据 profile，不是模型输入契约。它负责识别负荷组 v0.2
交付中的路径、编码、文件数量和字段；标准化后的稳定字段由 V3 契约负责。
因此，同一交付格式可重复自动读取；仅目录或文件名变化时修改 profile，不修改
Pyomo Core。

执行命令：

```powershell
python scripts/validate_inputs.py `
  --delivery-root "<0821代码组交付_光谷软件园目录>" `
  --source-profile wuhan_v02 `
  --full-audit
```

校验成功返回 `0`，数据或契约错误返回 `2`。校验器仅读取文件，并在开始和
结束时比较 SHA-256；不在交付目录创建、清洗或覆盖文件。

## 文件职责

- `03_buildings.geojson`：62 栋物理建筑及几何、用途、面积。
- `04_building_archetype_map.csv`：建筑到 DeST 原型的映射；混合用途建筑允许
  一栋对应多个分区，不能强行选择单一原型。
- `05_building_hourly_loads.parquet`：62 栋、8760 小时的标准建筑负荷，是运行
  负荷的权威来源。
- `Wuhan_DeST_Models_映射原型逐时负荷/`：原型负荷和气象来源。全量审计会
  解析其中 369 个 CSV，但不会把原型负荷再次叠加到 05。
- 映射工作簿、QA、来源日志和数据字典：用于追溯。工作簿结构异常不影响已
  生成的 03/04/05 通过 V0 校验，但会保留可读警告。

## 当前验收结果

- 交付共 394 个文件，其中 376 CSV、3 GeoJSON、2 Parquet、3 XLSX。
- DeST 目录共 370 个文件；369 个 CSV 均可解析。
- 30 份逐时气象和 66 份逐时负荷文件均具有严格 `0..8759` 小时索引；气象
  副本内容一致。
- 03、04、05 的建筑 ID 集合一致；05 有 543120 行，逐时供暖负荷有限且非负。
- 两个工作簿按来源文件处理，报告 `WORKBOOK_PROVENANCE_ONLY` 警告。

上述结论只证明源交付可自动读取和校验，不等同于设备性能、能源价格、候选
管网或正式模型参数已经齐全。

## V0 临时空间产物

适配器会额外写出 `candidate_sites.geojson`、`candidate_network.geojson` 和
`roads_or_feasible_space.geojson`，用于在道路模块交付前联调新 Core。唯一站点
是在 `EPSG:32650` 中按逐栋年度供暖量计算的负荷中心；候选物理管段是该站点
与全部建筑质心的确定性欧氏 MST。真实 v0.2 演练为 1 个站点、62 条管段。

这些文件统一标记 `candidate_source=provisional_geometric_mst`、
`road_constrained=false` 和 `construction_feasibility_verified=false`。它们不是
道路约束下的线路寻优结果，也不能作为施工方案或正式管网长度结论。
