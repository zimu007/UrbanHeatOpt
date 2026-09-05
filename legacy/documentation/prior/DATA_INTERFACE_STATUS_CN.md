# 数据接口开发现状总结

> 更新日期：2026-08-15

## 1. 当前结论

比赛运行层已经具备：

- 机器可读输入契约；
- 完整案例输入校验；
- 4栋建筑×24小时最小合成案例；
- 标准长表到旧模型输入的适配；
- `run_case.py` 非Notebook入口；
- 标准结果结构和成本年化基础；
- 输入SHA-256、稳定错误码和只读校验；
- 自动测试。

尚不能宣称正式武汉数据接口闭环完成，因为负荷组9文件尚未正式接收，真实逐栋对账和完整供暖季运行仍待完成。

## 2. 已实现校验

`competition/validation/inputs.py` 的 `validate_case_inputs()` 检查：

1. 配置Schema与YAML重复键；
2. 文件和必需字段；
3. 有限值、非负性和容量范围；
4. ID格式、唯一性和跨文件集合；
5. Asia/Shanghai连续整点时间；
6. 配置、负荷和外部时序范围；
7. EPSG:4326、几何有效性与类型；
8. DHW与数据版本；
9. 技术参数、引用和P0支持范围；
10. 聚类数与建筑数关系；
11. 输入文件SHA-256及校验前后只读快照。

错误使用稳定代码，例如：

```text
CONFIG_DUPLICATE_KEY
CONFIG_SCHEMA_ERROR
FILE_OR_FIELD_MISSING
ID_INVALID
TIME_INVALID
CRS_INVALID
FEATURE_NOT_IMPLEMENTED
```

## 3. 已实现适配与运行

- `competition/adapters/legacy_case.py`：生成旧模型兼容输入；
- `scripts/adapt_case_to_legacy.py`：适配命令入口；
- `scripts/run_case.py`：校验、适配和后续流程入口；
- `competition/results/standard.py`：标准结果输出；
- `competition/costing/annualized.py`：成本年化；
- `tests/fixtures/minimal_case/`：可提交的合成测试数据。

`run_case.py` 已存在并由测试验证可进入聚类阶段，但这不等同于正式武汉完整案例已经得到可信最优解。

## 4. 原始数据修正边界

原始标准输入严格只读。ID、单位、字段、时间、CRS和几何错误不得由程序猜测修复。只有长表转宽表、时间编号、内部投影和明确单位换算等无歧义操作可以生成独立派生文件。

## 5. 后续任务

1. 正式接收并联合校验负荷组9文件；
2. 输出 `interface_acceptance.md`；
3. 输出逐栋 `load_reconciliation.csv`；
4. 输出 `id_mismatch.csv` 与 `adapter_run.log`；
5. 完成最小案例真实求解与标准结果全链验证；
6. 接入光谷软件园真实授权数据；
7. 扩展热泵、储热、碳排和道路网络模型。

## 6. 准确状态表述

> 标准运行输入校验、最小合成案例、旧模型适配和命令入口已经建立；新增了SHA-256、稳定错误码、只读保证和禁止自动修正测试。正式负荷组交付、真实案例对账与完整竞赛模型仍未完成。
