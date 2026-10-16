"""Unit tests for racb.main module."""

from unittest.mock import patch, MagicMock
import pytest
import praw.exceptions
import prawcore
import requests
from racb.main import StreamMetrics, handle_exception, env_bool


@pytest.mark.unit
def test_stream_metrics():
    metrics = StreamMetrics()
    metrics.record_comment()
    metrics.record_comment()
    metrics.record_match()
    metrics.record_aux()

    assert metrics.interval_scanned == 2
    assert metrics.interval_matches == 1
    assert metrics.interval_aux == 1
    assert metrics.total_scanned == 2
    assert metrics.total_matches == 1
    assert metrics.total_aux == 1

    metrics.log_heartbeat()
    assert metrics.interval_scanned == 0
    assert metrics.interval_matches == 0
    assert metrics.interval_aux == 0
    assert metrics.total_scanned == 2


@pytest.mark.unit
def test_handle_exception_server_error():
    exc = prawcore.exceptions.ServerError(MagicMock())
    with patch('racb.main.shutdown.stop_requested_within'):
        with patch('racb.main.env_bool', return_value=False):
            should_raise = handle_exception(exc)
            assert should_raise is False


@pytest.mark.unit
def test_handle_exception_reddit_api_graceful():
    exc = praw.exceptions.RedditAPIException([['RATELIMIT', 'Rate limited', 'field']])

    with patch('racb.main.env_bool', return_value=False):
        should_raise = handle_exception(exc)
        assert should_raise is False


@pytest.mark.unit
def test_handle_exception_unexpected_raises():
    exc = ValueError('Something totally unexpected')
    with patch('racb.main.env_bool', return_value=False):
        should_raise = handle_exception(exc)
        assert should_raise is True


@pytest.mark.unit
def test_main_startup_auth_success():
    from racb.main import main

    with patch('racb.main.configure_logging'), \
         patch('racb.core.reddit.authenticate_all_bots') as mock_auth, \
         patch('racb.main.start_bot') as mock_start, \
         patch('sys.argv', ['racb']):
        main()
        mock_auth.assert_called_once()
        mock_start.assert_called_once()


@pytest.mark.unit
def test_main_startup_auth_failure_exits():
    from racb.main import main

    with patch('racb.main.configure_logging'), \
         patch('racb.core.reddit.authenticate_all_bots', side_effect=RuntimeError('Bot authentication failed')), \
         patch('racb.main.start_bot') as mock_start, \
         patch('sys.argv', ['racb']), \
         pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 1
    mock_start.assert_not_called()


@pytest.mark.unit
def test_handle_exception_matches_subclasses_and_rate_limits():
    too_many_requests = prawcore.exceptions.TooManyRequests(MagicMock())
    with patch('racb.main.shutdown.stop_requested_within') as mock_sleep, \
         patch('racb.main.env_bool', return_value=False):
        assert handle_exception(too_many_requests) is False
        mock_sleep.assert_called_once()


@pytest.mark.unit
def test_handle_exception_resets_database_on_connection_error():
    import psycopg2
    with patch('racb.main.shutdown.stop_requested_within'), patch('racb.main.env_bool', return_value=False), \
         patch('racb.main.db.reset_connection') as mock_reset:
        assert handle_exception(psycopg2.OperationalError('server closed the connection')) is False
        mock_reset.assert_called_once()


@pytest.mark.unit
def test_handle_comment_safely_skips_comment_on_reddit_error(make_mock_comment):
    from racb.main import handle_comment_safely
    forbidden = prawcore.exceptions.Forbidden(MagicMock())
    with patch('racb.main.phase1.handle_incoming_comment', side_effect=forbidden), \
         patch('racb.main.env_bool', return_value=False):
        handle_comment_safely(make_mock_comment())  # must not raise


@pytest.mark.unit
def test_handle_comment_safely_propagates_network_errors(make_mock_comment):
    from racb.main import handle_comment_safely
    server_error = prawcore.exceptions.ServerError(MagicMock())
    with patch('racb.main.phase1.handle_incoming_comment', side_effect=server_error), \
         patch('racb.main.env_bool', return_value=False):
        with pytest.raises(prawcore.exceptions.ServerError):
            handle_comment_safely(make_mock_comment())


@pytest.mark.unit
def test_run_job_safely_swallows_errors_so_job_is_rescheduled():
    from racb.workers import run_job_safely

    calls = []

    def failing_job():
        calls.append(1)
        raise ValueError('boom')

    with patch.dict('os.environ', {'DEBUG': 'false'}):
        assert run_job_safely(failing_job) is None
    assert calls == [1]


@pytest.mark.unit
def test_help_does_not_require_reddit_authentication():
    from racb.main import main
    with patch('racb.main.configure_logging'), \
         patch('racb.core.reddit.authenticate_all_bots') as mock_auth, \
         patch('sys.argv', ['racb', '--help']), \
         pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 0
    mock_auth.assert_not_called()



@pytest.mark.unit
@pytest.mark.parametrize('found, expected_exit_code', [(True, 0), (False, 1)])
def test_process_one_runs_oldest_entry_and_exits(found, expected_exit_code):
    from racb.main import main
    with patch('racb.main.configure_logging'), \
         patch('racb.core.reddit.authenticate_all_bots'), \
         patch('racb.main.phase3.process_oldest_entry', return_value=found) as mock_oldest, \
         patch('racb.main.start_bot') as mock_start, \
         patch('sys.argv', ['racb', '--process-one']), \
         pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == expected_exit_code
    mock_oldest.assert_called_once_with()
    mock_start.assert_not_called()


@pytest.mark.unit
def test_dispatch_comment_queues_only_matching_comments(make_mock_comment):
    from racb.main import dispatch_comment, metrics
    phase1_workers = MagicMock()
    scanned_before = metrics.total_scanned
    with patch('racb.main.known_subreddits.record') as mock_record:
        dispatch_comment(make_mock_comment(comment_id='match', body='r/pics'), phase1_workers)
        dispatch_comment(make_mock_comment(comment_id='other', body='nice picture'), phase1_workers)
    phase1_workers.submit.assert_called_once_with('match')
    assert mock_record.call_count == 2
    assert metrics.total_scanned == scanned_before + 2


@pytest.mark.unit
def test_handle_comment_id_fetches_the_comment_with_this_threads_instance():
    from racb.main import handle_comment_id
    with patch('racb.main.reddit.get_reddit_instance') as mock_instance, \
         patch('racb.main.handle_comment_safely') as mock_handle:
        handle_comment_id('abc')
    mock_instance.return_value.comment.assert_called_once_with(id='abc')
    mock_handle.assert_called_once_with(mock_instance.return_value.comment.return_value)


@pytest.mark.unit
def test_build_jobs_runs_phase2_and_phase3_under_one_lock():
    from racb.main import build_jobs
    with patch.dict('os.environ', {'LISTEN_ONLY': 'false', 'DEBUG': 'false'}):
        jobs = {job.name: job for job in build_jobs()}
    assert set(jobs) == {'cleanup', 'heartbeat', 'inbox', 'phase2', 'known-subreddits', 'phase3'}
    assert jobs['phase2'].lock is not None
    assert jobs['phase2'].lock is jobs['phase3'].lock
    assert jobs['inbox'].lock is None

    with patch.dict('os.environ', {'LISTEN_ONLY': 'true'}):
        assert 'phase3' not in {job.name for job in build_jobs()}


@pytest.mark.unit
def test_start_bot_stops_every_thread_on_shutdown():
    import threading
    from racb import main as racb_main
    from racb.core import shutdown

    def stream_until_stopped(phase1_workers):
        shutdown.request_stop()

    threads_before = set(threading.enumerate())
    with patch('racb.main.install_signal_handlers'), \
         patch('racb.main.run_stream', side_effect=stream_until_stopped), \
         patch.dict('os.environ', {'PHASE1_WORKERS': '2'}):
        racb_main.start_bot()
    leftover = [thread.name for thread in set(threading.enumerate()) - threads_before if thread.is_alive()]
    assert leftover == []


@pytest.mark.unit
def test_start_bot_reraises_a_thread_crash():
    from racb import main as racb_main
    from racb.core import shutdown
    error = RuntimeError('job crashed')

    def stream_until_a_thread_fails(phase1_workers):
        shutdown.fail(error, 'phase3')

    with patch('racb.main.install_signal_handlers'), \
         patch('racb.main.run_stream', side_effect=stream_until_a_thread_fails), \
         pytest.raises(RuntimeError, match='job crashed'):
        racb_main.start_bot()


@pytest.mark.unit
def test_main_loop_stops_reading_the_stream_on_shutdown(make_mock_comment):
    from racb.main import main_loop
    from racb.core import shutdown
    phase1_workers = MagicMock()
    comments = [make_mock_comment(comment_id='first'), make_mock_comment(comment_id='second')]

    def stream():
        yield comments[0]
        shutdown.request_stop()
        yield comments[1]

    with patch('racb.main.known_subreddits.record'):
        main_loop(stream(), phase1_workers)
    phase1_workers.submit.assert_called_once_with('first')
