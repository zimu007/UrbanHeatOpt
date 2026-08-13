# UrbanHeatOpt 竞赛版变更记录

## 2026-08-13 — MODEL-03 实现双设备简单年化经济目标

### 更改内容

- 新增冻结的 `EconomicInput`，显式接收公共逐时年度小时权重、逐时绝对电价、逐时绝对 LHV 气价、预期权重总和、逐需求节点接入投资/寿命和 HNS 罚值；所有映射均复制并冻结。
- 因固定维护字段和热量单位发生破坏性变化，将当前契约升级为 `competition_input_v2_1`；v2 明确弃用且禁止自动迁移。固定维护改为范围 `[0,1]` 的无量纲年度比例，按 `capacity × capex_CNY_per_kW × fraction` 计算；可变运维统一为 `CNY/kWh_th`。
- 为每条物理无向管段补充 `pipe_capex_CNY_per_m` 和 `lifetime_years`，为每个需求节点补充接入投资与寿命；分别按 `built×length×CAPEX/lifetime` 和 `connected×CAPEX/lifetime` 简单年化，一条物理段只计费一次。
- 建立设备、管网、接入年化投资，固定/可变运维、电费、气费、真实成本、HNS 罚项的正式 Pyomo Expressions；所有公开年度成本表达式以 `_CNY_per_year` 标明单位。删除上一节点的 HNS 临时代理目标，并断言模型只有一个活动的正式目标，目标引用 `optimization_objective_CNY_per_year = annual_real_cost_CNY_per_year + annual_hns_penalty_CNY_per_year`。
- 严格校验逐时权重全部 `>0`、逐时电气价格非负、权重总和与 `expected_weight_sum_h_per_year` 在绝对容差 `1e-9` 内一致，且接入映射必须完整覆盖需求节点。
- 新增体积气价输入边界标准化函数；只有体积气价与 LHV 使用同一体积基准时才转换一次为 `CNY/kWh_LHV`，模型核心不重复换算。
- 更新机器契约、案例 JSON Schema、数据契约、模型假设、项目待确认/真实数据暂缓清单和路线图；明确简单年化不使用折现率、CRF、规划期、更换投资或残值，附件未定义的站点固定 CAPEX 不纳入。

### 更改目的

把区域空气源热泵和燃气锅炉作为独立设备进行投资、调度和能源费用计算，并按用户批准口径建立单位闭合、可手算、可审计的年化成本目标，避免旧模型合并热源、隐藏价格缩放或混合真实成本与可靠性罚项。设备/管网公式来自附件；接入年化和 HNS 罚值是项目批准扩展，未伪装成附件原文。

### 验证方法与结果

```powershell
python -m pytest tests/test_contract_schema.py tests/test_competition_core_model.py -q
python -m pytest -q
```

- 专项回归共 `134 passed`，全仓回归共 `170 passed`；唯一警告为既有 GeoPandas/Shapely 弃用提醒。两小时中央案例手算得到：设备年化投资 6,600 CNY/year、管网 2,000、接入 1,000、固定维护 1,950、可变运维 8,549.2、电费 66,800、气费 45,170.4，真实成本和唯一目标均为 132,069.6 CNY/year；七项按绝对误差 `<=1e-6 CNY/year` 重算。
- 分布式案例单独核验本地热泵投资、维护、可变运维和电费；混合案例核验一栋接网、一栋本地时设备、物理管段和接入成本不重复；用户给出的两小时示例固定公共权重为 1，并分别使用电价 0.6/1.0、气价 0.8/1.0。
- 强制短缺案例的真实成本与 HNS 罚项分别计算；非法权重、权重和、价格、接入/管网投资及寿命均在构模前失败。
- 以附件示例 `3.54` 和明确传入的 `35.588 MJ/同体积基准` 做一次性换算回归，得到约 `0.35809823536 CNY/kWh_LHV`；数值只用于公式测试，不是正式武汉默认值。
- 本节点未修改旧 `model.py`、默认 Excel或仓库外 `IN_DATA`。

### 已知风险与边界

- 附件原文只写 `3.54 CNY/m³`，未确认体积基准状态、配套 LHV 与锅炉效率热值口径；正式转换仍保持阻塞。
- 附件只列出维护系数字段名，没有 `0.026` 或其他可直接采用的维护数值；代码没有编造默认值。附件文件为 `年化总成本.docx`，SHA-256 为 `E01E902AADD3B550A64366C35213F5B221AE10F5E049364AB9D2D447D2DEFD72`。
- 正式设备、管网、接入投资/寿命、能源价格、HNS 罚值和年度小时权重仍待项目责任方提供；所有测试数值均为 `synthetic_test`。
- 当前只实现内存 Pyomo 核心，尚未完成七文件校验器、适配器、最小 fixture、CLI、结果导出、余热成本或碳排。路线图仅勾选已验证的逐时电气成本和本轮公式手算，余热与碳排保持未勾选。

## 2026-08-12 — MODEL-02 建立三模式源—网—荷核心

### 更改内容

- 将竞赛核心扩展为严格的 `central`、`distributed` 和 `hybrid` 三种模式，输入包含一个 `site_node`、多个 `demand_nodes`、三种独立设备角色及按物理无向段一行登记的 `SegmentSpec`。
- 新增逐需求节点的本地空气源热泵安装、容量、逐时出力和显式耗电表达；中央模式强制全部接网，分布式模式强制无站无网，混合模式通过二元变量保证每个节点在接网与本地设备间二选一。
- 每条物理管段只有一个 `pipe_built`、一个容量和一个有符号热流变量；输入端点顺序定义流量正方向，反转端点只改变流量符号，不重复建设或计数。
- 新增基于物理段的商品流连通约束，接网需求节点必须从唯一站点可达；混合模式候选拓扑不可达的节点会被固定为本地供热，不能隔空接网。
- 将 `unserved_heat_kW` 真正纳入每个需求节点、每个小时的热平衡；不再预检拒绝总容量不足，使短缺在模型解中显式出现。
- 临时目标仅最小化总未供热量，不揉合不同量纲的 epsilon 或代理成本；装机、拓扑和技术选择在年化成本加入前不得解释为经济最优。

### 更改目的

建立一个可由 HiGHS 验证的最小源—网—荷物理闭环，使中央热源、建筑本地热泵、接网选择、管段建设、双向输热和未供热量拥有明确且互不混淆的模型语义，为下一节点加入附件年化公式和逐时能源价格提供稳定边界。

### 验证方法与结果

```powershell
conda run --no-capture-output -n urbanheatopt_env python -m pytest -q tests/test_competition_core_model.py
conda run --no-capture-output -n urbanheatopt_env python -m pytest -q
git diff --check
```

- 专项测试覆盖三种模式、混合案例一栋接网一栋本地、单物理段单建设变量、端点反转与流量符号、站点/节点逐时守恒、容量不足短缺、错误端点、自环、重复无向段和不可达拓扑。
- 单小时中央案例仍通过 60 kW 热泵、40 kW 锅炉、15 kWh_e 和 44.444... kWh_LHV 手算；强制中央总容量仅 40 kW 时，100 kW 负荷得到 60 kW 显式未供热量。
- 未修改旧 `model.py`、默认 Excel、求解配置或仓库外 `IN_DATA`。

### 已知风险与边界

- 当前只有一个中央站点且无中继节点；不支持多站竞争、站点自动生成、真实道路最短路径、管网热损失、泵耗、储热或余热。
- 当前模型只使用合成内存输入，没有实现 v2 七文件校验器、适配器、最小 fixture、CLI 或正式 S0～S3 场景运行。
- 临时目标仅用于优先减少未供热；存在多种零短缺解时，容量、建网和设备组合可能退化，不能作为经济结论。下一经济节点必须替换该目标并断言只有一个正式活动目标。
- 本节点不实现附件年化公式、电价、气价、时间权重、CAPEX/O&M 分项或成本导出；相关路线图项目保持未勾选。

## 2026-08-12 — MODEL-01 建立中央热泵与燃气锅炉独立核心

### 更改内容

- 新增独立于旧 `model.py` 的竞赛中央双设备核心，使用与 v2 `technologies.csv` 同名的 `TechnologySpec` 静态字段，并由设备类型、适用范围和能源载体派生中央空气源热泵与中央燃气锅炉角色。
- 两类设备分别建立装机容量和逐时热出力变量，受各自容量上下限约束，并共同满足一个汇总中央热负荷平衡。
- 固定性能模型显式计算 `热泵耗电 = 热输出 / COP` 和 `锅炉 LHV 耗气 = 热输出 / efficiency`；COP、效率、载体、范围、ID、容量、来源及契约静态字段在构模前严格校验。
- 新增竞赛层安全 Pyomo 求解接口，与既有安全口径一致：默认 HiGHS、单线程、固定时限和随机种子、延迟加载，只在 `optimal` 后加载变量；未调用或修改旧模型的求解方法。
- 新增中央双设备专项测试，覆盖独立集合/容量/调度、手算能耗、错误性能与角色、时间/负荷、求解设置、不可用求解器和非最优不加载。

### 更改目的

把中央空气源热泵和中央燃气锅炉从旧模型的通用 `isBoiler/OMVarCost` 结构中分离出来，使 COP、效率、电耗和 LHV 燃气耗成为可验证的模型量，为后续分布式设备、三模式、管网和年化成本扩展建立不依赖 legacy 语义的核心接口。

### 验证方法与结果

```powershell
conda run --no-capture-output -n urbanheatopt_env python -m pytest -q tests/test_competition_core_model.py
conda run --no-capture-output -n urbanheatopt_env python -m pytest -q
git diff --check
```

- 专项测试使用一个合成中央负荷，不读取任何真实数据。100 kW 单时段案例通过容量上限强制为 60 kW 热泵与 40 kW 锅炉，得到 15 kWh_e 和 44.444... kWh_LHV，与手算一致。
- 竞赛层 HiGHS 实际求解达到 `optimal`；非法配置在创建求解器前失败，不可用求解器不进入 `solve()`，不可行模型不加载变量。
- 本节点未修改旧 `model.py`、默认 Excel、配置文件或仓库外 `IN_DATA`。

### 已知风险与边界

- 本节点仅实现一个汇总中央负荷以及中央热泵、中央燃气锅炉；尚未实现 v2 契约要求的分布式空气源热泵、全分布式/全集中式/协同三模式、建筑接入、候选站、管网或未供热量。
- 当前目标函数只是最小总装机容量的非经济代理，用于获得确定的可解模型；未读取电价、气价、CAPEX、固定/可变运维或年化权重，不得称为年化总成本优化。
- `TechnologySpec` 已接收完整 v2 静态字段，但本节点仅消费物理性能与容量字段；经济字段将在后续年化成本节点中使用。
- 当前固定 COP/效率不随室外温度、供水温度或部分负荷率变化；性能曲线仍保持未完成。

## 2026-08-12 — CONTRACT-02 发布独立设备与能源价格输入契约 v2

### 更改内容

- 将比赛运行入口升级为 `competition_input_v2`，废止 v1 以 `fixed_heat_source/synthetic_heat` 代表所有设备的可执行口径。
- 冻结三个必需且 ID 相互独立的设备角色：中央空气源热泵、中央燃气锅炉和分布式空气源热泵；使用 `technology_type` 与 `applicable_scope` 表达，不新增含糊的“区域热泵”类型。
- 空气源热泵必须使用电力载体、固定 `COP>0` 且效率为空；燃气锅炉必须使用燃气载体、固定 LHV 效率 `0<efficiency<=1` 且 COP 为空。
- 统一电力输入 `kWh_e`、燃气输入 `kWh_LHV`、绝对电价 `CNY/kWh_e`、绝对 LHV 气价 `CNY/kWh_LHV` 和公共年化权重 `time_weight_h_per_year`；禁止价格 multiplier/index 和旧电价列名。
- 电力碳因子同步改为 `electricity_carbon_kgCO2e_per_kWh_e`，与 LHV 气侧因子共同保留输入追溯；当前成本目标不消费碳因子。
- 更新机器契约、案例 Schema、数据契约、模型假设和资源测试；路线图只勾选本输入契约节点，未勾选输入校验器、最小样例、独立设备模型、年化成本或碳排能力。

### 更改目的

纠正把区域热泵和燃气锅炉预先折算为一个合成有用热源的错误边界，使 COP、效率、能源输入和逐时价格成为后续模型可验证的独立输入，同时避免设备专属时间权重和相对价格指数造成无法追溯的年化成本。

### 验证方法与结果

```powershell
conda run --no-capture-output -n urbanheatopt_env python -m pytest -q tests/test_contract_schema.py
git diff --check
```

- 契约测试覆盖 v2 Schema、v1 拒绝、至少三个唯一启用 ID、三种设备角色、COP/效率与载体规则、绝对电气价格、公共正权重及碳因子单位命名。
- 本节点不读取真实数据，不修改 `model.py`、`_config.yaml`、默认 Excel 或仓库外 `IN_DATA`。

### 已知风险与边界

- JSON Schema 只能约束 `enabled_technology_ids` 至少三个且唯一；三个 ID 是否分别解析为中央热泵、中央燃气锅炉和本地热泵，必须由后续跨文件校验器执行。
- 当前旧模型仍将可调热源归入 `isBoiler` 集合，并使用合并的 `OMVarCost`；它尚未按 technology_id 独立建立容量、出力、电耗、燃气耗和成本分项。
- 正式 COP、效率、投资、运维、寿命、电气价格和 `time_weight_h_per_year` 数值仍需项目责任方提供；本节点只冻结格式和公式边界，没有编造武汉参数。

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
