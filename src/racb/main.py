"""Main application entry point for RACB."""

import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
import time

import praw
import prawcore
import requests
import schedule
import urllib3

from racb.version import __version__
from racb.core import reddit
from racb.phases import phase1, phase2, phase3, inbox, cleanup

logger = logging.getLogger(__name__)


def env_bool(key, default=False):
    val = os.environ.get(key)
    if val is None:
        return default
    return str(val).strip().lower() in ('true', '1', 'yes', 'y', 't')


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


def init_streams():
    logger.info('Initializing Reddit comment and inbox streams...')
    reddit_instance = reddit.get_reddit_instance()
    scanned_subreddits = 'all'
    subreddit = reddit_instance.subreddit(scanned_subreddits)
    c_stream = subreddit.stream.comments(skip_existing=True, pause_after=-1)
    i_stream = reddit_instance.inbox.stream(mark_read=False, pause_after=-1)
    logger.info('Streams initialized successfully. Listening for comments on r/all...')
    return (c_stream, i_stream)


def set_schedule():
    logger.info('Configuring background task schedule...')
    schedule.every(7).minutes.do(cleanup.delete_unwanted_submissions)
    schedule.every(10).minutes.do(metrics.log_heartbeat)
    schedule.every(20).minutes.do(phase2.filter_comments_from_db)
    listen_only = env_bool('LISTEN_ONLY', False)
    if not listen_only:
        schedule.every(6).minutes.do(phase3.process_comment_entries)

    debug = env_bool('DEBUG', False)
    if debug:
        schedule.run_all()


def main():
    configure_logging()
    logger.info(f'=== Starting RedditAutoCrosspostBot v{__version__} ===')

    parser = argparse.ArgumentParser(description="RedditAutoCrosspostBot runner")
    parser.add_argument('--only-phase2', action='store_true', help="Run Phase 2 filtering only and exit")
    args = parser.parse_args()
    if args.only_phase2:
        phase2.filter_comments_from_db(verbose=True)
        return

    start_bot()


def start_bot():
    set_schedule()
    c_stream, i_stream = init_streams()

    while True:
        try:
            main_loop(c_stream, i_stream)
        except Exception as e:
            should_raise = handle_exception(e)
            if should_raise:
                raise
            else:
                c_stream, i_stream = init_streams()


def handle_exception(e):
    should_raise = True
    debug = env_bool('DEBUG', False)
    if debug:
        return should_raise

    if type(e) in (
        prawcore.exceptions.ServerError,
        prawcore.exceptions.Forbidden,
        requests.exceptions.ConnectTimeout,
    ):
        logger.info(f'Encountered network error {e}. Waiting 30s and retrying.')
        time.sleep(30)
        should_raise = False
    elif type(e) is prawcore.exceptions.RequestException:
        is_max_retry_or_read_timeout_error = (
            e.original_exception
            and hasattr(e.original_exception, 'args')
            and len(e.original_exception.args) > 0
            and (
                isinstance(e.original_exception.args[0], urllib3.exceptions.MaxRetryError)
                or isinstance(e.original_exception.args[0], urllib3.exceptions.ReadTimeoutError)
            )
        )
        if is_max_retry_or_read_timeout_error:
            logger.info(f'Encountered network error {e}. Waiting 30s and retrying.')
            time.sleep(30)
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


def main_loop(c_stream, i_stream):
    for comment in c_stream:
        if comment is None:
            break
        phase1.handle_incoming_comment(comment, metrics=metrics)
    for comment in i_stream:
        if comment is None:
            break
        inbox.respond_to_comment(comment)
    schedule.run_pending()


if __name__ == '__main__':
    main()
