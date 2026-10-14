"""Responds to comments and feedback from the Reddit inbox."""

import logging
import re

import praw
import prawcore

from racb.core import reddit
from racb.core.reddit import get_reddit_instance
from racb import i18n

logger = logging.getLogger(__name__)

NEGATIVE_PHRASES = [
    'bad bot',
    'delete this',
    'no',
]

POSITIVE_PHRASES = [
    'good bot',
]


BOT_NAMES = [
    reddit.AUTO_CROSSPOST_BOT_NAME,
    reddit.SUB_DOESNT_EXIST_BOT_NAME,
    reddit.SAME_SUBREDDIT_BOT_NAME,
    reddit.SAME_POST_BOT_NAME,
]


def process_unread_inboxes(limit=100):
    """Scheduled job: evaluates unread inbox items of all 4 bots (each footer invites feedback)."""
    for bot_name in BOT_NAMES:
        reddit_instance = get_reddit_instance(bot_name)
        try:
            unread_items = list(reddit_instance.inbox.unread(limit=limit))
        except (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException) as e:
            logger.warning(f'Could not fetch inbox of /u/{bot_name}: {e!r}')
            continue
        for item in unread_items:
            try:
                respond_to_comment(item, reddit_instance=reddit_instance)
            except (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException) as e:
                logger.warning(f'Could not process inbox item {getattr(item, "id", "?")} of /u/{bot_name}: {e!r}')


def respond_to_comment(comment, reddit_instance=None):
    if comment.type == 'username_mention':
        pass
    elif comment.type == 'comment_reply':
        handle_comment_reply(comment, reddit_instance=reddit_instance)


def handle_comment_reply(comment, reddit_instance=None):
    reddit = reddit_instance or get_reddit_instance()
    if not isinstance(comment, praw.models.Comment):
        return
    sentiment = check_sentiment(comment.body)
    if sentiment is not None:
        logger.info(f'Received {sentiment} feedback: {getattr(comment, "permalink", "?")}')
    try:
        if sentiment == 'positive':
            respond_to_positive_sentiment(comment)
        elif sentiment == 'negative':
            respond_to_negative_sentiment(comment)

        if sentiment is not None:
            reddit.inbox.mark_read([comment])
    except prawcore.exceptions.Forbidden as e:
        if e.response.reason == 'Forbidden':
            logger.info(f'Got 403 Forbidden error, probably because the bot is banned in {comment.subreddit_name_prefixed}')
            reddit.inbox.mark_read([comment])
        else:
            raise


def check_sentiment(text):
    cleaned_text = re.sub(r'[^\w\s]', '', text, flags=re.S).strip().lower()
    if cleaned_text in NEGATIVE_PHRASES:
        return 'negative'
    elif cleaned_text in POSITIVE_PHRASES:
        return 'positive'
    return None


def respond_to_positive_sentiment(comment):
    # comment.reply('🤖🌹')
    pass


def respond_to_negative_sentiment(comment):
    # source_subreddit = comment.subreddit.display_name
    # text = i18n.get_translated_string('RESPOND_TO_NEGATIVE_SENTIMENT', subreddit=source_subreddit)
    # return comment.reply(text)
    pass
