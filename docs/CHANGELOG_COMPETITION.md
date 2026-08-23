# UrbanHeatOpt 竞赛版变更记录

## 2026-08-23 — WUHAN-V0-01 完成光谷 V0 三模式自动闭环与 QA

### 更改内容与目的

- 光谷正式入口补齐标准化输入快照，保存本次实际进入模型的配置、建筑、负荷、技术、逐时能源参数、候选站、物理管段和管径表；源交付目录仍保持只读。
- 独立 QA 增加中央与分布式 20% 峰值容量裕度重算，逐时考虑热泵可用容量修正，并明确水蓄热不计入裕度。
- 新增从 v0.2 准备层到统一 Pipeline 的端到端回归，核验三模式、epsilon-constraint Pareto、标准输出、快照和 QA；新增 `WUHAN_V02_V0_RESULT.md` 保存实际运行证据和正式数据阻塞。

### 验证与边界

- 公开命令对真实 v0.2 完成 394 文件审计、62 栋×8760 小时标准化，并选取年供暖量最高的 8 栋与峰值日 `2021-01-08`；集中、分布、混合均为 `optimal`。
- 共保留 13 个非重复非支配点，13/13 通过热平衡、容量、连通、储热、成本、碳排及峰值容量裕度 QA；相同命令重复运行未改变源文件或标准化输出哈希。
- 全量自动测试 `251 passed, 27 warnings`；`conda run -n urbanheatopt_env python scripts/check_environment.py` 返回 0，CPU PyArrow、带时区 Parquet 和 APPSI HiGHS 均通过。测试警告来自既有 GDAL 数据目录探测及第三方 GeoPandas、PyProj、Pandas 弃用提示，不影响本次验收结果。
- 结果严格标为 `weighted_period_test`。固定 COP、临时设备/管网成本、国家缺省 LHV/碳因子和非道路几何 MST 均为 `scenario_assumption`，不得用于正式年度或施工结论。

## 2026-08-23 — PIPELINE-V02-01 接通光谷数据新核心并加入峰值容量裕度

### 更改内容与目的

- `CoreModelInput` 新增可配置 `peak_capacity_margin_fraction`；中央模式按接网负荷校核热泵与锅炉合计可用容量，分布式按节点校核本地热泵，混合模式按固定接网决策分别校核。
- 明确该规则是峰值容量裕度而非 N-1，不要求锅炉固定比例，水蓄热不计入容量裕度；逐时热泵容量修正会影响可用能力。
- 新增光谷 v0.2 V0 案例准备层：完整校验 62 栋×8760 小时后，稳定选择年度供暖量最高的 8 栋和全园区峰值自然日，并生成 draft.2 标准输入。
- 公共 `run_case.py` 增加 `--delivery-root` 执行方式，直接委托统一 Pipeline 和新 Core；旧模型没有回退入口。

### 验证与边界

- 真实 v0.2 自动准备得到 8 栋、24 小时、8 条临时 MST 管段；集中、分布、混合三模式均由 HiGHS 求得 `optimal`，20% 裕度已进入模型，未供热为数值零容差内。
- 专项测试覆盖 100 kW 峰值对应 120 kW 可用设备容量、逐节点分布式裕度、非法参数、储热排除、稳定选楼/选日、正式入口路由和 `v1-full` 拒绝 V0 假设。
- 当前仍是 `weighted_period_test`；设备经济参数、固定 COP、网络和能源参数均为 `scenario_assumption`，不能作为正式年度或施工结论。

## 2026-08-23 — SPATIAL-V02-01 建立 V0 确定性临时候选站与管网

### 更改内容与目的

- 在 `EPSG:32650` 中按逐栋年度供暖量计算唯一负荷中心站，并在该站与全部建筑质心之间生成确定性的欧氏最小生成树。
- 固定节点排序、距离并列、树定向和 `segment_###` 编号；适配器自动输出候选站、候选物理管段和临时候选通道 GeoJSON。
- 所有空间产物都写入 `provisional_geometric_mst`、`road_constrained=false` 和 `construction_feasibility_verified=false`，防止把调试网络误作道路优化成果。

### 验证与边界

- 专项测试覆盖负荷加权中心手算、MST 连通和边数、输入顺序不变性、逐字节重复输出、CRS/ID/负荷错误路径及源 GeoDataFrame 不变性。
- 该模块不读取道路数据、不优化站址或线路，也不提供施工可实施结论；它只为 V0 连接、成本与 Pareto 联调提供确定性候选。

## 2026-08-23 — ENERGY-V02-01 固定 V0 分时能源价格与 LHV 碳参数

### 更改内容与目的

- 新增 `provisional_v0` 假设 profile，逐小时生成用户确认的七段绝对电价，不把价格倍率误作时间权重。
- 天然气继续使用 LHV：保存 `3.54 CNY/Nm³` 和 `38.931 MJ/Nm³`，仅在输入边界派生 `0.327348386 CNY/kWh_LHV`；模型只读取派生能量价。
- 接入湖北电力 `0.4044 kgCO2/kWh_e` 和天然气直接燃烧 `0.199944 kgCO2/kWh_LHV`；后者明确为 CO₂-only V0 范围。
- v0.2 适配输出增加 `external_timeseries.parquet` 和 `assumptions_used.yaml`，正式结果发布门仍拒绝这些临时参数。

### 验证与边界

- 专项测试覆盖完整 24 小时电价、时区/连续性、一次气价换算、LHV 碳因子和统一公共时间权重；fixture 由生成脚本按同一口径重建。
- 所有价格均为未含税推荐假设；没有将其声明为武汉项目正式结算价格。

## 2026-08-23 — ADAPTER-V02-01 实现光谷 v0.2 标准源适配

### 更改内容与目的

- 输入契约升为 `competition_input_3.0.0-draft.2`，新增建筑多分区原型映射、
  新风语义和峰值容量裕度配置字段；燃气口径继续保持 LHV。
- 新增 v0.2 适配器和 CLI，将 03 的 `conditioned_area_m2` 无缩放改名为
  `heated_area_m2`，保留 04 混合用途分区，并将 05 的 kW 长表稳定排序后
  写入独立标准化目录。
- 新风负荷明确视为已包含，适配器只写语义标记，不执行二次叠加；输出源校验
  报告、适配报告和字段映射。

### 验证与边界

- 真实 v0.2 临时目录演练得到 62 栋、68 条原型分区、6 栋多分区建筑、
  543120 条负荷和 8760 小时；最小负荷 0 kW，未出现隐式缩放。
- draft.2 契约/loader 对分区主键、ID 集合、分区面积和只读哈希进行校验；
  设备、外部时序和候选网尚未补齐，因此本节点不单独运行优化。

## 2026-08-23 — INTAKE-V02-01 建立光谷 v0.2 自动接收校验

### 更改内容与目的

- 新增 `wuhan_v02` 源 profile，冻结 01–09、DeST 原型目录、文件编码、数量和
  标准字段边界；文件路径变化由 profile 管理，不污染模型契约。
- 新增只读接收校验器，并扩展 `validate_inputs.py` 支持
  `--delivery-root --source-profile wuhan_v02 --full-audit`。
- 单一用途和混合用途原型映射采用不同条件规则，避免把合法空 `zone_*` 误判
  为数据缺失；源文件开始/结束 SHA-256 必须一致。

### 验证与边界

- 真实 v0.2 全量审计通过：394 个文件全部识别，369 个 DeST CSV 均可解析，
  30 个逐时气象和 66 个逐时负荷文件均满足 `0..8759`，03/04/05 建筑 ID
  一致，05 为 543120 行。
- 专项测试 `10 passed`；两个工作簿仅作来源追溯，产生
  `WORKBOOK_PROVENANCE_ONLY` 警告，不修改、不读取其他 `IN_DATA`。
- 本节点未执行字段适配、候选生成或模型求解，不能据此声称真实案例闭环完成。

## 2026-08-21 — RELEASE-01 建立正式版 V1.0 只读发布门

### 更改内容与目的

- 新增 V1.0 发布资格检查器，核验冻结契约、正式程序版本、2160 小时完整
  供暖季、权重、能力状态、统一新核心和全部 Pareto QA。
- `v1-full` 在 Core 中使用硬约束禁止 HNS；Loader 要求 2160 个连续小时且
  每小时权重为 1。
- 发布检查只读取输入和既有结果，不运行求解、不创建目录、不修改文件；
  V0、draft 契约或接口占位状态均明确返回受阻。

### 当前结论

- 正式数据、冻结 3.0.0 契约、温度性能 Provider、自动候选生成和正式完整季
  结果尚未交付，因此当前程序只能命名为测试版 V0.x。
- 本节点实现的是发布门和阻塞报告，不伪造 2160 小时结果，也不勾选完整季任务。
- 发布门/Core 专项 `124 passed, 1 warning`；全量
  `226 passed, 12 warnings`；V0 结果实测返回发布门退出码 2；
  `git diff --check` 通过。

## 2026-08-21 — PHYSICS-01 接入物理模块接口与简化网络物理

### 更改内容与目的

- 定义热泵性能 Provider 和候选站/管网 Provider；主线只消费经校验的逐时
  `cop`、`capacity_ratio` 与候选空间对象。
- Core 接入逐时 COP/容量修正、三档管径线性热损和泵耗；热损进入站点热
  平衡，泵耗进入购电、电费和运行碳排。
- 标准输出增加逐管段逐时流量、容量、热损和泵耗，独立 QA 增加站点平衡及
  泵耗成本/碳排重算。
- `v1-full` 禁止使用 V0 固定性能 Provider；自动候选生成仍显式阻止运行。

### 验证与边界

- 手算测试覆盖逐时 COP/容量修正、`10 m × 0.1 kW/m = 1 kW` 管损和
  `50 kW × 0.02 = 1 kW_e` 泵耗。
- V0 完整 Pipeline 仍输出 12 个通过 QA 的非支配点；其管损/泵耗输入为 0，
  只代表调试简化。
- 接口/Core/Pipeline 专项 `128 passed, 1 warning`；全量
  `223 passed, 12 warnings`；`git diff --check` 通过。
- 温度插值算法、道路候选生成算法和正式参数尚未交付，本节点不宣称 V1.0。

## 2026-08-21 — V0-01 完成三模式小案例标准输出与独立 QA

### 更改内容与目的

- 新增 2 个需求节点、24 小时、1 个给定能源站、2 条物理管段、3 档管径的
  V3 合成案例；独立设备包括中央热泵、燃气锅炉、分布式热泵和水蓄热。
- 新 Core 增加离散管径选择以及储热容量、充放功率、效率、逐时损失和循环
  SOC 约束；三种模式继续使用同一模型结构。
- 标准结果按 Pareto 点导出容量、接网、管网、逐时调度、储热、成本、碳排、
  求解状态和 QA；QA 从导出所依据的物理决策独立重算成本与碳排。
- 增加完整 Pipeline 双跑回归，确认输入 SHA-256 不变且 Pareto 数值在
  `1e-9` 绝对容差内一致。

### 验证与边界

- 实际命令：`python scripts/run_case.py --case tests/fixtures/v3_smoke_case --profile v0-smoke`；
  输出 12 个非支配点，`qa_summary.json` 为全部通过。
- 专项测试 `115 passed, 1 warning`；全量 `214 passed, 12 warnings`；
  `git diff --check` 通过。
- 所有案例值均为 `synthetic_test`。温度相关 COP、管损、泵耗、自动候选生成
  和正式完整供暖季仍未实现，本节点不得称为 V1.0。

## 2026-08-21 — PARETO-01 实现三模式 epsilon-constraint 编排

### 更改内容与目的

- 每种模式独立求最低成本和最低运行碳排端点，并在端点间扫描碳上限。
- 每个点重新构建模型、显式限制 HNS、筛除支配及重复点，并标记归一化膝点。
- Pipeline 输出逐模式及合并非支配前沿，不使用主观权重或 TOPSIS。

### 边界

- 当前点文件保存成本、碳排、HNS 和标签；逐时调度等完整方案在标准输出节点落盘。
- V0 固定 5 点、V1 契约固定 11 点；完整季计算性能尚未验证。
- Pareto/Pipeline 专项 `7 passed`；全量 `211 passed, 13 warnings`；
  `git diff --check` 通过。

## 2026-08-21 — MODEL-03 接入 CRF 经济成本与运行碳排

### 更改内容与目的

- 设备、物理管段、接入和站点投资按统一折现率及各自寿命使用 CRF 年化。
- 新增购电、燃气和总运行物理碳排表达；新增单列政策碳价成本。
- 默认优化目标仍为真实年化成本加 HNS 罚值，不把碳价重复并入 Pareto 目标。

### 边界

- 当前碳排覆盖中央/分布式热泵和燃气锅炉；泵耗将在物理接口节点接入。
- 储热资本成本和运行约束尚未进入核心。
- 公式专项纳入核心测试；全量 `208 passed, 13 warnings`；
  `git diff --check` 通过。

## 2026-08-21 — PIPELINE-01 接通统一新核心入口

### 更改内容与目的

- 新增不可变 `CanonicalCaseData`、V3 只读文件加载器和统一 Case Pipeline。
- 正式 `run_case.py` 仅调用新 Pyomo 核心；旧执行链迁至显式
  `run_legacy_case.py`，不自动回退。
- 三种模式从同一 Canonical 快照构模并求解，运行清单明确记录契约、数据哈希、
  参数版本和未完成能力。

### 边界

- 当前 Pipeline 仍使用核心已有固定 COP 和单档等效管段；储热、温度 COP、
  碳排、三档管径选择和 Pareto 后续接入。
- `candidate_source=generate` 当前明确失败，不伪装成候选自动生成已完成。
- 专项验证 `4 passed`；全量 `205 passed, 13 warnings`；
  `git diff --check` 通过。

## 2026-08-21 — CONTRACT-04 补齐 V3 经济控制字段

- 更改内容：为站点固定投资、站点寿命、逐节点接入投资、接入寿命和 HNS
  罚值增加唯一配置落点；技术表补齐 `assumption_flag`。
- 目的：主线适配不得通过硬编码零值猜测缺失经济输入。
- 边界：字段已冻结，CRF 和站点/接入成本尚在后续模型节点实现。

## 2026-08-21 — CONTRACT-03 冻结 3.0.0-draft.1 输入接口

### 更改内容与目的

- 新增独立 V3 draft Schema、机器契约和中文说明，不覆盖 v2.1。
- 冻结四类设备、温度 COP 系数、三档管径、储热、管损泵耗、碳排和
  epsilon-constraint Pareto 的数据边界。
- 明确程序测试版 V0.x 与输入契约版本是两条独立版本轴。

### 验证与边界

- 本节点只冻结接口；物理模块、新主线、碳目标和 Pareto 尚未因此实现。
- Schema 专项 `5 passed`；全量 `201 passed, 13 warnings`；
  `git diff --check` 通过。

## 2026-08-21 — BASELINE-02 隔离旧契约并恢复回归基线

### 更改内容与目的

- 按 `contract_version` 分离 `competition_input_v1` legacy smoke 校验与当前
  `competition_input_v2_1` Schema。
- 新增测试基线记录，防止新契约原地解释旧固定热源案例。

### 验证与边界

- 全量验证：`196 passed, 13 warnings`；`git diff --check` 通过。
- legacy smoke 仍调用旧适配/旧模型；新 Pyomo 核心的标准输入、单命令和
  V0 小案例尚未接通。

## 2026-08-15 — VALIDATOR-02 合并输入校验增强

### 更改内容

- 保留 `competition/validation/inputs.py`、`CaseInputs` 和 `validate_case_inputs()` 作为唯一校验架构，没有引入第二套 `competition/validation.py`。
- 在兼容现有适配器、结果导出和 `run_case.py` 的前提下，增加 YAML 重复键拒绝、稳定错误码、契约输入 SHA-256 和校验前后全文件快照只读检查。
- `scripts/validate_inputs.py` 保留基础校验 API，改为输出 JSON 摘要；成功返回 0，输入契约失败返回 2。
- 新增 `tests/test_input_validation.py`，覆盖 SHA-256、只读、重复键、错误 ID/单位/字段不自动修正和未实现技术明确失败。
- 新增两份数据接口中文文档，并语义合并路线图、数据契约、README 和脚本说明；保留既有最小案例、适配器、运行入口、结果导出和成本年化状态。
- `adapt_case_to_legacy.py` 的 `--output` 增加被忽略目录下的默认值，同时保留显式输出路径兼容性。

### 合并边界

- 未覆盖 `.gitignore`、`clustering.py`、`model.py`、负荷组问题清单或最小案例；
- 未复制 `.vscode`、缓存、Notebook 输出或原项目的平行校验模块；
- 未执行 Git 暂存、提交或推送。

### 验证结果

```text
环境检查：通过
最小案例输入校验：通过（4栋建筑、24小时、1项技术）
最小案例旧格式适配：通过
tests/test_input_validation.py：6 passed
tests/test_validate_and_adapt_case.py：6 passed
tests/test_cost_formula.py：5 passed
完整测试套件：65 passed
git diff --check：通过
```

## 2026-08-11 — SOLVER-01 确定可移植求解器

### 更改内容

- 将 `_config.yaml` 的默认求解器从 Gurobi 改为 `highs`，固定 `solver_threads: 1`、`solver_time_limit_seconds: 60`、`solver_random_seed: 202611` 和 `solver_tee: false`；保留原 `mip_gap: 0.015`。
- `model.py` 的外部配置只接受 `highs` 或 `gurobi`；`highs` 在内部映射到 Pyomo 的 `appsi_highs`，配置为空时也安全回退到 `highs`。
- 在创建求解器前校验名称、MIP gap、线程数、时限、随机种子和日志开关；求解前检查请求的求解器是否可用，不把不可用 Gurobi 静默替换成其他求解器。
- 所有求解均使用 `load_solutions=False`；只有终止状态严格为 `optimal` 才将解加载到模型。不可行、时限或其他非最优状态会抛出中文错误，使后续导出不再继续。
- 新增 `tests/test_solver_interface.py`，覆盖真实 HiGHS 最优/不可行模型、空值回退、内部名称误填、不可用 Gurobi、求解参数透传和非法配置门禁。

### 更改目的

消除默认依赖商业 Gurobi、线程数随机器变化和非最优解继续导出的风险，使现有 Windows Conda 环境可以用开源 CPU HiGHS 以固定参数重复调用，同时保留用户明确选择 Gurobi 的兼容入口。

### 验证方法与结果

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/check_environment.py
conda run --no-capture-output -n urbanheatopt_env python -m pytest -q tests/test_solver_interface.py
conda run --no-capture-output -n urbanheatopt_env python -m pytest -q
git diff --check
```

- 修改前专项测试复现为 14 项失败、1 项通过，覆盖默认 Gurobi、错误空值回退、缺少可用性检查、CPU 数线程和不可行解加载等旧行为。
- 修改后 15 项求解器接口测试全部通过：真实 HiGHS 一变量模型返回 `optimal` 且值为 1；不可行模型不加载变量；不可用 Gurobi 不进入 `solve()`。
- HiGHS 收到固定的 `mip_rel_gap`、单线程、60 秒时限和随机种子，并且显式延迟解加载。
- 完整测试套件共 48 项通过；环境检查继续确认 APPSI HiGHS 可用并返回 `optimal`。

### 已知风险与边界

- 本机没有可用 Gurobi，因此只验证了其显式选择、可用性门禁和不静默回退；没有执行真实 Gurobi 求解。
- 本节点只验证求解器接口和一变量最优/不可行模型，尚未建立最小合成案例，也未运行完整 UrbanHeatOpt 成本模型、CLI 或结果导出。
- `time_limit` 等非 `optimal` 状态按当前 P0 安全口径一律失败，即使求解器可能已有 incumbent 也不加载或导出。
- 标准 `case_config.yaml` 的嵌套求解器配置将在后续完整适配器/CLI 中映射到原项目平面 `_config.yaml`；本节点没有越级实现该适配。

## 2026-08-11 — CONTRACT-01 统一负荷单位与时间输入输出

### 更改内容

- 新增 `docs/DATA_CONTRACT.md`，统一七类运行输入的文件名、字段、类型、单位、ID、时间、CRS、条件列和跨文件校验，并整理负荷组九文件交接边界。
- 新增 `competition/schemas/input_contract.yaml` 和 `case_config.schema.json`，将 kW/kWh/CNY、Asia/Shanghai、`timestamp → hour=1...N`、EPSG:4326→32650、随机种子、DHW、P0 功能边界、HiGHS 配置和 QA 容差写成机器可读规则。
- 新增 `docs/MODEL_ASSUMPTIONS.md`，明确标准输入、适配产物和原项目模板三层边界，并记录隐式单位缩放、DHW、余热、未供热、网络和成本口径等 legacy 风险。
- 新增 `docs/questions_for_load_team.md`、`docs/questions_for_project_team.md` 和 `docs/P0_DEFERRED_REAL_DATA.md`，逐项记录真实数据所需回答、责任方、当前阻塞和解除条件。
- 新增契约资源测试和单位/时间回归测试，验证 Schema、固定 kW、浮点精度、聚类守恒、`timestamp → hour=1...N` 以及错误时间/数值拒绝路径。
- 将现有 `jsonschema 4.26.0` 登记为直接环境依赖，并在 README 增加竞赛规范入口；没有执行新的依赖安装。
- 新增 `competition/adapters/load_timeseries.py`，把标准长表稳定转换为浮点 kW 旧宽表并保存时间双射；拒绝错误时区、非整点、重复、缺口、乱序、覆盖不一致、负值和非有限值。
- 删除热负荷生成器的隐式 `×1000` 和 `uint32` 截断、聚类的隐式 `/1000`，同步修正年度 kWh 对账；两项 P0 在代码与回归测试通过后勾选。

### 更改目的

先建立唯一、可机读且可测试的输入语义，使后续校验器、适配器、聚类和求解不会各自猜测单位、小时编号、生活热水、坐标系或字段名；同时把无法从现有文档确定的真实参数留给负荷组/项目组回答，不用合成值冒充武汉参数。

### 验证方法与结果

```powershell
conda install -n urbanheatopt_env --override-channels -c conda-forge `
  --freeze-installed --dry-run --json jsonschema=4.26.0 `
  openssl=3.6.3=hf411b9b_0
conda run --no-capture-output -n urbanheatopt_env python scripts/check_environment.py
conda run --no-capture-output -n urbanheatopt_env python -m pytest -q `
  tests/test_contract_schema.py tests/test_load_unit_time_contract.py
git diff --check
```

- jsonschema 直接依赖 dry-run 为 `UNLINK=0`、`LINK=0`、`FETCH=0`，确认当前环境已具备该版本且没有包变更。
- 环境检查确认 jsonschema 版本与 conda-forge 来源，并再次通过 CPU Arrow、带时区 Parquet 和 APPSI HiGHS `optimal` 验证。
- 33 个契约及单位/时间测试全部通过。
- 有效 `competition_input_v1` 合成结构通过 Draft 2020-12 Schema；标准功率单位只能为 kW，求解器配置只能写 `highs/gurobi`，固定随机种子为 202611。
- 4 栋 × 24 小时内存合成数据在长表、旧宽表和两组聚类之间累计值严格守恒；小数 kW 未被放大、缩小或截断。
- Asia/Shanghai 时间戳稳定映射为 `hour=1...24`；0 起点、缺号、重复、倒序、浮点 hour、错误时区、非整点和建筑覆盖不一致均被拒绝。
- 测试只读取仓库内文档/Schema 和内存合成字典，不读取、复制或修改真实数据。
- 未修改 `model.py`、`data.py`、`_config.yaml` 或默认 Excel。

### 已知风险与边界

- 本提交只执行负荷单位与时间边界检查；七类文件、CRS、几何和完整跨文件校验器未实现。
- 候选站坐标、网络拓扑、热源主外键和经济公式未在本节点定义或实现，不作为单位/时间任务的阻塞条件。
- 正式供暖季、供回水温度、规划期、折现率、设备参数、价格、碳因子、道路和资源数据仍待项目责任方确认；没有设置武汉默认值。
- JSON Schema 不能单独拒绝 Python/YAML 非标准 NaN/Inf，时间先后、供回水大小、ID 空白和跨文件关系也属于下一节点校验器，不宣称已执行这些检查。
- 契约登记的热泵、锅炉、储热和余热类型不表示比赛管线已经实现；P0 smoke 的目标仍只是合成固定热源。
- 负荷组九文件尚未收到，正式接收、逐栋对账、光谷案例和完整供暖季保持暂缓。

## 2026-08-11 — ENV-01 记录并固定竞赛运行环境

### 更改内容

- 清理 `environment.yml` 中重复的 Pyomo 和 Conda/Pip 双重 Pandas 声明，改为 `conda-forge + nodefaults`，补齐原代码直接使用的依赖。
- 固定 PyArrow 25.0.0 的 CPU 构建约束和 pytest 9.1.1；当前环境中既有 SciPy、HighsPy 的 pip 安装保持不变。全新环境清单改由 conda-forge 提供 SciPy，避免 Conda 先安装后再被 pip 覆盖。
- 新增 `scripts/check_environment.py`，检查解释器、主要包版本、Conda 来源、CPU Arrow、GIS 激活变量、带时区 Parquet 往返和 APPSI HiGHS 最小求解。
- 新增 `docs/ENVIRONMENT_REPORT.md`，记录零替换 dry-run、精确构建、验证证据和已知风险。
- 修正 README 的环境使用说明：优先直接激活 Conda 环境；未激活时使用带 `--no-capture-output` 的 `conda run`，并说明原激活包装脚本依赖未初始化子模块。
- 仅勾选工作包 A 的“记录 Python 和主要依赖版本”；默认 HiGHS、完整 CLI 和全新环境实际重建均保持未勾选。

### 更改目的

在数据契约和功能代码开发前建立可重复检查的运行环境门槛，确保 Parquet 标准输入和开源 HiGHS 求解能力确实可用，同时遵守“现有包零替换”和“不把尚未实现的竞赛功能视为已完成”的边界。

### 验证方法与结果

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/check_environment.py
conda env create --name urbanheatopt_env_p0_dryrun --file environment.yml --dry-run --json
git diff --check
```

- 依赖安装 dry-run 为 `UNLINK=0`、`LINK=47`、`FETCH=47`；实际只新增 PyArrow、pytest 及其必要依赖。
- Python 3.12.2 以及 Pandas、GeoPandas、Pyomo、HiGHS 等既有核心版本和构建保持不变。
- PyArrow 25.0.0、libarrow、libparquet 和 pyarrow-core 均为 conda-forge CPU 构建，没有 Arrow CUDA 包。
- 带 `Asia/Shanghai` 时区的 Parquet 精确往返通过；APPSI HiGHS 返回 `optimal`，一变量模型目标值为 1。
- 全新环境 dry-run 成功解析 342 个 Conda 包，Conda 求解部分的非 conda-forge 包和 Arrow CUDA 包均为 0；清单另有 4 个显式 PyPI 依赖，没有实际创建第二个环境。

### 已知风险与边界

- `environment.yml` 不是完整间接依赖锁文件；精确 Windows 构建记录在环境报告中。
- 当前已安装环境的 SciPy 和 HighsPy 仍来自 pip，这是为避免替换现有核心包而保留的已知例外；全新环境清单中的 SciPy 改由 conda-forge 提供，因此实际重建后仍需回归验证。HighsPy 来源迁移需另行批准。
- 全新隔离环境尚未实际创建，路线图相应工程验收项未勾选。
- Conda 25.11.1 在 Windows GBK 终端默认捕获中文输出会报编码错误，已通过不改变计算的 `--no-capture-output` 参数规避。
- 激活包装脚本依赖未初始化子模块；本节点未初始化、删除或修改该子模块。
- 本节点未修改默认求解器、模型、输入数据或案例，也未读取仓库外真实数据。

## 2026-08-11 — PREP-01 建立竞赛版轻量项目框架

### 更改内容

- 新增 `competition/adapters`、`competition/schemas`、`competition/configs`、`competition/pipelines` 和 `competition/reports` Python 包骨架，并说明各层职责。
- 新增 `cases/guanggu_software_park`、`cases/wuhan_new_city`、`scripts`、`tests/fixtures/minimal_case` 等目录说明。
- 在案例和测试目录中明确真实数据、敏感数据与合成测试数据的边界；要求合成值使用 `synthetic_test` 标记。
- 在路线图“建议的开发目录”后记录骨架建立状态，并明确骨架不代表功能已经实现。

### 更改目的

先建立稳定且语义明确的竞赛层承载位置，使后续数据契约、校验器、适配器、管线、报告和测试可以小步开发并独立回退，同时避免大规模搬动上游 UrbanHeatOpt 代码。

### 验证方法与结果

```powershell
git diff --check
git diff --name-only HEAD
Get-ChildItem competition,cases,scripts,tests -Recurse -File
```

- 新增 13 个非空文件，均位于计划允许的四个顶层目录。
- 未移动、删除或修改原作者核心代码、默认模板、历史案例、文档构建目录、Notebook 或子模块。
- 未创建校验、适配、求解等功能代码，未读取或复制任何真实输入数据。
- `git diff --check` 通过。

### 已知风险与边界

- 当前目录仅为骨架；`scripts/` 中的公共命令和 `tests/fixtures/minimal_case/` 中的合成数据将在后续 P0 阶段实现。
- `Fehring/`、`default/`、`docs/`、`docs_src/`、Notebook 和 `Conda-Activation-Scripts` 均保留原状；是否归档历史文件不属于本次任务。
- 真实案例目录只含边界说明，真实数据仍未收到，也不得因为目录存在而勾选任何真实案例验收项。

## 2026-08-11 — P0-02 修正数据文件版本管理规则

### 更改内容

- 将 `.gitignore` 从按 CSV、GeoJSON、XLSX 等扩展名全局排除，改为按正式案例原始数据目录、临时工作区、运行结果、日志和缓存目录排除。
- 明确允许 `tests/fixtures/` 中的小型 CSV、GeoJSON、XLSX、Parquet、数据字典和校验基准进入版本管理。
- 调整 `.gitattributes` 的规则顺序和类型声明，统一文本换行，并将 XLS/XLSX、Parquet、PDF、图片和 GIS 伴随文件明确作为二进制文件处理。
- 勾选路线图中的“重写数据忽略规则”P0，并记录验收范围。

### 更改目的

使后续合成案例、接口样例和校验结果可被 Git 追踪，同时继续阻止正式原始数据、临时文件和可重建结果误入仓库；避免 Git 的文本换行转换损坏二进制输入或文档。

### 验证方法与结果

```powershell
git check-ignore -v --no-index tests/fixtures/minimal_case/sample.csv
git check-ignore -v --no-index tests/fixtures/minimal_case/buildings.geojson
git check-ignore -v --no-index tests/fixtures/minimal_case/qa.xlsx
git check-ignore -v --no-index tests/fixtures/minimal_case/loads.parquet
git check-ignore -v --no-index cases/guanggu_software_park/raw/private.csv
git check-ignore -v --no-index results/minimal/smoke/run_summary.json
git check-attr -a -- tests/fixtures/minimal_case/qa.xlsx tests/fixtures/minimal_case/loads.parquet tests/fixtures/minimal_case/sample.csv
git diff --check
```

- 四类 fixture 路径均未被忽略，可正常纳入版本管理。
- 正式案例 `raw` 路径和根目录 `results` 路径仍被对应目录规则忽略。
- XLSX、Parquet 的 `text` 属性为 `unset` 且 `binary` 为 `set`；CSV 为文本且使用 LF。
- 修改前后 `default/` 中八个既有 XLSX 的 SHA-256 保持一致；未改动任何输入文件内容。
- `git diff --check` 通过。

### 已知风险与边界

- Git 无法仅凭文件扩展名判断“真实数据”或“小型样例”；团队必须继续将正式原始数据放在 `cases/**/raw/` 或仓库外受控目录，将可公开的合成数据放在 `tests/fixtures/`。
- 本次没有重新规范化历史文件，也没有移动、删除或改写原作者文件；已有历史中的文本换行状态保持不变。
- 仓库外的 `D:\\co_WH_heatOPT\\IN_DATA` 未读取、未修改，也不受本仓库忽略规则保护。

## 2026-08-10 — P0-01 建立可回溯版本管理基线

### 更改内容

- 复用现有 Git 仓库和竞赛开发分支 `WH_heatOPT_li`，未重复执行 `git init`，也未改写现有历史。
- 将上游提交 `e385220b80e86f8cc9b2006f6dec6eb082126ae3` 登记为带注释的本地标签 `upstream_snapshot`。
- 将 `CODE_TEAM_ROADMAP(1).md` 和 `PROJECT_REQUIREMENTS_CN(1).md` 规范为项目引用的正式文件名 `CODE_TEAM_ROADMAP.md` 和 `PROJECT_REQUIREMENTS_CN.md`。
- 勾选路线图中的首个 P0 任务，并记录基线提交、开发分支和验收结论。

### 更改目的

在任何功能开发前建立明确、可验证、可回退的上游基线，使后续竞赛版修改都能与原始 UrbanHeatOpt 快照进行差异比较，同时把项目要求和开发路线图纳入版本管理。

### 基线信息

- 上游远端：`https://github.com/zimu007/UrbanHeatOpt.git`
- 上游基线：`e385220b80e86f8cc9b2006f6dec6eb082126ae3`
- 基线标签：`upstream_snapshot`
- 竞赛开发分支：`WH_heatOPT_li`
- 规范命名前 `CODE_TEAM_ROADMAP(1).md` 的 SHA-256：`4C03349C4435CFE12E53D8AB32939C8F71F6DDF2BFF33FCDE2A8DF75C587CBCF`
- 规范命名前 `PROJECT_REQUIREMENTS_CN(1).md` 的 SHA-256：`D7C0520E91BA90322287AA1FABF289513711B313AA75BF8DBB5746D8892D160C`

### 验证方法

```powershell
git rev-parse "upstream_snapshot^{commit}"
git branch --show-current
git merge-base --is-ancestor upstream_snapshot HEAD
git diff --check
git diff --name-status upstream_snapshot..HEAD
git status --short --branch
```

验收要求：标签必须解析到上述上游基线，当前分支必须为 `WH_heatOPT_li`，基线必须是当前提交的祖先，相对基线不得包含代码、配置或输入数据修改，提交后的工作树必须干净。

### 已知风险与边界

- Reflog 中的提交 `88eb18e1c000ee245eac82dd02b28c455349d263` 当前仍可读取，但按用户选择不添加恢复分支或标签。若将来执行 `git gc`、`git prune` 或其他仓库清理操作，该提交可能失去恢复机会；本次未执行任何此类命令。
- 本次没有修改功能代码、配置或输入数据，也没有运行 Python、求解器或大规模案例。
- 统一输入格式、输入校验器和其余 P0 任务均明确留待后续任务，不在本次基线提交中实施。
