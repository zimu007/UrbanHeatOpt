# 目录整理说明（A节点1）

迁移前代码：aebdeada611a1c4048aa28ce3bcabeb8015a4913。迁移前全量测试：451 passed / 3 skipped，证据位于runs/A_INTEGRATION_20260905/before/。

## 分类与处置

1. 当前生产/参考模块：由competition迁入src/urbanheatopt，按data、parameters、spatial、model、optimization、qa、reporting组织。core_model仍为依赖及参考实现，不删除。
2. 可能有关的旧模块：根目录model、data、clustering等迁入legacy/upstream，相关旧回归通过显式legacy入口运行。
3. 历史文档：原需求、路线图留在docs/decisions/history；旧网站源/生成文件归入legacy/documentation。新主线文档不把旧状态当当前状态。
4. 完全无关的缓存：只从交付包排除，不批量删除。原目录中剩余缓存不构成另一套主线。
5. 可能无关但含数据的default、Fehring和既有cases：保持原位，不能只因未被主线引用而删除；Conda子模块保持不变。
6. 未跟踪docs/ai_review、runs、仓库外IN_DATA与OUT_RESULT不移动、不提交。

逐文件旧路径、新路径、旧哈希、分类、用途和验收方式见baselines/layout_migration.json。外部调用者需要用新路径；旧文件名不保留自动回退入口。

## 核心保全

文件字节哈希因import路径调整发生变化，不宣称“旧哈希未变”。原参考核心哈希保存在profile_resources/core_model_freeze.yaml的original_v1_sha256，迁移后哈希单列。更新哈希前先由tools/verify_layout.py验证五份数学模块67个函数/类主体等价，再执行全量测试。

## 验证命令

```powershell
python tools/verify_layout.py
python tools/check_core_model_frozen.py
python -m pytest -q
python run.py --help
```

本阶段不更改价格、物理公式，不构建或求解新的光谷全季模型。入口尚未交付的能力明确退出2。

## 本次验收（2026-09-05）

迁移后全量测试453 passed / 3 skipped / 43 warnings，退出码0，日志在`runs/A_INTEGRATION_20260905/layout_final/`；新增两项为结构保全测试。迁移前451项原有测试全部保持通过。五份数学模块67个函数/类主体等价检查通过，冻结哈希检查通过，`git diff --check`通过。测试覆盖原有小案例求解、成本/碳排及QA；不据此宣称重新运行了62栋全季。三个跳过项和依赖库警告保持原有状态。
