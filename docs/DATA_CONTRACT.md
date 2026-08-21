# UrbanHeatOpt 竞赛版输入数据契约

## 1. 适用范围与权威来源

本契约定义比赛运行入口 `competition_input_v2_1`。它统一七类运行输入、跨文件关系、单位、时间、ID、坐标系和校验行为，供后续校验器、适配器和最小合成案例共同使用。v2.1 在 v2 的独立设备/绝对价格口径上增加简单年化经济字段，并统一维护费与热量单位；原 v2 已标记为不可执行的弃用契约，禁止自动迁移。

权威顺序如下：

1. 字段、类型、枚举和条件规则：`competition/schemas/input_contract.yaml`；
2. `case_config.yaml` 结构：`competition/schemas/case_config.schema.json`；
3. 人类可读解释和交接边界：本文档；
4. 原作者 `_config.yaml`、`Building_TS.csv` 等只属于兼容层，不得反向改变标准输入语义。

机器契约当前状态为 `competition_input_v1_validation_and_legacy_adapter_enforced`：`competition/validation/inputs.py` 已执行配置、文件、字段、数值、ID、时间、CRS、几何、技术引用和主要跨文件检查，并返回稳定错误码及输入 SHA-256；`competition/adapters/legacy_case.py` 已实现 P0 标准输入到旧模型格式的派生适配。原始输入保持只读，错误 ID、单位、字段、时间或几何不得自动修正。契约登记某种技术仍不代表当前比赛管线已经实现该技术。

## 2. 案例目录、编码和只读边界

一个可运行案例目录必须使用以下固定名称：

```text
<case>/
├── buildings.geojson
├── building_hourly_loads.parquet
├── technologies.csv
├── roads_or_feasible_space.geojson
├── resource_anchors.geojson          # 可选
├── external_timeseries.parquet
└── case_config.yaml
```

通用要求：

- CSV、YAML、JSON 和 GeoJSON 使用 UTF-8；CSV 使用逗号分隔和点号小数；
- Parquet 必须保留逻辑类型，不能把时间戳或布尔值降级为字符串；
- 原始输入只读。校验和适配不得覆盖、改名或补写源文件；
- 派生文件必须写入独立工作目录，并记录源文件 SHA-256；
- 未知核心字段、拼错的字段或未知单位必须报错，不能静默猜测；
- 额外追溯字段可以保留，但必须由数据字典说明，P0 不得让它们暗中影响计算。

## 3. 统一单位契约

| 物理量 | 唯一标准单位 | 规则 |
|---|---|---|
| 热功率、设备容量、资源可用量 | `kW` | 浮点、有限、通常非负；标准入口不接受 W/MW |
| 热量、储热能量 | `kWh` | 对 1 小时时步，`kWh = kW × 1 h` |
| 电力输入量 | `kWh_e` | 与热量分开标记；固定 COP 热泵满足 `电输入=供热量/COP` |
| 燃气输入量 | `kWh_LHV` | 低位热值口径；燃气锅炉满足 `燃气输入=供热量/效率` |
| 币种 | `CNY` | 不允许 EUR/CNY 静默换算；价格基年在配置中显式给出 |
| 电价 | `CNY/kWh_e` | 逐时绝对价格，不接受乘数、指数或基准价的隐式组合 |
| 气价 | `CNY/kWh_LHV` | 逐时绝对价格，效率必须使用同一 LHV 口径 |
| 电力碳强度 | `kgCO2e/kWh_e` | 与电力输入量口径一致；当前仅追溯，不进入成本目标 |
| 燃气碳强度 | `kgCO2e/kWh_LHV` | 与燃气 LHV 输入量口径一致；当前仅追溯，不进入成本目标 |
| 面积 | `m2` | 运行建筑的 `heated_area_m2 > 0` |
| 长度 | `m` | 只在投影坐标系中计算 |
| 温度 | `degC` | 室外、供水和回水温度均使用摄氏度 |
| 碳排 | `kgCO2e` | 因子按相应能源输入量声明分母 |
| 时间步 | `1h` | 每个功率值表示以 `timestamp` 开始的一小时平均功率 |
| 年化小时权重 | `h/year` | `time_weight_h_per_year`；同一小时对所有技术共用且必须 `>0` |
| COP、效率 | 无量纲 | 不得附带百分数单位；效率输入使用 0～1 小数 |
| 固定维护比例 | `fraction/year` | `fixed_maintenance_fraction_per_year`；无量纲年度比例，不是 CNY/kW-year |

强制规则：

- `heating_kW` 进入标准边界后始终保持浮点 `kW`；聚类只能求和，不能 `/1000`；
- 不允许依据数值大小、文件名或历史经验推断单位；
- 若上游不是 kW，换算必须发生在进入本标准之前，由适配日志记录源单位、目标单位、公式和换算次数；
- 本项目标准适配路径的单位换算次数为 0，禁止重复 `×1000`、`/1000` 或整数截断；
- 建筑负荷已由负荷组完成面积缩放，`case_config.demand.area_scaling_already_applied` 固定为 `true`，代码不得再次缩放。

这就是“冻结单位契约”：它防止原代码中的隐式 `/1e3` 与历史生成器中的 `×1e3` 相互抵消，或者在接入外部 kW 数据时造成千倍错误。只有后续代码删除隐式换算并通过守恒测试后，路线图中的单位 P0 才能勾选。

## 4. 统一时间契约

标准时间轴规则：

- `timestamp` 必须是带时区的 Parquet 时间戳，时区固定为 `Asia/Shanghai`；
- `timestamp` 表示小时区间起点；案例范围为 `[start, end)`，即起点包含、终点不包含；
- 相邻时间戳严格相差 1 小时；不得重复、缺失、额外插入或乱序；
- `building_hourly_loads.parquet` 规范排序为 `timestamp` 升序、`building_id` 升序；
- 每栋建筑必须覆盖完全相同的时间戳集合；
- `external_timeseries.parquet` 必须与负荷文件拥有完全相同的时间戳集合；
- 配置中的起止时间必须带显式 UTC 偏移，并与两个 Parquet 文件精确一致；
- 正式案例必须明确完整供暖季；24/48/168 小时案例必须设置 `complete_heating_season: false`，不得称为年度结果。

兼容原模型时，适配器对排好序的唯一时间戳稳定映射：

```text
第 1 个 timestamp → hour=1
第 2 个 timestamp → hour=2
...
第 N 个 timestamp → hour=N
```

适配器必须保存 `timestamp_hour_map.csv`，不得接受 `hour=0...N-1`、缺口、重复或乱序的旧宽表。这就是“冻结时间索引”：真实时间只由标准 `timestamp` 表达，`hour=1...N` 只是一次性兼容编号。只有后续映射和拒绝路径测试完成后，路线图中的时间 P0 才能勾选。

## 5. ID、数值和坐标系通则

### 5.1 ID

- ID 是区分大小写的非空 UTF-8 字符串；
- 首尾不得有空白，不自动 `strip`、大小写转换、数值化或补前导零；
- 同一命名空间内必须唯一；
- `building_id` 在建筑与负荷文件中的集合必须完全相等；
- `technology_id`、`resource_id` 和外部时序列引用必须精确匹配；
- 校验器可以报告规范化建议，但不得在源数据上自动修正。

### 5.2 数值

所有模型数值必须为有限数；`NaN`、正负无穷和无法解析的字符串一律失败。除室外温度外，负荷、面积、容量、成本、价格、碳因子和资源可用量不得为负。JSON Schema 本身不能可靠拒绝 Python/YAML 的非标准 `NaN/Inf` 对象，因此输入加载器必须在 Schema 和 DataFrame 校验之前扫描并拒绝这些值；该执行逻辑属于下一节点“输入校验器”，本节点只冻结规则。

### 5.3 CRS 与几何

- 所有标准 GeoJSON 输入 CRS 固定为 `EPSG:4326`。对符合 RFC 7946 且没有已废弃 `crs` 成员的 GeoJSON，`case_config.crs.input: EPSG:4326` 就是显式 CRS 声明；若遗留文件带 `crs` 成员，则其值必须与 EPSG:4326 一致；
- 内部面积、距离、贴靠和网络计算统一投影至 `EPSG:32650`；
- 不允许在经纬度坐标上计算面积或距离；
- 几何必须非空、有效，并符合各文件允许的几何类型；
- 投影失败、地理 CRS 被用于米制运算或多个空间文件 CRS 不一致时立即失败。

`EPSG:32650` 是 `competition_input_v2_1` 的冻结内部投影。正式项目若批准其他工程坐标系，必须升级契约版本并重新做空间与长度回归，不能只改一个案例值。

## 6. 七类运行输入

### 6.1 `buildings.geojson`

几何只允许 `Polygon` 或 `MultiPolygon`。

| 字段 | 类型 | 必需 | 校验 |
|---|---|---:|---|
| `building_id` | string | 是 | 非空、首尾无空白、唯一 |
| `use_type` | string | 是 | 非空；正式受控词表仍需项目组确认 |
| `heated_area_m2` | float | 是 | 有限且 `>0`，单位 m² |
| `archetype_id` | string | 是 | 非空；运行阶段仅作追溯，真实交付时与原型表核对 |
| `geometry` | Polygon/MultiPolygon | 是 | 非空、有效、CRS 为 EPSG:4326 |

可保留 `floors`、`confidence`、`source`、`scale_factor` 等追溯字段。辅助字段为空不能导致整栋建筑被删除；后续旧模型适配器只对兼容层真正需要的字段建立明确派生规则。

标准字段 `heated_area_m2` 显式映射到旧接口的 `heated_area`。旧接口中的 `YearlyDemand`、`MaxDemand`、`LocalHeatProdCost`、`GeneralisedThermCap` 和 `GeneralisedThermCond` 不是标准建筑输入；其来源和短时案例处理见 `MODEL_ASSUMPTIONS.md`。

### 6.2 `building_hourly_loads.parquet`

| 字段 | Parquet 逻辑类型 | 必需 | 校验 |
|---|---|---:|---|
| `timestamp` | timezone-aware timestamp | 是 | Asia/Shanghai、整小时、连续、规范排序 |
| `building_id` | string | 是 | 与建筑文件集合完全一致 |
| `heating_kW` | float64 | 是 | 有限、`>=0`、单位严格为 kW |
| `dhw_included` | boolean | 是 | 当前 v2.1 要求案例内为同一个布尔值 |
| `data_version` | string | 是 | 非空且案例内唯一，与配置一致 |
| `cooling_kW` | float64 | 否 | 若存在则有限、非负；P0 不进入供热模型 |
| `quality_flag` | string | 否 | `measured/simulated/scaled/estimated` |

主键为 `(timestamp, building_id)`。不允许空值、重复主键、建筑间小时覆盖不同或时间轴与配置不一致。

`dhw_included` 描述文件中的负荷事实，不能从曲线数值推断。它必须与 `case_config.dhw.input_includes_dhw` 一致；`competition_input_v2_1` 固定 `add_in_adapter: false`，因此适配器永远不额外合成生活热水。合成 smoke 允许 `false`，但所有结果和摘要必须标记 `space_heating_only`；正式全服务案例在 DHW 合成功能实现前必须为 `true`。若真实交付存在逐栋混合状态，需要负荷组先澄清并升级契约。

### 6.3 `technologies.csv`

CSV 使用以下公共列：

| 字段 | 类型/单位 | 规则 |
|---|---|---|
| `technology_id` | string | 唯一、非空 |
| `technology_type` | enum | 见机器契约登记类型 |
| `applicable_scope` | `local/central/both` | 技术可安装范围 |
| `energy_carrier` | enum | `electricity/gas/waste_heat/none`；v2.1 不接受 `synthetic_heat` |
| `cop` | float or empty | 热泵条件必填且 `>0` |
| `efficiency` | float or empty | 锅炉条件必填，范围 `(0,1]` |
| `capacity_min_kW` | float kW | `>=0` |
| `capacity_max_kW` | float kW | `>0` 且不小于下限 |
| `capex_CNY_per_kW` | float | `>=0` |
| `fixed_maintenance_fraction_per_year` | float fraction/year | 有限、`0<=value<=1`；用于 `capacity × capex × fraction` |
| `variable_om_CNY_per_kWh_th` | float CNY/kWh_th | 有限、`>=0`；只按有用热输出计 |
| `lifetime_years` | integer | `>=1` |
| `source` | string | 非空引用、文件或可追溯说明 |
| `assumption_flag` | enum | `measured/manufacturer/literature/project_confirmed/scenario_assumption/synthetic_test` |

条件规则：

- 中央空气源热泵：`technology_type=air_source_heat_pump`、`applicable_scope=central`、`energy_carrier=electricity`、`cop>0` 且 `efficiency` 为空；
- 中央燃气锅炉：`technology_type=gas_boiler`、`applicable_scope=central`、`energy_carrier=gas`、`0<efficiency<=1` 且 `cop` 为空；效率使用 LHV 口径；
- 分布式空气源热泵：`technology_type=air_source_heat_pump`、`applicable_scope=local`、`energy_carrier=electricity`、`cop>0` 且 `efficiency` 为空；
- 三种角色必须分别拥有并启用独立 `technology_id`，具体 ID 不写死；同一站内热泵和锅炉不能合并成一行或一个合成热源；
- 合成案例所有参数必须标记 `synthetic_test`；
- `fixed_heat_source` 和 `synthetic_heat` 已从 v2.1 可执行契约中删除；v1 数据不能只改版本号后继续运行，必须拆分为上述真实设备角色；
- 固定维护费唯一按 `capacity_kW × capex_CNY_per_kW × fixed_maintenance_fraction_per_year` 计算；旧 v2 字段 `fixed_om_CNY_per_kW_year` 的单位与公式不同，v2.1 不接收；
- 用户提供的附件只列出 `fixed_maintenance_coefficient` 字段名，未提供可直接采用的数值、适用设备和完整单位，因此代码不填默认比例；正式值必须由项目责任方给出来源；
- `variable_om_CNY_per_kWh_th` 只表示按有用热输出计的可变运维，不得包含购电或燃气费用；能源费用必须由逐时载体输入量与绝对价格计算；
- 可变运维来源值只有在明确标准化为 `CNY/kWh_th` 后才能进入正式案例；含义或分母不明确时，正式运行必须停止；
- 曲线型热泵、储热和余热类型可以登记字段，但在对应实现和测试完成前不得声称比赛管线支持；
- `06_equipment_performance.csv` 是上游性能曲线交接，不等同于本文件。容量、投资、运维、寿命和来源仍必须由有依据的数据补齐；
- 设备价格、COP、效率或寿命缺失时不得由代码自行编造。

储热扩展列将在储热正式实现时升级契约。`competition_input_v2_1` 的目标可执行范围只允许固定 COP 的空气源热泵和固定效率的燃气锅炉，并继续强制 `waste_heat_enabled=false`、`storage_enabled=false`、`resource_anchors=null`。竞赛内存 Core 已实现三设备独立调度与三种供热模式；七文件运行校验器、文件适配器、CLI 和旧 `model.py` 仍未实现这一接口，未实现入口必须以 `FEATURE_NOT_IMPLEMENTED` 失败。

### 6.4 `roads_or_feasible_space.geojson`

必需字段：

| 字段 | 类型 | 规则 |
|---|---|---|
| `feature_id` | string | 非空、唯一 |
| `spatial_role` | enum | `road/allowed/excluded` |
| `geometry` | geometry | 按配置模式检查 |

模式：

- `spatial.input_mode: roads`：只允许 LineString/MultiLineString，所有 `spatial_role=road`；
- `spatial.input_mode: feasible_space`：只允许 Polygon/MultiPolygon，至少一项 `allowed`，可包含 `excluded`，不得混入 `road`；
- 试点使用简化几何时，结果只能标注为算法规划候选，不得声称具备施工可行性；
- 真实道路等级、禁建属性和贴靠容差不由负荷数据推断。

### 6.5 `resource_anchors.geojson`（可选）

文件存在时只允许 Point：

| 字段 | 类型 | 规则 |
|---|---|---|
| `resource_id` | string | 非空、唯一 |
| `resource_type` | string | 非空；正式词表待项目组确认 |
| `technology_id` | string | 必须引用启用技术 |
| `source` | string | 非空、可追溯 |
| `assumption_flag` | enum | 与机器契约一致 |

未来启用资源锚点时，可用量列名唯一采用 `resource_available_kW__<resource_id>`，不再允许锚点文件自由引用任意列名。当前 P0 v2 强制 `case_config.files.resource_anchors: null`，不创建伪造的空曲线；普通空气源热泵候选点由代码生成，不要求负荷组人工给点。

### 6.6 `external_timeseries.parquet`

始终必需的列：

| 字段 | 类型 | 规则 |
|---|---|---|
| `timestamp` | timezone-aware timestamp | 与负荷时间集合完全一致 |
| `data_version` | string | 非空、案例内唯一、与配置一致 |
| `time_weight_h_per_year` | float64 | 有限且 `>0`；同一小时对全部技术公共，单位 h/year |

按启用技术条件必需的规范列：

| 列名 | 触发条件 | 单位/约束 |
|---|---|---|
| `outdoor_temperature_C` | 启用空气源热泵 | `degC`，有限，可为负 |
| `electricity_price_CNY_per_kWh_e` | 存在电力技术 | 非负、有限的绝对电价，不接受 multiplier/index |
| `gas_price_CNY_per_kWh_LHV` | 存在燃气技术 | 非负、有限，低位热值口径 |
| `electricity_carbon_kgCO2e_per_kWh_e` | 存在电力技术 | 非负、有限，电输入量口径 |
| `gas_carbon_kgCO2e_per_kWh_LHV` | 存在燃气技术 | 非负、有限，低位热值口径 |
| `resource_available_kW__<resource_id>` | 每个资源锚点 | 非负、有限、kW |

v2 启用电力和燃气设备，因此两列逐时绝对价格均为必需。禁止旧名 `electricity_price_CNY_per_kWh`，也禁止任何 `*_price_multiplier` 或 `*_price_index` 字段。`time_weight_h_per_year` 全部值之和必须与配置的 `economics.expected_weight_sum_h_per_year` 在绝对容差 `1e-9 h/year` 内一致。两列碳因子当前仅保留输入追溯，成本最小化核心目标暂不消费，不能据此声称碳模型已经完成。室外温度在固定 COP 首版中只保留为可追溯输入，不改变 COP，也不能再次修正已经包含天气作用的 DeST 负荷；曲线性能需未来升级后才能使用。

### 6.7 `case_config.yaml`

YAML 载入后必须通过 Draft 2020-12 JSON Schema，未知键失败。所有影响结果的字段显式给出，不依赖 JSON Schema 的 `default`。

| 配置块 | 必需内容 |
|---|---|
| 顶层 | 契约版本、案例/场景 ID、数据版本、数据分类 |
| `time` | 起止、Asia/Shanghai、1h、半开区间、是否完整供暖季 |
| `units` | kW、kWh、kWh_e、kWh_LHV、CNY、两类绝对价格、两类载体碳强度、h/year、无量纲性能参数等唯一单位 |
| `crs` | EPSG:4326 → EPSG:32650 |
| `files` | 六个固定文件名及可空资源锚点 |
| `clustering` | KMeans、聚类数、`random_seed=202611`、`n_init=10` |
| `spatial` | 输入模式、候选点/管网规则、米制可行性容差 |
| `demand` | `area_scaling_already_applied: true` |
| `dhw` | 输入是否含 DHW；`add_in_adapter: false` |
| `features` | P0 v2.1 固定余热、储热均为 `false` |
| `network` | 供回水温度；供水必须高于回水 |
| `planning` | 规划年限、折现率、价格基年、CNY；当前只追溯，折现率和规划期不进入已批准的简单年化公式 |
| `economics` | `simple_capex_divided_by_lifetime_years`、预期年度权重和可变运维的有用热口径；站点固定 CAPEX 明确不纳入 |
| `network_economics` | 管道单位长度投资 `CNY/m` 和管道寿命；适配后逐物理段计费一次 |
| `connection_economics` | 每个需求节点的接入投资和寿命；节点集合必须与生成的需求节点完全一致 |
| `reliability` | 未供热惩罚 `hns_penalty_CNY_per_kWh`，有限且非负 |
| `enabled_technology_ids` | 至少三个不同 ID，并分别解析为中央 ASHP、中央燃气锅炉和分布式 ASHP |
| `solver` | `highs/gurobi`、单线程、时限、`0<=gap<1`、只在 optimal 后加载解 |
| `qa` | 守恒、平衡、未供热、成本和确定性容差 |

合成 fixture 固定 `solver.name: highs`、`threads: 1`、`time_limit_seconds: 60`、`random_seed: 202611`。正式供回水温度、规划期、折现率、价格基年和 gap 必须由项目输入明确给出，契约不提供武汉默认值；但在当前附件口径中，规划期和折现率只作追溯，不能暗中改成 CRF、折现、更换或残值公式。责任方与关闭条件见 `questions_for_project_team.md`。

### 6.8 年化成本公式

令 `w_t=time_weight_h_per_year[t]`，其单位为 h/year。当前唯一正式目标按下式建立：

```text
设备年化投资 = Σ(装机容量_kW × 单位投资_CNY/kW ÷ 寿命_year)
管网年化投资 = Σ(是否建设 × 长度_m × 单位投资_CNY/m ÷ 寿命_year)
接入年化投资 = Σ(是否接入 × 接入投资_CNY ÷ 寿命_year)
固定维护费 = Σ(装机容量_kW × 单位投资_CNY/kW × 年度维护比例_1/year)
可变运维费 = Σ(有用热输出_kW × w_t_h/year × 费率_CNY/kWh_th)
电费 = Σ(热泵热输出_kW ÷ COP × w_t_h/year × 电价_CNY/kWh_e)
燃气费 = Σ(锅炉热输出_kW ÷ LHV效率 × w_t_h/year × 气价_CNY/kWh_LHV)
真实成本 = 上述七项之和
未供热惩罚 = Σ(未供热_kW × w_t_h/year × 罚值_CNY/kWh)
优化目标 = 真实成本 + 未供热惩罚
```

所有逐时功率与小时权重相乘后得到年度能量，因此运行项单位闭合为 CNY/year；简单年化投资和固定维护同样为 CNY/year。未供热惩罚单独保留，不能混入 `annual_real_cost_CNY`，从而可以同时报告真实经济成本和可靠性罚项。

设备与管网的 `CAPEX/lifetime`、逐时电/气费用和维护费分类采用用户批准的附件口径；接入年化与 HNS 罚值是本项目经批准的扩展项，并非附件原文公式，正式参数仍保持阻塞。模型不使用折现率、资本回收因子、规划期、更换投资或残值；附件没有给出独立站点固定 CAPEX 公式，因此当前明确排除该项。`competition/economics.py` 提供一次性的体积气价标准化函数：只有体积气价与 LHV 使用完全相同体积基准且 LHV 有可追溯来源时，才能转换为 `CNY/kWh_LHV`；模型核心只接收转换后的绝对能量气价，不再重复换算。附件原始文件为 `年化总成本.docx`，SHA-256 为 `E01E902AADD3B550A64366C35213F5B221AE10F5E049364AB9D2D447D2DEFD72`。

## 7. 跨文件自动校验

后续校验器必须在任何适配、聚类或求解之前聚合检查，并一次性返回所有可定位错误：

| 类别 | 必查内容 |
|---|---|
| 文件 | 必需文件存在、扩展名和格式可读、可选资源文件与配置一致 |
| 字段 | 必需列、逻辑类型、未知核心列、条件必需列 |
| 数值 | 空值、NaN/Inf、非负、上下限、COP/效率条件规则 |
| ID | 唯一、首尾空白、建筑集合一致、技术和资源引用完整 |
| 时间 | 时区、整点、主键唯一、排序、1h 连续、覆盖一致、配置范围一致 |
| 单位 | 配置常量、列名单位、禁止 W/MW 和隐式换算 |
| CRS/几何 | CRS、投影、合法性、空几何、几何类型、模式匹配 |
| DHW/缩放 | 布尔值与配置一致、禁止二次 DHW、禁止二次面积缩放 |
| 版本 | 两个 Parquet 的 `data_version` 与配置一致 |
| 技术 | 三个设备角色各有独立启用 ID；COP/效率、scope、载体及逐时绝对价格均匹配 |
| 经济 | 权重和与配置一致；固定维护是比例；可变运维已标准化为 CNY/kWh_th；管网、接入寿命和投资完整；体积气价未被直接消费 |
| 功能边界 | `fixed_heat_source`、合成热、余热、储热、资源锚点和未实现的模型入口以稳定错误码拒绝 |

退出码：

- `0`：契约通过；
- `2`：输入契约错误，不写任何派生数据；
- `1`：程序自身异常。

错误报告至少包含稳定错误码、文件、字段/行键和中文说明。机器契约声明了 `FILE_MISSING`、`ID_MISMATCH`、`TIMESTAMP_GAP`、`UNIT_MISMATCH`、`CRS_MISSING`、`GEOMETRY_INVALID`、`FEATURE_NOT_IMPLEMENTED`、`NODE_REFERENCE_MISSING`、`NETWORK_LENGTH_MISMATCH` 和 `ASSET_LIFETIME_TOO_SHORT` 等稳定错误码。Schema 当前能拒绝结构和冻结常量；时间先后、供回水大小、非有限值、ID 空白及跨文件关系由下一节点校验器执行，不能把本节点测试误称为这些运行校验已经实现。

## 8. 适配产物与旧模型边界

标准输入是唯一主数据。兼容文件属于可重建产物：

| 产物 | 最低规则 |
|---|---|
| `timestamp_hour_map.csv` | `timestamp,hour`；hour 严格为 1...N |
| `Building_TS.csv` | 第一列 hour，其余列为 building_id；浮点 kW，无 `/1000` |
| `Heat_Demand.csv` | 第一列 hour；其余负荷列为浮点 kW，无隐式换算 |
| `candidate_sites.geojson` | 后续生成器至少输出 `site_id`、方法标识和 Point；本节点不规定坐标计算算法 |
| 其他模型中间文件 | 当前只登记 legacy 文件名；不得用 `isBoiler` 或合并成本列覆盖 v2.1 的 technology_id、COP/效率和能源载体语义 |

当前契约冻结单位、时间、三种设备角色、能源价格和简单年化成本接口。竞赛内存核心已按三个独立设备角色及上述经济公式求解，但文件适配器仍须保存 `timestamp ↔ hour` 双射、保持每个 `technology_id` 独立，并把配置中的管网/接入经济边界显式映射到物理段和需求节点。旧 `model.py` 仍只按 `isBoiler` 和合并运行成本处理，不得作为 v2.1 经济核心。候选站坐标和真实道路拓扑仍留给后续任务。

## 9. 负荷组九文件交接边界

以下是上游正式交付，不等同于七类运行输入。本轮没有真实文件，因此只冻结已知最低要求，不宣称正式接收完成。

| 交接文件 | 已知最低要求 | 接收时必须核验/确认 |
|---|---|---|
| `01_archetypes.csv` | `archetype_id` 唯一；DeST 原型元数据、年度指标、单位 | 其他列名、设定温度、气象年、冷热/DHW 边界 |
| `02_archetype_hourly_loads.parquet` | 原型逐时冷热负荷；时间连续、无空值、累计可对账 | 字段、单位、时区、符号、按面积或绝对值 |
| `03_buildings.geojson` | `building_id` 唯一、几何有效、CRS 明确 | 面积口径、用途、与标准建筑字段映射 |
| `04_building_archetype_map.csv` | 每栋恰有一个主原型；缩放和置信度可追踪 | 精确字段、缩放公式、置信度词表 |
| `05_building_hourly_loads.parquet` | 标准长表、ID 全匹配、kW、统一时间戳 | 必须补充/确认 `dhw_included`，并确认缩放已完成 |
| `06_equipment_performance.csv` | 性能曲线变量、单位、来源和假设标记完整 | 插值、有效域、外推、缺失值；不能替代完整技术经济表 |
| `07_data_dictionary.md` | 与实际字段/单位一致 | 类型、可空、枚举、符号、时区、公式和缺失处理 |
| `08_qa_report.xlsx` | 行数、缺失、峰值、累计和异常说明 | Sheet/列结构、基准总量、容差和异常关闭标准 |
| `09_source_log.csv` | 来源、日期、许可、负责人可追溯 | 精确字段、版本、处理步骤和文件 SHA-256 |

正式接收前的问题列在 `questions_for_load_team.md`；供回水、经济和空间参数责任见 `questions_for_project_team.md`；真实数据阻塞和解除条件列在 `P0_DEFERRED_REAL_DATA.md`。收到交付后仍需返回 `interface_acceptance.md`、`load_reconciliation.csv`、`id_mismatch.csv`、`adapter_run.log` 和更新后的问题清单。

## 10. 版本变更规则

- 兼容性补充只更新文档修订号；
- 新增可选追溯列可以保持 `competition_input_v2_1`，但必须登记；
- 改字段名、单位、时间含义、CRS、DHW 或缩放语义属于破坏性变化，必须发布新契约版本；
- 校验器必须记录其支持的契约版本并拒绝未知版本；
- 任何真实文件更新必须有新 `data_version` 和源日志，不得无说明覆盖旧版。
