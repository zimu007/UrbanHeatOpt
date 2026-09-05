# 20260905：审计来源许可冲突按最终报价处理

## 决定及边界

用户明确决定：“仅做审计参考的直接按照最终报价即可”。新参数包未标暂定的值按本研究冻结值登记，显式暂定标签及来源适用范围仍保留。

此次仅调整A输入选择策略，不改变报价数值、经济公式、TES公式或原始CSV。此前发现的8项主表`code_use_allowed=1`、来源表为0的绑定，使用`final_quote_override_20260905`明确放行；默认读取函数仍支持严格一致策略`require_consistent`以供历史/负向回归。

包位置：`D:\co_WH_heatOPT\IN_DATA\原始输入数据\v0.2\0823代码组交付_光谷软件园_v0.3\经济参数完整可运行修订版_20260831`。

主表为`economic_parameters_code_ready.csv`；旧来源许可见`economic_parameter_sources_merged.csv`。只读取最终主表数值，不从审计说明中猜价。

## 精确绑定

| parameter_id | source_id | 现有选择用途 |
|---|---|---|
| hot_water_tes_tank_body_cost_per_m3 | SRC_HOT_TES_HOSPITAL_PRICE | 登记 tank 体积报价依据，不与能量容量费重复应用 |
| hot_water_tes_effective_energy_cost | SRC_HOT_TES_HOSPITAL_PRICE | 选为TES能量容量投资交接字段 |
| hot_water_tes_fixed_bop_cost | SRC_HOT_TES_HOSPITAL_PRICE | 选为TES固定费用；原表明确防重复零值，不是空白补0 |
| hot_water_tes_service_life_actual | SRC_HOT_TES_HOSPITAL_SCOPE | 选为TES寿命交接字段 |
| hot_water_tes_pressure_type | SRC_HOT_TES_HOSPITAL_PRICE | 压力型式追溯登记 |
| hot_water_tes_quote_boundary | SRC_HOT_TES_HOSPITAL_PRICE | 报价范围追溯登记 |
| heating_pipe_mixed_dn_installed_reference_2017 | SRC_WB_HEBEI_CLEAN_HEATING_2017 | 旧混合DN参考记录，不替换已选新版三档报价 |
| building_heat_exchange_station_package_reference_2019 | SRC_WB_HEBEI_CLEAN_HEATING_2017 | 旧换热站参考记录，不替换已选新版接入费 |

源记录用途还必须是`audit_reference`、`audit_price_reference_not_code`或`sensitivity_only`。不在表内的绑定、未知来源用途、被禁用的已选参数仍失败；空值、来源缺失、单位不符、非法状态和重复ID检查没有放宽。

## 可追溯实现

- `src/urbanheatopt/parameters/revised_economics.py`定义窄范围许可策略；配置`configs/cases/guanggu_v2.yaml`显式选择它。
- 源校验与经济快照使用同一策略；不能出现外层通过、内层使用另一策略的分歧。
- `effective_parameters.json`保留原来源`code_use_allowed=false`，并输出`permission_resolutions`、用户确认依据、应用/未应用理由。读取成功不等于已进入目标函数。
- 新选择版本为`a_selection_1.1.1`；策略、原来源、处理记录均参与快照哈希。旧失败运行和旧快照保留，不复用其状态。
- `source_hashes`与全源前后哈希证明原始文件未被修改。

## 不变事项

用户进一步确认“新包已有的从缺失清单去掉，暂做审计的数据按最终结果显示”。这里最终指本研究选定的参数，不虚称尚未执行的模型已产出最终方案。快照增加quotation_display_status/policy，审计参考值不再当作待报价；历史替代价仍不能与新价重复计费。桌面清单仅保留4类真实数据/语义差额；B/C代码任务保留在就绪报告，既有报价不再额外要求合同补证，不增加计算规模。

1800万元混合边界站房费用仍仅用于已批准压力情景；0.026的单位语义仍不足；真实DN热力容量仍不从报价自动推断。最终报价许可不是对这些边界的新授权。

本节点可以完成参数及输入准备，不执行模型求解。B仍须接入新参数、月度需量费和相关约束，C负责独立结果复算与展示。复现命令和当前新证据见`docs/runbooks/A_INTEGRATION_CN.md`。
