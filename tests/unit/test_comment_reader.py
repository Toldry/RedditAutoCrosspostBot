"""Unit tests for racb.comment_reader (reading every new comment by ID block)."""

import datetime
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import prawcore
import pytest

from racb import comment_reader
from racb.comment_reader import (
    BLOCK_SIZE,
    CURSOR_NAME,
    MAX_BACKLOG_IDS,
    CommentReader,
    block_fullnames,
    comment_id_to_number,
    is_shown_on_r_all,
    number_to_comment_id,
)
from racb.core import shutdown

WAIT_SECONDS = 5


def wait_until(condition, timeout=WAIT_SECONDS):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def fake_comment(number, **fields):
    # The fields /api/info returns for a comment (it has no over_18 or quarantine)
    defaults = {'id': number_to_comment_id(number), 'subreddit': 'pics', 'subreddit_type': 'public',
                'created_utc': 1_700_000_000.0 + number}
    defaults.update(fields)
    return SimpleNamespace(**defaults)


class FakeCursorStore:
    """Stands in for racb.core.db's cursor functions."""

    def __init__(self, saved=None):
        self.saved = saved
        self.saves = []

    def get_stream_cursor(self, name):
        assert name == CURSOR_NAME
        return self.saved

    def save_stream_cursor(self, name, next_comment_id, done_blocks):
        self.saves.append((next_comment_id, list(done_blocks)))


def make_reader(head=10_000, saved=None, fetch_block=None, handle_comment=None, metrics=None):
    store = FakeCursorStore(saved)
    reader = CommentReader(
        handle_comment or MagicMock(),
        reader_count=2,
        metrics=metrics,
        fetch_head=lambda: head,
        fetch_block=fetch_block or (lambda bot_name, start: []),
        cursor_store=store,
    )
    return reader, store


def queued_blocks(reader):
    blocks = []
    while not reader.blocks.empty():
        blocks.append(reader.blocks.get_nowait())
    return blocks


@pytest.mark.unit
def test_comment_ids_convert_to_numbers_and_back():
    assert comment_id_to_number('z') == 35
    assert comment_id_to_number('t1_10') == 36
    assert number_to_comment_id(0) == '0'
    for comment_id in ('1', 'k3l2x9a', 'zzzzzzz'):
        assert number_to_comment_id(comment_id_to_number(comment_id)) == comment_id


@pytest.mark.unit
def test_a_block_is_100_consecutive_fullnames():
    fullnames = block_fullnames(comment_id_to_number('a0'))
    assert len(fullnames) == BLOCK_SIZE
    assert fullnames[0] == 't1_a0'
    assert fullnames[1] == 't1_a1'
    assert fullnames[-1] == f't1_{number_to_comment_id(comment_id_to_number("a0") + BLOCK_SIZE - 1)}'


@pytest.mark.unit
@pytest.mark.parametrize('fields, shown', [
    ({}, True),
    ({'subreddit_type': 'restricted'}, True),
    # Phase 1 drops these by their submission: /api/info comments carry neither field
    ({'over_18': True}, True),
    ({'quarantine': True}, True),
    ({'subreddit_type': 'private'}, False),
    ({'subreddit_type': 'user'}, False),
    ({'subreddit': 'u_someone'}, False),
    ({'subreddit': SimpleNamespace(display_name='U_Someone')}, False),
    ({'subreddit': SimpleNamespace(display_name='pics')}, True),
])
def test_only_comments_r_all_would_show_are_handled(fields, shown):
    assert is_shown_on_r_all(fake_comment(1, **fields)) is shown


@pytest.mark.unit
def test_is_shown_on_r_all_never_reads_missing_fields_through_getattr():
    class LazyComment:
        def __getattr__(self, name):
            raise AssertionError(f'{name} would make PRAW fetch the comment')

    comment = LazyComment()
    comment.subreddit = 'pics'
    assert is_shown_on_r_all(comment) is True


@pytest.mark.unit
def test_fetch_head_takes_the_newest_of_several_comments():
    listing = [SimpleNamespace(id='k3l2x9a'), SimpleNamespace(id='k3l2x9c'), SimpleNamespace(id='k3l2x9b')]
    instance = MagicMock()
    instance.subreddit.return_value.comments.return_value = iter(listing)
    with patch('racb.comment_reader.reddit.get_reddit_instance', return_value=instance):
        assert comment_reader.fetch_head() == comment_id_to_number('k3l2x9c')
    assert instance.subreddit.return_value.comments.call_args.kwargs['limit'] > 1


@pytest.mark.unit
def test_fetch_head_raises_on_an_empty_listing():
    instance = MagicMock()
    instance.subreddit.return_value.comments.return_value = iter([])
    with patch('racb.comment_reader.reddit.get_reddit_instance', return_value=instance):
        with pytest.raises(RuntimeError):
            comment_reader.fetch_head()


@pytest.mark.unit
def test_a_fresh_start_begins_after_the_newest_comment():
    reader, _ = make_reader(head=10_000)
    reader.poll_head()
    assert reader.next_id == 10_001
    assert queued_blocks(reader) == []


@pytest.mark.unit
def test_only_complete_blocks_up_to_the_head_are_queued():
    head = {'value': 10_000}
    reader, _ = make_reader()
    reader.fetch_head = lambda: head['value']
    reader.poll_head()
    head['value'] = 10_000 + 2 * BLOCK_SIZE + 50
    reader.poll_head()
    assert queued_blocks(reader) == [10_001, 10_001 + BLOCK_SIZE]
    assert reader.next_id == 10_001 + 2 * BLOCK_SIZE


@pytest.mark.unit
def test_a_restart_resumes_from_the_saved_cursor_and_skips_blocks_already_done():
    start = 10_000 - 3 * BLOCK_SIZE + 1
    saved = {'next_comment_id': start, 'done_blocks': [start + BLOCK_SIZE],
             'updated_at': datetime.datetime(2026, 10, 16, 20, 0)}
    reader, _ = make_reader(head=10_000, saved=saved)
    reader.poll_head()
    assert queued_blocks(reader) == [start, start + 2 * BLOCK_SIZE]


@pytest.mark.unit
def test_a_cursor_too_far_behind_is_ignored():
    saved = {'next_comment_id': 1, 'done_blocks': [], 'updated_at': None}
    reader, _ = make_reader(head=MAX_BACKLOG_IDS + 10, saved=saved)
    reader.poll_head()
    assert reader.next_id == MAX_BACKLOG_IDS + 11
    assert queued_blocks(reader) == []


@pytest.mark.unit
def test_a_cursor_that_cannot_be_read_starts_at_the_head():
    reader, store = make_reader(head=500)
    store.get_stream_cursor = MagicMock(side_effect=RuntimeError('db down'))
    with patch.dict('os.environ', {'DEBUG': 'false'}):
        reader.poll_head()
    assert reader.next_id == 501


@pytest.mark.unit
def test_the_reader_skips_ahead_when_too_far_behind():
    metrics = MagicMock()
    head = {'value': 10_000}
    reader, _ = make_reader(metrics=metrics)
    reader.fetch_head = lambda: head['value']
    reader.poll_head()
    head['value'] = 10_000 + MAX_BACKLOG_IDS + 500
    reader.poll_head()
    metrics.record_skipped_ids.assert_called_once_with(MAX_BACKLOG_IDS + 500)
    assert reader.next_id == head['value'] + 1


@pytest.mark.unit
def test_the_cursor_holds_the_watermark_and_the_blocks_done_above_it():
    head = {'value': 0}
    reader, store = make_reader()
    reader.fetch_head = lambda: head['value']
    reader.poll_head()
    head['value'] = 3 * BLOCK_SIZE
    reader.poll_head()
    first, second, third = queued_blocks(reader)
    # Blocks finish out of order: the second and third before the first
    reader.finish(second)
    reader.finish(third)
    reader.save_cursor()
    assert store.saves[-1] == (first, [second, third])
    reader.finish(first)
    reader.save_cursor()
    assert store.saves[-1] == (third + BLOCK_SIZE, [])


@pytest.mark.unit
def test_read_block_hands_on_shown_comments_and_records_the_request():
    handled = []
    metrics = MagicMock()
    comments = [fake_comment(1), fake_comment(2, subreddit_type='private'), fake_comment(3)]
    fetched = []

    def fetch_block(bot_name, start):
        fetched.append((bot_name, start))
        return comments

    reader, _ = make_reader(fetch_block=fetch_block, handle_comment=handled.append, metrics=metrics)
    reader.next_id, reader.in_flight = 200, {100}
    reader.read_block('same_post_bot', 100)
    assert fetched == [('same_post_bot', 100)]
    assert handled == [comments[0], comments[2]]
    assert metrics.record_request.call_args.args[1] == 3
    metrics.record_newest_created.assert_called_once_with(comments[2].created_utc)
    assert reader.pending() == 0


@pytest.mark.unit
def test_read_block_retries_network_errors():
    attempts = []

    def flaky_fetch(bot_name, start):
        attempts.append(start)
        if len(attempts) == 1:
            raise prawcore.exceptions.ServerError(MagicMock())
        return [fake_comment(start)]

    handled = []
    reader, _ = make_reader(fetch_block=flaky_fetch, handle_comment=handled.append)
    reader.next_id, reader.in_flight = 200, {100}
    with patch.dict('os.environ', {'DEBUG': 'false'}), \
         patch('racb.comment_reader.back_off_after_network_error', return_value=False) as mock_back_off:
        reader.read_block('AutoCrosspostBot', 100)
    assert len(attempts) == 2
    mock_back_off.assert_called_once()
    assert len(handled) == 1


@pytest.mark.unit
def test_read_block_gives_up_after_repeated_errors_and_moves_on():
    metrics = MagicMock()

    def failing_fetch(bot_name, start):
        raise prawcore.exceptions.ServerError(MagicMock())

    reader, _ = make_reader(fetch_block=failing_fetch, metrics=metrics)
    reader.next_id, reader.in_flight = 200, {100}
    with patch.dict('os.environ', {'DEBUG': 'false'}), \
         patch('racb.comment_reader.back_off_after_network_error', return_value=False) as mock_back_off:
        reader.read_block('AutoCrosspostBot', 100)
    assert mock_back_off.call_count == comment_reader.MAX_BLOCK_ATTEMPTS
    metrics.record_failed_block.assert_called_once_with()
    assert reader.pending() == 0


@pytest.mark.unit
def test_a_block_interrupted_by_shutdown_stays_unfinished_so_the_restart_reads_it():
    def failing_fetch(bot_name, start):
        raise prawcore.exceptions.ServerError(MagicMock())

    reader, _ = make_reader(fetch_block=failing_fetch)
    reader.next_id, reader.in_flight = 200, {100}
    with patch.dict('os.environ', {'DEBUG': 'false'}), \
         patch('racb.comment_reader.back_off_after_network_error', return_value=True):
        reader.read_block('AutoCrosspostBot', 100)
    assert reader.pending() == 1


@pytest.mark.unit
def test_readers_take_turns_between_the_bot_accounts():
    reader = CommentReader(MagicMock(), reader_count=5)
    names = [thread._args[0] for thread in reader.threads]
    assert names == ['AutoCrosspostBot', 'sub_doesnt_exist_bot', 'same_subreddit_bot', 'same_post_bot',
                     'AutoCrosspostBot']


@pytest.mark.unit
def test_reader_threads_read_every_queued_block_then_stop_on_shutdown():
    handled = []
    handled_lock = threading.Lock()

    def handle(comment):
        with handled_lock:
            handled.append(comment.id)

    def fetch_block(bot_name, start):
        return [fake_comment(number) for number in range(start, start + BLOCK_SIZE)]

    head = {'value': 0}
    reader, store = make_reader(fetch_block=fetch_block, handle_comment=handle)
    reader.fetch_head = lambda: head['value']
    reader.poll_head()
    head['value'] = 5 * BLOCK_SIZE
    reader.poll_head()
    reader.start()
    assert wait_until(lambda: reader.pending() == 0)
    shutdown.request_stop()
    for thread in reader.threads:
        thread.join(WAIT_SECONDS)
    assert sorted(handled, key=comment_id_to_number) == [number_to_comment_id(n) for n in range(1, 501)]
    reader.save_cursor()
    assert store.saves[-1] == (501, [])


@pytest.mark.unit
def test_run_polls_until_shutdown_and_saves_the_cursor_at_the_end():
    reader, store = make_reader(head=1000)
    polls = []

    def poll_then_stop():
        polls.append(1)
        shutdown.request_stop()
        return 1000

    reader.fetch_head = poll_then_stop
    reader.run()
    assert polls == [1]
    assert store.saves[-1] == (1001, [])


@pytest.mark.unit
def test_run_hands_head_poll_errors_to_handle_exception():
    errors = []

    def handle_exception(error):
        errors.append(error)
        shutdown.request_stop()
        return False

    reader, _ = make_reader()
    reader.handle_exception = handle_exception
    reader.fetch_head = MagicMock(side_effect=prawcore.exceptions.ServerError(MagicMock()))
    reader.run()
    assert len(errors) == 1


@pytest.mark.unit
def test_a_failing_cursor_save_is_logged_not_raised():
    reader, store = make_reader(head=1000)
    reader.poll_head()
    store.save_stream_cursor = MagicMock(side_effect=RuntimeError('db down'))
    with patch.dict('os.environ', {'DEBUG': 'false'}):
        reader.save_cursor()
