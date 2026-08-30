"""Build a new V2 road network from an explicit OSM snapshot, without solving."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from competition.intake.guanggu_v03 import resolve_guanggu_v03_source_roots
from competition.road_joint_v2.network import atomize_snapshot, write_network, RoadNetworkError, read_optional_layers
from competition.road_joint_v2.economic_package import file_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delivery-root', required=True)
    parser.add_argument('--osm-snapshot', required=True)
    parser.add_argument('--output-root', required=True)
    parser.add_argument('--allowed-corridors', type=Path)
    parser.add_argument('--forbidden-areas', type=Path)
    parser.add_argument('--obstacle-buildings', type=Path)
    args = parser.parse_args()
    root = Path(args.output_root)
    if root.exists():
        parser.error('输出目录已存在，拒绝覆盖')
    import geopandas as gpd
    import pandas as pd
    delivery = resolve_guanggu_v03_source_roots(args.delivery_root).delivery_root
    osm = Path(args.osm_snapshot)
    inputs = [delivery / '03_buildings.geojson', delivery / '05_building_hourly_loads.parquet', osm]
    layers, extra_paths = read_optional_layers(delivery, allowed_corridors=args.allowed_corridors,
        forbidden_areas=args.forbidden_areas, obstacle_buildings=args.obstacle_buildings)
    inputs += extra_paths
    before = {str(p.resolve()): file_hash(p) for p in inputs}
    try:
        buildings = gpd.read_file(inputs[0])
        loads = pd.read_parquet(inputs[1])
        network = atomize_snapshot(json.loads(osm.read_text(encoding='utf-8')), buildings,
                                  loads.groupby('building_id')['heating_kW'].sum().to_dict(), **layers)
        network['input_sha256'] = before
        if before != {str(p.resolve()): file_hash(p) for p in inputs}:
            raise RoadNetworkError('源文件哈希发生变化')
        write_network(network, root)
        print(json.dumps({'nodes': len(network['nodes']), 'edges': len(network['edges']),
                          'sites': len(network['sites']), 'network_sha256': network['network_sha256'],
                          'solver_executed': False, 'output': str(root.resolve())}))
        return 0
    except RoadNetworkError as exc:
        root.mkdir(parents=True, exist_ok=False)
        (root / 'geometry_failures.json').write_text(json.dumps({'error':str(exc), 'failures':exc.failures, 'input_sha256':before}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
