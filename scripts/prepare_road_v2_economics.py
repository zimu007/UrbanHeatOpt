"""Validate economics, write a fresh snapshot and a versioned desktop gap list."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from competition.intake.guanggu_v03 import resolve_guanggu_v03_source_roots
from competition.road_joint_v2.economic_package import (
    PACKAGE_DIRECTORY, PackageError, read_package, effective_parameters, write_gap_report,
    describe_energy_input,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delivery-root', required=True)
    parser.add_argument('--output-root', required=True)
    parser.add_argument('--desktop-report', default=str(Path.home() / 'Desktop' / '缺失数据清单.md'))
    args = parser.parse_args()
    try:
        import pandas as pd
        roots = resolve_guanggu_v03_source_roots(args.delivery_root)
        package = read_package(roots.delivery_root / PACKAGE_DIRECTORY)
        loads = pd.read_parquet(roots.delivery_root / '05_building_hourly_loads.parquet')
        # Peak is computed from the complete input, not a smoke subset.
        peak = float(loads.groupby('hour')['heating_kW'].sum().max())
        import yaml
        profile=yaml.safe_load((Path(__file__).resolve().parents[1]/'competition/configs/guanggu_v03.yaml').read_text(encoding='utf-8'))
        external=pd.read_parquet(roots.delivery_root/'external_timeseries.parquet')
        snapshot = effective_parameters(package, peak, energy_context=describe_energy_input(external,profile['technology_rules']['natural_gas_lhv_MJ_per_Nm3']))
        output = Path(args.output_root)
        output.mkdir(parents=True, exist_ok=False)
        (output / 'effective_parameters.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        write_gap_report(snapshot, args.desktop_report)
        print(json.dumps({'parameter_count': len(package['parameters']), 'pending_count': len(package['pending']),
                          'snapshot_sha256': snapshot['snapshot_sha256'], 'output': str(output.resolve()),
                          'solver_executed': False}, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
