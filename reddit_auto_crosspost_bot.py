"""Main entry point of the program
"""

import logging
from logging.handlers import RotatingFileHandler
import argparse
import time

import requests
import schedule
import prawcore
import urllib3
import os

import inbox_responder
import listener
import reddit_instantiator
import replier
import unwated_submission_remover
import low_score_comments_remover

# https://www.pythonforengineers.com/build-a-reddit-bot-part-1/

def handle_commandline_arguments():
    parser = argparse.ArgumentParser(description='Run the reddit AutoCrosspostBot.')
    parser.add_argument("--production", default=False, action="store_true" , help="Set when running in production environment")
    parser.add_argument("--listen_only", default=False, action="store_true" , help="When set, the bot only listens to the comment stream but does not reply to items")

    args = parser.parse_args()
    os.environ['DEBUG'] = str(not args.production)
    os.environ['LISTEN_ONLY'] = str(args.listen_only)


def configure_logging():
    file_handler = RotatingFileHandler("app.log", mode='a', delay=0,
                                       maxBytes=5 * 1024 * 1024,
                                       backupCount=1, encoding='utf-8')
    stream_handler = logging.StreamHandler()

    file_handler.setLevel(logging.INFO)
    stream_handler.setLevel(logging.INFO)
    debug = bool(os.environ.get('DEBUG'))
    if debug:
        stream_handler.setLevel(logging.DEBUG)

    logging_blacklist = ['prawcore', 'urllib3.connectionpool', 'schedule']
    for item in logging_blacklist:
        logging.getLogger(item).disabled = True

    logging.basicConfig(format='%(asctime)-15s - %(name)s - %(levelname)s - %(message)s',
                        level=logging.DEBUG,
                        handlers=[
                            file_handler,
                            stream_handler
                        ])


def main():
    configure_logging()
    logging.info('Running reddit_auto_crosspost_bot')

    schedule.every(7).minutes.do(unwated_submission_remover.delete_unwanted_submissions)
    schedule.every(20).seconds.do(inbox_responder.respond_to_inbox) #TODO switch implementation to stream
    schedule.every(60).minutes.do(low_score_comments_remover.delete_comments_with_low_score)
    listen_only = bool(os.environ.get('LISTEN_ONLY'))
    if not listen_only:
        schedule.every(6).minutes.do(replier.respond_to_saved_comments)

    debug = bool(os.environ.get('DEBUG'))
    if debug:
        schedule.run_all()
    
    while True:
        try:
            listen_to_comment_stream()
        except (prawcore.exceptions.ServerError,
                prawcore.exceptions.Forbidden,
                requests.exceptions.ConnectTimeout,
                ) as e:
            if debug:
                raise
            else:
                # Sometimes the reddit service fails (e.g. error 503)
                # One time I got an error 403 (unaothorized) for no apparent reason
                # Other times the internet connection fails
                # just wait a bit a try again
                logging.info(f'Ecnountered network error {e}. Waiting and retrying.')
                time.sleep(30)
        except prawcore.exceptions.RequestException as e:
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
            else:
                raise


def listen_to_comment_stream():
    reddit = reddit_instantiator.get_reddit_instance()
    scanned_subreddits = 'all'
    subreddit_object = reddit.subreddit(scanned_subreddits)

    logging.info('Listening to comment stream...')
    for comment in subreddit_object.stream.comments(skip_existing=True):
        try:
            listener.handle_incoming_comment(comment)
            schedule.run_pending()
        except Exception as e:
            logging.exception(e)
            debug = bool(os.environ.get('DEBUG'))
            if debug:
                raise

handle_commandline_arguments()
if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        logging.exception(e)
        raise
    

# TODO Change title of crossposts specific subreddits according to their rules (e.g. when crossposting into /r/TIHI rename the post to "Thanks I hate it")