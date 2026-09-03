"""Maintains a singleton instance of the `praw.Reddit` class"""

__all__ = ['get_reddit_instance']

import logging
import os

import praw
import dotenv

from _version import __version__

praw_instances = None

AUTO_CROSSPOST_BOT_NAME = 'AutoCrosspostBot'
SUB_DOESNT_EXIST_BOT_NAME = 'sub_doesnt_exist_bot'
SAME_SUBREDDIT_BOT_NAME = 'same_subreddit_bot'
SAME_POST_BOT_NAME = 'same_post_bot'

bot_details = {
    AUTO_CROSSPOST_BOT_NAME: {
        'app_client_id': 'UwKgkrvtl9fpUw',
        'env_password_key': 'PASSWORD',
        'env_app_client_secret_key': 'APP_CLIENT_SECRET',
        'version': __version__,
    },
    SUB_DOESNT_EXIST_BOT_NAME: {
        'app_client_id': 'Vf4yyKKLXa6zzg',
        'env_password_key': 'PASSWORD__SUB_DOESNT_EXIST',
        'env_app_client_secret_key': 'APP_CLIENT_SECRET__SUB_DOESNT_EXIST',
        'version': __version__,
    },
    SAME_SUBREDDIT_BOT_NAME: {
        'app_client_id': 'odiQgdKS4qBfNQ',
        'env_password_key': 'PASSWORD__SAME_SUBREDDIT',
        'env_app_client_secret_key': 'APP_CLIENT_SECRET__SAME_SUBREDDIT',
        'version': __version__,
    },
    SAME_POST_BOT_NAME: {
        'app_client_id': 'DijrDnmiFaXwZw',
        'env_password_key': 'PASSWORD__SAME_POST',
        'env_app_client_secret_key': 'APP_CLIENT_SECRET__SAME_POST',
        'version': __version__,
    },
}


def _instantiate_praw(username, app_client_id, password, app_client_secret, version):
    clientname = username
    developername = 'orqa'
    useragent = f'linux:{clientname}:v{version} (by /u/{developername})'

    logging.info(f'Connecting to reddit via praw to instantiate connection instance. username={username}')
    reddit = praw.Reddit(
        client_id=app_client_id,
        client_secret=app_client_secret,
        user_agent=useragent,
        username=username,
        password=password,
        ratelimit_seconds=300,  # Native PRAW rate limit handling up to 5 minutes
    )
    return reddit


def get_reddit_instance(username=AUTO_CROSSPOST_BOT_NAME):
    """Returns a singleton instance of the `praw.Reddit` object"""
    global praw_instances
    if not praw_instances:
        praw_instances = {}
        dotenv.load_dotenv()

        bot_names = [AUTO_CROSSPOST_BOT_NAME, SUB_DOESNT_EXIST_BOT_NAME, SAME_SUBREDDIT_BOT_NAME, SAME_POST_BOT_NAME]
        for name in bot_names:
            app_client_id = bot_details[name]['app_client_id']
            password = os.environ.get(bot_details[name]['env_password_key'])
            app_client_secret = os.environ.get(bot_details[name]['env_app_client_secret_key'])
            version = bot_details[name]['version']

            reddit_instance = _instantiate_praw(
                username=name,
                app_client_id=app_client_id,
                password=password,
                app_client_secret=app_client_secret,
                version=version,
            )
            praw_instances[name] = reddit_instance

    return praw_instances[username]