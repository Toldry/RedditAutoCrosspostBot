"""Responds to comments and feedback from the Reddit inbox."""

import functools
import logging
import re

import praw
import prawcore

from racb.core import reddit
from racb.core.reddit import get_reddit_instance
from racb import config, i18n

logger = logging.getLogger(__name__)

# Feedback phrases recognized in every subreddit, on top of each locale's BAD_BOT_PHRASE / GOOD_BOT_PHRASE
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
                respond_to_comment(item, reddit_instance=reddit_instance, bot_name=bot_name)
            except (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException) as e:
                logger.warning(f'Could not process inbox item {getattr(item, "id", "?")} of /u/{bot_name}: {e!r}')


def respond_to_comment(comment, reddit_instance=None, bot_name=reddit.AUTO_CROSSPOST_BOT_NAME):
    if comment.type == 'username_mention':
        pass
    elif comment.type == 'comment_reply':
        handle_comment_reply(comment, reddit_instance=reddit_instance, bot_name=bot_name)


def handle_comment_reply(comment, reddit_instance=None, bot_name=reddit.AUTO_CROSSPOST_BOT_NAME):
    # Not called `reddit`, which would shadow the module and break `reddit.full_url` below
    reddit_instance = reddit_instance or get_reddit_instance()
    if not isinstance(comment, praw.models.Comment):
        return
    sentiment = check_sentiment(comment.body)
    if sentiment is not None:
        logger.info(f'Received {sentiment} feedback: {reddit.full_url(reddit.comment_permalink(comment))}')
    try:
        if sentiment == 'positive':
            respond_to_positive_sentiment(comment, bot_name)
        elif sentiment == 'negative':
            respond_to_negative_sentiment(comment, bot_name)
        # An 'explained_negative' reply ("Bad bot, this sub bans memes") already says why, so it gets no
        # answer; it is only logged and marked read. scripts/negative_feedback_report.py collects it.

        if sentiment is not None:
            reddit_instance.inbox.mark_read([comment])
    except prawcore.exceptions.Forbidden as e:
        if e.response.reason == 'Forbidden':
            logger.info(f'Got 403 Forbidden error, probably because the bot is banned in {comment.subreddit_name_prefixed}')
            reddit_instance.inbox.mark_read([comment])
        else:
            raise
    except praw.exceptions.RedditAPIException as e:
        # E.g. a locked thread or an archived post. Retrying would fail the same way every 10 minutes.
        logger.info(f'Could not reply to feedback {reddit.full_url(reddit.comment_permalink(comment))}: {e}')
        reddit_instance.inbox.mark_read([comment])


def normalize_feedback(text):
    """Lowercases `text`, drops punctuation and collapses whitespace, so "Bad bot!" equals "bad bot"."""
    without_punctuation = re.sub(r'[^\w\s]', '', text, flags=re.S)
    return ' '.join(without_punctuation.split()).lower()


@functools.cache
def feedback_phrases():
    """Returns (bad bot phrases, good bot phrases) in every supported language, normalized."""
    negative = {normalize_feedback(phrase) for phrase in NEGATIVE_PHRASES}
    positive = {normalize_feedback(phrase) for phrase in POSITIVE_PHRASES}
    bad_bot = {'bad bot'}
    for locale in i18n.available_locales():
        _, bad_phrase = i18n.lookup_in_languages('BAD_BOT_PHRASE', [locale, i18n.DEFAULT_LANGUAGE])
        _, good_phrase = i18n.lookup_in_languages('GOOD_BOT_PHRASE', [locale, i18n.DEFAULT_LANGUAGE])
        bad_bot.add(normalize_feedback(bad_phrase))
        positive.add(normalize_feedback(good_phrase))
    return bad_bot, negative | bad_bot, positive


def check_sentiment(text):
    """Classifies a reply to a bot.

    Returns 'negative' or 'positive' for a bare feedback phrase ("bad bot", "Schlechter Bot", "good bot", ...),
    'explained_negative' for a "bad bot" phrase followed by an explanation ("Bad bot, this sub bans memes"),
    and None for anything else.
    """
    bad_bot, negative, positive = feedback_phrases()
    cleaned_text = normalize_feedback(text)
    if cleaned_text in negative:
        return 'negative'
    elif cleaned_text in positive:
        return 'positive'
    elif any(cleaned_text.startswith(phrase + ' ') for phrase in bad_bot):
        return 'explained_negative'
    return None


def is_first_positive_reply(comment):
    """Checks that no older "good bot" style reply sits next to `comment` under the same bot comment.

    Only replies Reddit returns with the parent are compared; ones hidden behind "load more" are not.
    """
    parent = comment.parent()
    if not isinstance(parent, praw.models.Comment):
        return True
    parent.refresh()
    own_key = (comment.created_utc, comment.id)
    for sibling in parent.replies:
        if not isinstance(sibling, praw.models.Comment) or sibling.id == comment.id:
            continue
        if check_sentiment(sibling.body or '') == 'positive' and (sibling.created_utc, sibling.id) < own_key:
            return False
    return True


def reply_to_feedback(comment, string_key, bot_name):
    """Replies to `comment` in its subreddit's language, or only logs the reply when DRY_RUN is enabled."""
    text = i18n.get_translated_string(string_key, subreddit=comment.subreddit.display_name, bot_name=bot_name)
    if config.is_dry_run():
        logger.info(f'[DRY RUN] Would reply as /u/{bot_name} to {reddit.full_url(reddit.comment_permalink(comment))}:\n{text}')
        return None
    reply = comment.reply(text)
    logger.info(
        f'/u/{bot_name} replied to {reddit.author_label(comment)} feedback '
        f'(r/{comment.subreddit.display_name}): {reddit.full_url(getattr(reply, "permalink", "?"))}'
    )
    return reply


def respond_to_positive_sentiment(comment, bot_name=reddit.AUTO_CROSSPOST_BOT_NAME):
    if not is_first_positive_reply(comment):
        logger.info(f'Not thanking {reddit.full_url(reddit.comment_permalink(comment))}: an earlier reply already said the same')
        return None
    return reply_to_feedback(comment, 'RESPOND_TO_POSITIVE_SENTIMENT', bot_name)


def respond_to_negative_sentiment(comment, bot_name=reddit.AUTO_CROSSPOST_BOT_NAME):
    return reply_to_feedback(comment, 'RESPOND_TO_NEGATIVE_SENTIMENT', bot_name)
