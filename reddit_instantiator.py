"""Maintains a singleton instance of the `praw.reddit` class"""

__all__ = ['get_reddit_instance']

import functools
import logging
import re
import os
import time

import praw
import dotenv

praw_instances = None
AUTO_CROSSPOST_BOT_NAME = 'AutoCrosspostBot'
SUB_DOESNT_EXIST_BOT_NAME = 'sub_doesnt_exist_bot'
SAME_SUBREDDIT_BOT_NAME = 'same_subreddit_bot'


def _instantiate_praw(username, app_client_id,password, app_client_secret, version):
    clientname = username
    developername = 'orqa'
    useragent = f'{clientname}/{version} by /u/{developername}'

    logging.info(f'Connecting to reddit via praw to instantiate connection instance. username={username}')
    reddit = praw.Reddit(client_id=app_client_id,
                         client_secret=app_client_secret,
                         user_agent=useragent,
                         username=username,
                         password=password)
    return reddit


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


def get_reddit_instance(username=AUTO_CROSSPOST_BOT_NAME):
    """ Returns a singleton instance of the `praw.reddit` object
    """
    global praw_instances
    if not praw_instances:
        _decorate_praw()
        praw_instances = {}
        dotenv.load_dotenv()
        reddit1 = _instantiate_praw(username=AUTO_CROSSPOST_BOT_NAME,
                                    app_client_id='UwKgkrvtl9fpUw',
                                    password=os.environ.get('PASSWORD'), 
                                    app_client_secret=os.environ.get('APP_CLIENT_SECRET'),
                                    version='0.5')
        reddit2 = _instantiate_praw(username=SUB_DOESNT_EXIST_BOT_NAME,
                                    app_client_id='Vf4yyKKLXa6zzg',
                                    password=os.environ.get('PASSWORD__SUB_DOESNT_EXIST'), 
                                    app_client_secret=os.environ.get('APP_CLIENT_SECRET__SUB_DOESNT_EXIST'),
                                    version='0.1')
        reddit3 = _instantiate_praw(username=SAME_SUBREDDIT_BOT_NAME,
                                    app_client_id='odiQgdKS4qBfNQ',
                                    password=os.environ.get('PASSWORD__SAME_SUBREDDIT'), 
                                    app_client_secret=os.environ.get('APP_CLIENT_SECRET__SAME_SUBREDDIT'),
                                    version='0.1')

        praw_instances[AUTO_CROSSPOST_BOT_NAME] = reddit1
        praw_instances[SUB_DOESNT_EXIST_BOT_NAME] = reddit2
        praw_instances[SAME_SUBREDDIT_BOT_NAME] = reddit3
    return praw_instances[username]
