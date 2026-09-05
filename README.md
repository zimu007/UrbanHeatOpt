# UrbanHeatOpt 竞赛代码

本仓库用于光谷片区供热源荷匹配与成本—运行碳排规划优化，不是施工图设计工具。

## 当前入口与状态

新主线源码为 `src/urbanheatopt/`，根入口为 `run.py`。本轮只负责目录、输入参数和模块集成，不实现B的模型升级或C的可视化。

V0 smoke command (synthetic test data only):

```powershell
conda run --no-capture-output -n urbanheatopt_env python tools/check_environment.py
conda run --no-capture-output -n urbanheatopt_env python run.py --help
conda run --no-capture-output -n urbanheatopt_env python -m pytest -q
```

`run.py validate/prepare`已接入自动源校验、新经济包与全供暖季标准化；solve、report、diagnose、tes-check在B/C接入前退出2，不回退旧模型。源码入口无需安装新依赖；项目打包定义见pyproject.toml。

```powershell
python run.py validate --config configs/cases/guanggu_v2.yaml
python run.py prepare --config configs/cases/guanggu_v2.yaml
```

输出在`work/guanggu_v2/<新RUN_ID>/`，桌面缺失清单自动更新。当前新包有8项“参数允许、来源禁用”冲突，真实源与2160h标准化通过、参数验收退出2；未完成新模型求解。详细操作与状态见[集成手册](docs/runbooks/A_INTEGRATION_CN.md)，总体方向及三人分工见`docs/team/`。

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
