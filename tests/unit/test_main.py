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
