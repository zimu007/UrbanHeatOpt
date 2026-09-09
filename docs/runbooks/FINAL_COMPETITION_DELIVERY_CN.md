# UrbanHeatOpt 竞赛最终运行与交付手册

## 1. 当前可复核成果

当前合格全季研究结果为：

```text
runs/v2/FULL_STUDY_20260908T182700_R3
```

它覆盖62栋建筑、2160个连续供暖小时、集中式/分布式/混合式、成本端点、词典序碳端点和ε-constraint Pareto。12个求解点全部通过资格门禁和独立QA，三模式合并前沿含5个非支配点。权威综合膝点为`hybrid_epsilon_025`：22栋集中接网、40栋分布式、候选站5，年化真实成本约4313.09万元/年，运行碳排约5365.02 tCO2e/年，未供热为0。

冻结经济参数下TES最优容量为0；这表示“该价格边界下不值得投资”，不是公式未运行。独立敏感性证据为：

```text
runs/v2/tes_sensitivity/TES_SENSITIVITY_20260909_R2
```

只把TES能量、功率和固定投资同时乘0.5后，模型选择4047.847 kWh_th，最大充/放热功率均为1921.526 kW_th；结构一致、求解最优、独立QA通过。该情景只证明TES模块能形成真实跨时充放，不替代冻结主经济结论。

当前展示成果为：

```text
deliverables/competition_final/FINAL_20260909_R5/presentation
```

其中包含9组PNG/SVG、PDF图册、展示CSV/JSON、Excel工作簿、结果导读及CP评分追踪。展示数据直接来自上述两个合格运行目录，不重新求解或修改决策。

## 2. 从干净环境安装

在仓库根目录打开PowerShell。建议把环境建立在仓库同级固定位置，避免系统中同名环境被误选：

```powershell
conda env create -p ..\.conda_envs\urbanheatopt_env -f environment.yml
.\RUN_URBANHEATOPT.cmd --check
```

环境文件固定Python 3.12.2、Pyomo 6.8.2、HiGHS 1.15.1、Pandas 2.2.2、GeoPandas 1.0.1、PyArrow 25.0.0 CPU构建、NumPy 2.1.3、SciPy 1.18.0及Pytest 9.1.1。启动器会同时设置正确的`CONDA_PREFIX`、`GDAL_DATA`和`PROJ_DATA`；不要直接调用用户目录内另一套同名环境的`python.exe`。

## 3. 一条主线运行

先验证环境，再准备不可变案例：

```powershell
.\RUN_URBANHEATOPT.cmd --check
.\RUN_URBANHEATOPT.cmd prepare --config configs\cases\guanggu_v2.yaml --run-id "PREP_<唯一时间戳>"
```

准备完成后，从输出JSON取得`case_bundle.json`绝对路径并启动全季研究：

```powershell
.\RUN_URBANHEATOPT.cmd solve --config configs\cases\guanggu_v2.yaml --bundle "<PREP目录>\case_bundle.json" --request-set full-study --run-id "FULL_STUDY_<唯一时间戳>" --output-root runs\v2
```

配置默认每个HiGHS任务使用4线程，并在CPU与可用内存允许时最多并发3个候选站进程；每个候选进程仍是独立模型、独立日志和独立QA。并发只改变执行顺序，不改变数学模型。若机器资源不足，设置`candidate_workers: 1`可串行运行。

全季运行仅在以下条件同时满足时合格：所有候选任务得到可行解或认证不可行证据，最终认证gap不超过1%，未供热相对量不超过`1e-6`，热平衡/容量/管网/成本/碳排独立复算通过，且未调用旧模型。

## 4. TES采用性检查

在合格全季研究之后执行：

```powershell
.\RUN_URBANHEATOPT.cmd tes-check --bundle "<PREP目录>\case_bundle.json" --study-root "runs\v2\<合格全季RUN_ID>" --run-id "TES_SENSITIVITY_<唯一时间戳>" --output-root runs\v2\tes_sensitivity --threads 4
```

默认依次检查投资乘数`1,0.5,0.25,0.1`，遇到最高一个产生正TES容量且有真实充、放热的情景即停止。除TES投资外不得改变负荷、站址、接网、管型、价格或物理参数。输出同时保留求解器二进制值、物理安装状态和实际使用状态，避免把数值为1但容量为0的求解器占位变量误报为“已采用TES”。

## 5. 生成成果展示

```powershell
.\RUN_URBANHEATOPT.cmd report --run-root "runs\v2\<合格全季RUN_ID>" --tes-sensitivity-root "runs\v2\tes_sensitivity\<TES_RUN_ID>" --output-root "deliverables\competition_final\<展示RUN_ID>\presentation"
```

报告器会先校验全季汇总、所选膝点、TES来源膝点、RoadCase等价性、求解状态与独立QA，再生成图表。冻结主结果和TES投资敏感性必须在标题及图注中分开，不可把敏感性结果冒充正式参数结论。

## 6. 生成四个白名单交付包

先运行测试并提交最终源码，再使用不可变Git提交号打包：

```powershell
.\RUN_URBANHEATOPT.cmd --pytest -q
conda run --no-capture-output -p ..\.conda_envs\urbanheatopt_env python tools\build_competition_release.py --repo-root . --git-ref "<最终提交SHA>" --example-input tests\fixtures\v3_smoke_case --result-root runs\v2\<合格全季RUN_ID> --tes-sensitivity-root runs\v2\tes_sensitivity\<TES_RUN_ID> --figure-root deliverables\competition_final\<展示RUN_ID>\presentation --output-root deliverables\competition_final\<展示RUN_ID>\packages
```

输出分别为源码、合成示例输入、合格结果证据和图册。工具拒绝覆盖已有目录、拒绝从`IN_DATA/raw/原始输入数据`打包、拒绝损坏或非2160小时Parquet、拒绝未通过qualified/gap/独立QA的结果。源码包只含竞赛主线、必要配置、精选测试和复现工具；GUI、历史配置、旧输入适配器、原始数据及运行目录不进入源码包。

上式直接运行独立工具脚本；`RUN_URBANHEATOPT.cmd`只转发`run.py`子命令，不能在其后直接填写工具脚本路径。

`src/urbanheatopt/parameters/legacy_economics.py`目前虽然保留历史命名，但仍被现行经济适配器调用，因此随主线源码保留；这不代表运行时回退旧`model.run_model()`。后续若重命名，必须作为单独重构并完成全量回归，不能在赛前交付节点临时删除。

## 7. 结果边界

- 当前网络优化范围是5个候选能源站各自的确定性最短路径树，不是完整自由拓扑，也不是施工图级管网。
- 输入、候选站、网络、经济参数和结果均有哈希；图表不能替代数值结果与QA。
- 当前经济参数适合比赛研究比较，不应表述为最终合同报价或施工投资结论。
- 冻结参数下TES为0是模型结论；TES 0.5倍投资情景是功能和阈值敏感性证据。
- 全季R3由旧的单候选串行设置完成；当前4线程/候选并发实现已通过合成回归和TES全季敏感性实跑，但如需对外声称新的总耗时，必须用新RUN_ID完整重跑并记录墙钟时间。
