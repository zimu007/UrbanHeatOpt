# UrbanHeatOpt 竞赛版变更记录

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
