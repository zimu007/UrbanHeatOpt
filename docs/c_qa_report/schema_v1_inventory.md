# V1 / R3 结果目录 Schema 盘点(字段级)

日期：2026-09-05 · 只读源：`release_assets/R3_extracted/COMPACT_FULLSEASON_V1_20260903_R3`
> 说明：按任务书"先读实际 schema,不假定与旧 road_v2 CSV 字段相同"。下表为本次逐文件核实结果。

## 1. 顶层文件
| 文件 | 大小 | 作用 / 关键字段 |
|---|---|---|
| `case.json` | 6.4 MB | 序列化 RoadCase(road_joint_v2_case_1)。`buildings[62]`、`hours[2160]`、`demand[133920]`(62×2160)、`timestamps[2160]`、`mode`(该文件 central 基)、`peak_margin=0.2`、`capacity_ratio[4320]`、`cop[4320]`、`technologies[3]`、`pipes[3]`、`sites[5]`、`economics{…}`、`network{…}`、`storage{…}`、`parameter_version`(SHA) |
| `economics.*` | — | `discount_rate=0.05`、`electricity_price_CNY_per_kWh_e`{2160h 逐时,TOU:0.36/0.384/0.8/1.192}、`electricity_carbon_kgCO2e_per_kWh_e`=0.4044、`gas_price_CNY_per_kWh_LHV`=0.351391、`gas_carbon_kgCO2e_per_kWh_LHV`=0.201957、`expected_weight_sum_h_per_year`=2160、`connection_capex_CNY`{每栋 1000}、`connection_lifetime_years`=30、`station_fixed_capex_CNY`=10000、`station_lifetime_years`=30、`hns_penalty_CNY_per_kWh`=1e6、`policy_carbon_price_CNY_per_tCO2e`=0、`time_weight_h_per_year`=1.0 |
| `technologies[]` | — | `technology_id`(central_hp/central_boiler/local_hp)、`applicable_scope`、`energy_carrier`(electricity/gas)、`capex_CNY_per_kW`(2400/270/2400)、`lifetime_years`(20/15/20)、`fixed_maintenance_fraction_per_year`(0.01/0.02/0.01)、`cop`(3.2/-/3.0)、`efficiency`(-/0.94/-)、`assumption_flag=scenario_assumption` |
| `pipes[]` | — | 三档:`test_small/medium/large`,`capex_CNY_per_route_m` 500/700/900,`lifetime_years`=30,`pair_loss_kW_per_route_m` 0.02/0.03/0.04,`pumping_kWh_e_per_kWh_th_m` 1e-5 |
| `compact_task_plan.json` | 47.7 MB | 51 任务计划(task_id、依赖、MPS、预算、ε) |
| `compact_pareto_frontiers.json` | 260 KB | `combined_frontier[8]`、`mode_all_points`{central:25,distributed:1,hybrid:25}、`mode_frontiers`、`final_replay_complete`、`missing_or_failed_task_ids[]` |
| `completion_manifest.json` | 7 KB | `status=complete`、`certified_point_count=51`、`combined_frontier_point_count=8`、`final_replay_point_count=8`、`no_tes_baseline_frontier_point_count=8`、各 SHA |
| `tes_upgrade_status.json` | 266 B | `requested=true,qualified=false,status=fallback_to_no_tes`(原因:无 TES 基线未在扫描截止前完成) |
| `run_status.json` / `run_control.json` / `recovery_control.json` | — | 运行/恢复状态(时间戳易变,不作 QA 依据) |

## 2. 前沿点字段(combined_frontier 每点)
`mode`、`point_id`、`labels[]`(cost_endpoint/carbon_endpoint/epsilon)、`annual_real_cost_CNY_per_year`、`annual_operating_carbon_kgCO2e_per_year`、`annual_hns_penalty_CNY_per_year`、`unserved_heat_kWh`、`epsilon_kgCO2e_per_year`、`incumbent_objective`、`best_objective_bound`、`reported_mip_gap`、`is_knee`、`termination_condition`、`model_sha256`、`solver_evidence_file`、`solver_log_file`

## 3. `compact_tasks/<task_id>/`
`attempt_0001/`、`state.json`、`success.json`、`worker_reservation.json`；attempt_0001 内含 `model.mps`、`solver.log`、`solver_evidence.json`、`solution/`(同 final_replay 结构)。

## 4. `final_replay/<point_id>/`(8 个,与合并前沿点一一对应)
`success.json`(含 `replay_cost_CNY_per_year`、`replay_carbon_kgCO2e_per_year`、`point_id`、`mode`、`site_id`、`qa{compact_full_horizon_qa, export_independent_qa,…}`、`output_sha256`)；`attempt_0001/{model.mps, solver.log, solver_evidence.json, solution/}`。

**`solution/` 解目录字段(=独立复算的原料)**
| 文件 | 说明 |
|---|---|
| `capacity_decisions.csv` | `location_id, technology_id, scope, capacity_kW_th, installed, capex_CNY_per_kW_th, lifetime_years, fixed_om_fraction` |
| `dispatch_hourly.parquet` | 逐 `location×technology×scope×hour`:`heat_kW_th, energy_input_kW, energy_carrier(electricity/gas)`;155520 行 = 72 组×2160h |
| `network_hourly.parquet` | 逐 `edge×hour`:`signed/forward/reverse_flow_kW_th, loss_kW_th, pump_kW_e, utilization, capacity_excess_kW`——**泵耗电在此,不在 dispatch** |
| `node_balance_check.parquet` | `node_id, hour, residual_kW`(残差,Q 以 ≤1e-6 kW 判) |
| `cost_breakdown.csv` | `component, annual_CNY`:device_investment/fixed_om/pipe_investment/station_investment/connection_investment/storage_investment/electricity_cost/gas_cost/variable_om |
| `solution_summary.json` | mode、model_version、building_count=62、hour_count=2160、real_cost、carbon_kgCO2e、carbon_tCO2e、`formal_engineering_result=false`、`network_policy_version=planning_corridor_2.1.0` |
| `independent_recalculation.json` | 管道自带复算(成本分项/电碳/气碳/tCO2e)——**供对照,不作为 C 独立证据** |
| `qa_summary.json` | max_errors{heat_balance_kW 等}、violations[]、unserved、qa_basis |
| `capacity/access/building_connection/network_decisions(.csv/.geojson)`、`storage_decisions.csv`、`building_hourly.parquet`、`storage_hourly.parquet` | 决策与逐时明细 |

## 5. 关键口径陷阱(已核实)
- **电费/电碳必须含泵耗**(`network_hourly.pump_kW_e`),仅 dispatch 会偏低 1.4%~60%(见 `qa_v1_layer1.md`)。
- 燃气为 **LHV** 口径;气价/气碳按 kWh_LHV。
- `carbon_tCO2e = carbon_kgCO2e/1000`;cost 端点 gas 主导、carbon 端点电主导。
- 时间:2160 连续供暖季小时,`expected_weight_sum=2160` → "per year" 即冬季 2160h 运行量,**不是 8760h**。
- 无 TES;`storage_*` 文件为占位/空,不解读为储热结论。
