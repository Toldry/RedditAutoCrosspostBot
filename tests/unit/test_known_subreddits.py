"""Unit tests for racb.core.known_subreddits module."""

from unittest.mock import patch
import pytest
from racb.core import known_subreddits


@pytest.fixture(autouse=True)
def empty_buffer():
    known_subreddits._pending_names.clear()
    yield
    known_subreddits._pending_names.clear()


@pytest.mark.unit
def test_flush_writes_buffered_names_once_and_skips_user_profiles():
    for name in ['pics', 'AskReddit', 'pics', 'u_someone', '']:
        known_subreddits.record(name)

    with patch('racb.core.known_subreddits.db.record_known_subreddits') as mock_record:
        assert known_subreddits.flush() == 2
        assert known_subreddits.flush() == 0
    mock_record.assert_called_once_with(['AskReddit', 'pics'])


@pytest.mark.unit
def test_flush_keeps_names_buffered_when_the_database_fails():
    known_subreddits.record('pics')
    with patch('racb.core.known_subreddits.db.record_known_subreddits', side_effect=RuntimeError('db down')):
        with pytest.raises(RuntimeError):
            known_subreddits.flush()
    assert known_subreddits._pending_names == {'pics'}


@pytest.mark.unit
def test_buffer_is_capped():
    with patch.object(known_subreddits, 'MAX_PENDING_NAMES', 2):
        for name in ['a1', 'b2', 'c3']:
            known_subreddits.record(name)
    assert len(known_subreddits._pending_names) == 2
