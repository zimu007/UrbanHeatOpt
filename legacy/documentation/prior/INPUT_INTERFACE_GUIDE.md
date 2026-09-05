# 数据接口输入与修正规则

## 1. 两层输入

### 1.1 负荷组交付层

负荷组完整交付包括9个文件：

| 文件 | 格式 | 内容 | 当前状态 |
|---|---|---|---|
| `01_archetypes.csv` | UTF-8 CSV | DeST原型元数据和年度指标 | 待正式数据接收 |
| `02_archetype_hourly_loads.parquet` | Parquet | 原型逐时冷热负荷 | 待正式数据接收 |
| `03_buildings.geojson` | GeoJSON / EPSG:4326 | 建筑轮廓和清单 | 运行层已有同类校验 |
| `04_building_archetype_map.csv` | UTF-8 CSV | 建筑—原型映射 | 待正式数据接收 |
| `05_building_hourly_loads.parquet` | Parquet | 建筑逐时标准长表 | 已有校验与适配 |
| `06_equipment_performance.csv` | UTF-8 CSV | 设备性能曲线 | 待技术曲线扩展 |
| `07_data_dictionary.md` | Markdown | 字段和单位定义 | 待正式数据核对 |
| `08_qa_report.xlsx` | XLSX | 上游QA结果 | 待正式数据核对 |
| `09_source_log.csv` | UTF-8 CSV | 来源、许可和版本记录 | 待正式数据核对 |

这9个文件是数据交接包，不会全部直接进入优化模型。

### 1.2 比赛运行层

标准案例固定读取：

| 文件 | 读取方式 | 核心要求 |
|---|---|---|
| `case_config.yaml` | YAML + JSON Schema | 单位、时间、CRS、文件、技术和求解配置；未知键失败 |
| `buildings.geojson` | GeoPandas | EPSG:4326，Polygon/MultiPolygon，建筑ID唯一 |
| `building_hourly_loads.parquet` | Pandas/PyArrow | Asia/Shanghai连续整点，浮点kW，建筑覆盖一致 |
| `technologies.csv` | Pandas | 技术类型、容量、成本、寿命、来源和假设标记 |
| `roads_or_feasible_space.geojson` | GeoPandas | 道路模式为线，可建设空间模式为面 |
| `external_timeseries.parquet` | Pandas/PyArrow | 时间与负荷完全一致，按技术提供外部参数 |
| `resource_anchors.geojson` | GeoPandas | 可选；P0禁用余热时必须为 `null` |

完整权威定义见 `competition/schemas/` 和 `docs/DATA_CONTRACT.md`。

## 2. 当前实现路径

唯一输入校验架构为：

```text
competition/validation/inputs.py
├── CaseInputs
├── InputValidationError
└── validate_case_inputs()
```

相关入口：

```text
scripts/validate_inputs.py
scripts/adapt_case_to_legacy.py
scripts/run_case.py
```

仓库已包含4栋建筑、24小时、1个合成固定热源的 `tests/fixtures/minimal_case/`，用于校验、适配和流程测试。它不代表武汉、光谷或Fehring真实数据。

## 3. 自动修正原则

### 3.1 原始输入不自动修正

以下问题有歧义或会改变物理意义，必须停止并返回稳定错误码：

- 文件名或字段名拼错、缺少必需文件/字段；
- ID首尾空白、大小写不一致、前导零丢失、重复或跨文件错位；
- W/MW与kW、其他币种与CNY等单位错误；
- 无时区、错误时区、非整点、缺时、重复或乱序；
- NaN、Inf、负负荷、负成本或非法容量；
- CRS缺失/错误、空几何、非法几何或错误类型；
- DHW、数据版本、技术引用或配置关系不一致。

校验器不得自动 `strip` ID、猜单位、补时、插值、重命名字段或修复几何。应由数据提供方修正源数据并更新 `data_version`。

### 3.2 允许无歧义的派生转换

适配器可以在独立运行目录中生成：

- 标准长表到UrbanHeatOpt宽表；
- `timestamp` 到 `hour=1...N` 的稳定映射；
- EPSG:4326到EPSG:32650的内部投影；
- 明确规则定义的旧模型字段；
- 上游已明确源单位时的一次显式换算。

派生过程不得覆盖源文件，并应记录输入SHA-256、数据版本、转换公式和转换前后统计。

## 4. 校验输出与只读保证

```powershell
python scripts/validate_inputs.py --case tests/fixtures/minimal_case
```

- 成功：退出码0，输出JSON摘要、案例规模和输入SHA-256；
- 失败：退出码2，输出稳定错误码与可读信息；
- 校验前后会比较案例文件快照，发现任何变化立即失败；
- 原始输入不会被自动修改。

## 5. 当前边界

已实现运行层校验、最小fixture、旧模型适配、`run_case.py`入口、标准结果导出和成本年化基础。仍未完成负荷组9文件正式接收、真实案例对账、完整供暖季运行和竞赛扩展技术。
