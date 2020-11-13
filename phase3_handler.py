"""Replies to processed comment entries
"""

import logging
import textwrap
import os
import datetime
from distutils import util

import praw
import pytimeparse

import consts
import phase2_handler
import racb_db
import reddit_instantiator
import my_i18n as i18n


def process_comment_entries():
    logging.info('Processing comment entries')
    PHASE3_WAITING_PERIOD = os.environ.get('PHASE3_WAITING_PERIOD')
    waiting_period_seconds = pytimeparse.timeparse.timeparse(PHASE3_WAITING_PERIOD)
    comment_entries = racb_db.get_comments_older_than(waiting_period_seconds)
    for comment_entry in comment_entries:
        handle_comment(comment_entry)
        racb_db.delete_comment(comment_entry)


def handle_comment(comment_entry):
    logging.info(f'Begin processing comment entry : {comment_entry["permalink"]}')
    result = phase2_handler.run_filters(comment_entry)
    if result == False:
        return

    source_comment, target_subreddit = result
    try:
        cross_post = source_comment.submission.crosspost(subreddit=target_subreddit, send_replies=False)
        logging.info(f'Crosspost succesful. link to post: www.reddit.com{cross_post.permalink}')
        reply_to_crosspost(source_comment, cross_post, target_subreddit)
    except Exception as e:
        handled_with_grace = handle_crosspost_exception(e, source_comment, target_subreddit)
        if not handled_with_grace:
            logging.error(f'Crosspost failed due to a problem: {str(e)}' + '\n\n'
                          + f'This occured while attempting to crosspost based on this comment: {source_comment.permalink}')
            logging.exception(e)
            debug = bool(util.strtobool(os.environ.get('DEBUG')))
            if debug:
                raise


def reply_to_crosspost(source_comment, cross_post, target_subreddit):
    text = i18n.get_translated_string('REPLY_TO_CROSSPOST', target_subreddit)
    PHASE3_WAITING_PERIOD = os.environ.get('PHASE3_WAITING_PERIOD')
    timedelta_string = PHASE3_WAITING_PERIOD
    text = text.format(source_subreddit_name_prefixed=source_comment.subreddit_name_prefixed,
                       target_subreddit=target_subreddit,
                       source_comment_permalink=source_comment.permalink,
                       source_comment_score=source_comment.score,
                       source_submission_id=source_comment.submission.id,
                       timedelta_string=timedelta_string,
                       source_comment_author_name=source_comment.author.name,)
    return cross_post.reply(text)


def handle_crosspost_exception(e, comment, target_subreddit):
    """Attempts to handle exceptions that arise while crossposting. Returns True if the error was handled gracefully
    """
    if not isinstance(e, praw.exceptions.RedditAPIException):
        return False

    if e.error_type == 'NO_CROSSPOSTS':
        logging.info(f'Crossposts are not allowed in /r/{target_subreddit}')
        return True
    # wtf does this even mean, reddit? why are some urls considered invalid for crossposting?
    elif e.error_type == 'INVALID_CROSSPOST_THING':
        logging.info(f'Got that weird unhelpful INVALID_CROSSPOST_THING message again: {e.message}')
        return True
    elif e.error_type == 'SUBREDDIT_NOTALLOWED':
        logging.info(f'Not allowed to post in /r/{target_subreddit}')
        return True
    elif e.error_type == 'NO_IMAGES':
        logging.info(f'Not allowed to post images in /r/{target_subreddit}')
        return True
    elif e.error_type == 'NO_LINKS':
        logging.info(f'Not allowed to post links in /r/{target_subreddit}')
        return True
    elif e.error_type == 'NO_SELFS':
        logging.info(f'Not allowed to post text posts in /r/{target_subreddit}')
        return True
    elif e.error_type == 'NO_VIDEOS':
        logging.info(f'Not allowed to post videos in /r/{target_subreddit}')
        return True
    elif e.error_type == 'OVER18_SUBREDDIT_CROSSPOST':
        logging.info(f'Not allowed to crosspost 18+ content in /r/{target_subreddit}')
        return True
    else:
        # Unfamiliar reddit error
        return False

