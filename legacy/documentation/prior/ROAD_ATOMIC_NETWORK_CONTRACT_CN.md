# 道路原子边契约 road_atomic_network_2.1.0

所有几何和长度使用EPSG:32650、米。代码实现位于 `competition/road_joint_v2/network.py`。

## 数据对象

- nodes：node_id唯一；node_type为road、road_attachment、service_junction、building；x_m/y_m有限。building保存Polygon/MultiPolygon，display_point_only=true时坐标只是标记；候选支线分别终止于建筑边界，不将质心至边界虚构成管道。
- edges：edge_id唯一，node_u/node_v引用nodes且不同；coordinates为按u到v排序的完整折线；length_m与折线长度误差≤1e-6m；edge_type为road或building_service；osm_way_ids保存来源；route_basis固定supply_return_pair_route_m。
- sites：site_id唯一且独立于道路节点，attachment_node_id引用非建筑节点。当前候选站恰好位于走廊吸附点，站内接管长度0是测试几何约定，不代表工程上没有站内管道。
- 同一条原子边只代表一条供回水双管路由，路由长度/费用不能乘2。dn_mm未提供时不能声称流速或真实DN通过校核。
- access_options：option_id、building_id、attachment_node_id、building_boundary_point、coordinates、candidate_rank、length_m及有序edge_ids。物理路径从道路接入点至建筑，长度等于所有原子边之和。

## 当前生成规则与已知限制

planning_corridor_2.1.0允许primary/secondary/tertiary及link、unclassified、residential、living_street、service。bridge/tunnel或非零layer仍不误接为同层道路；管网本身按地下管网规划概念解释，不研究埋深、沟槽等施工细节。施工可实施性不作为本次运行门槛。不新增道路等级附加费用。

建筑接入为边界到附近道路的最多3条合格直线，按长度和稳定ID排序；取消45°及禁止跨路规则，不新增支线长度硬阈值。仍不穿已知建筑或已提供禁建区。62栋为负荷集合，上游第63栋数据中心仅作障碍。候选不足3条不凑数，没有候选则报错。候选站不建设不会关闭attachment_node_id。

仅横穿道路不自动产生三通；明确接入点、道路路口以及正长度共享路由按物理关系连接。共享线段先拆分去重，不重复计费；不同位置平行走廊不合并。微米级坐标归一化仅处理数值误差，不代表工程退距。真实空间新验收输出399节点、578边、5站，源文件哈希不变；完整优化与QA仍另行验收。

每栋接入方案选择变量之和=建筑接网决策；选中方案须建设其完整路径，共享段只建设一次。建成图中建筑度数=接网决策，禁止建筑中转。候选图允许多备选，但连通性不能通过互斥的建筑支线伪造道路连通。

## 可选图层

- 允许走廊：UTF-8 GeoJSON，唯一corridor_id，LineString/MultiLineString，明确CRS；与已准入道路的交点形成候选连接。
- 禁建区：UTF-8 GeoJSON，唯一area_id，Polygon/MultiPolygon，明确CRS；未提供只标注覆盖未知，不阻止算法验证。
- 额外建筑障碍：GeoJSON，唯一building_id、Polygon/MultiPolygon、CRS；相同负荷ID的几何不一致必须报冲突，不能改变负荷集合。

## 历史回归

2.0.0单支线和45°等旧规则只保留为显式历史生成器测试，不在公共入口使用。旧结果/MPS保留；新策略、网络、V2代码或参数变更必须生成新的任务和哈希。

## 新核心热流约定

边的signed_flow_kW_th是中点热功率，正方向u→v。两端从节点进入管段的热功率分别为F+L/2与-F+L/2，其中L为双管路由常数热损。发送端功率不得超过选定档容量，接收端不允许倒向吸热。方向变量约束正反流互斥，泵耗为路由长度×绝对中点热功率×系数。

道路节点接收的端口热量加本节点已建站出力/TES净出力，等于建筑提取量或0。另用连通商品流确保接网建筑可达已建站；不通过未供热松弛放过断网。
