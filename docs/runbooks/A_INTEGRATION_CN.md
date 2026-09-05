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

本次最新输入复验在`work/guanggu_v2/A_HANDOFF_FINAL_20260905`，使用conda run：443源文件（原交付412＋旧经济4＋新经济17＋设备补丁10），input_valid=true，canonical_valid=true；62栋、2160h、133920行标准负荷。前后源哈希一致。此前`A_REAL_VERIFIED_20260905`、`A_HANDOFF_20260905`也保留，不能覆盖。

parameter_valid=false、snapshot_complete=false、model_ready=false、solver_executed=false；退出码2是**包内许可冲突被正确拦截**，不是HiGHS算不动。

新版参数主表8项code_use_allowed=1，其来源表3个source_id仍为0：SRC_HOT_TES_HOSPITAL_PRICE、SRC_HOT_TES_HOSPITAL_SCOPE、SRC_WB_HEBEI_CLEAN_HEATING_2017。明细见桌面缺失清单及run_summary.errors。等待确认逐参数许可是否覆盖旧来源禁用；目前选择策略为require_consistent，不能自行删除校验使状态变绿。

用户确认“未标暂定视为冻结”已落实为numerical_freeze：研究使用冻结与原source范围分开。proxy/public/literature属性不被改成实际合同。显式provisional/pending/debug/assumption仍标暂定；code_use_allowed许可独立检查。

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
| parameter_selection.csv | 逐参数应用/未采用原因，model_consumed=false |
| inputs/external_timeseries.parquet | 全部准备通过后供B消费的新经济时序 |
| case_bundle.json | 全部准备与hash通过后生成，不允许半合格冒充 |
| model_readiness_report.json | 每次都输出；参数未过亦明确未构模 |
| spatial_interface_report.json | 可选候选站/容量接口的读取状态；缺失≠无限 |
| input_gaps.json、缺失数据清单.md | 同一机器清单渲染；桌面同步带备份 |

当前参数冲突时不会生成effective_parameters和CaseBundle，这符合设计；不要使用旧运行生成的同名文件充数。

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
- 环境缺包：报告缺项，不未经同意安装或升级；GDAL_DATA警告与几何读写失败区别记录。本次直接调用环境内python未激活GIS变量的失败已复核：改用conda run后GIS/Parquet/APPSI小模型通过，只剩highspy版本要求1.15.1而本机1.11.0；记录在runs/A_INTEGRATION_20260905/conda_environment。未安装或放宽版本校验。
- 原始参数需要更正：由参数组维护源文件、ID不改；新数据必须重新validate/prepare，不沿用旧snapshot_id。

## 6. 交接状态

A：目录与任务书已提交；参数和接口代码与成功/失败合成测试已交付，真实源标准化通过；新包许可冲突阻止参数最终验收。

B：新版字段→目标函数（尤其月需量）、地块/容量约束、有无TES单例和V2端点未在本轮实施。C：ResultBundle独立复算及展示未在本轮实施。老师/用户：来源许可冲突裁决及地块/容量研究边界。

本轮未提交任何原始数据、桌面副本或运行结果，不推送。V1结果目录、标签和数学实现保留。

最终稳定代码全量回归552 passed/3 skipped/42 warnings，证据`runs/A_INTEGRATION_20260905/acceptance_stable`。67个数学函数/类主体与原基线等价，原参考核心迁移后冻结哈希通过。此前失败日志保留：包括合成测试根目录不存在run.py的修复、测试期间代码hash变化拒绝旧任务；最终回归在源码不再变更时重跑，不放宽门禁。
