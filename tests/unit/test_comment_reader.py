"""Unit tests for racb.comment_reader (polling the r/all comment listing on several threads)."""

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import prawcore
import pytest

from racb import comment_reader
from racb.comment_reader import CommentReader, SeenComments
from racb.core import reddit, shutdown

WAIT_SECONDS = 5


def wait_until(condition, timeout=WAIT_SECONDS):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def fake_comment(comment_id, created_utc=1_800_000_000.0):
    return SimpleNamespace(id=comment_id, created_utc=created_utc)


def listing(*comment_ids):
    """A listing as Reddit returns it: newest first."""
    return [fake_comment(comment_id, 1_800_000_000.0 + index) for index, comment_id in
            reversed(list(enumerate(comment_ids)))]


@pytest.fixture(autouse=True)
def reset_shutdown():
    shutdown.reset()
    yield
    shutdown.reset()


def make_reader(handle_comment=None, metrics=None, fetch_listing=None, reader_count=2):
    return CommentReader(
        handle_comment or MagicMock(),
        reader_count=reader_count,
        metrics=metrics,
        fetch_listing=fetch_listing or (lambda bot_name: []),
        min_seconds_between_polls=0.01,
    )


@pytest.mark.unit
def test_seen_comments_returns_only_new_fullnames_and_forgets_the_oldest():
    seen = SeenComments(capacity=3)
    assert seen.add_new(['t1_a', 't1_b']) == ['t1_a', 't1_b']
    assert seen.add_new(['t1_b', 't1_c', 't1_d']) == ['t1_c', 't1_d']
    assert len(seen) == 3
    # t1_a was the oldest, so it is forgotten and counts as new again
    assert seen.add_new(['t1_a']) == ['t1_a']


@pytest.mark.unit
def test_handle_listing_hands_on_new_comments_oldest_first():
    handled = []
    reader = make_reader(handled.append)
    reader.handle_listing(listing('a', 'b', 'c'), 1.0)
    reader.handle_listing(listing('b', 'c', 'd', 'e'), 1.0)
    assert [comment.id for comment in handled] == ['a', 'b', 'c', 'd', 'e']


@pytest.mark.unit
def test_handle_listing_counts_a_gap_only_when_no_comment_was_seen_before():
    metrics = MagicMock()
    reader = make_reader(metrics=metrics)
    reader.handle_listing([], 1.0)              # Empty: nothing to tell
    reader.handle_listing(listing('a', 'b'), 1.0)   # First comments: cannot tell either
    reader.handle_listing(listing('b', 'c'), 2.0)   # Overlaps the previous poll: nothing missed
    reader.handle_listing(listing('d', 'e'), 3.0)   # All new: comments between c and d may be missed
    reader.handle_listing(listing('d', 'e'), 3.0)   # All seen: nothing new, no gap
    calls = [call.args for call in metrics.record_listing_poll.call_args_list]
    assert calls == [
        (1.0, 0, 0, False),
        (1.0, 2, 2, False),
        (2.0, 2, 1, False),
        (3.0, 2, 2, True),
        (3.0, 2, 0, False),
    ]


@pytest.mark.unit
def test_handle_listing_records_the_newest_creation_time():
    metrics = MagicMock()
    reader = make_reader(metrics=metrics)
    reader.handle_listing([fake_comment('b', 20.0), fake_comment('a', 10.0)], 1.0)
    metrics.record_newest_created.assert_called_once_with(20.0)
    metrics.reset_mock()
    reader.handle_listing([fake_comment('b', 20.0)], 1.0)
    metrics.record_newest_created.assert_not_called()


@pytest.mark.unit
def test_readers_take_turns_between_the_accounts():
    reader = make_reader(reader_count=len(reddit.BOT_NAMES) + 1)
    accounts = [thread._args[0] for thread in reader.threads]
    assert accounts == list(reddit.BOT_NAMES) + [reddit.BOT_NAMES[0]]
    # Staggered: each reader starts a little after the previous one
    delays = [thread._args[1] for thread in reader.threads]
    assert delays == sorted(delays) and delays[0] == 0 and delays[1] > 0


@pytest.mark.unit
def test_reader_threads_read_until_shutdown_and_hand_on_each_comment_once():
    handled = []
    lock = threading.Lock()
    polls = []

    def fetch_listing(bot_name):
        with lock:
            polls.append(bot_name)
            number = len(polls)
        # Each poll returns the 3 newest of a growing list, so consecutive polls overlap
        return listing(*[f'c{index}' for index in range(max(0, number - 3), number)])

    def handle_comment(comment):
        with lock:
            handled.append(comment.id)

    reader = make_reader(handle_comment, fetch_listing=fetch_listing).start()
    assert wait_until(lambda: len(polls) >= 20)
    shutdown.request_stop()
    for thread in reader.threads:
        thread.join(WAIT_SECONDS)
    assert not any(thread.is_alive() for thread in reader.threads)
    assert len(handled) == len(set(handled))
    assert len(handled) >= 20


@pytest.mark.unit
def test_poll_backs_off_after_a_network_error():
    reader = make_reader(fetch_listing=MagicMock(side_effect=prawcore.exceptions.ServerError(MagicMock())))
    with patch('racb.comment_reader.back_off_after_network_error', return_value=False) as mock_back_off:
        assert reader.poll('bot') is False
    mock_back_off.assert_called_once()


@pytest.mark.unit
def test_poll_logs_an_unexpected_error_and_waits(caplog):
    import logging
    reader = make_reader(fetch_listing=MagicMock(side_effect=ValueError('odd')))
    with patch('racb.comment_reader.shutdown.stop_requested_within', return_value=False) as mock_wait, \
         caplog.at_level(logging.ERROR, logger='racb.comment_reader'):
        assert reader.poll('bot') is False
    mock_wait.assert_called_once_with(comment_reader.ERROR_RETRY_SECONDS)
    assert 'Could not read the r/all comment listing' in caplog.text


@pytest.mark.unit
def test_poll_raises_with_debug():
    reader = make_reader(fetch_listing=MagicMock(side_effect=ValueError('odd')))
    with patch('racb.comment_reader.config.is_debug', return_value=True), pytest.raises(ValueError):
        reader.poll('bot')


@pytest.mark.unit
def test_run_returns_on_shutdown():
    reader = make_reader()
    thread = threading.Thread(target=reader.run, daemon=True)
    thread.start()
    shutdown.request_stop()
    thread.join(WAIT_SECONDS)
    assert not thread.is_alive()


@pytest.mark.unit
def test_fetch_listing_asks_r_all_for_the_most_comments_as_the_given_account():
    with patch('racb.comment_reader.reddit.get_reddit_instance') as mock_instance:
        mock_instance.return_value.subreddit.return_value.comments.return_value = iter([fake_comment('a')])
        comments = comment_reader.fetch_listing('some_bot')
    mock_instance.assert_called_once_with('some_bot')
    mock_instance.return_value.subreddit.assert_called_once_with('all')
    mock_instance.return_value.subreddit.return_value.comments.assert_called_once_with(
        limit=comment_reader.LISTING_LIMIT
    )
    assert [comment.id for comment in comments] == ['a']
