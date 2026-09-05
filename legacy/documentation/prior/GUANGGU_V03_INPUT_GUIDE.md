# 光谷 v0.3 输入校验、标准化与模型门禁操作指南

## 1. 本功能完成到什么程度

本功能已经能够由代码独立完成以下流程，不需要 AI 逐文件读取：

```text
定位 guanggu_v03 源配置
→ 递归发现并分类全部文件
→ 读取、哈希并校验源数据
→ 提取 2160 小时完整供暖季
→ 统一字段、时间、单位和 LHV 口径
→ 建立不可变 CanonicalSeasonData
→ 独立复验标准数据
→ 生成模型就绪 JSON 和桌面中文报告
```

当前真实 v0.3 的输入层结论为：

- `source_validation_passed=true`；
- `canonical_validation_passed=true`；
- `model_ready=false`；
- `solver_executed=false`。

前两项通过表示数据已经可被代码自动读取并转换为统一的 2160 小时数据快照，
不表示正式模型已经运行。空间、设备经济、储热和 v0.3 新核心连接层等阻塞解除前，
`run_case.py` 会在创建求解器之前以退出码 2 停止。

## 2. 应从哪里运行

在 VS Code 中选择“文件 → 打开文件夹”，打开：

```text
D:\co_WH_heatOPT\UrbanHeatOpt
```

然后选择“终端 → 新建终端”。所有下列命令都在这个仓库根目录的 PowerShell
终端运行，不是在某个 `.py` 文件的编辑窗口中逐行执行。

### 2.1 检查环境

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/check_environment.py
```

正常结果：Python、CPU PyArrow、Parquet 时区往返和 APPSI HiGHS 最小模型均通过，
退出码为 0。本输入任务不安装新依赖。

### 2.2 执行真实 v0.3 全流程输入验收

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/validate_inputs.py `
  --delivery-root "D:\co_WH_heatOPT\IN_DATA\原始输入数据\v0.2\0823代码组交付_光谷软件园_v0.3" `
  --source-profile guanggu_v03 `
  --scope heating-season `
  --full-audit
```

正常结果应同时出现：62 栋、2160 小时、133920 行、412 个输入文件，且源与标准
校验均为 `true`。当前 `model_ready=false` 是正确门禁结果，不影响本命令以退出码 0
完成“输入验收”。

### 2.3 验证正式运行门禁

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/run_case.py `
  --delivery-root "D:\co_WH_heatOPT\IN_DATA\原始输入数据\v0.2\0823代码组交付_光谷软件园_v0.3" `
  --source-profile guanggu_v03 `
  --profile v1-full
```

在当前缺失项解除前，正确结果是：打印每项阻塞并返回退出码 2；不实例化求解器、
不调用旧 `model.run_model()`、不生成 Pareto 或“运行成功”摘要。退出码 2 在这里表示
门禁按设计阻止了不完整模型，不是程序崩溃。

### 2.4 运行自动测试

```powershell
conda run --no-capture-output -n urbanheatopt_env python -m pytest -q
```

输入专项测试位于 `tests/test_guanggu_v03_intake.py`。它覆盖成功流程、未分类文件、
错误供暖季映射、被过滤建筑、缺字段聚合、重复/负负荷、时间错位、错误数据版本、
燃气双重权威口径、HHV 曲线隔离、不可变标准快照和模型门禁。

## 3. 代码如何自动工作

| 层 | 文件 | 职责 |
|---|---|---|
| 源清单 | `competition/configs/guanggu_v03.yaml` | 冻结数据版本、412 文件分类、关键文件路径、格式、编码、必需列、行数和供暖季边界 |
| 源校验器 | `competition/intake/guanggu_v03.py` | 递归发现文件，读取 CSV/Parquet/GeoJSON/XLSX/JSON/MD/TXT，重算校验和并聚合错误 |
| 适配器 | `competition/adapters/guanggu_v03.py` | 只读源文件，提取供暖季，统一字段、时间、LHV 和 COP 查表边界，写标准化副本 |
| 不可变数据 | `competition/canonical.py` | 深拷贝保存建筑、负荷、外部时序、技术表、性能曲线、时间映射和源哈希 |
| 标准复验 | `validate_canonical_season_data()` | 不依赖源校验结论，再次检查 ID、小时、时区、单位、非负性、LHV 和 HHV 隔离 |
| 就绪门禁 | `competition/readiness.py` | 分开输出输入有效性、模型就绪性和求解状态，并生成缺失项与桌面说明 |
| 薄命令 | `scripts/validate_inputs.py` | 解析参数、调用上述流程并把契约错误转换为退出码 2 |
| 正式入口门禁 | `scripts/run_case.py` | `model_ready=false` 时在构模前停止，禁止静默回退旧模型 |

以后同一交付规范只需把新数据放入新的交付目录并执行命令。如果仅目录名或文件名
变化，修改 source profile；如果源字段别名变化，修改对应适配规则；稳定的 Core 字段、
单位和物理语义不随每批数据一起修改。只有语义或单位发生破坏性变化时才升级契约。

## 4. 自动检查的主要内容

### 4.1 文件与版本

- 全目录文件数、扩展名、分类和 SHA-256；
- `--full-audit` 下未分类文件直接失败；
- 标准文件存在、可读、编码正确、行数和必需字段齐全；
- 所有 `data_version` 必须是 `guanggu-v0.3-20260823`；
- 369 个 DeST CSV 逐个读取并检查小时索引，4 个工作簿逐工作表读取；
- 交付 QA 和说明只作旁证，不代替代码重算。

### 4.2 建筑、空间和映射

- 00、03、04、05 建筑集合一致且为 62 栋；
- `gsr_way_610079670` 不得重新进入计算输入；
- 建筑 ID 非空唯一、面积为有限正数；
- GeoJSON 必须有 `EPSG:4326`、有效 Polygon/MultiPolygon；
- 混合用途分区字段完整，分区面积之和与建筑面积一致；
- 末端统一 `fan_coil`、新风负荷已包含、供回水 45/40℃。

### 4.3 负荷、时间和供暖季

- 全年负荷 543120 行，每栋严格 `hour=0..8759`；
- 主键 `building_id+hour` 唯一，负荷为有限非负浮点数；
- 每栋小时覆盖相同，05 与 `Building_TS.csv` 按建筑和小时对账；
- 供暖季严格按 `8016..8759` 后接 `0..1415`；
- 生成 `heating_season_hour=0..2159` 和模型 `hour=1..2160`；
- 重建带 `Asia/Shanghai` 时区的连续跨年 timestamp，同时保留源 timestamp/hour；
- 标准负荷恰好 `62×2160=133920` 行，适配前后总热量守恒。

### 4.4 外部时序、燃气和设备性能

- 外部时序 8760 行，与负荷共用 hour、timestamp 和供暖季映射；
- 气温、电价、气价、碳因子和时间权重有限、连续且单位明确；
- 逐时电价重算为基价乘倍率；三方案清单中的关键 SHA 必须完全一致；
- 天然气体积价格和体积碳因子只在适配边界除以 `kWh_LHV/Nm³` 一次；
- 如果源表同时把体积值和 `CNY/kWh_LHV` 声明为权威输入，直接失败；
- 常规锅炉执行效率来自 LHV 参数；06 中 HHV 锅炉曲线只保留追溯标记；
- 热泵 COP 和容量修正系数必须为正；高于 15℃的小时保留原温度，查表温度封顶15℃。

## 5. 生成哪些文件

默认标准化产物写入仓库内被忽略的：

```text
runs/input_validation/guanggu_v03_<时间戳>/
```

包括：

- `source_validation_report.json`：全部源校验、问题、数据集统计和412个哈希；
- `canonical_validation_report.json`：标准数据独立复验；
- `adaptation_report.json`：转换规则、数量和守恒；
- `model_readiness_report.json`：模型阻塞、所需字段、单位和解除条件；
- `field_mapping.csv`：源字段到标准字段的映射；
- 标准建筑、映射、133920行负荷、2160小时外部时序、技术/性能登记和小时映射。

桌面文件：

```text
C:\Users\leonl\Desktop\光谷v0.3输入校验与模型就绪状态.md
```

该文件每次由最新 JSON/标准快照自动渲染，包含四状态结论、22个 COP 封顶小时、
字段转换、交付数据字典、所有校验警告、详细缺失项、412文件分类/读取状态/哈希及
复现命令，不需要人工维护另一套结果。

## 6. 退出码和故障处理

| 退出码 | 含义 | 操作 |
|---:|---|---|
| 0 | 本命令目标完成；对校验命令只表示输入层通过 | 查看 JSON/桌面报告，不得据此称求解完成 |
| 2 | 文件、契约或模型就绪门禁失败 | 按错误代码、文件、字段和解除条件修正输入或补齐模块 |
| 1 | 程序异常 | 保存终端堆栈/错误文本，由代码组修复，不修改源文件规避 |

源目录始终只读。标准化结果必须写到其他目录；代码还会在适配结束后重新计算全部
源文件哈希，只要任何源文件发生变化即失败。

## 7. 当前尚不能宣称完成的内容

当前输入层已经完整自动化，但正式模型仍缺少或未接通：道路约束候选站/候选网、
三档管径及正式管损泵耗、站点与接入投资、无歧义的设备经济映射、水蓄热完整参数
映射、温度 COP/低温容量衰减 Provider、20%峰值容量裕度的 v0.3 Case Builder 接线，
以及62栋×2160小时三模式/Pareto/独立 QA 实跑。

这些项的最新机器结论以桌面报告和 `model_readiness_report.json` 为准；任何必需项
仍为 `blocked` 时，程序不会创建求解结果。
