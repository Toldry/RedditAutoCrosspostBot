"""Main entry point of the program"""

import logging
from logging.handlers import RotatingFileHandler
import argparse
import time
import os

import praw
import requests
import schedule
import prawcore
import urllib3

import reddit_instantiator
import unwanted_submission_remover
import inbox_handler
import phase1_handler
import phase2_handler
import phase3_handler


comment_stream = None
inbox_stream = None


def env_bool(key, default=False):
    val = os.environ.get(key)
    if val is None:
        return default
    return str(val).strip().lower() in ('true', '1', 'yes', 'y', 't')


def configure_logging():
    file_handler = RotatingFileHandler("app.log", mode='a', delay=0,
                                       maxBytes=5 * 1024 * 1024,
                                       backupCount=1, encoding='utf-8')
    stream_handler = logging.StreamHandler()

    debug = env_bool('DEBUG', False)
    level = logging.INFO
    if debug:
        level = logging.DEBUG

    file_handler.setLevel(logging.INFO)
    stream_handler.setLevel(level)

    logging_blacklist = ['prawcore', 'urllib3.connectionpool', 'schedule']
    for item in logging_blacklist:
        logging.getLogger(item).disabled = True

    logging.basicConfig(format='%(asctime)-15s - %(name)s - %(levelname)s - %(message)s',
                        level=level,
                        handlers=[
                            file_handler,
                            stream_handler
                        ])


def init_streams():
    reddit = reddit_instantiator.get_reddit_instance()
    scanned_subreddits = 'all'
    subreddit = reddit.subreddit(scanned_subreddits)
    c_stream = subreddit.stream.comments(skip_existing=True, pause_after=-1)
    i_stream = reddit.inbox.stream(mark_read=False, pause_after=-1)
    return (c_stream, i_stream)


def set_schedule():
    schedule.every(7).minutes.do(unwanted_submission_remover.delete_unwanted_submissions)
    schedule.every(20).minutes.do(phase2_handler.filter_comments_from_db)
    listen_only = env_bool('LISTEN_ONLY', False)
    if not listen_only:
        schedule.every(6).minutes.do(phase3_handler.process_comment_entries)

    debug = env_bool('DEBUG', False)
    if debug:
        schedule.run_all()


def main():
    configure_logging()
    logging.info('Running reddit_auto_crosspost_bot')

    parser = argparse.ArgumentParser()
    parser.add_argument('--only-phase2', action='store_true')
    args = parser.parse_args()
    if args.only_phase2:
        phase2_handler.filter_comments_from_db(verbose=True)
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

    if type(e) in (prawcore.exceptions.ServerError,
                   prawcore.exceptions.Forbidden,
                   requests.exceptions.ConnectTimeout,):
        logging.info(f'Encountered network error {e}. Waiting 30s and retrying.')
        time.sleep(30)
        should_raise = False
    elif type(e) is prawcore.exceptions.RequestException:
        is_max_retry_or_read_timeout_error = (
            e.original_exception and 
            hasattr(e.original_exception, 'args') and 
            len(e.original_exception.args) > 0 and 
            (   isinstance(e.original_exception.args[0], urllib3.exceptions.MaxRetryError) or
                isinstance(e.original_exception.args[0], urllib3.exceptions.ReadTimeoutError)
            )
        )
        if is_max_retry_or_read_timeout_error:
            logging.info(f'Encountered network error {e}. Waiting 30s and retrying.')
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
            logging.info(f'Handled expected RedditAPIException gracefully: {error_types}')
            should_raise = False

    if should_raise:
        logging.exception(e)
    return should_raise


def main_loop(c_stream, i_stream):
    for comment in c_stream:
        if comment is None:
            break
        phase1_handler.handle_incoming_comment(comment)
    for comment in i_stream:
        if comment is None:
            break
        inbox_handler.respond_to_comment(comment)
    schedule.run_pending()


if __name__ == '__main__':
    main()