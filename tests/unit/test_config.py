"""Unit tests for racb.config module."""

import os
from unittest.mock import patch
import pytest
from racb import config


@pytest.mark.unit
def test_env_bool():
    with patch.dict('os.environ', {'B_TRUE': 'True', 'B_YES': 'yes', 'B_FALSE': 'False', 'B_ZERO': '0'}):
        assert config.env_bool('B_TRUE') is True
        assert config.env_bool('B_YES') is True
        assert config.env_bool('B_FALSE') is False
        assert config.env_bool('B_ZERO') is False
        assert config.env_bool('B_MISSING', default=True) is True


@pytest.mark.unit
def test_parse_duration_supports_months_and_years():
    assert config.parse_duration('3 months') == 90 * 86400
    assert config.parse_duration('1 month') == 30 * 86400
    assert config.parse_duration('6 Months') == 180 * 86400
    assert config.parse_duration('1 year') == 365 * 86400


@pytest.mark.unit
def test_parse_duration_delegates_to_pytimeparse():
    assert config.parse_duration('2 hours') == 7200
    assert config.parse_duration('90 days') == 90 * 86400
    assert config.parse_duration('0 seconds') == 0
    assert config.parse_duration('not a duration') is None
    assert config.parse_duration(None) is None


@pytest.mark.unit
def test_zero_waiting_period_is_respected():
    with patch.dict('os.environ', {'PHASE2_WAITING_PERIOD': '0 seconds'}):
        assert config.phase2_waiting_period_seconds() == 0


@pytest.mark.unit
def test_invalid_waiting_period_falls_back_to_default():
    with patch.dict('os.environ', {'PHASE3_WAITING_PERIOD': 'whenever'}):
        assert config.phase3_waiting_period_seconds() == 90 * 86400


@pytest.mark.unit
def test_six_months_is_not_silently_ninety_days():
    with patch.dict('os.environ', {'PHASE3_WAITING_PERIOD': '6 months'}):
        assert config.phase3_waiting_period_seconds() == 180 * 86400


@pytest.mark.unit
def test_every_bot_has_an_enabled_switch_that_defaults_to_on():
    from racb.core import reddit
    assert set(config.BOT_ENABLED_KEYS) == set(reddit.bot_details)
    with patch.dict('os.environ', {}, clear=True):
        assert all(config.is_bot_enabled(name) for name in reddit.bot_details)
    with patch.dict('os.environ', {'SAME_POST_BOT_ENABLED': 'False'}):
        assert not config.is_bot_enabled(reddit.SAME_POST_BOT_NAME)
        assert config.is_bot_enabled(reddit.AUTO_CROSSPOST_BOT_NAME)


@pytest.mark.unit
@pytest.mark.parametrize('value, expected', [
    (None, 'ids'), ('ids', 'ids'), ('Listing', 'listing'), (' listing ', 'listing'), ('firehose', 'ids'),
])
def test_stream_source(value, expected):
    from racb import config
    environment = {} if value is None else {'STREAM_SOURCE': value}
    with patch.dict('os.environ', environment):
        if value is None:
            os.environ.pop('STREAM_SOURCE', None)
        assert config.stream_source() == expected


@pytest.mark.unit
@pytest.mark.parametrize('value, expected', [('8', 8), ('0', 1), ('many', 4)])
def test_stream_reader_count(value, expected):
    from racb import config
    with patch.dict('os.environ', {'STREAM_READERS': value}):
        assert config.stream_reader_count() == expected
