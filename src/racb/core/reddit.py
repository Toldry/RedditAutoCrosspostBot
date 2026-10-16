"""Maintains a singleton instance of the `praw.Reddit` class"""

__all__ = [
    'get_reddit_instance',
    'authenticate_all_bots',
    'AUTO_CROSSPOST_BOT_NAME',
    'SUB_DOESNT_EXIST_BOT_NAME',
    'SAME_SUBREDDIT_BOT_NAME',
    'SAME_POST_BOT_NAME',
    'full_url',
]

import logging
import os

import praw
import praw.endpoints
import dotenv

from racb.version import __version__

logger = logging.getLogger(__name__)

# Reddit answers PRAW's delete path, `POST /api/del/`, with a plain-text "404 page not found" (its router
# has no route with the trailing slash), so every `Submission.delete()` / `Comment.delete()` failed. The same
# endpoint without the slash works (confirmed live, October 2026). PRAW reads this shared dict on each
# call, so patching it here fixes every delete made through any PRAW instance in this process.
praw.endpoints.API_PATH['del'] = 'api/del'

praw_instances = None

REDDIT_BASE_URL = 'https://reddit.com'


def full_url(permalink):
    """Turn a Reddit permalink such as `/r/sub/comments/abc/title/def/` into a clickable URL.

    Values that are not site-relative paths (already a URL, or a placeholder like `?`) are returned as is.
    """
    permalink = str(permalink)
    if not permalink.startswith('/'):
        return permalink
    return REDDIT_BASE_URL + permalink


def author_label(comment):
    """Returns `/u/<name>` for `comment`'s author, or `[deleted]` when the account is gone."""
    author = getattr(comment, 'author', None)
    return f'/u/{author.name}' if author else '[deleted]'


def comment_permalink(comment):
    """Returns `comment`'s permalink, also for a comment from an inbox listing.

    Inbox comments have no `permalink`, only a `context` link (the permalink plus `?context=3`).
    """
    # Read `context` from __dict__: getattr on a lazy PRAW object would fetch it
    context = getattr(comment, '__dict__', {}).get('context')
    if context:
        return context.split('?', 1)[0]
    return getattr(comment, 'permalink', '?')


def log_posted_reply(bot_name, suggestion_comment, target_subreddit, reply):
    """Logs a comment a bot has just posted about a subreddit suggestion, with the new comment's full URL."""
    source_subreddit = suggestion_comment.subreddit.display_name
    logger.info(
        f'/u/{bot_name} replied to {author_label(suggestion_comment)} suggestion '
        f'(r/{source_subreddit} -> r/{target_subreddit}): {full_url(getattr(reply, "permalink", "?"))}'
    )

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

    logger.info(f'Instantiating PRAW client for /u/{username} (User-Agent: {useragent})')
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


def authenticate_all_bots():
    """Verifies that all 4 bot accounts can successfully authenticate against the Reddit API.

    Raises:
        RuntimeError: If any bot fails authentication or user identity verification.
    """
    logger.info('Authenticating all Reddit bot accounts...')
    bot_names = [
        AUTO_CROSSPOST_BOT_NAME,
        SUB_DOESNT_EXIST_BOT_NAME,
        SAME_SUBREDDIT_BOT_NAME,
        SAME_POST_BOT_NAME,
    ]
    failed_bots = {}
    for name in bot_names:
        logger.info(f'Verifying authentication for /u/{name}...')
        try:
            reddit_instance = get_reddit_instance(name)
            me = reddit_instance.user.me()
            if me is None:
                raise RuntimeError(f'Bot /u/{name} is not authenticated (user.me() returned None, read-only mode)')
            if me.name.lower() != name.lower():
                raise RuntimeError(f'Bot /u/{name} authenticated as unexpected user /u/{me.name}')
            logger.info(f'Successfully authenticated /u/{me.name}')
        except Exception as e:
            err_msg = f'Failed to authenticate bot /u/{name}: {e}'
            logger.error(err_msg)
            failed_bots[name] = str(e)

    if failed_bots:
        failures_summary = '; '.join(f'/u/{name}: {err}' for name, err in failed_bots.items())
        raise RuntimeError(f'Authentication failed for {len(failed_bots)} bot(s): {failures_summary}')

    logger.info('All bot accounts authenticated successfully.')
