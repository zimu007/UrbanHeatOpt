# A任务书：主线、参数与集成

版本：2026-09-05；角色：接口负责人。与总体方向同版，V1历史与真实输入只读。

## 1. 职责和交付次序

先完成目录迁移及小案例回归；再发布四份任务文档；随后交付新版参数读取、CaseBundle及统一validate／prepare入口。B、C未完成的能力显式阻止求解，不能调用旧case.json或旧model作回退。

## 2. 已落实的目录

仓库 `D:\co_WH_heatOPT\UrbanHeatOpt`。主源码在`src/urbanheatopt`；数据在`data/`、参数在`parameters/`、空间在`spatial/`、数学在`model/`、调度在`optimization/`、QA在`qa/`、展示接口在`reporting/`。

`model/compact.py`保留当前紧凑树实现；`model/reference_core.py`和`model/road_core.py`是仍被引用的参考实现，不因历史较早而删除。`legacy/upstream`保存原作者代码，`tools/legacy_cli`是显式旧命令。原`default/Fehring`及含历史数据目录不批量删除。

迁移清单在`baselines/layout_migration.json`。V1标签、旧哈希和结果索引在`baselines/README.md`。目录节点453 passed/3 skipped、67个数学函数／类主体一致；仅重定位导入，不改公式。

## 3. 输入和参数工作包

### A1 自动读取

统一配置`configs/cases/guanggu_v2.yaml`，指向v0.2根目录，自动发现v0.3交付、LHV补丁和20260831扩展包；保留412原交付与扩展包分别审计。不得读v0.1，不按修改时间在runs找case，不修改原始数据。

复用`data/intake/guanggu_v03.py`及`data/adapters/guanggu_v03.py`的独立校验：62栋、全年8760h、供暖季8016…8759→0…1415→1…2160，跨年时区Asia/Shanghai。源时间、源小时与模型小时均追溯。负荷不包含设备COP转换和管损，不在适配器重复加入这些量。

### A2 新经济包

新增独立20260831选择规则，不覆盖`parameters/legacy_economics.py`历史读取。按parameter_id、source_id和明确状态解析；严格布尔、单位、范围、空值、重复、来源引用。code_ready、报价、时序与管型重复字段须一致，不能把表内QA结论当验收。

报价优先安装价而非设备本体价；旧寿命和旧气价登记为替代。应用表同时记录原值、标准值、单位、来源、状态和原因。显式零可接受，空白不是零。 .026仅登记。新版价格／LHV会改变快照及任务身份。

### A3 统一数据对象

交接规范位于`docs/architecture/HANDOFF_CONTRACT_CN.md`（接口节点交付），运行对象位于`data/bundles.py`。新增模块在节点完成前属于待实现路径，不等同于既有能力。

| 对象 | 主键／核心字段 | 消费方 |
|---|---|---|
| CaseBundle | interface_version、bundle_id、building_id、source_hour、hour、timestamp、heating_kW、有效参数、空间引用、文件sha256 | B只读 |
| SolveRequest | bundle_id、mode、objective、epsilon_kgCO2_per_year、TES开关、站点／树限制、solver设置 | B执行 |
| ResultBundle | request_id、point_id、solver状态／gap、结果文件索引、单位、qa状态、全部版本 | C只读 |

不可变要求：对象内无可被下游原地修改的字典／DataFrame；返回副本或冻结数据。磁盘文件以hash复验，不因Python frozen对象就宣称文件不可变。合成接口示例不伪装真实求解结果。

## 4. 数学字段的接线责任

A生成2160h新绝对电价、气价、燃气碳因子和共用1h权重；提供月度费42、虚拟总表边界、CRF折现率、TES参数、站房情景。B才把它们接入目标／约束；C独立验算。

区分以下四列并输出：读取成功、交接字段存在、B数学能力接通、实际结果验证。当前compact缺月度需量费的情况不允许用新价格时序掩盖。

## 5. 统一入口与运行指导

VS Code打开仓库根目录，在PowerShell执行（无需放宽系统脚本策略）：

```powershell
conda run --no-capture-output -n urbanheatopt_env python tools/check_environment.py
conda run --no-capture-output -n urbanheatopt_env python run.py validate --config configs/cases/guanggu_v2.yaml
conda run --no-capture-output -n urbanheatopt_env python run.py prepare --config configs/cases/guanggu_v2.yaml
conda run --no-capture-output -n urbanheatopt_env python -m pytest -q
```

validate检查源和新参数；prepare在新目录生成标准数据、有效参数、不可变CaseBundle及能力报告。输出不得覆盖历史，同一RUN_ID冲突退出2。数据错误退出2，程序异常退出1，成功0；prepare为0但model_ready=false是正确交接状态，不是完整求解成功。

solve／diagnose／tes-check／report在相应B/C适配器通过测试前必须返回未接通说明，不暗中走legacy。完整命令、输出文件及本次证据将记录`docs/runbooks/A_INTEGRATION_CN.md`。

## 6. 验收和交接

- 源哈希前后相同；输入／参数错误构模前聚合停止。
- 新价24h覆盖及12→1→2月连续；LHV单次转换，不混用旧价。
- 任何参数、单位或来源变化影响快照ID；跨模式共用同一CaseBundle。
- 下游修改返回副本不改变对象；磁盘篡改必须被hash校验发现。
- 未支持模型能力拒绝运行且未实例化求解器；无legacy回退。
- 指导书路径／命令与实际代码一致，桌面是仓库源文件同hash副本。
- 四个本地中文提交逐节点记录内容、目的、证据与剩余；不提交真实数据／结果／桌面，不自动推送。

本轮A完成后仍等待B模型接线与C独立QA，不能提前标记V2全季完成。
