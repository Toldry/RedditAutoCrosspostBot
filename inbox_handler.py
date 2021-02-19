"""Responds to comments fom the inbox"""

import logging
import re
import textwrap

import praw
import prawcore

import reddit_instantiator
import my_i18n as i18n

NEGATIVE_PHRASES = [
    'bad bot',
    'delete this',
    'no',
]

POSITIVE_PHRASES = [
    'good bot',
]


def respond_to_comment(comment):
    if comment.type == 'username_mention':
        pass
    elif comment.type == 'comment_reply':
        handle_comment_reply(comment)


def handle_comment_reply(comment):
    # author = None
    # if comment.distinguished == 'moderator':
    #     author = 'moderator'
    # else:
    #     author = f'/u/{comment.author.name}'
    # logging.info(f'Received inbox comment by {author}: {comment.body}')
    reddit = reddit_instantiator.get_reddit_instance()
    if not isinstance(comment, praw.models.Comment):
        return
    sentiment = check_sentiment(comment.body)
    try:
        if sentiment == 'positive':
            respond_to_positive_sentiment(comment)
        elif sentiment == 'negative':
            respond_to_negative_sentiment(comment)

        if sentiment is not None:
            reddit.inbox.mark_read([comment])
    except prawcore.exceptions.Forbidden as e:
        if e.response.reason == 'Forbidden':
            logging.info(f'Got 403 Forbidden error, probably because the bot is banned in {comment.subreddit_name_prefixed}')
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
    source_subreddit = comment.subreddit.display_name
    text = i18n.get_translated_string('RESPOND_TO_NEGATIVE_SENTIMENT', source_subreddit=source_subreddit)

    return comment.reply(text)
