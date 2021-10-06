"""Main entry point of the program"""

import logging
from logging.handlers import RotatingFileHandler
import argparse
import time
from distutils import util
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
import sub_name_string_matc


comment_stream = None
inbox_stream = None

def configure_logging():
    file_handler = RotatingFileHandler("app.log", mode='a', delay=0,
                                       maxBytes=5 * 1024 * 1024,
                                       backupCount=1, encoding='utf-8')
    stream_handler = logging.StreamHandler()

    debug = bool(util.strtobool(os.environ.get('DEBUG')))
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
    comment_stream = subreddit.stream.comments(skip_existing=True, pause_after=-1)
    inbox_stream = reddit.inbox.stream(mark_read=False, pause_after=-1)
    return (comment_stream, inbox_stream)

def set_schedule():
    schedule.every(7).minutes.do(unwanted_submission_remover.delete_unwanted_submissions)
    schedule.every(20).minutes.do(phase2_handler.filter_comments_from_db)
    listen_only = bool(util.strtobool(os.environ.get('LISTEN_ONLY')))
    if not listen_only:
        schedule.every(6).minutes.do(phase3_handler.process_comment_entries)

    debug = bool(util.strtobool(os.environ.get('DEBUG')))
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
    comment_stream, inbox_stream = init_streams()
    
    while True:
        try:
            main_loop(comment_stream, inbox_stream)
        except Exception as e:
            should_raise = handle_exception(e)
            if should_raise:
                raise
            else:
                comment_stream, inbox_stream = init_streams()

def handle_exception(e):
    should_raise = True
    debug = bool(util.strtobool(os.environ.get('DEBUG')))
    if debug:
        return should_raise
    if type(e) in (prawcore.exceptions.ServerError,
                   prawcore.exceptions.Forbidden,
                   requests.exceptions.ConnectTimeout,):
        # Sometimes the reddit service fails (e.g. error 503)
        # One time I got an error 403 (unaothorized) for no apparent reason
        # Other times the internet connection fails
        # just wait a bit a try again
        logging.info(f'Ecnountered network error {e}. Waiting and retrying.')
        time.sleep(30)
        should_raise = False
    elif type(e) is prawcore.exceptions.RequestException:
        is_max_retry_or_read_timeout_error = (
            e.original_exception and 
            e.original_exception.args and 
            len(e.original_exception.args) > 0 and 
            (   isinstance(e.original_exception.args[0], urllib3.exceptions.MaxRetryError) or
                isinstance(e.original_exception.args[0], urllib3.exceptions.ReadTimeoutError)
            )
        )
        if is_max_retry_or_read_timeout_error:
            logging.info(f'Ecnountered network error {e}. Waiting and retrying.')
            time.sleep(30)
            should_raise = False
    elif type(e) is praw.exceptions.RedditAPIException:
        if e.error_type == 'DELETED_COMMENT':
            logging.info(f'Attempted to interact with a deleted comment. {e}')
            should_raise = False
        elif e.error_type == 'THREAD_LOCKED':
            logging.info(f'Attempted to comment in a locked thread. {e}')
            should_raise = False

    if should_raise:
        logging.exception(e)
    return should_raise



def main_loop(comment_stream, inbox_stream):
    for comment in comment_stream:
        if comment is None:
            break
        phase1_handler.handle_incoming_comment(comment)
    for comment in inbox_stream:
        if comment is None:
            break
        inbox_handler.respond_to_comment(comment)
    schedule.run_pending()

if __name__ == '__main__':
    main()
    

# TODO Change title of crossposts specific subreddits according to their rules (e.g. when crossposting into /r/TIHI rename the post to "Thanks I hate it")
# TODO Investigate what caused this bug: 
# https://www.reddit.com/r/MightyHarvest/comments/k6sj6v/this_is_our_8_year_old_pear_tree_puts_out_one/
# https://www.reddit.com/r/MightyHarvest/comments/inordb/my_pear_tree_one_yearly_perfect_pear_no_leaves/