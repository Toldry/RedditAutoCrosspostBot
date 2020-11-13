"""Maintains a singleton instance of the `praw.reddit` class"""

__all__ = ['get_reddit_instance']

import functools
import logging
import re
import os
import time

import praw
import dotenv

reddit = None


def _instantiate_reddit():
    username = 'AutoCrosspostBot'
    clientname = username
    app_client_id = 'UwKgkrvtl9fpUw'
    # password = 'REDACTED'
    # app_client_secret = 'REDACTED'
    version = '0.5'
    developername = 'orqa'
    useragent = f'{clientname}/{version} by /u/{developername}'

    dotenv.load_dotenv()
    password = os.environ.get('PASSWORD')
    app_client_secret = os.environ.get('APP_CLIENT_SECRET')
    
    logging.info('Connecting to reddit via praw to instantiate connection instance')
    global reddit
    reddit = praw.Reddit(client_id=app_client_id,
                         client_secret=app_client_secret,
                         user_agent=useragent,
                         username=username,
                         password=password)


def _decorate_praw():
    praw.models.Comment.reply = _wait_and_retry_when_ratelimit_reached(praw.models.Comment.reply)
    praw.models.Submission.crosspost = _wait_and_retry_when_ratelimit_reached(praw.models.Submission.crosspost)
    praw.models.Submission.reply = _wait_and_retry_when_ratelimit_reached(praw.models.Submission.reply)


def _wait_and_retry_when_ratelimit_reached(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        while True:
            try:
                return func(*args, **kwargs)
            except praw.exceptions.RedditAPIException as e:
                if e.error_type != 'RATELIMIT':
                    raise
                amount, timeunit = re.compile(
                    r'you are doing that too much\. try again in (\d+) (second|minute)s?\.').search(e.message).groups()
                amount = int(amount)
                logging.info(f'Posting rate limit reached. waiting {amount} {timeunit}s...')
                if timeunit == 'minute': amount *= 60
                time.sleep(amount)

    return wrapper


def get_reddit_instance():
    """ Returns a singleton instance of the `praw.reddit` object
    """
    if not reddit:
        _decorate_praw()
        _instantiate_reddit()
    return reddit
