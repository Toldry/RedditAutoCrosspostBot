"""Retrieves aged comment entries from the DB and crossposts them"""

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
    logging.info('Beginning phase 3 processing')
    PHASE3_WAITING_PERIOD = os.environ.get('PHASE3_WAITING_PERIOD')
    waiting_period_seconds = pytimeparse.timeparse.timeparse(PHASE3_WAITING_PERIOD)
    comment_entries = racb_db.get_comments_older_than(waiting_period_seconds)
    for comment_entry in comment_entries:
        handle_comment(comment_entry)
        racb_db.delete_comment(comment_entry)
    logging.info('Finished phase 3 processing')

def handle_comment(comment_entry):
    logging.info(f'Begin processing comment entry : {comment_entry["permalink"]}')
    result = phase2_handler.run_filters(comment_entry)
    if not result.passes_filter:
        return

    source_comment = result.comment
    target_subreddit = result.target_subreddit
    exec_crosspost(source_comment, target_subreddit)

def exec_crosspost(source_comment, target_subreddit, reply_to_crosspost_flag = True):
    class Result:
        success = False
        crosspost = None
        failure_reason = None

    result = Result()

    crosspost_title = get_crosspost_title_for_crosspost(source_comment.submission, target_subreddit)

    try:
        cross_post = source_comment.submission.crosspost(subreddit=target_subreddit, title=crosspost_title, send_replies=False)
        logging.info(f'Crosspost succesful. link to post: www.reddit.com{cross_post.permalink}')
        result.success = True
        result.crosspost = cross_post
        if reply_to_crosspost_flag:
            reply_to_crosspost(source_comment, cross_post, target_subreddit)
    except Exception as e:
        hce_res = handle_crosspost_exception(e, source_comment, target_subreddit)
        if hce_res.handled_with_grace:
            result.failure_reason = hce_res.error_type
        else: 
            logging.error(f'Crosspost failed due to a problem: {str(e)}' + '\n\n'
                          + f'This occured while attempting to crosspost based on this comment: {source_comment.permalink}')
            logging.exception(e)
            debug = bool(util.strtobool(os.environ.get('DEBUG')))
            if debug:
                raise
    
    return result

def get_crosspost_title_for_crosspost(submission, target_subreddit):
    if target_subreddit.lower() == 'totallynotrobots':
        return submission.title.upper()
    else:
        # Will use this submission’s title if None (default: None).
        # https://praw.readthedocs.io/en/latest/code_overview/models/submission.html#praw.models.Submission.crosspost
        return None 


def reply_to_crosspost(source_comment, cross_post, target_subreddit):
    source_subreddit = source_comment.subreddit.display_name
    text = i18n.get_translated_string('REPLY_TO_CROSSPOST', source_subreddit)
    PHASE3_WAITING_PERIOD = os.environ.get('PHASE3_WAITING_PERIOD')
    timedelta_string = PHASE3_WAITING_PERIOD
    if source_comment.author:
        source_comment_author_name = source_comment.author.name
    else:
        source_comment_author_name = i18n.get_translated_string('THE_USER_WHO_COMMENTED', source_subreddit, add_suffix=False)
    
    text = text.format(source_subreddit=source_subreddit,
                       target_subreddit=target_subreddit,
                       source_comment_permalink=source_comment.permalink,
                       source_comment_score=source_comment.score,
                       source_submission_id=source_comment.submission.id,
                       timedelta_string=timedelta_string,
                       source_comment_author_name=source_comment_author_name,)
    return cross_post.reply(text)



def handle_crosspost_exception(e, comment, target_subreddit):
    class Result:
        handled_with_grace = False
        error_type = None
    result = Result()

    if not isinstance(e, praw.exceptions.RedditAPIException):
        return result

    familiar_error_types = ['NO_CROSSPOSTS',
                            'INVALID_CROSSPOST_THING',
                            'SUBREDDIT_NOTALLOWED',
                            'NO_IMAGES',
                            'NO_LINKS',
                            'NO_SELFS',
                            'NO_VIDEOS',
                            'OVER18_SUBREDDIT_CROSSPOST',
                            'THREAD_LOCKED', 
                            'IMAGES_NOTALLOWED', 
                            'SUBMIT_VALIDATION_FLAIR_REQUIRED']
    if e.error_type in familiar_error_types:
        result.handled_with_grace = True
        result.error_type = e.error_type
        
    return result

