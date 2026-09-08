# UrbanHeatOpt 竞赛代码

本仓库用于光谷片区供热源荷匹配与成本—运行碳排规划优化，不是施工图设计工具。

## 当前入口与状态

新主线源码为 `src/urbanheatopt/`，根入口为 `run.py`。本轮只负责目录、输入参数和模块集成，不实现B的模型升级或C的可视化。

固定环境和统一启动命令：

```powershell
.\RUN_URBANHEATOPT.cmd --check
.\RUN_URBANHEATOPT.cmd --pytest -q
.\RUN_URBANHEATOPT.cmd --help
```

启动器使用实际环境前缀而非同名环境，避免误用另一套Python或HiGHS。新电脑安装方式、固定版本和排错见[固定环境说明](docs/runbooks/ENVIRONMENT_SETUP_CN.md)。

`run.py validate/prepare`已接入自动源校验、新经济包、全供暖季标准化和RoadCase构建；`run.py solve`显式消费CaseBundle与SolveRequest，调用紧凑五树新核心，不回退旧模型。生产请求集支持三模式成本端点、词典序碳端点、无TES的ε-constraint前沿与膝点，以及集中式/混合式膝点结构固定后的TES ON复算；纯分布式没有区域站TES，明确记为不适用。report、diagnose仍是未启用门禁。源码入口无需安装新依赖；项目打包定义见pyproject.toml。

```powershell
.\RUN_URBANHEATOPT.cmd validate --config configs\cases\guanggu_v2.yaml
.\RUN_URBANHEATOPT.cmd prepare --config configs\cases\guanggu_v2.yaml
.\RUN_URBANHEATOPT.cmd solve --config configs\cases\guanggu_v2.yaml --bundle "<prepare目录>\case_bundle.json" --request-set full-study --run-id "<唯一RUN_ID>" --output-root runs\v2
```

准备输出在`work/guanggu_v2/<新RUN_ID>/`，求解输出在`runs/v2/<新RUN_ID>/`。真实输入、参数、网络和62栋×2160h RoadCase已能自动准备；求解只有在显式给出请求、新目录且全部候选站界与QA合格时才产生ResultBundle。代码接线通过不代表真实全季结果已经完成。详细操作与状态见[集成手册](docs/runbooks/A_INTEGRATION_CN.md)，总体方向及三人分工见`docs/team/`。

## 目录

- `src/urbanheatopt/data/`：校验、适配与标准输入；`parameters/`：参数与情景。
- `spatial/`：候选网络；`model/`：紧凑模型及参考模型。
- `optimization/`：求解与任务；`qa/`：独立复算；`reporting/`：结果读取与历史绘图接口。
- `configs/`：运行配置；`tests/`：回归及合成fixture。
- `docs/architecture/`：结构与接口；`docs/team/`：任务书；`docs/runbooks/`：操作说明。
- `legacy/`：旧入口依赖及历史文档；`tools/legacy_cli/`：显式历史命令，不是新主线默认命令。
- `baselines/`：V1固定版本、迁移清单、哈希与复现边界。

## 冻结成果与历史边界

V1 R3结果对应`compact-fullseason-v1-r3`（58d83226）；当前整理前代码为`compact-fullseason-v1-unlimited`（aebdeada）。两者不得混用。V1是62栋×2160h、无TES、五个固定站址对应树网络内的规划算法基线，不是完整环网全局最优。冻结报告与产物清单见 [docs/releases/compact_fullseason_v1_r3/README.md](docs/releases/compact_fullseason_v1_r3/README.md)，该基线可审计，但未标注为正式工程结果。

服务器结果：`../OUT_RESULT/COMPACT_FULLSEASON_V1_20260903_R3/`。原始输入：`../IN_DATA/原始输入数据/v0.2/`。均不进入Git、不被目录整理改写。

目录重构的源文件哈希会因导入路径改变；公式保全通过 `python tools/verify_layout.py` 与回归测试验证，不虚称新旧文件字节相同。旧源码和原哈希可从冻结标签恢复。

`default/`、`Fehring/`、Conda子模块及现有`runs/`保留原位：其中可能混有数据/历史证据，不进行整目录搬移。比赛代码包排除这些非主线资产和缓存，详见目录迁移说明。

原作者Notebook说明保存在`legacy/upstream/README_original.md`；LICENSE和CITATION保留。
