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
SAME_POST_BOT_NAME = 'same_post_bot'

bot_details = {}
bot_details[AUTO_CROSSPOST_BOT_NAME] = {
    'app_client_id':'UwKgkrvtl9fpUw',
    'env_password_key': 'PASSWORD',
    'env_app_client_secret_key': 'APP_CLIENT_SECRET',
    'version':'1.0',
}
bot_details[SUB_DOESNT_EXIST_BOT_NAME] = {
    'app_client_id':'Vf4yyKKLXa6zzg',
    'env_password_key': 'PASSWORD__SUB_DOESNT_EXIST',
    'env_app_client_secret_key': 'APP_CLIENT_SECRET__SUB_DOESNT_EXIST',
    'version':'0.1',
}
bot_details[SAME_SUBREDDIT_BOT_NAME] = {
    'app_client_id':'odiQgdKS4qBfNQ',
    'env_password_key': 'PASSWORD__SAME_SUBREDDIT',
    'env_app_client_secret_key': 'APP_CLIENT_SECRET__SAME_SUBREDDIT',
    'version':'0.1',
}
bot_details[SAME_POST_BOT_NAME] = {
    'app_client_id':'DijrDnmiFaXwZw',
    'env_password_key': 'PASSWORD__SAME_POST',
    'env_app_client_secret_key': 'APP_CLIENT_SECRET__SAME_POST',
    'version':'0.1',
}


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
                amount, timeunit = re.compile(r'(\d+) (second|minute)s?').search(e.message).groups()
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

        bot_names = [AUTO_CROSSPOST_BOT_NAME, SUB_DOESNT_EXIST_BOT_NAME, SAME_SUBREDDIT_BOT_NAME, SAME_POST_BOT_NAME]
        for name in bot_names:
            app_client_id=bot_details[name]['app_client_id']
            password=os.environ.get(bot_details[name]['env_password_key'])
            app_client_secret=os.environ.get(bot_details[name]['env_app_client_secret_key'])
            version=bot_details[name]['version']

            reddit_instance = _instantiate_praw(username=name,
                                                app_client_id=app_client_id,
                                                password=password, 
                                                app_client_secret=app_client_secret,
                                                version=version)
            praw_instances[name] = reddit_instance

    return praw_instances[username]