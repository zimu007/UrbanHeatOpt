# UrbanHeatOpt 竞赛开发环境报告

## 1. 结论与范围

截至 2026-08-30，Conda 环境 `urbanheatopt_env` 的批准依赖基线已通过竞赛版前置环境检查：

- Python 和主要计算、GIS、优化依赖版本符合本报告记录；
- PyArrow 采用 conda-forge 的固定 CPU 构建，没有 Arrow CUDA 包；
- 带 `Asia/Shanghai` 时区的 Parquet 数据可以精确写入并读回；
- Pyomo APPSI 可以调用 HiGHS，并在一变量测试模型上返回 `optimal`；
- HighsPy `1.15.1` 已在隔离环境中通过求解器接口、`road_joint_v2` 小型真求解和 `v3_smoke_case` 完整管线回归；
- `GDAL_DATA`、`PROJ_DATA` 和 `CONDA_PREFIX` 在正确激活后有效。

本报告只证明当前环境与依赖清单具备继续开发 P0 的条件。默认求解器已在后续 `SOLVER-01` 节点切换为 HiGHS；本报告仍不证明标准输入校验器、合成案例或完整竞赛流程已经实现。

## 2. 安装决策和零替换证据

最初对 Anaconda `defaults` 的 dry-run 会替换 30 个现有包，并选择 CUDA Arrow 依赖链，因此没有执行。按批准方案改用 conda-forge，并固定以下 CPU 包和保护包：

```text
pyarrow=25.0.0=py312h2e8e312_0
pyarrow-core=25.0.0=py312h12c7521_0_cpu
libarrow=25.0.0=h20c36f3_3_cpu
libparquet=25.0.0=h7051d1f_3_cpu
pytest=9.1.1=pyhc364b38_2
openssl=3.6.3=hf411b9b_0
```

实际安装前使用的约束为：

```powershell
conda install -n urbanheatopt_env --override-channels -c conda-forge `
  --freeze-installed --dry-run --json `
  pyarrow=25.0.0=py312h2e8e312_0 `
  pyarrow-core=25.0.0=py312h12c7521_0_cpu `
  libarrow=25.0.0=h20c36f3_3_cpu `
  libparquet=25.0.0=h7051d1f_3_cpu `
  pytest=9.1.1=pyhc364b38_2 `
  openssl=3.6.3=hf411b9b_0
```

dry-run 结果为 `UNLINK=0`、`LINK=47`、`FETCH=47`。因此实际安装只新增 PyArrow、pytest 及必要依赖，没有卸载或替换现有包。若省略 OpenSSL 的精确构建保护，求解器会把 `openssl 3.6.3 hf411b9b_0` 换成 `hf411b9b_1`，不再满足零替换要求。

2026-08-30 的求解器更新只将 pip 提供的 HighsPy 从 `1.11.0` 升级到 `1.15.1`；其他固定包和 Conda 构建不变。正式切换前先在独立覆盖环境完成了兼容和数值回归，避免长时间求解进程加载的 DLL 被原位替换。

## 3. 当前关键版本和构建

| 包 | 版本 | 构建/来源 |
|---|---:|---|
| Python | 3.12.2 | `h2628c8c_0_cpython` / conda-forge |
| NumPy | 2.1.3 | `py312h49bc9c5_0` / conda-forge |
| Pandas | 2.2.2 | `py312h72972c8_1` / conda-forge |
| GeoPandas | 1.0.1 | `pyhd8ed1ab_3` / conda-forge |
| Fiona | 1.10.1 | `py312h3f2e00f_6` / conda-forge |
| GEOS | 3.14.1 | `hdade9fe_0` / conda-forge |
| GDAL core | 3.12.3 | `h9aca766_3` / conda-forge |
| PyProj | 3.7.2 | `py312h8b773d8_3` / conda-forge |
| Shapely | 2.1.2 | `py312h37f46ab_2` / conda-forge |
| Scikit-learn | 1.9.0 | `np2py312hea30aaf_0` / conda-forge |
| SciPy | 1.18.0 | `pypi_0`，保留既有来源 |
| NetworkX | 3.6.1 | `pyhcf101f3_0` / conda-forge |
| Pyomo | 6.8.2 | `py312h275cf98_1` / conda-forge |
| HighsPy | 1.15.1 | `pypi_0`，保留 pip 来源 |
| PyArrow | 25.0.0 | `py312h2e8e312_0` / conda-forge |
| PyArrow core | 25.0.0 | `py312h12c7521_0_cpu` / conda-forge |
| libarrow | 25.0.0 | `h20c36f3_3_cpu` / conda-forge |
| libparquet | 25.0.0 | `h7051d1f_3_cpu` / conda-forge |
| pytest | 9.1.1 | `pyhc364b38_2` / conda-forge |
| jsonschema | 4.26.0 | conda-forge；契约提交中登记为直接依赖 |
| OpenSSL | 3.6.3 | `hf411b9b_0` / conda-forge |

除明确升级的 HighsPy 外，Python、NumPy、Pandas、GeoPandas、Fiona、GEOS、GDAL、PyProj、Shapely、Scikit-learn、SciPy、NetworkX、Pyomo 和 OpenSSL 的版本/构建均未变化。

`jsonschema 4.26.0` 在现有环境中已经由 Jupyter 依赖链安装；输入契约开始直接使用 Draft 2020-12 校验后，将其加入 `environment.yml` 只用于声明直接依赖，没有执行新的环境安装或包替换。

`conda list` 在启用 pip 互操作时可能把顶层 `pyarrow` 显示为 `pypi_0`。这不是重复安装：`pyarrow` Conda 元包本身不持有文件，636 个 Python 文件由 `pyarrow-core` 的 conda-forge CPU 包持有，`pyarrow-25.0.0.dist-info/INSTALLER` 的内容也是 `conda`。

## 4. 验证命令与结果

推荐从仓库根目录运行：

```powershell
conda run --no-capture-output -n urbanheatopt_env python scripts/check_environment.py
```

验证结果：

- 所有固定版本均可导入且版本一致；
- `libarrow`、`libparquet`、`pyarrow-core` 均为 `25.0.0` 的 `*_cpu` 构建；
- Arrow CUDA Conda 记录数为 0；
- `Asia/Shanghai` 时区、建筑 ID 和浮点 `heating_kW` 经 Parquet 往返后数据类型和值完全一致；
- APPSI HiGHS 可用，一变量模型目标值为 `1.000000000000`，终止状态为 `optimal`；
- 检查脚本不联网、不写仓库，只在系统临时目录创建并自动清理 Parquet 文件。

对修改后的 `environment.yml` 还执行了全新环境 dry-run：解析出 342 个 Conda 包，`channels=conda-forge,nodefaults`，Conda 求解部分的非 conda-forge 包为 0，Arrow CUDA 包为 0，并保持上述 Python、Pandas、GeoPandas、Pyomo 和 CPU Arrow 版本。清单另有 4 个显式 PyPI 依赖：Contextily、HighsPy、Overpass 和 sphinxcontrib-mermaid。该操作没有实际创建第二个环境。

Windows 上 Conda 25.11.1 默认捕获中文子进程输出时会触发 GBK/UTF-8 编码异常，因此命令使用 `--no-capture-output`。这只改变输出传递方式，不改变环境或计算结果。

## 5. 已知风险与后续边界

- `environment.yml` 是直接依赖清单，不是所有间接包的逐字节锁文件；未来仓库更新后，间接依赖构建可能变化。当前精确 Windows 构建已记录在本报告中。
- SciPy 保留现有 pip 来源；HighsPy 也继续由 pip 提供，但已有意升级到 `1.15.1`。为避免全新环境发生 Conda/Pip 双重覆盖，`environment.yml` 将 SciPy 作为 conda-forge 直接依赖；因此真正重建新环境后仍需做数值回归。
- 只对全新环境方案做了 dry-run，没有实际新建并从头运行第二个环境，因此路线图“新环境按说明可以安装和运行”仍未勾选。
- 顶层激活包装脚本依赖尚未初始化的 `Conda-Activation-Scripts` 子模块，并使用相对路径。本节点没有初始化或删除子模块；当前应使用直接 `conda activate` 或本报告中的 `conda run` 命令。
- 后续 `SOLVER-01` 已将项目默认求解器切换为 HiGHS，并增加 `optimal` 加载门禁；尚未用完整 UrbanHeatOpt 案例验证成本模型和结果导出。
- 本节点没有读取或修改任何真实武汉、光谷或负荷组数据。
