"""Main application entry point for RACB."""

import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import sys
import threading
import time

import praw
import prawcore

from racb.version import __version__
from racb import config, i18n
from racb.config import env_bool
from racb.comment_reader import CommentReader
from racb.core import db, known_subreddits, reddit, shutdown
from racb.phases import phase1, phase2, phase3, inbox, cleanup
from racb.workers import PeriodicJob, Phase1Workers

logger = logging.getLogger(__name__)

NETWORK_RETRY_DELAY_SECONDS = 30
TOO_MANY_REQUESTS_DELAY_SECONDS = 60

# How long a shutdown waits for the jobs and Phase 1 workers to finish what they are doing.
# Keep it below `stop_grace_period` in docker-compose.yml, after which Docker kills the process.
SHUTDOWN_TIMEOUT_SECONDS = 50

MINUTE = 60

# Network-level failures: back off, then rebuild the stream
TRANSIENT_NETWORK_ERRORS = reddit.TRANSIENT_NETWORK_ERRORS
DATABASE_CONNECTION_ERRORS = db.CONNECTION_ERRORS


class StreamMetrics:
    """Tracks throughput and events for Reddit comment and inbox streaming.

    The stream or reader threads and the Phase 1 workers record events while the heartbeat job reads and
    resets the counters, so every access holds a lock.

    Besides the counts, the heartbeat says how the comments were read (requests, comments per request,
    seconds per request) and how far behind the newest comment the bot is, which tells whether it keeps up
    with Reddit.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.interval_scanned = 0
        self.interval_matches = 0
        self.interval_aux = 0
        self.total_scanned = 0
        self.total_matches = 0
        self.total_aux = 0
        self.interval_dropped = 0
        self.interval_requests = 0
        self.interval_request_comments = 0
        self.interval_request_seconds = 0.0
        self.interval_head_polls = 0
        self.interval_head_seconds = 0.0
        self.interval_empty_head_polls = 0
        self.interval_rejected_heads = 0
        self.interval_failed_blocks = 0
        self.interval_skipped_ids = 0
        # Creation time of the newest comment read so far, and how many IDs wait to be read
        self.newest_created_utc = None
        self.lag_ids = None
        self.last_log_time = time.time()

    def record_comment(self):
        with self._lock:
            self.interval_scanned += 1
            self.total_scanned += 1

    def record_match(self):
        with self._lock:
            self.interval_matches += 1
            self.total_matches += 1

    def record_aux(self):
        with self._lock:
            self.interval_aux += 1
            self.total_aux += 1

    def record_dropped(self):
        """Counts a matching comment dropped because the Phase 1 queue was full."""
        with self._lock:
            self.interval_dropped += 1

    def record_request(self, seconds, comments_returned):
        """Counts one request that read comments (a listing batch or an ID block)."""
        with self._lock:
            self.interval_requests += 1
            self.interval_request_seconds += seconds
            self.interval_request_comments += comments_returned

    def record_head_poll(self, seconds, empty=False):
        """Counts one head poll; `empty` if the r/all listing returned no comments."""
        with self._lock:
            self.interval_head_polls += 1
            self.interval_head_seconds += seconds
            if empty:
                self.interval_empty_head_polls += 1

    def record_rejected_head(self):
        """Counts a head comment ID ignored because it was implausibly far ahead (see comment_reader)."""
        with self._lock:
            self.interval_rejected_heads += 1

    def record_failed_block(self):
        """Counts a block of comment IDs given up on after repeated errors."""
        with self._lock:
            self.interval_failed_blocks += 1

    def record_skipped_ids(self, count):
        """Counts comment IDs skipped because the reader fell too far behind."""
        with self._lock:
            self.interval_skipped_ids += count

    def record_newest_created(self, created_utc):
        with self._lock:
            if self.newest_created_utc is None or created_utc > self.newest_created_utc:
                self.newest_created_utc = created_utc

    def set_lag_ids(self, lag_ids):
        with self._lock:
            self.lag_ids = lag_ids

    def log_heartbeat(self):
        with self._lock:
            now = time.time()
            elapsed_min = max(1, round((now - self.last_log_time) / 60))
            message = (
                f'[Heartbeat] Last ~{elapsed_min}m: scanned {self.interval_scanned:,} comments on r/all | '
                f'{self.interval_matches} recommendation(s) stored | {self.interval_aux} aux bot reply(s) '
                f'[Total uptime: {self.total_scanned:,} scanned, {self.total_matches} matches]'
            )
            if self.interval_dropped:
                message += f' | {self.interval_dropped} match(es) dropped, Phase 1 queue full'
            message += self._reading_summary(now)
            # Heads came back but nothing was read: the reader is stuck, as on 2026-10-17
            stalled = (
                self.interval_head_polls > self.interval_empty_head_polls and self.interval_requests == 0
            )
            self.interval_scanned = 0
            self.interval_matches = 0
            self.interval_aux = 0
            self.interval_dropped = 0
            self.interval_requests = 0
            self.interval_request_comments = 0
            self.interval_request_seconds = 0.0
            self.interval_head_polls = 0
            self.interval_head_seconds = 0.0
            self.interval_empty_head_polls = 0
            self.interval_rejected_heads = 0
            self.interval_failed_blocks = 0
            self.interval_skipped_ids = 0
            self.last_log_time = now
        logger.info(message)
        if stalled:
            logger.warning(
                '[Heartbeat] The newest comment ID was found but no comments were read: the comment reader is stalled'
            )

    def _reading_summary(self, now):
        """The heartbeat's second half: how comments were read, and how far behind."""
        parts = []
        if self.interval_requests:
            parts.append(
                f'reads: {self.interval_requests:,} requests, '
                f'{self.interval_request_comments / self.interval_requests:.1f} comments/request, '
                f'{self.interval_request_seconds / self.interval_requests:.2f} s/request'
            )
        else:
            parts.append('reads: 0 requests')
        if self.interval_head_polls:
            details = []
            if self.interval_empty_head_polls:
                details.append(f'{self.interval_empty_head_polls:,} empty')
            if self.interval_rejected_heads:
                details.append(f'{self.interval_rejected_heads:,} rejected')
            empty = f' ({", ".join(details)})' if details else ''
            parts.append(
                f'head: {self.interval_head_polls:,} polls{empty}, '
                f'{self.interval_head_seconds / self.interval_head_polls:.2f} s/poll'
            )
        behind = []
        if self.newest_created_utc is not None:
            behind.append(f'{max(0, now - self.newest_created_utc):.0f} s')
        if self.lag_ids is not None:
            behind.append(f'{self.lag_ids:,} IDs')
        if behind:
            parts.append('behind: ' + ', '.join(behind))
        if self.interval_failed_blocks:
            parts.append(f'{self.interval_failed_blocks} ID block(s) failed')
        if self.interval_skipped_ids:
            parts.append(f'{self.interval_skipped_ids:,} IDs skipped')
        return ' | ' + ' | '.join(parts)


metrics = StreamMetrics()


def configure_logging():
    log_dir = os.environ.get('LOG_DIR', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file_path = os.path.join(log_dir, 'app.log')

    file_handler = RotatingFileHandler(
        log_file_path,
        mode='a',
        delay=0,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8',
    )
    stream_handler = logging.StreamHandler()

    debug = env_bool('DEBUG', False)
    level = logging.DEBUG if debug else logging.INFO

    file_handler.setLevel(level)
    stream_handler.setLevel(level)

    logging_blacklist = ['prawcore', 'urllib3.connectionpool']
    for item in logging_blacklist:
        logging.getLogger(item).disabled = True

    logging.basicConfig(
        format='%(asctime)s [%(levelname)-8s] [%(threadName)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=level,
        handlers=[
            file_handler,
            stream_handler,
        ],
        force=True,
    )
    logging.getLogger().setLevel(level)


def init_comment_stream(continue_after_id=None):
    """Returns PRAW's r/all comment stream (STREAM_SOURCE=listing).

    After a stream error, `continue_after_id` (the newest comment seen, as a fullname) makes the new stream
    start right after it, instead of skipping everything posted during the back-off.
    """
    logger.info('Initializing Reddit comment stream...')
    reddit_instance = reddit.get_reddit_instance()
    scanned_subreddits = 'all'
    subreddit = reddit_instance.subreddit(scanned_subreddits)
    if continue_after_id:
        c_stream = subreddit.stream.comments(continue_after_id=continue_after_id, pause_after=-1)
    else:
        c_stream = subreddit.stream.comments(skip_existing=True, pause_after=-1)
    logger.info('Stream initialized successfully. Listening for comments on r/all...')
    return c_stream


def build_jobs():
    """Returns the scheduled jobs, each to run on its own thread."""
    # Phase 2 and Phase 3 both take rows from scraped_comments, and Phase 3 re-runs Phase 2's filters,
    # so they never run at the same time
    scraped_comments_lock = threading.Lock()
    debug = env_bool('DEBUG', False)
    jobs = [
        PeriodicJob('heartbeat', 10 * MINUTE, metrics.log_heartbeat),
        # With DEBUG, Phase 2 also runs once at startup; it is the only job that never writes to Reddit.
        # Cleanup and Phase 3 keep their normal schedule.
        PeriodicJob('phase2', 20 * MINUTE, phase2.filter_comments_from_db,
                    lock=scraped_comments_lock, run_at_start=debug),
        PeriodicJob('known-subreddits', 5 * MINUTE, known_subreddits.flush),
    ]
    # Jobs that write to Reddit run only for enabled bots
    if config.is_bot_enabled(reddit.AUTO_CROSSPOST_BOT_NAME):
        jobs.append(PeriodicJob('cleanup', 7 * MINUTE, cleanup.delete_unwanted_submissions))
        jobs.append(PeriodicJob('phase3', 6 * MINUTE, phase3.process_comment_entries, lock=scraped_comments_lock))
    if any(config.is_bot_enabled(bot_name) for bot_name in inbox.BOT_NAMES):
        jobs.append(PeriodicJob('inbox', 10 * MINUTE, inbox.process_unread_inboxes))
    return jobs


def log_disabled_bots():
    """Logs, at startup, which bots will not write to Reddit."""
    disabled = [name for name in config.BOT_ENABLED_KEYS if not config.is_bot_enabled(name)]
    if not disabled:
        return
    logger.info(f'Disabled bots (no comments, posts or deletions): {", ".join("/u/" + name for name in disabled)}')
    if len(disabled) == len(config.BOT_ENABLED_KEYS):
        logger.info('All bots are disabled: only reading r/all and storing matches in the database')


def parse_args():
    parser = argparse.ArgumentParser(description="RedditAutoCrosspostBot runner")
    parser.add_argument(
        '--process-one',
        action='store_true',
        help=(
            "Run Phase 3 (final checks, crosspost, reply, row delete) on the oldest DB entry only and exit, "
            "even if it is not due yet. Combine with DRY_RUN=True to only log."
        ),
    )
    return parser.parse_args()


def main():
    # .env must be loaded before anything reads configuration, logging setup included
    config.load_environment()
    configure_logging()
    args = parse_args()
    logger.info(f'=== Starting RedditAutoCrosspostBot v{__version__} ===')
    if config.is_dry_run():
        logger.info('DRY_RUN is enabled: no replies, crossposts or deletions will be made')

    if args.process_one:
        process_one()

    log_disabled_bots()
    check_catalogs_compiled()
    authenticate(reddit.BOT_NAMES)

    start_bot()


def check_catalogs_compiled():
    try:
        i18n.check_catalogs_compiled()
    except i18n.CatalogNotCompiledError as e:
        logger.critical(f'Startup failed: {e}')
        sys.exit(1)


def authenticate(bot_names):
    try:
        reddit.authenticate_bots(bot_names)
    except Exception as e:
        logger.critical(f'Startup authentication failed: {e}')
        sys.exit(1)


def process_one():
    """`--process-one`: runs Phase 3 on the oldest DB row, then exits.

    Nothing else starts: no stream, no jobs, no worker threads. Phase 3 reads Reddit and crossposts only as
    /u/AutoCrosspostBot, so the other 3 accounts are not even authenticated.
    """
    if not config.is_bot_enabled(reddit.AUTO_CROSSPOST_BOT_NAME):
        logger.critical('--process-one crossposts as /u/AutoCrosspostBot, which AUTO_CROSSPOST_BOT_ENABLED disables')
        sys.exit(2)
    check_catalogs_compiled()
    authenticate([reddit.AUTO_CROSSPOST_BOT_NAME])
    found = phase3.process_oldest_entry()
    sys.exit(0 if found else 1)


def install_signal_handlers():
    """Turns SIGTERM (`docker stop`) and SIGINT (Ctrl-C) into a graceful shutdown of every thread."""
    def request_stop(signum, frame):
        if shutdown.stop_requested():
            return
        logger.info(f'Received {signal.Signals(signum).name}; shutting down...')
        shutdown.request_stop()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


def start_bot():
    """Runs the bot until a signal or a fatal error stops it.

    The main thread reads new comments (`run_stream`). The scheduled jobs and the Phase 1 workers run on
    their own threads (`racb.workers`), so they never pause reading.
    """
    install_signal_handlers()
    logger.info('Configuring background task schedule...')
    jobs = build_jobs()
    for job in jobs:
        job.start()
    worker_count = config.phase1_worker_count()
    phase1_workers = Phase1Workers(handle_comment_id, worker_count, metrics=metrics).start()
    logger.info(f'Started {len(jobs)} scheduled job thread(s) and {worker_count} Phase 1 worker thread(s)')

    reader_threads = []
    try:
        reader_threads = run_stream(phase1_workers)
    finally:
        shutdown.request_stop()
        wait_for_threads(jobs + phase1_workers.threads + (reader_threads or []), phase1_workers)

    error = shutdown.fatal_error()
    if error is not None:
        raise error
    logger.info('Shutdown complete')


def run_stream(phase1_workers):
    """Reads new comments on the main thread until shutdown, from STREAM_SOURCE.

    Returns the reader threads it started, so the shutdown can wait for them.
    """
    if config.stream_source() == 'listing':
        run_listing_stream(phase1_workers)
        return []
    reader_count = config.stream_reader_count()
    reader = CommentReader(
        lambda comment: dispatch_comment(comment, phase1_workers),
        reader_count,
        metrics=metrics,
        handle_exception=handle_exception,
    )
    logger.info(f'Reading every new comment by ID on {reader_count} reader thread(s)')
    reader.start()
    reader.run()
    return reader.threads


def run_listing_stream(phase1_workers):
    """STREAM_SOURCE=listing: reads PRAW's r/all comment stream, one request at a time."""
    c_stream = None
    newest_seen = {'fullname': None}
    while not shutdown.stop_requested():
        try:
            if c_stream is None:
                c_stream = init_comment_stream(continue_after_id=newest_seen['fullname'])
            main_loop(c_stream, phase1_workers, newest_seen)
        except Exception as e:
            should_raise = handle_exception(e)
            if should_raise:
                raise
            else:
                c_stream = None


def wait_for_threads(threads, phase1_workers):
    """Waits up to SHUTDOWN_TIMEOUT_SECONDS in total for `threads`, then gives up on the rest.

    The threads are daemons, so any still running (e.g. Phase 3 in the middle of one entry's media scan)
    end with the process.
    """
    deadline = time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS
    for thread in threads:
        thread.join(max(0, deadline - time.monotonic()))
    still_running = [thread.name for thread in threads if thread.is_alive()]
    if still_running:
        logger.warning(f'Threads still running after {SHUTDOWN_TIMEOUT_SECONDS}s, abandoning them: {still_running}')
    unhandled = phase1_workers.pending()
    if unhandled:
        logger.warning(f'{unhandled} matching comment(s) were still waiting for Phase 1 and are dropped')


def handle_exception(e):
    """Handles errors that escaped the stream loop (mostly stream fetches)."""
    should_raise = True
    debug = env_bool('DEBUG', False)
    if debug:
        return should_raise

    # shutdown.stop_requested_within() instead of time.sleep(), so a shutdown does not wait out the back-off
    if isinstance(e, prawcore.exceptions.TooManyRequests):
        logger.warning(f'Rate limited by Reddit ({e}). Waiting {TOO_MANY_REQUESTS_DELAY_SECONDS}s and retrying.')
        shutdown.stop_requested_within(TOO_MANY_REQUESTS_DELAY_SECONDS)
        should_raise = False
    elif isinstance(e, TRANSIENT_NETWORK_ERRORS + (prawcore.exceptions.Forbidden,)):
        logger.info(f'Encountered network error {e!r}. Waiting {NETWORK_RETRY_DELAY_SECONDS}s and retrying.')
        shutdown.stop_requested_within(NETWORK_RETRY_DELAY_SECONDS)
        should_raise = False
    elif isinstance(e, DATABASE_CONNECTION_ERRORS):
        logger.warning(f'Database connection error {e!r}. Reconnecting in {NETWORK_RETRY_DELAY_SECONDS}s.')
        db.reset_connection()
        shutdown.stop_requested_within(NETWORK_RETRY_DELAY_SECONDS)
        should_raise = False
    elif isinstance(e, praw.exceptions.RedditAPIException):
        error_types = []
        if hasattr(e, 'items') and e.items:
            error_types = [item.error_type for item in e.items]
        elif hasattr(e, 'error_type'):
            error_types = [e.error_type]

        graceful_types = {'DELETED_COMMENT', 'THREAD_LOCKED', 'SOMETHING_IS_BROKEN', 'RATELIMIT'}
        if any(err in graceful_types for err in error_types):
            logger.info(f'Handled expected RedditAPIException gracefully: {error_types}')
            should_raise = False

    if should_raise:
        logger.exception(e)
    return should_raise


def dispatch_comment(comment, phase1_workers):
    """Handles one new comment without any I/O, on the main thread or a reader thread.

    Only comments that look like a subreddit suggestion go on to Phase 1, on a worker thread. Reader
    threads call this several at once; everything it touches is locked or thread safe.
    """
    try:
        metrics.record_comment()
        # Seeing a comment in a subreddit proves it exists; this only buffers the name (no I/O)
        known_subreddits.record(comment.subreddit.display_name)
        if phase1.check_pattern(comment) is not None:
            phase1_workers.submit(comment.id)
    except Exception as e:
        if env_bool('DEBUG', False):
            raise
        logger.exception(f'Unexpected error while reading stream comment {getattr(comment, "id", "?")}: {e!r}')


def handle_comment_id(comment_id):
    """Runs Phase 1 on a Phase 1 worker thread, for a comment the stream thread queued."""
    # Fetched again through this thread's own PRAW instance (one request), since PRAW is not thread safe
    comment = reddit.get_reddit_instance().comment(id=comment_id)
    handle_comment_safely(comment)


def handle_comment_safely(comment):
    """Runs Phase 1 for one comment so that an error affecting only this comment skips just this comment.

    Errors such as a 403 because an auxiliary bot is banned from the subreddit are logged and the comment
    is skipped; the worker moves straight on to the next one. Network-level errors propagate, so the
    Phase 1 worker can back off before making more requests.
    """
    try:
        phase1.handle_incoming_comment(comment, metrics=metrics)
    except TRANSIENT_NETWORK_ERRORS:
        raise
    except Exception as e:
        if env_bool('DEBUG', False):
            raise
        permalink = getattr(comment, 'permalink', '?')
        if isinstance(e, DATABASE_CONNECTION_ERRORS):
            db.reset_connection()
            logger.error(f'Database error while storing {reddit.full_url(permalink)}: {e!r}')
        elif isinstance(e, (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException)):
            logger.info(f'Skipping comment {reddit.full_url(permalink)} after Reddit error: {e!r}')
        else:
            logger.exception(f'Unexpected error while handling comment {reddit.full_url(permalink)}: {e!r}')


def main_loop(c_stream, phase1_workers, newest_seen=None):
    """Reads one batch from the stream. Returns when the batch is done or a shutdown was requested.

    With `pause_after=-1`, PRAW yields None after every response, so one batch is one request. Its
    duration and size go to the heartbeat. `newest_seen['fullname']` keeps the newest comment for a
    stream restart.
    """
    started = time.monotonic()
    batch_size = 0
    newest_created = None
    for comment in c_stream:
        if comment is None or shutdown.stop_requested():
            break
        batch_size += 1
        dispatch_comment(comment, phase1_workers)
        if newest_seen is not None:
            newest_seen['fullname'] = f't1_{comment.id}'
        created = vars(comment).get('created_utc')
        if created is not None and (newest_created is None or created > newest_created):
            newest_created = created
    metrics.record_request(time.monotonic() - started, batch_size)
    if newest_created is not None:
        metrics.record_newest_created(newest_created)


if __name__ == '__main__':
    main()
