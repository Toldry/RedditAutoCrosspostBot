"""Unit tests for racb.main module."""

from unittest.mock import patch, MagicMock
import pytest
import praw.exceptions
import prawcore
import requests
from racb.core import reddit
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
         patch('racb.core.reddit.authenticate_bots') as mock_auth, \
         patch('racb.main.start_bot') as mock_start, \
         patch('sys.argv', ['racb']):
        main()
        mock_auth.assert_called_once_with(reddit.BOT_NAMES)
        mock_start.assert_called_once()


@pytest.mark.unit
def test_main_startup_auth_failure_exits():
    from racb.main import main

    with patch('racb.main.configure_logging'), \
         patch('racb.core.reddit.authenticate_bots', side_effect=RuntimeError('Bot authentication failed')), \
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
         patch('racb.core.reddit.authenticate_bots') as mock_auth, \
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
         patch('racb.core.reddit.authenticate_bots') as mock_auth, \
         patch('racb.main.phase3.process_oldest_entry', return_value=found) as mock_oldest, \
         patch('racb.main.start_bot') as mock_start, \
         patch('racb.main.phase2.filter_comments_from_db') as mock_phase2, \
         patch('sys.argv', ['racb', '--process-one']), \
         pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == expected_exit_code
    mock_oldest.assert_called_once_with()
    # Only the account Phase 3 uses is authenticated, and nothing else runs
    mock_auth.assert_called_once_with([reddit.AUTO_CROSSPOST_BOT_NAME])
    mock_start.assert_not_called()
    mock_phase2.assert_not_called()


@pytest.mark.unit
def test_dispatch_comment_queues_only_matching_comments(make_mock_comment):
    from racb.main import dispatch_comment, metrics
    phase1_workers = MagicMock()
    scanned_before = metrics.total_scanned
    with patch('racb.main.known_subreddits.record') as mock_record:
        match = make_mock_comment(comment_id='match', body='r/pics', created_utc=1_700_000_000.0)
        dispatch_comment(match, phase1_workers)
        dispatch_comment(make_mock_comment(comment_id='other', body='nice picture'), phase1_workers)
    # Not before the author's unnoticed-edit window has passed
    phase1_workers.submit.assert_called_once_with('match', not_before=1_700_000_000.0 + 240)
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
    with patch.dict('os.environ', {'DEBUG': 'false'}):
        jobs = {job.name: job for job in build_jobs()}
    assert set(jobs) == {'cleanup', 'heartbeat', 'inbox', 'phase2', 'known-subreddits', 'phase3'}
    assert jobs['phase2'].lock is not None
    assert jobs['phase2'].lock is jobs['phase3'].lock
    assert jobs['inbox'].lock is None


ALL_BOTS_DISABLED = {
    'AUTO_CROSSPOST_BOT_ENABLED': 'false',
    'SUB_DOESNT_EXIST_BOT_ENABLED': 'false',
    'SAME_SUBREDDIT_BOT_ENABLED': 'false',
    'SAME_POST_BOT_ENABLED': 'false',
}


@pytest.mark.unit
def test_build_jobs_skips_the_writing_jobs_of_disabled_bots():
    from racb.main import build_jobs
    with patch.dict('os.environ', {'AUTO_CROSSPOST_BOT_ENABLED': 'false'}):
        assert {job.name for job in build_jobs()} == {'heartbeat', 'inbox', 'phase2', 'known-subreddits'}
    with patch.dict('os.environ', ALL_BOTS_DISABLED):
        assert {job.name for job in build_jobs()} == {'heartbeat', 'phase2', 'known-subreddits'}


@pytest.mark.unit
def test_process_one_refuses_when_autocrosspostbot_is_disabled():
    from racb.main import main
    with patch('racb.main.configure_logging'), \
         patch('racb.core.reddit.authenticate_bots') as mock_auth, \
         patch('racb.main.phase3.process_oldest_entry') as mock_oldest, \
         patch('sys.argv', ['racb', '--process-one']), \
         patch.dict('os.environ', {'AUTO_CROSSPOST_BOT_ENABLED': 'false'}), \
         pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 2
    mock_oldest.assert_not_called()
    mock_auth.assert_not_called()


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
    comments = [make_mock_comment(comment_id=comment_id, created_utc=0) for comment_id in ('first', 'second')]

    def stream():
        yield comments[0]
        shutdown.request_stop()
        yield comments[1]

    with patch('racb.main.known_subreddits.record'):
        main_loop(stream(), phase1_workers)
    phase1_workers.submit.assert_called_once()
    assert phase1_workers.submit.call_args.args == ('first',)


@pytest.mark.unit
def test_heartbeat_counts_empty_head_polls(caplog):
    import logging
    metrics = StreamMetrics()
    metrics.record_head_poll(1.0)
    metrics.record_head_poll(3.0, empty=True)
    metrics.record_request(1.0, 100)
    with caplog.at_level(logging.INFO, logger='racb.main'):
        metrics.log_heartbeat()
    assert 'head: 2 polls (1 empty), 2.00 s/poll' in caplog.records[-1].getMessage()
    assert metrics.interval_empty_head_polls == 0


@pytest.mark.unit
def test_heartbeat_reports_reads_and_lag(caplog):
    import logging
    import time
    metrics = StreamMetrics()
    metrics.record_request(2.0, 100)
    metrics.record_request(1.0, 80)
    metrics.record_head_poll(4.0)
    metrics.record_newest_created(time.time() - 12)
    metrics.set_lag_ids(1234)
    metrics.record_failed_block()
    with caplog.at_level(logging.INFO, logger='racb.main'):
        metrics.log_heartbeat()
    message = caplog.records[-1].getMessage()
    assert 'reads: 2 requests, 90.0 comments/request, 1.50 s/request' in message
    assert 'head: 1 polls, 4.00 s/poll' in message
    assert 'behind: 12 s, 1,234 IDs' in message
    assert message.endswith('1 ID block(s) failed')
    assert metrics.interval_requests == 0
    assert metrics.interval_failed_blocks == 0


@pytest.mark.unit
def test_heartbeat_counts_rejected_heads(caplog):
    import logging
    metrics = StreamMetrics()
    metrics.record_head_poll(1.0)
    metrics.record_head_poll(1.0, empty=True)
    metrics.record_rejected_head()
    metrics.record_request(1.0, 100)
    with caplog.at_level(logging.INFO, logger='racb.main'):
        metrics.log_heartbeat()
    assert 'head: 2 polls (1 empty, 1 rejected), 1.00 s/poll' in caplog.records[-1].getMessage()
    assert metrics.interval_rejected_heads == 0


@pytest.mark.unit
def test_heartbeat_warns_when_heads_were_found_but_nothing_was_read(caplog):
    # The 2026-10-17 outage: head polls kept succeeding, the readers made no request for hours
    import logging
    metrics = StreamMetrics()
    metrics.record_head_poll(1.0)
    with caplog.at_level(logging.INFO, logger='racb.main'):
        metrics.log_heartbeat()
    assert [record.levelname for record in caplog.records] == ['INFO', 'WARNING']
    assert 'stalled' in caplog.records[-1].getMessage()


@pytest.mark.unit
@pytest.mark.parametrize('head_polls, empty_head_polls, requests', [
    (0, 0, 0),  # STREAM_SOURCE=listing: no head polls
    (3, 3, 0),  # Only empty listings: no head to read up to
    (3, 0, 5),  # Reading normally
])
def test_heartbeat_does_not_warn_without_a_stall(caplog, head_polls, empty_head_polls, requests):
    import logging
    metrics = StreamMetrics()
    for poll in range(head_polls):
        metrics.record_head_poll(1.0, empty=poll < empty_head_polls)
    for _ in range(requests):
        metrics.record_request(1.0, 100)
    with caplog.at_level(logging.INFO, logger='racb.main'):
        metrics.log_heartbeat()
    assert [record.levelname for record in caplog.records] == ['INFO']


@pytest.mark.unit
def test_main_loop_records_each_batch_as_one_request(make_mock_comment):
    from racb.main import main_loop, metrics
    newest_seen = {'fullname': None}
    requests_before = metrics.interval_requests
    comments = [make_mock_comment(comment_id=comment_id, created_utc=0) for comment_id in ('first', 'second')]
    with patch('racb.main.known_subreddits.record'):
        main_loop(iter(comments + [None]), MagicMock(), newest_seen)
    assert metrics.interval_requests == requests_before + 1
    assert newest_seen['fullname'] == 't1_second'


@pytest.mark.unit
def test_listing_stream_resumes_after_the_newest_comment_after_an_error():
    from racb.main import run_listing_stream
    from racb.core import shutdown
    calls = []

    def fake_main_loop(c_stream, phase1_workers, newest_seen):
        calls.append(newest_seen['fullname'])
        if len(calls) == 1:
            newest_seen['fullname'] = 't1_abc'
            raise prawcore.exceptions.ServerError(MagicMock())
        shutdown.request_stop()

    with patch('racb.main.init_comment_stream') as mock_init, \
         patch('racb.main.main_loop', side_effect=fake_main_loop), \
         patch('racb.main.handle_exception', return_value=False):
        run_listing_stream(MagicMock())
    assert [call.kwargs['continue_after_id'] for call in mock_init.call_args_list] == [None, 't1_abc']


@pytest.mark.unit
def test_init_comment_stream_continues_after_a_given_comment():
    from racb.main import init_comment_stream
    with patch('racb.main.reddit.get_reddit_instance') as mock_instance:
        init_comment_stream(continue_after_id='t1_abc')
        init_comment_stream()
    comments = mock_instance.return_value.subreddit.return_value.stream.comments
    assert comments.call_args_list[0].kwargs == {'continue_after_id': 't1_abc', 'pause_after': -1}
    assert comments.call_args_list[1].kwargs == {'skip_existing': True, 'pause_after': -1}


@pytest.mark.unit
@pytest.mark.parametrize('source', ['ids', 'listing'])
def test_run_stream_reads_from_stream_source(source):
    from racb.main import run_stream
    with patch.dict('os.environ', {'STREAM_SOURCE': source, 'STREAM_READERS': '3'}), \
         patch('racb.main.run_listing_stream') as mock_listing, \
         patch('racb.main.CommentReader') as mock_reader:
        threads = run_stream(MagicMock())
    if source == 'listing':
        mock_listing.assert_called_once()
        mock_reader.assert_not_called()
        assert threads == []
    else:
        mock_listing.assert_not_called()
        assert mock_reader.call_args.args[1] == 3
        mock_reader.return_value.start.assert_called_once_with()
        mock_reader.return_value.run.assert_called_once_with()
        assert threads is mock_reader.return_value.threads
