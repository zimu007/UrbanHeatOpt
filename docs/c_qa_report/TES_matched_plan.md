# C · TES OFF/ON matched comparison 复算契约(预备,待 V2 数据)

状态:2026-09-06 · 等 B 交付 revised_20260831 三组 2160h TES OFF/ON 后再执行。
目的:B 正式 TES 对照固定 site/connection/network-tree/objective/carbon-cap,只重优化热源容量+TES 容量+dispatch;则 C 独立复算做 **matched Δ**,不重解。

## 1. B 侧承诺的对照设计(据 0906 回复)
- 三个代表点各做 **TES OFF vs TES ON**:economic / knee / low-carbon;
- 同组固定:site、connection、network/tree、objective、carbon cap;
- 只重优化:热源容量、TES 容量、dispatch;
- 每组 OFF/ON 的唯一区别应为 TES 相关决策。

## 2. C 的 matched 复算输出(每组 3 对 = 6 个解)
对每对 OFF/ON 输出:
- Δ年化成本 = cost_ON − cost_OFF(CNY/yr,分项:设备/TES/固定运维/管网/接入/电费(含泵耗)/燃气)
- Δ运行碳(kg/t CO2e,分电/气/TES 效率损失)
- Δ未供热、Δ电费中的需量部分(若适用)
- TES ON 的:SOC 首末守恒、充放效率损失、容量 vs 功率边界、是否触碰充放互斥
- 判定/证据:ON 相对 OFF 的成本差为负才有经济意义;零容量可接受、被跳过不可接受(据总体方向 §6)

## 3. 复用/新增
- 运行期成本/碳:直接复用 `qa_engine.py` Layer-1(已含泵耗口径)对 OFF/ON 各跑一遍;
- TES 项新增(engine 待扩展):`storage_decisions` 容量/功率、`storage_hourly` SOC 时序、充放效率与静置损失、投资年化(现 V1 全为 0/占位);
- 输出:每对 `matched_<group>.json` + 汇总 csv + 一张 **TES 配对对比图**(补齐图册 8/8)。

## 4. 需要 B 随结果提供(避免 C 猜)
1. 每组的 carbon cap / objective / site / tree 标识(用于确认"同组只差 TES");
2. TES 开启版本是否含 `storage_*` 真实决策文件(success.json 里 enable_tes=true);
3. 若 TES ON 与 OFF 同碳约束下解出相同目标(退化为无意义),请 B 给出区分标注。

## 5. 前置检查(V2 数据到货后先跑)
- `enable_tes` 与 `tes_upgrade_status` 字段核对;
- 确认同组 OFF/ON 的 `case_sha256` 在除 TES 外一致(或 B 明确差异清单)。
