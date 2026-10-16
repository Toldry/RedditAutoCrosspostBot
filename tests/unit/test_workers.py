"""Unit tests for racb.workers (scheduled job threads and the Phase 1 worker pool)."""

import threading
import time
from unittest.mock import MagicMock, patch

import prawcore
import pytest

from racb.core import shutdown
from racb.workers import PeriodicJob, Phase1Workers

WAIT_SECONDS = 5


def wait_until(condition, timeout=WAIT_SECONDS):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.mark.unit
def test_periodic_job_repeats_until_shutdown():
    calls = []
    job = PeriodicJob('test-job', 0.01, lambda: calls.append(1), run_at_start=True)
    job.start()
    assert wait_until(lambda: len(calls) >= 3)
    shutdown.request_stop()
    job.join(WAIT_SECONDS)
    assert not job.is_alive()


@pytest.mark.unit
def test_periodic_job_waits_one_interval_before_its_first_run():
    calls = []
    job = PeriodicJob('test-job', 60, lambda: calls.append(1))
    job.start()
    time.sleep(0.1)
    shutdown.request_stop()
    job.join(WAIT_SECONDS)
    assert calls == []
    assert not job.is_alive()


@pytest.mark.unit
def test_periodic_job_keeps_running_after_a_failing_run():
    calls = []

    def failing_job():
        calls.append(1)
        raise ValueError('boom')

    with patch.dict('os.environ', {'DEBUG': 'false'}):
        job = PeriodicJob('test-job', 0.01, failing_job, run_at_start=True)
        job.start()
        assert wait_until(lambda: len(calls) >= 2)
        shutdown.request_stop()
        job.join(WAIT_SECONDS)
    assert shutdown.fatal_error() is None


@pytest.mark.unit
def test_periodic_job_failure_in_debug_stops_the_process():
    error = ValueError('boom')

    def failing_job():
        raise error

    with patch.dict('os.environ', {'DEBUG': 'true'}):
        job = PeriodicJob('test-job', 0.01, failing_job, run_at_start=True)
        job.start()
        job.join(WAIT_SECONDS)
    assert shutdown.stop_requested()
    assert shutdown.fatal_error() is error


@pytest.mark.unit
def test_jobs_sharing_a_lock_never_overlap():
    lock = threading.Lock()
    running = []
    overlaps = []
    runs = []

    def make_job_func(name):
        def job_func():
            if running:
                overlaps.append(name)
            running.append(name)
            time.sleep(0.02)
            running.remove(name)
            runs.append(name)
        return job_func

    jobs = [
        PeriodicJob('first', 0.001, make_job_func('first'), lock=lock, run_at_start=True),
        PeriodicJob('second', 0.001, make_job_func('second'), lock=lock, run_at_start=True),
    ]
    for job in jobs:
        job.start()
    assert wait_until(lambda: {'first', 'second'} <= set(runs))
    shutdown.request_stop()
    for job in jobs:
        job.join(WAIT_SECONDS)
    assert overlaps == []


@pytest.mark.unit
def test_phase1_workers_handle_submitted_comment_ids():
    handled = []
    workers = Phase1Workers(handled.append, worker_count=2).start()
    for comment_id in ['a', 'b', 'c']:
        assert workers.submit(comment_id)
    assert wait_until(lambda: sorted(handled) == ['a', 'b', 'c'])
    shutdown.request_stop()
    for thread in workers.threads:
        thread.join(WAIT_SECONDS)
        assert not thread.is_alive()
    assert [thread.name for thread in workers.threads] == ['phase1-1', 'phase1-2']


@pytest.mark.unit
def test_phase1_workers_drop_comments_when_the_queue_is_full():
    metrics = MagicMock()
    # Not started, so nothing takes items off the queue
    workers = Phase1Workers(lambda comment_id: None, worker_count=1, metrics=metrics, queue_size=2)
    assert workers.submit('a')
    assert workers.submit('b')
    assert not workers.submit('c')
    assert workers.pending() == 2
    metrics.record_dropped.assert_called_once_with()


@pytest.mark.unit
def test_phase1_worker_backs_off_after_a_network_error_and_keeps_going():
    handled = []

    def handle(comment_id):
        if comment_id == 'rate-limited':
            raise prawcore.exceptions.TooManyRequests(MagicMock())
        handled.append(comment_id)

    with patch('racb.workers.shutdown.wait', return_value=False) as mock_wait, \
         patch.dict('os.environ', {'DEBUG': 'false'}):
        workers = Phase1Workers(handle, worker_count=1).start()
        workers.submit('rate-limited')
        workers.submit('next')
        assert wait_until(lambda: handled == ['next'])
        shutdown.request_stop()
        workers.threads[0].join(WAIT_SECONDS)
    mock_wait.assert_called_once_with(60)
    assert shutdown.fatal_error() is None


@pytest.mark.unit
def test_phase1_worker_crash_stops_the_process():
    error = RuntimeError('unexpected')

    def handle(comment_id):
        raise error

    workers = Phase1Workers(handle, worker_count=1).start()
    workers.submit('a')
    workers.threads[0].join(WAIT_SECONDS)
    assert shutdown.stop_requested()
    assert shutdown.fatal_error() is error
