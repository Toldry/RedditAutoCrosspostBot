"""Unit tests for racb.core.cpu_usage."""

import pytest

from racb.core.cpu_usage import CpuUsage, read_steal_and_total_ticks


@pytest.mark.unit
def test_read_steal_and_total_ticks_parses_the_cpu_line(tmp_path):
    proc_stat = tmp_path / 'stat'
    # user nice system idle iowait irq softirq steal guest guest_nice
    proc_stat.write_text('cpu  100 0 50 800 10 0 5 35 7 0\ncpu0 1 2 3 4 5 6 7 8 9 10\n')
    assert read_steal_and_total_ticks(str(proc_stat)) == (35, 1000)


@pytest.mark.unit
def test_read_steal_and_total_ticks_is_none_without_proc_stat(tmp_path):
    assert read_steal_and_total_ticks(str(tmp_path / 'missing')) is None


@pytest.mark.unit
def test_cpu_usage_reports_steal_since_the_previous_sample():
    readings = iter([(10, 1000), (30, 1200), (30, 1200)])
    usage = CpuUsage(read_ticks=lambda: next(readings))
    process_percent, steal_percent = usage.sample()
    assert process_percent >= 0
    assert steal_percent == pytest.approx(10.0)
    # No ticks elapsed: no steal figure rather than a division by zero
    assert usage.sample()[1] is None


@pytest.mark.unit
def test_cpu_usage_without_proc_stat_reports_only_the_process():
    usage = CpuUsage(read_ticks=lambda: None)
    assert usage.sample()[1] is None
