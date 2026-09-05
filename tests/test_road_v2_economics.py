from pathlib import Path
import csv
import json

import pytest

from urbanheatopt.parameters.legacy_economics import (
    PackageError, read_package, effective_parameters, write_gap_report,
)


def write_table(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def package(tmp_path):
    write_table(tmp_path / 'economic_parameter_sources.csv', [dict(
        source_id='s1', code_use_allowed='1', source_title='synthetic_test',
        url='https://example.invalid/test', verification_status='test')])
    write_table(tmp_path / 'economic_parameters_provisional.csv', [dict(
        parameter_id='ashp_central_capex_baseline', value='2400',
        unit='RMB/kW_heat', parameter_status='provisional_same_baseline_pending_vendor_model',
        code_use_allowed='1', source_id='s1', month='', start_hour='', end_hour='')])
    write_table(tmp_path / 'pending_confirmation_Cai.csv', [dict(
        item_id='P006', parameter_id='ashp_central_equipment_only_cost', value='',
        unit='RMB/kW_heat', parameter_status='pending_confirmation',
        code_use_allowed='0', related_source_id='s1', parameter_name='热泵报价',
        required_information='容量和报价边界', why_needed='缺正式报价')])
    return tmp_path


def test_read_by_id_not_order_and_hash_inputs(package):
    data = read_package(package)
    assert data['parameters']['ashp_central_capex_baseline']['value'] == 2400
    assert len(data['input_sha256']) == 3
    assert data['pending'][0]['value'] is None


@pytest.mark.parametrize('column,value', [
    ('value', ''), ('value', 'NaN'), ('value', '-1'), ('unit', 'RMB/MW'),
    ('code_use_allowed', 'maybe'), ('parameter_status', 'unknown'),
    ('source_id', 'missing'),
])
def test_reject_invalid_fields(package, column, value):
    path = package / 'economic_parameters_provisional.csv'
    rows = list(csv.DictReader(path.open(encoding='utf-8-sig')))
    rows[0][column] = value
    write_table(path, rows)
    with pytest.raises(PackageError):
        read_package(package)


def test_duplicate_id(package):
    path = package / 'economic_parameters_provisional.csv'
    rows = list(csv.DictReader(path.open(encoding='utf-8-sig')))
    write_table(path, rows + rows)
    with pytest.raises(PackageError, match='重复'):
        read_package(package)


def test_disabled_is_registered_but_cannot_apply(package):
    path = package / 'economic_parameters_provisional.csv'
    rows = list(csv.DictReader(path.open(encoding='utf-8-sig')))
    rows[0]['code_use_allowed'] = 'False'
    write_table(path, rows)
    data = read_package(package)
    assert data['parameters'][rows[0]['parameter_id']]['code_use_allowed'] is False
    with pytest.raises(PackageError):
        effective_parameters(data, peak_kW=100, require_all=False)


def test_gap_report_is_snapshot_driven_and_versioned(package, tmp_path):
    snapshot = effective_parameters(read_package(package), peak_kW=100, require_all=False)
    assert snapshot['values']['pipe_capacity_kW_th'] == [72, 120, 180]
    assert snapshot['values']['pipe_dn_mm'] == [None, None, None]
    assert snapshot['energy_policy'] == 'preserve_v03_external_timeseries'
    report = tmp_path / 'desktop' / '缺失数据清单.md'
    write_gap_report(snapshot, report)
    old = report.read_bytes()
    write_gap_report(snapshot, report)
    assert next(report.parent.glob('缺失数据清单.*.bak.md')).read_bytes() == old
    text = report.read_text(encoding='utf-8')
    assert 'P006' in text and '2400' in text and '运行日志' not in text
    assert json.loads(json.dumps(snapshot))['snapshot_sha256']


def test_policy_period_gap_and_overlap_are_rejected(package):
    path = package / 'economic_parameters_provisional.csv'
    rows = []
    for month in (12, 1, 2):
        rows.append(dict(parameter_id=f'tou_multiplier_m{month:02d}_flat_1',
            value='1', unit='dimensionless', applicable_month=str(month), start_hour='0', end_hour='24',
            code_use_allowed='1', parameter_status='policy_applicable_scenario_only', source_id='s1'))
    write_table(path, rows)
    assert len(read_package(package)['parameters']) == 3
    rows[0]['end_hour'] = '23'
    write_table(path, rows)
    with pytest.raises(PackageError, match='缺口或重叠'):
        read_package(package)


def test_extension_inventory_does_not_relax_original_baseline(package):
    from urbanheatopt.data.intake.guanggu_v03 import GuangguV03Report, _validate_inventory
    from urbanheatopt.parameters.legacy_economics import PACKAGE_DIRECTORY
    from shutil import copytree
    root = package / 'delivery'
    copytree(package, root / PACKAGE_DIRECTORY, ignore=lambda p, names: ['delivery'])
    (root / PACKAGE_DIRECTORY / 'README.md').write_text('synthetic_test', encoding='utf-8')
    (root / 'base.txt').write_text('base', encoding='utf-8')
    profile = dict(expected_inventory=dict(total_files=1, extensions={'.txt': 1}),
                   classified_root_files=['base.txt'], classified_directories={})
    def audit():
        report = GuangguV03Report('test', 'test', 'test', root, True)
        _validate_inventory(report, root, list(root.rglob('*.*')), profile)
        return report
    assert audit().valid
    (root / PACKAGE_DIRECTORY / 'unexpected.csv').write_text('x\n1', encoding='utf-8')
    assert any(x.code == 'UNCLASSIFIED_FILE' for x in audit().issues)
