"""Maintains one `praw.Reddit` instance per bot account per thread.

PRAW is not thread safe: each instance owns a `requests.Session`, an OAuth authorizer and a prawcore
rate limiter, none of them locked. So every thread (the stream loop, each Phase 1 worker, each scheduled
job) gets its own instances, created lazily the first time that thread asks for an account. Objects a
PRAW call returns (comments, submissions, listings) stay bound to the instance that fetched them, so pass
IDs, not PRAW objects, from one thread to another.
"""

__all__ = [
    'get_reddit_instance',
    'authenticate_bots',
    'authenticate_all_bots',
    'AUTO_CROSSPOST_BOT_NAME',
    'SUB_DOESNT_EXIST_BOT_NAME',
    'SAME_SUBREDDIT_BOT_NAME',
    'SAME_POST_BOT_NAME',
    'full_url',
]

import logging
import os
import threading

import praw
import praw.endpoints
import prawcore
import dotenv
import requests

from racb.version import __version__

logger = logging.getLogger(__name__)

# On 2026-10-15 Reddit answered PRAW's delete path, `POST /api/del/`, with a plain-text "404 page not found"
# for every item, while the same endpoint without the trailing slash worked. By 2026-10-16 both paths
# worked again, so it was probably a temporary Reddit-side problem. The slash-less path is kept because it
# works either way. PRAW reads this shared dict on each call, so this covers every PRAW instance in the process.
praw.endpoints.API_PATH['del'] = 'api/del'

# Reddit or the network failing for a while (rate limit, outage, timeout): worth retrying later
TRANSIENT_NETWORK_ERRORS = (
    prawcore.exceptions.ServerError,
    prawcore.exceptions.RequestException,
    prawcore.exceptions.TooManyRequests,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)

# Holds `praw_instances`, a dict of bot name -> praw.Reddit, separately for every thread
_thread_state = threading.local()

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


BOT_NAMES = [AUTO_CROSSPOST_BOT_NAME, SUB_DOESNT_EXIST_BOT_NAME, SAME_SUBREDDIT_BOT_NAME, SAME_POST_BOT_NAME]


def _thread_instances():
    instances = getattr(_thread_state, 'praw_instances', None)
    if instances is None:
        instances = {}
        _thread_state.praw_instances = instances
    return instances


def get_reddit_instance(username=AUTO_CROSSPOST_BOT_NAME):
    """Returns the calling thread's `praw.Reddit` object for `username`, creating it on first use."""
    instances = _thread_instances()
    if username not in instances:
        dotenv.load_dotenv()
        details = bot_details[username]
        instances[username] = _instantiate_praw(
            username=username,
            app_client_id=details['app_client_id'],
            password=os.environ.get(details['env_password_key']),
            app_client_secret=os.environ.get(details['env_app_client_secret_key']),
            version=details['version'],
        )
    return instances[username]


def reset_thread_instances():
    """Forgets the calling thread's PRAW instances (for tests)."""
    _thread_state.praw_instances = None


def authenticate_all_bots():
    """Verifies that all 4 bot accounts can successfully authenticate against the Reddit API.

    This checks the calling thread's instances; other threads log in with the same credentials later.

    Raises:
        RuntimeError: If any bot fails authentication or user identity verification.
    """
    authenticate_bots(BOT_NAMES)


def authenticate_bots(bot_names):
    """Verifies that the given bot accounts can authenticate; see `authenticate_all_bots`.

    Every account is tried before raising, so one failure does not hide another.
    """
    logger.info(f'Authenticating {len(bot_names)} Reddit bot account(s)...')
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

    logger.info('Bot account(s) authenticated successfully.')
