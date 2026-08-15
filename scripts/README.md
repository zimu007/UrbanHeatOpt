# 命令脚本目录

本目录用于放置环境检查、输入校验、数据适配和案例运行等薄命令入口；业务规则应由 `competition/` 包实现，脚本只负责参数解析和退出码转换。

脚本不得把真实输入复制进仓库，也不得覆盖原始数据；所有运行产物应写入被版本规则忽略的结果或工作目录。

当前入口：

```powershell
python scripts/check_environment.py
python scripts/create_minimal_case.py --help
python scripts/validate_inputs.py --case tests/fixtures/minimal_case
python scripts/adapt_case_to_legacy.py --case tests/fixtures/minimal_case
python scripts/run_case.py --case tests/fixtures/minimal_case --skip-model
python scripts/export_standard_results.py --help
```

`validate_inputs.py` 成功时输出 JSON 案例摘要和输入 SHA-256；契约失败时输出稳定错误码并返回退出码 2。校验过程只读。适配器和运行入口只在被忽略的 `runs/` 或用户指定输出目录生成派生文件，不覆盖标准输入。
