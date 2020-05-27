"""Responds to comments fom the inbox"""

import logging
import re
import textwrap

import praw

import consts
import reddit_instantiator

NEGATIVE_PHRASES = [
    'bad bot',
    'delete this',
    'no',
]

POSITIVE_PHRASES = [
    'good bot',
]


def respond_to_inbox():
    logging.info('Checking inbox')
    reddit = reddit_instantiator.get_reddit_instance()
    for comment in reddit.inbox.unread(limit=None, mark_read=False):
        if not isinstance(comment, praw.models.Comment):
            continue
        sentiment = check_sentiment(comment.body)
        if sentiment == 'positive':
            respond_to_positive_sentiment(comment)
        elif sentiment == 'negative':
            respond_to_negative_sentiment(comment)

        if sentiment is not None:
            reddit.inbox.mark_read([comment])


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
    text = f'''\
    Thanks for the feedback, would you mind detailing why this crosspost was inappropriate? 
    
    The creator of this bot will look at the responses and try to change the code to reduce the incidences like these.'''
    text = textwrap.dedent(text)
    text += consts.POST_SUFFIX_TEXT

    return comment.reply(text)
