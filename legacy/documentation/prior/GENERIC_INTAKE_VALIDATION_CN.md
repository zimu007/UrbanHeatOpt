# 通用数据接收校验层

## 1. 定位

数据链分为两个严格边界：

```text
外部交付 → 通用接收校验 → 来源适配器 → competition_input_v1 → 运行输入校验 → 模型
```

`competition.intake.validate_delivery()` 面向每次收到的异构数据，回答“文件是否可读、规则是否完整、能否安全进入适配器”。`competition.validation.validate_case_inputs()` 面向唯一的运行格式并返回 `CaseInputs`，回答“适配结果能否交给模型”。接收层不替代运行层，也不包含优化计算。

## 2. 配置驱动原则

每批交付使用 `intake_manifest_v1` YAML 描述，而不是把 DeST、武汉、固定建筑数量或中文字段写进核心代码。Manifest 可以声明：

- 单文件或 glob 多文件、CSV/XLSX/Parquet/GeoJSON、编码与工作表；
- 必需字段、行数、单列或复合主键、长表/宽表布局；
- 空值的 `reject`、`allow` 或 `fill_zero` 策略；
- 数值上下限、小负值容差和 `clip_zero` 策略；
- 连续数值时间索引；
- 空间 CRS、允许几何类型和几何有效性；
- 枚举值、长参数表的参数—单位契约；
- 跨数据集 ID 的 `equal` 或 `subset` 关系。

未声明的空值、单位、字段、关系或异常值不得由代码猜测修复。Manifest 拒绝未知顶层/数据集键、YAML 重复键和越过 `source_root` 的路径。

## 3. 只读与报告

校验器读取前记录每个文件的 SHA-256，结束后再次比较。`fill_zero` 和 `clip_zero` 只记录为后续适配器允许执行的标准化动作，接收校验本身不写回文件。JSON 报告包含：

- `status`、manifest 版本和数据集规模；
- 每个源文件 SHA-256；
- 稳定错误码和严重程度；
- 拟执行标准化的字段、数量、动作和容差。

## 4. 命令

```powershell
python scripts/validate_delivery.py --manifest path/to/intake.yaml
python scripts/validate_delivery.py --manifest path/to/intake.yaml --source-root path/to/delivery
python scripts/adapt_wuhan_delivery.py --source path/to/delivery --output runs/wuhan_delivery_validation
```

通过返回 `0`，数据或 manifest 不合格返回 `2`。当前武汉样本配置位于 `competition/configs/wuhan_delivery_intake.yaml`，它只是来源配置示例，不是核心代码中的固定逻辑。

## 5. 与路线图验收项的关系

当前完成的是接收前门：机器读取、结构、主键、数值、单位、时间、关系、只读和哈希。路线图工作包 B 仍需来源适配器完成：

- DeST 原型到建筑原型的显式映射；
- 原始时间和负荷符号转为标准 `timestamp/heating_kW/cooling_kW`；
- 本地建筑坐标到明确空间边界的派生；
- 设备参数长表到运行宽表；
- `load_reconciliation.csv`、`id_mismatch.csv`、`adapter_run.log` 和 `interface_acceptance.md`；
- 两次运行等价、单位只换算一次、年度热量与峰值误差不超过 0.1%。

在这些适配与对账交付完成前，不应把工作包 B 标记为完成。

## 6. 当前武汉验证用适配器

`scripts/adapt_wuhan_delivery.py` 已实现一条不求解的验证链：

- 使用 DeST 单位面积热负荷乘建筑面积并除以 1000，得到逐栋 `heating_kW`；
- 空值填零，单位面积原始负荷中位于 `[-0.001, 0) W/m²` 的数值噪声归零；
- 生成 2025 年 Asia/Shanghai 连续 8760 小时时间戳；
- 将本地代表点和 `area_m2/floors` 派生为验证用方形轮廓；
- 生成标准负荷、建筑、空间、外部气温和验证用固定热源文件；
- 输出负荷对账、ID 差异、接收说明与运行日志；
- 最后调用 `validate_case_inputs()`，并以返回的 `CaseInputs` 确认标准案例可被当前运行接口读取，但不创建优化模型。

空间绝对位置和固定热源是 `synthetic_test` 验证夹具，不能作为真实选址、管网或技术经济结论。设备交付已经通过接收校验，但 ASHP、GSHP、GB 和 TES 转为可执行模型技术仍属于后续模型接口任务。
