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
    HEADS_BELOW_POSITION_BEFORE_RESET,
    MAX_BACKLOG_IDS,
    MAX_COMMENT_IDS_PER_SECOND,
    CommentReader,
    block_fullnames,
    comment_id_to_number,
    is_shown_on_r_all,
    newest_consistent_id,
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


class FakeClock:
    """Stands in for time.time(), so tests decide how much time passes between head polls."""

    def __init__(self, start=1_800_000_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


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
def test_fetch_head_returns_none_on_an_empty_listing():
    instance = MagicMock()
    instance.subreddit.return_value.comments.return_value = iter([])
    with patch('racb.comment_reader.reddit.get_reddit_instance', return_value=instance):
        assert comment_reader.fetch_head() is None


@pytest.mark.unit
def test_an_empty_head_listing_is_skipped_and_the_next_poll_catches_up():
    reader, _ = make_reader(head=1000)
    reader.poll_head()
    head = {'value': None}
    reader.fetch_head = lambda: head['value']
    reader.poll_head()
    reader.poll_head()
    assert reader.empty_head_polls == 2
    assert queued_blocks(reader) == []
    head['value'] = 1000 + 3 * BLOCK_SIZE
    reader.poll_head()
    assert reader.empty_head_polls == 0
    assert queued_blocks(reader) == [1001, 1101, 1201]


@pytest.mark.unit
def test_only_a_long_streak_of_empty_head_polls_is_logged(caplog):
    import logging
    reader, _ = make_reader(head=1000)
    reader.poll_head()
    reader.fetch_head = lambda: None
    with caplog.at_level(logging.INFO, logger='racb.comment_reader'):
        for _ in range(comment_reader.EMPTY_HEAD_POLLS_BEFORE_WARNING - 1):
            reader.poll_head()
        assert not caplog.records
        reader.poll_head()
        reader.poll_head()
        assert [record.levelname for record in caplog.records] == ['WARNING']
        reader.fetch_head = lambda: 1000
        reader.poll_head()
    assert [record.levelname for record in caplog.records] == ['WARNING', 'INFO']
    assert reader.empty_head_polls == 0


@pytest.mark.unit
def test_a_short_streak_of_empty_head_polls_logs_nothing(caplog):
    import logging
    reader, _ = make_reader(head=1000)
    reader.poll_head()
    head = {'value': None}
    reader.fetch_head = lambda: head['value']
    with caplog.at_level(logging.INFO, logger='racb.comment_reader'):
        reader.poll_head()
        head['value'] = 1000
        reader.poll_head()
    assert not caplog.records


@pytest.mark.unit
def test_run_survives_an_empty_head_listing():
    errors = []
    polls = []

    def empty_then_stop():
        polls.append(1)
        if len(polls) == 2:
            shutdown.request_stop()
        return None

    reader, _ = make_reader()
    reader.handle_exception = lambda error: errors.append(error) or True
    reader.fetch_head = empty_then_stop
    with patch('racb.comment_reader.HEAD_POLL_SECONDS', 0):
        reader.run()
    assert polls == [1, 1]
    assert errors == []


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
    clock = FakeClock()
    with patch('racb.comment_reader.time.time', clock):
        reader.poll_head()
        # A real backlog that large only builds up over time, e.g. while the head polls failed
        clock.advance(600)
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


# Regression tests for the 2026-10-17 outage. The r/all listing returned a comment whose ID was about 6 * 10^18
# (real IDs were about 8 * 10^10). The reader skipped ahead to it, no real head was ever above its position
# again, and it read nothing for hours while the heartbeat said `reads: 0 requests` and a huge negative lag.
BOGUS_HEAD = 6_022_465_357_511_254_311
REAL_HEAD = comment_id_to_number('ph9dx3x')


@pytest.mark.unit
def test_newest_consistent_id_ignores_an_outlier_in_the_listing(caplog):
    import logging
    ids = [number_to_comment_id(REAL_HEAD - offset) for offset in range(99)]
    ids.append(number_to_comment_id(BOGUS_HEAD))
    with caplog.at_level(logging.WARNING, logger='racb.comment_reader'):
        assert newest_consistent_id(ids) == REAL_HEAD
    assert [record.levelname for record in caplog.records] == ['WARNING']


@pytest.mark.unit
def test_newest_consistent_id_skips_malformed_ids():
    assert newest_consistent_id(['ph9dx3x', '', 'not-base-36!', None]) == REAL_HEAD
    assert newest_consistent_id(['!!']) is None
    assert newest_consistent_id([]) is None


@pytest.mark.unit
def test_fetch_head_ignores_a_bogus_id_in_the_listing():
    listing = [SimpleNamespace(id=number_to_comment_id(REAL_HEAD - offset)) for offset in range(50)]
    listing.insert(10, SimpleNamespace(id=number_to_comment_id(BOGUS_HEAD)))
    instance = MagicMock()
    instance.subreddit.return_value.comments.return_value = iter(listing)
    with patch('racb.comment_reader.reddit.get_reddit_instance', return_value=instance):
        assert comment_reader.fetch_head() == REAL_HEAD


@pytest.mark.unit
def test_a_bogus_head_far_ahead_is_rejected_not_followed(caplog):
    import logging
    metrics = MagicMock()
    head = {'value': REAL_HEAD}
    reader, _ = make_reader(metrics=metrics)
    reader.fetch_head = lambda: head['value']
    clock = FakeClock()
    with patch('racb.comment_reader.time.time', clock), \
            caplog.at_level(logging.WARNING, logger='racb.comment_reader'):
        reader.poll_head()
        for _ in range(5):
            clock.advance(10)
            head['value'] = BOGUS_HEAD
            reader.poll_head()
    assert reader.next_id == REAL_HEAD + 1
    metrics.record_skipped_ids.assert_not_called()
    assert metrics.record_rejected_head.call_count == 5
    # One WARNING for the whole streak, not one per poll
    assert [record.levelname for record in caplog.records] == ['WARNING']


@pytest.mark.unit
def test_reading_continues_through_bogus_heads():
    # Replays the incident: real heads, then bogus ones mixed in, then real ones again
    metrics = MagicMock()
    head = {'value': REAL_HEAD}
    reader, _ = make_reader(metrics=metrics)
    reader.fetch_head = lambda: head['value']
    clock = FakeClock()
    real_head = REAL_HEAD
    queued = []
    with patch('racb.comment_reader.time.time', clock):
        reader.poll_head()
        for poll in range(30):
            clock.advance(10)
            real_head += 1000  # About 100 new comments a second
            head['value'] = BOGUS_HEAD if poll % 3 == 1 else real_head
            reader.poll_head()
            queued.extend(queued_blocks(reader))
    assert len(queued) == (real_head - REAL_HEAD) // BLOCK_SIZE
    assert queued == sorted(set(queued))
    assert reader.next_id <= real_head + 1
    lag_values = [call.args[0] for call in metrics.set_lag_ids.call_args_list]
    assert all(0 <= lag < MAX_BACKLOG_IDS for lag in lag_values)
    metrics.record_skipped_ids.assert_not_called()


@pytest.mark.unit
def test_a_head_jump_is_allowed_when_enough_time_passed():
    head = {'value': REAL_HEAD}
    reader, _ = make_reader()
    reader.fetch_head = lambda: head['value']
    clock = FakeClock()
    with patch('racb.comment_reader.time.time', clock):
        reader.poll_head()
        clock.advance(3600)
        jump = MAX_BACKLOG_IDS + 3600 * MAX_COMMENT_IDS_PER_SECOND
        head['value'] = REAL_HEAD + jump
        reader.poll_head()
    assert reader.next_id == REAL_HEAD + jump + 1


@pytest.mark.unit
def test_a_head_slightly_behind_the_last_one_queues_nothing_and_reading_goes_on():
    # Reddit's listing is not strictly ordered over time: a later poll can return an older newest comment
    head = {'value': REAL_HEAD}
    reader, _ = make_reader()
    reader.fetch_head = lambda: head['value']
    reader.poll_head()
    head['value'] = REAL_HEAD - 3 * BLOCK_SIZE
    reader.poll_head()
    assert queued_blocks(reader) == []
    assert reader.next_id == REAL_HEAD + 1
    head['value'] = REAL_HEAD + 2 * BLOCK_SIZE
    reader.poll_head()
    assert queued_blocks(reader) == [REAL_HEAD + 1, REAL_HEAD + 1 + BLOCK_SIZE]


@pytest.mark.unit
def test_a_position_far_ahead_of_every_head_resets_to_the_head(caplog):
    # What the deployed reader was stuck in: a fresh start on a bogus first head has no reference to reject it
    import logging
    head = {'value': BOGUS_HEAD}
    reader, _ = make_reader()
    reader.fetch_head = lambda: head['value']
    reader.poll_head()
    assert reader.next_id == BOGUS_HEAD + 1
    head['value'] = REAL_HEAD
    with caplog.at_level(logging.WARNING, logger='racb.comment_reader'):
        for _ in range(HEADS_BELOW_POSITION_BEFORE_RESET - 1):
            reader.poll_head()
        assert reader.next_id == BOGUS_HEAD + 1
        reader.poll_head()
    assert reader.next_id == REAL_HEAD + 1
    assert [record.levelname for record in caplog.records] == ['WARNING']
    head['value'] = REAL_HEAD + 2 * BLOCK_SIZE
    reader.poll_head()
    assert queued_blocks(reader) == [REAL_HEAD + 1, REAL_HEAD + 1 + BLOCK_SIZE]


@pytest.mark.unit
def test_a_restart_with_a_bogus_first_head_resumes_from_the_cursor():
    clock = FakeClock()
    start = REAL_HEAD - 5 * BLOCK_SIZE
    saved_at = datetime.datetime.fromtimestamp(clock() - 60, datetime.timezone.utc).replace(tzinfo=None)
    saved = {'next_comment_id': start, 'done_blocks': [start + BLOCK_SIZE], 'updated_at': saved_at}
    head = {'value': BOGUS_HEAD}
    reader, _ = make_reader(saved=saved)
    reader.fetch_head = lambda: head['value']
    with patch('racb.comment_reader.time.time', clock):
        reader.poll_head()
        assert reader.next_id == start
        assert queued_blocks(reader) == []
        clock.advance(10)
        head['value'] = REAL_HEAD
        reader.poll_head()
    assert queued_blocks(reader) == [start, start + 2 * BLOCK_SIZE, start + 3 * BLOCK_SIZE,
                                     start + 4 * BLOCK_SIZE]


@pytest.mark.unit
def test_a_restart_with_a_cursor_ahead_of_the_head_starts_at_the_head():
    # The cursor the stalled reader saved on 2026-10-17: just past the bogus ID
    saved = {'next_comment_id': BOGUS_HEAD + 1, 'done_blocks': [],
             'updated_at': datetime.datetime(2026, 10, 17, 6, 0)}
    reader, _ = make_reader(head=REAL_HEAD, saved=saved)
    reader.poll_head()
    assert reader.next_id == REAL_HEAD + 1
    assert queued_blocks(reader) == []


@pytest.mark.unit
def test_the_reader_keeps_making_read_requests_while_heads_are_bogus():
    # End to end with reader threads: reading must not stop when bogus heads come in
    reads = []
    real_head = {'value': REAL_HEAD}
    polls = []

    def fetch_head():
        polls.append(1)
        if len(polls) % 2 == 0:
            return BOGUS_HEAD
        real_head['value'] += 2 * BLOCK_SIZE
        return real_head['value']

    def fetch_block(bot_name, start):
        reads.append(start)
        return [fake_comment(start)]

    handled = []
    reader, _ = make_reader(fetch_block=fetch_block, handle_comment=handled.append)
    reader.fetch_head = fetch_head
    reader.start()
    try:
        for _ in range(12):
            reader.poll_head()
        assert wait_until(lambda: len(reads) >= 10)
    finally:
        shutdown.request_stop()
        for thread in reader.threads:
            thread.join(WAIT_SECONDS)
    assert reader.next_id <= real_head['value'] + 1
    assert len(handled) >= 10
