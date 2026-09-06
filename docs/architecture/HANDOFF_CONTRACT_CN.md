# A / B / C 标准交接契约 handoff_1.0.0

版本：2026-09-05。适用范围：新输入和经济包主线的接口集成，不修改冻结 V1 数学模型。源码为 `src/urbanheatopt/data/bundles.py`，测试为 `tests/test_handoff_bundles.py`。

## 1. 三个对象分别证明什么

| 对象 | 产生者→消费者 | 能证明 | 不能单独证明 |
| --- | --- | --- | --- |
| `CaseBundle` | A→B | 输入快照、字段、版本、文件身份与状态可交接 | 模型已消费全部新参数、已求解 |
| `SolveRequest` | A统一入口→B | 本次模式、目标、ε、TES开关、求解设置明确 | 求解器可用、数学模型正确 |
| `ResultBundle` | B→C/A | 结果证据满足结构条件，关联输入及请求 | 仅有 `optimal` 就代表工程结论正确 |

三者必须显式包含 `interface_version: handoff_1.0.0`。不静默兼容旧契约，不向历史结果追加伪造的新版本标签。数据版本、参数版本、接口版本、数学模型版本是不同字段，不能互换。

内部仅存储一份规范 JSON 字符串；构造时对调用方数据复制，对外 `payload`、`to_dict()` 返回新深副本。下游修改副本不会改变 A 的输入对象。`bundle_id` 是完整规范内容的 SHA-256：参数版本、数值、文件哈希等任何内容变化都产生新身份，字典键排序不影响身份。列表顺序保留语义，A须稳定排序产物与能力列表。

## 2. CaseBundle 字段

| 字段 | 类型/规则 | 语义 |
| --- | --- | --- |
| `interface_version` | 固定字符串 | 此交接协议版本 |
| `data_version` | 非空字符串 | 源数据冻结版本 |
| `parameter_version` | 非空字符串 | 有效参数快照版本，不能只写目录名称 |
| `git_sha` | 非空字符串 | 准备本次快照的代码提交；未提交修改另存文件哈希 |
| `artifacts` | 对象列表 | 独立工作目录中的标准化产物 |
| `artifacts[].role` | 非空字符串 | 如 buildings、loads、external_timeseries、effective_parameters、spatial_candidates |
| `artifacts[].path` | 绝对路径，路径不得重复 | 标准化文件实际位置；不可从旧 runs 猜测 |
| `artifacts[].sha256` | 64位小写十六进制 | 该文件实际字节哈希 |
| `source_hashes` | `绝对路径: SHA-256`对象 | 实际消费的原始文件，不修改原件 |
| `units` | 非空字段→单位对象 | 建议显式含 kW_th、kWh_th、kWh_e、kWh_LHV、CNY/year、kgCO2e 等 |
| `capabilities_required` | 不重复的非空字符串列表 | 本情景要求模型消费的能力，不能作为“已实现”标志 |
| `status.input_valid` | 严格 JSON布尔 | 源数据校验结果 |
| `status.parameter_valid` | 严格 JSON布尔 | 数值、单位、来源、状态与选择规则通过 |
| `status.canonical_valid` | 严格 JSON布尔 | 标准数据复验通过 |
| `status.snapshot_complete` | 严格 JSON布尔 | 前三项均通过且快照产物与源哈希齐全 |

`"False"`、`"true"`、0、1均不等价于布尔值。NaN/Infinity和不可JSON序列化对象拒绝进入接口。这里不重复定义每张负荷表的完整schema；表级字段仍由输入校验器负责。

`verify_artifacts(check_sources=True)`重新读取产物和源文件并逐个对比哈希；发现删除、替换或修改时返回失败及具体路径。构造对象不是磁盘验收，调用方必须在构模前复验。默认 `write()`使用独占创建，不覆盖已有文件。

## 3. SolveRequest 字段

| 字段 | 允许值/单位 |
| --- | --- |
| `case_bundle_id` | 被消费CaseBundle的SHA-256 |
| `mode` | central / distributed / hybrid |
| `objective` | cost / carbon / lexicographic_carbon |
| `epsilon_carbon_kg` | null 或有限非负 kgCO2e；仅cost目标使用碳上限 |
| `tes_enabled` | 严格布尔，不隐式默认为开启 |
| `solver.name` | highs / gurobi / auto；不代表许可证或可用性检查通过 |
| `solver.threads` | ≥1的整数，不接受布尔值 |
| `solver.random_seed` | ≥0的整数 |
| `solver.mip_gap` | [0,1]有限数值 |
| `solver.time_limit_s` | null 或有限正数，秒 |

站点/树、网络版本等附加字段可带入请求并参与身份哈希，但须由后续B消费接口另行校验。本版不把未经消费者验证的附加字段解释为已启用约束。

## 4. ResultBundle与资格判定

必需字段：`interface_version`、`case_bundle_id`、`solve_request_id`、`run_id`、`artifacts`、`solver_executed`、`termination_condition`、`qualified`。

终止条件允许：not_executed、optimal、feasible、infeasible、unbounded、error、interrupted、time_limit。未执行求解只能写not_executed。未求解、失败、未认证超时不能标记qualified。

`qualified=true`还要求：

- `solve_evidence`中incumbent、best_bound、certified_gap、accepted_gap都是有限非负数值；最小化下界不大于可行上界且达到gap要求。按现有接口口径重算 `abs(incumbent-best_bound)/max(1,abs(incumbent))`，与声明gap误差不得大于1e-8，不能仅手填gap=0。
- `qa.passed=true`。
- 产物至少具有solver_log和independent_qa两类证据文件路径及SHA-256。

这些是防止显然错误状态的结构门槛，不替代B的可行性/gap证书，也不替代C从导出明细的独立重算。C不能以目标函数值重新命名为“重算成本”，不能为展示新增虚构求解点。通过结构检查的合成接口测试不是项目真实求解结果。

## 5. 新主线就绪门禁（2026-09-06更新）

`ready_report(case_bundle, integration_evidence=...)`独立输出`input_ready`、`parameter_ready`、`model_capability_ready`、`research_solve_ready`、`publication_ready`、`solver_executed`和`result_qualified`，且列出阻塞项与负责人。

A侧现在自动生成5个虚拟研究候选站、62栋/2160小时容量边界、三种模式的成本SolveRequest，并以真实形状CaseBundle调用B1经济投影和B2容量投影。联调证据写入`ab_adapter_smoke.json`；它不是YAML手填状态，也不实例化求解器。DN250/400/500物理参考容量不进入执行边界，执行侧使用已确认的规划容量代理档。

`research_solve_ready=true`仅表示研究输入和B1/B2接口具备交接条件；它不等于已经构模、已经求解或结果合格。站房固定投资边界未确认期间，`publication_ready`必须为false。即使研究门禁通过，`solver_executed`仍为false，直至B产生带求解日志和独立QA的ResultBundle。正式入口仍禁止静默调用legacy或V1现成结果。

常见能力ID：

| ID | 下一责任与完成条件 |
| --- | --- |
| `monthly_demand_charge` | B：虚拟总表月度最大需量费表达式及三个月手算 |
| `effective_parameter_mapping` | B：有效参数逐项进入模型，费用边界及重算通过 |
| `tes` | B：有/无储热单例、效率及SOC首末闭合通过 |
| `site_capacity` | A/B：5个研究候选站、站点总容量、设备/电/气上限经B2消费 |
| `pipe_capacity` | A/B：三档规划容量经B2消费，DN参考容量只作审计 |
| `result_bundle` | B输出、C复算：新结果字段/单位/哈希与展示接口联调 |

输入和参数通过而B1/B2联调失败时，`prepare`输出退出码2及具体错误；联调通过时可交付研究任务，但准备完成仍不等于新增经济包已在优化目标中求解或正式发布。

## 6. 调用与验证

在仓库根目录运行接口专项测试：

```powershell
conda run -n urbanheatopt_env python -m pytest tests/test_handoff_bundles.py -q
```

API示意（payload须来自A已校验的准备过程，不是手工拼造正式数据）：

```python
case = CaseBundle.from_dict(payload)
case.write(new_run_directory / "case_bundle.json")
case = CaseBundle.read(new_run_directory / "case_bundle.json")
integrity = case.verify_artifacts(check_sources=True)
evidence = json.loads((new_run_directory / "ab_adapter_smoke.json").read_text())
readiness = ready_report(case, integration_evidence=evidence)
# research_solve_ready与publication_ready、solver_executed必须分别判断。
```

专项测试覆盖：深层不可变、规范身份、版本变化、独占写入、产物/源文件篡改、严格布尔与数值、非法请求、失败资格、日志/QA证据要求，以及门禁不导入或调用模型/求解器。接口不修改输入文件，不执行全季优化。
