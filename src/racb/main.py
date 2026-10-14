"""Main application entry point for RACB."""

import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import time

import praw
import prawcore
import psycopg2
import requests
import schedule

from racb.version import __version__
from racb import config
from racb.config import env_bool
from racb.core import db, reddit
from racb.phases import phase1, phase2, phase3, inbox, cleanup

logger = logging.getLogger(__name__)

NETWORK_RETRY_DELAY_SECONDS = 30
TOO_MANY_REQUESTS_DELAY_SECONDS = 60

# Network-level failures: back off, then rebuild the stream
TRANSIENT_NETWORK_ERRORS = (
    prawcore.exceptions.ServerError,
    prawcore.exceptions.RequestException,
    prawcore.exceptions.TooManyRequests,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)
DATABASE_CONNECTION_ERRORS = (psycopg2.OperationalError, psycopg2.InterfaceError)

# Value of --process-one when it is given without an ID. Not a string, because argparse would
# pass a string const through type=int.
NEXT_DUE_ENTRY = object()


class StreamMetrics:
    """Tracks throughput and events for Reddit comment and inbox streaming."""

    def __init__(self):
        self.interval_scanned = 0
        self.interval_matches = 0
        self.interval_aux = 0
        self.total_scanned = 0
        self.total_matches = 0
        self.total_aux = 0
        self.last_log_time = time.time()

    def record_comment(self):
        self.interval_scanned += 1
        self.total_scanned += 1

    def record_match(self):
        self.interval_matches += 1
        self.total_matches += 1

    def record_aux(self):
        self.interval_aux += 1
        self.total_aux += 1

    def log_heartbeat(self):
        elapsed_min = max(1, round((time.time() - self.last_log_time) / 60))
        logger.info(
            f'[Heartbeat] Last ~{elapsed_min}m: scanned {self.interval_scanned:,} comments on r/all | '
            f'{self.interval_matches} recommendation(s) stored | {self.interval_aux} aux bot reply(s) '
            f'[Total uptime: {self.total_scanned:,} scanned, {self.total_matches} matches]'
        )
        self.interval_scanned = 0
        self.interval_matches = 0
        self.interval_aux = 0
        self.last_log_time = time.time()


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

    logging_blacklist = ['prawcore', 'urllib3.connectionpool', 'schedule']
    for item in logging_blacklist:
        logging.getLogger(item).disabled = True

    logging.basicConfig(
        format='%(asctime)s [%(levelname)-8s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=level,
        handlers=[
            file_handler,
            stream_handler,
        ],
        force=True,
    )
    logging.getLogger().setLevel(level)


def init_comment_stream():
    logger.info('Initializing Reddit comment stream...')
    reddit_instance = reddit.get_reddit_instance()
    scanned_subreddits = 'all'
    subreddit = reddit_instance.subreddit(scanned_subreddits)
    c_stream = subreddit.stream.comments(skip_existing=True, pause_after=-1)
    logger.info('Stream initialized successfully. Listening for comments on r/all...')
    return c_stream


def run_job_safely(job_func):
    """Runs a scheduled job, logging (instead of propagating) its errors.

    The `schedule` library only computes a job's next run after the job returns normally, so a job
    that raises would be re-run on every loop iteration. It would also reset the comment stream.
    """
    try:
        return job_func()
    except Exception as e:
        if config.is_debug():
            raise
        if isinstance(e, DATABASE_CONNECTION_ERRORS):
            db.reset_connection()
        logger.exception(f'Scheduled job {job_func.__name__} failed: {e!r}')
        return None


def set_schedule():
    logger.info('Configuring background task schedule...')
    schedule.every(7).minutes.do(run_job_safely, cleanup.delete_unwanted_submissions)
    schedule.every(10).minutes.do(metrics.log_heartbeat)
    schedule.every(10).minutes.do(run_job_safely, inbox.process_unread_inboxes)
    schedule.every(20).minutes.do(run_job_safely, phase2.filter_comments_from_db)
    listen_only = env_bool('LISTEN_ONLY', False)
    if not listen_only:
        schedule.every(6).minutes.do(run_job_safely, phase3.process_comment_entries)

    debug = env_bool('DEBUG', False)
    if debug:
        # Only jobs that never write to Reddit; cleanup and Phase 3 keep their normal schedule
        run_job_safely(phase2.filter_comments_from_db)


def parse_args():
    parser = argparse.ArgumentParser(description="RedditAutoCrosspostBot runner")
    parser.add_argument('--only-phase2', action='store_true', help="Run Phase 2 filtering only and exit")
    parser.add_argument(
        '--process-one',
        nargs='?',
        const=NEXT_DUE_ENTRY,
        type=int,
        metavar='ID',
        help=(
            "Run Phase 3 (final checks, crosspost, reply, row delete) on one DB entry and exit. "
            "ID is the scraped_comments id; it is processed even if it is not due yet. "
            "Without ID, the oldest entry that is due is processed. Combine with DRY_RUN=True to only log."
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

    try:
        reddit.authenticate_all_bots()
    except Exception as e:
        logger.critical(f'Startup authentication failed: {e}')
        sys.exit(1)

    if args.only_phase2:
        phase2.filter_comments_from_db(verbose=True)
        return

    if args.process_one is not None:
        entry_id = None if args.process_one is NEXT_DUE_ENTRY else args.process_one
        found = phase3.process_single_entry(entry_id)
        sys.exit(0 if found else 1)

    start_bot()


def start_bot():
    set_schedule()
    c_stream = init_comment_stream()

    while True:
        try:
            main_loop(c_stream)
        except Exception as e:
            should_raise = handle_exception(e)
            if should_raise:
                raise
            else:
                c_stream = init_comment_stream()


def handle_exception(e):
    """Handles errors that escaped the per-comment and per-job handlers (mostly stream fetches)."""
    should_raise = True
    debug = env_bool('DEBUG', False)
    if debug:
        return should_raise

    if isinstance(e, prawcore.exceptions.TooManyRequests):
        logger.warning(f'Rate limited by Reddit ({e}). Waiting {TOO_MANY_REQUESTS_DELAY_SECONDS}s and retrying.')
        time.sleep(TOO_MANY_REQUESTS_DELAY_SECONDS)
        should_raise = False
    elif isinstance(e, TRANSIENT_NETWORK_ERRORS + (prawcore.exceptions.Forbidden,)):
        logger.info(f'Encountered network error {e!r}. Waiting {NETWORK_RETRY_DELAY_SECONDS}s and retrying.')
        time.sleep(NETWORK_RETRY_DELAY_SECONDS)
        should_raise = False
    elif isinstance(e, DATABASE_CONNECTION_ERRORS):
        logger.warning(f'Database connection error {e!r}. Reconnecting in {NETWORK_RETRY_DELAY_SECONDS}s.')
        db.reset_connection()
        time.sleep(NETWORK_RETRY_DELAY_SECONDS)
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


def handle_comment_safely(comment):
    """Runs Phase 1 for one comment so that an error affecting only this comment skips just this comment.

    Previously every such error (e.g. a 403 because an auxiliary bot is banned from the subreddit)
    escaped to `handle_exception`, which slept and rebuilt the stream, dropping comments.
    Network-level errors still propagate so the outer loop can back off.
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
            logger.error(f'Database error while storing {permalink}: {e!r}')
        elif isinstance(e, (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException)):
            logger.info(f'Skipping comment {permalink} after Reddit error: {e!r}')
        else:
            logger.exception(f'Unexpected error while handling comment {permalink}: {e!r}')


def main_loop(c_stream):
    for comment in c_stream:
        if comment is None:
            break
        handle_comment_safely(comment)
    schedule.run_pending()


if __name__ == '__main__':
    main()
