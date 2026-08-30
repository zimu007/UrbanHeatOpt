# 道路原子边契约 road_atomic_network_2.0.0

所有几何和长度使用EPSG:32650、米。代码实现位于 `competition/road_joint_v2/network.py`。

## 数据对象

- nodes：node_id唯一；node_type为road、road_attachment、building；x_m/y_m有限。
- edges：edge_id唯一，node_u/node_v引用nodes且不同；coordinates为按u到v排序的完整折线；length_m与折线长度误差≤1e-6m；edge_type为road或building_service；osm_way_ids保存来源；route_basis固定supply_return_pair_route_m。
- sites：site_id唯一且独立于道路节点，attachment_node_id引用非建筑节点。当前候选站恰好位于走廊吸附点，站内接管长度0是测试几何约定，不代表工程上没有站内管道。
- 同一条原子边只代表一条供回水双管路由，路由长度/费用不能乘2。dn_mm未提供时不能声称流速或真实DN通过校核。

## 当前生成规则与已知限制

只接受地面primary/secondary及其link；bridge/tunnel或非零layer被排除。共享OSM节点才连通，未把二维交线自动接通。重复方向的完全重合段去重，不模糊合并邻近平行路。无分叉节点仅在公路等级一致、无建筑/站点接入时等价合并，保留原折线和来源。

建筑接入为边界到道路的最近直线，检查≥45°入口角、不穿楼、不横跨其他道路；建筑保持叶节点。这只是当前生成器的候选搜索范围，失败不证明所有折线路径都不可行。候选站不建设不会关闭attachment_node_id。

真实数据首次检查有12栋直线接入失败。结果：`runs/road_joint_v2/IMPLEMENT_20260830/spatial/geometry_failures.json`。不得放宽穿楼/角度规则或偷偷回退欧氏网络。已询问是否允许避障折线；确认前阻止真实V2构模。

## 新核心热流约定

边的signed_flow_kW_th是中点热功率，正方向u→v。两端从节点进入管段的热功率分别为F+L/2与-F+L/2，其中L为双管路由常数热损。发送端功率不得超过选定档容量，接收端不允许倒向吸热。方向变量约束正反流互斥，泵耗为路由长度×绝对中点热功率×系数。

道路节点接收的端口热量加本节点已建站出力/TES净出力，等于建筑提取量或0。另用连通商品流确保接网建筑可达已建站；不通过未供热松弛放过断网。
