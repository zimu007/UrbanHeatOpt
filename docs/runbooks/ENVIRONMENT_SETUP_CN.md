# UrbanHeatOpt 固定环境安装与启动说明

## 固定版本

项目只以根目录 `environment.yml` 为依赖事实来源。该文件固定 Python、Pyomo、HiGHS、Pandas、GeoPandas、PyArrow 等直接运行依赖的精确版本；`tools/check_environment.py` 会在每次统一启动前重新检查版本、来源、CPU Arrow 构建、GIS资源目录、Parquet和HiGHS最小求解。

当前关键版本为：

| 组件 | 版本 |
|---|---:|
| Python | 3.12.2 |
| Pyomo | 6.8.2 |
| highspy / HiGHS | 1.15.1 |
| NumPy | 2.1.3 |
| Pandas | 2.2.2 |
| SciPy | 1.18.0 |
| GeoPandas | 1.0.1 |
| Shapely | 2.1.2 |
| PyArrow | 25.0.0 CPU |
| pytest | 9.1.1 |

完整版本以 `environment.yml` 和环境检查程序为准，不能只依据本表安装部分包。

## 新电脑创建环境

在仓库根目录的 PowerShell 中执行。推荐把环境放在仓库同级的 `.conda_envs`，避免不同用户机器上的同名环境互相覆盖：

```powershell
conda env create --prefix "..\.conda_envs\urbanheatopt_env" --file environment.yml
```

如果电脑已经有同名环境，统一启动器仍优先使用上述项目环境，而不是调用用户目录里的同名环境。

## 唯一推荐入口

无需执行 `conda init`，也无需修改系统 `PATH`：

```powershell
.\RUN_URBANHEATOPT.cmd --check
.\RUN_URBANHEATOPT.cmd --pytest -q
.\RUN_URBANHEATOPT.cmd prepare --config configs\cases\guanggu_v2.yaml --run-id <唯一ID>
.\RUN_URBANHEATOPT.cmd solve --config configs\cases\guanggu_v2.yaml --bundle <case_bundle.json> --request-set cost-endpoints --run-id <唯一ID> --output-root runs\v2
.\RUN_URBANHEATOPT.cmd --gui
```

在其他目录安装环境时，先设置：

```powershell
$env:URBANHEATOPT_ENV_PREFIX = "D:\实际路径\urbanheatopt_env"
```

启动器优先级依次为显式环境变量、仓库内环境、仓库同级环境。它使用 `conda run --prefix` 锁定实际环境，不使用可能解析到另一套环境的 `conda run --name urbanheatopt_env`。

## 常见错误

- `conda`不是命令：不代表环境不存在。使用统一启动器；它会从常见安装位置发现`conda.exe`。
- 环境门禁报告HiGHS版本错误：通常是误用了用户目录下的旧同名环境。检查启动器打印的`environment=...`。
- `GDAL_DATA`或`PROJ_DATA`缺失：不要直接调用环境目录中的`python.exe`，应通过统一启动器进入Conda环境。
- 只运行`pytest`通过不能证明环境正确；必须先通过`--check`，再把求解日志中的Python、HiGHS和输入哈希作为运行证据。
