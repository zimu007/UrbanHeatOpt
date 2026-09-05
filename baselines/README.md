# V1保全与目录迁移

- 服务器成果版本：`compact-fullseason-v1-r3` → `58d83226d69e7c6a2ccb5ef02b7a212f588cc182`。
- 本轮迁移前版本：`compact-fullseason-v1-unlimited` → `aebdeada611a1c4048aa28ce3bcabeb8015a4913`。
- 原始服务器输出在仓库外`../OUT_RESULT/COMPACT_FULLSEASON_V1_20260903_R3/`，不移动、不覆盖。
- `layout_migration.json`记录移动前逐文件哈希及目标路径；`tools/verify_layout.py`比较冻结Git对象与迁移后函数/类AST，仅忽略导入声明、文档字符串和等价模块名称变化。

## 显式恢复旧源码（不切换当前分支、不覆盖当前目录）

使用新的输出文件名，在独立目录解压；本轮不自动执行回放或恢复历史运行任务。

```powershell
git archive --format=zip --output="../OUT_RESULT/V1_R3_source_replay_NEW.zip" compact-fullseason-v1-r3
```

执行前确认目标ZIP不存在；禁止覆盖已有归档。旧代码依赖沿用旧环境说明，Conda激活子模块不在git archive内；可直接使用已有Conda环境。旧结果缺少的服务器原始绝对路径必须显式映射，不能伪造哈希通过。新V2不得复用V1任务的成功标记。
