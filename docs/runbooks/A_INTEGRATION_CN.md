# A输入与主线集成操作手册

更新：2026-09-05。只完成输入、参数、接口和门禁，不进行V2优化／TES／绘图。

## 1. 最短操作

VS Code打开`D:\co_WH_heatOPT\UrbanHeatOpt`，新建PowerShell终端。无需执行.ps1或修改Windows执行策略。

```powershell
conda run --no-capture-output -n urbanheatopt_env python tools/check_environment.py
conda run --no-capture-output -n urbanheatopt_env python run.py validate --config configs/cases/guanggu_v2.yaml
conda run --no-capture-output -n urbanheatopt_env python run.py prepare --config configs/cases/guanggu_v2.yaml
```

前两条分别检查环境和真实完整源数据＋新经济参数。prepare额外生成供暖季标准文件并验证。所有路径相对仓库根解释，不从历史runs搜索case。自动产生唯一RUN_ID；可附`--run-id MY_A_CHECK_001`指定新ID，已存在时报错2，不覆盖。

输出默认`work/guanggu_v2/<RUN_ID>/`，终端末尾`output_dir`给完整路径；桌面`缺失数据清单.md`自动同步，旧版备份为`缺失数据清单.backup_时间戳.md`。

## 2. 当前真实结果及正确理解

20260905按用户最终报价决定重新准备，输出在`work/guanggu_v2/A_FINAL_QUOTES_VERIFIED_20260905`：443源文件（原交付412＋旧经济4＋新经济17＋设备补丁10），input_valid=true，canonical_valid=true；62栋、2160h、133920行标准负荷，前后源哈希一致。此前许可冲突时的`A_HANDOFF_FINAL_20260905`及本次首次成功的`A_FINAL_QUOTES_20260905`保留，不覆盖。

parameter_valid=true、snapshot_complete=true；prepare退出0，已生成有效参数和CaseBundle。model_ready=false、solver_executed=false仍是正确状态：B/C消费适配器尚未验收，不能把输入准备成功当作优化完成。

此前8项主表允许、来源禁用的绑定已由用户确认按最终报价处理。配置策略为final_quote_override_20260905；原来源表仍为0，源文件未修改，effective_parameters.permission_resolutions逐项保留处理依据。只有已确认绑定被放行，未知来源、禁用执行参数、空值和单位错误仍失败。详见[决定记录](../decisions/FINAL_QUOTE_PERMISSION_20260905.md)。

用户确认“未标暂定视为冻结”已落实为numerical_freeze：研究使用冻结与原source范围分开。proxy/public/literature属性不被改成实际合同。显式provisional/pending/debug/assumption仍标暂定；code_use_allowed许可独立检查。

用户进一步确认审计报价按本研究最终值展示，quotation_display_status与quotation_display_policy记录这一口径。不重复收集已有报价；不把设备本体价和安装价、历史管价和最新管价同时计入。最终参数不等于已经产生最终优化结果。

## 3. 完整自动链

1. 配置根目录定位v0.3与LHV补丁；拒绝v0.1，输出限制为仓库work/runs。
2. 递归库存、分类、SHA、可读性、建筑/区域ID、面积、负荷543120行、小时、时区、曲线、全局天气副本和Building_TS对账。原QA报告只是旁证。
3. 新17文件扩展单独检查。PDF/PNG仅验签名与SHA，明确未做内容真伪鉴定。
4. 参数按稳定ID及单位登记，87主参数、46时价登记、11设备报价、3管型；详细表ID集合及值/来源/状态与主表相符。三档价格、DN、热损、泵耗交叉核对。
5. 供暖季8016…8759→0…1415，模型hour1…2160，跨年Asia/Shanghai；负荷不缩放。
6. 原接收快照在source_accepted，供B消费的新版绝对价格在inputs/external_timeseries.parquet；不能误用source_accepted里的历史价。新字段保留source_v03_*追溯。
7. 新电价平价×冬季倍率；新包起止时段为空，显式采用旧包同ID已确认政策分段0–6、6–12、12–14、14–16、16–18、18–20、20–24；倍率只读新包。气价/碳因子均从体积值及新LHV重新计算一次，不对已标准价格再次除热值。
8. 写参数选择理由、快照和CaseBundle；原始输入再hash检查并重扫库存，变化则拒绝完整快照。
9. 新模型无验收通过的B消费适配器时，就绪报告如实阻止求解；solve/report等不回退V1或legacy。

## 4. 输出文件

| 文件／目录 | 用途 |
|---|---|
| run_summary.json | 分开记录输入／参数／标准化／模型／求解／结果状态与退出码 |
| source_validation_report.json | 全源独立校验、分类、计数与警告 |
| input_hashes_before/after.json | 原数据及配置完整性，前后比较 |
| code_provenance.json | Git SHA、未提交状态、实际Python源码hash |
| source_accepted/ | 保留源口径的标准化负荷、建筑、曲线及时间映射 |
| effective_parameters.json | 参数合法时生成：值、来源、状态、冻结/暂定、选择理由、快照ID |
| permission_resolutions（上项JSON内） | 8项原许可冲突、最终报价依据及是否选用；不改源许可字段 |
| parameter_selection.csv | 逐参数应用/未采用原因，model_consumed=false |
| inputs/external_timeseries.parquet | 全部准备通过后供B消费的新经济时序 |
| case_bundle.json | 全部准备与hash通过后生成，不允许半合格冒充 |
| model_readiness_report.json | 每次都输出；参数未过亦明确未构模 |
| spatial_interface_report.json | 可选候选站/容量接口的读取状态；缺失≠无限 |
| input_gaps.json、缺失数据清单.md | 同一机器清单渲染；桌面同步带备份 |

当前成功运行已生成effective_parameters和CaseBundle。若将来出现新错误，仍不得用旧运行同名文件充数。

桌面清单只保留4类：候选站位置/容量、管型热力容量、站房费用拆分、0.026单位语义；后两项是基础研究情景的可选完善。已有报价、旧DN敏感性记录不再列缺失，B/C代码工作只留在就绪报告及任务书。清单整理不新增变量、时段、约束或工程审批要求。

## 5. 验证及排错

```powershell
conda run --no-capture-output -n urbanheatopt_env python -m pytest -q
conda run --no-capture-output -n urbanheatopt_env python tools/verify_layout.py
conda run --no-capture-output -n urbanheatopt_env python tools/check_core_model_frozen.py
conda run --no-capture-output -n urbanheatopt_env python run.py solve --config configs/cases/guanggu_v2.yaml
```

最后一条当前应退出2并提示B消费者未接通；不是优化命令已完成。完整快照存在后，可附`--bundle <完整case_bundle.json路径>`复核hash及缺少能力，仍不会静默求解。

- 退出0：仅该命令所述校验／准备通过；看其他状态不要推断求解成功。
- 退出2：输入、参数、路径、版本或尚未接通能力；读errors及缺失清单。
- 退出1：程序异常；保留日志、运行目录和code_provenance交A，不手动补0。
- 工作目录错误：先`cd D:\co_WH_heatOPT\UrbanHeatOpt`。
- 若终端不识别conda，把命令开头的`conda`替换成`& 'C:\Users\leonl\anaconda3\Scripts\conda.exe'`，其余参数不变；无需改系统PATH或脚本执行策略。
- 环境缺包：报告缺项，不未经同意安装或升级；GDAL_DATA警告与几何读写失败区别记录。本次直接调用环境内python未激活GIS变量的失败已复核：改用conda run后GIS/Parquet/APPSI小模型通过，只剩highspy版本要求1.15.1而本机1.11.0；记录在runs/A_INTEGRATION_20260905/conda_environment。未安装或放宽版本校验。
- 原始参数需要更正：由参数组维护源文件、ID不改；新数据必须重新validate/prepare，不沿用旧snapshot_id。

## 6. 交接状态

A：目录、任务书、参数和接口已交付，真实源标准化及最终报价参数通过；已生成可供B消费的完整快照。输入准备不要求B/C工作完成。

B：新版字段→目标函数（尤其月需量）、地块/容量约束、有无TES单例和V2端点未在本轮实施。C：ResultBundle独立复算及展示未在本轮实施。老师/用户：仅后续地块/容量研究边界等仍需提供，来源许可冲突已解决，不再重复询问。

本轮未提交任何原始数据、桌面副本或运行结果，不推送。V1结果目录、标签和数学实现保留。

上一节点全量回归552 passed/3 skipped，证据`runs/A_INTEGRATION_20260905/acceptance_stable`。本节点603 passed/3 skipped/42 warnings，最新全量证据为`runs/A_INTEGRATION_20260905/final_quotes_tests`，真实准备证据为`final_quotes_verified_prepare`，独立文件与参数复验为`final_quotes_artifacts`。67个数学函数/类主体与原基线等价，参考核心冻结复验通过。此前失败日志保留，不以旧通过结果代替新回归。
