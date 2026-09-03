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
