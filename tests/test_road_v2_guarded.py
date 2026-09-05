import os
import pytest
from tools.legacy_cli.run_road_v2_guarded import memory_budget, process_memory, run_guarded
from urbanheatopt.optimization.reference_tasks import create_plan
from test_road_v2_core import shared_case


def test_budget_never_exceeds_three_quarters_of_available():
    assert memory_budget(16*1024**3,100)==12*1024**3
    assert memory_budget(16*1024**3,2)==2*1024**3
    with pytest.raises(ValueError):
        memory_budget(None)
    with pytest.raises(ValueError):
        memory_budget(100,float('nan'))


def test_process_monitor_reads_only_current_test_process():
    sample=process_memory(os.getpid())
    assert sample and sample['rss_bytes']>0 and sample['private_bytes']>0


def test_guarded_small_task_records_log_without_modifying_problem(tmp_path):
    root=tmp_path/'guarded'
    create_plan(shared_case(),root,full_scale=False,point_count=5)
    assert run_guarded(root,'central-cost',max_wall_seconds=120)==0
    assert (root/'tasks/central-cost/success.json').exists()
    assert list((root/'resource_monitor/central-cost').glob('*/summary.json'))
