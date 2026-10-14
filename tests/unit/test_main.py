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
    with patch('time.sleep'):
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
    with patch('time.sleep') as mock_sleep, patch('racb.main.env_bool', return_value=False):
        assert handle_exception(too_many_requests) is False
        mock_sleep.assert_called_once()


@pytest.mark.unit
def test_handle_exception_resets_database_on_connection_error():
    import psycopg2
    with patch('time.sleep'), patch('racb.main.env_bool', return_value=False), \
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
    from racb.main import run_job_safely
    import schedule

    calls = []

    def failing_job():
        calls.append(1)
        raise ValueError('boom')

    scheduler = schedule.Scheduler()
    scheduler.every(10).minutes.do(run_job_safely, failing_job)
    job = scheduler.jobs[0]
    with patch.dict('os.environ', {'DEBUG': 'false'}):
        job.run()
    assert calls == [1]
    assert job.should_run is False  # next_run moved 10 minutes ahead instead of staying overdue


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
