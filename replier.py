"""Replies to processed comment entries
"""

import logging
import re
import textwrap
import urllib.parse

import praw
import requests
import time

from . import consts
from . import environment
from . import listener
from . import racb_db
from . import reddit_instantiator

TIME_DELAY_SECONDS = 60*60  # 60 min
COMMENT_SCORE_THRESHOLD = 9


def respond_to_saved_comments():
    logging.info('Responding to saved comments')
    # get comments that were scraped TIME_DELAY_SECONDS seconds ago
    comment_entries = racb_db.get_comments_before_timedelta(TIME_DELAY_SECONDS)
    for comment_entry in comment_entries:
        logging.info(
            f"Begin processing saved comment: {comment_entry['permalink']}")
        comment = get_full_comment_from_reddit(comment_entry)
        available = check_comment_availability(comment)
        if available:
            handle_comment(comment)
        racb_db.remove_comment(comment_entry)  # remove entry after processing


def get_full_comment_from_reddit(comment_info):
    reddit = reddit_instantiator.get_reddit_instance()
    return reddit.comment(
        url=r'https://www.reddit.com' + comment_info['permalink'])


def get_existing_crosspost(comment, other_subreddit):
    """Attempts to find whether an existing crosspost
    already exists in other_subreddit.
    Returns the oldest replica submission if found.
    Returns `None` if no existing crosspost exists.
    Returns a `string_reason` if it is impossible to
    crosspost to the other sub (due to it not existing,
    or being private, or otherwise)
    """
    reddit = reddit_instantiator.get_reddit_instance()
    try:
        # See git isseue to understand why this is necessary
        # https://github.com/praw-dev/praw/issues/880
        query = f'url:\"{comment.submission.url}\"'
        submissions = reddit.subreddit(other_subreddit).search(query=query,
                                                               sort='new',
                                                               time_filter='all')
        # iterate over submissions to fetch them
        submissions = [s for s in submissions]
    except Exception as e:
        error_message = e.args[0]
        # when reddit tries redirecting a search query of
        # a link to the submission page,
        # that means 0 results were found for the search query
        if error_message == 'Redirect to /submit':
            return None
        # when reddit redirects to /subreddits/search that means
        # the subreddit `other_subreddit` doesn't exist
        elif (error_message == 'Redirect to /subreddits/search'
              or error_message == 'received 404 HTTP response'):
            return 'SUBREDDIT_DOES_NOT_EXIST'
        # this error is recieved when the other_subreddit is private
        # "You must be invited to visit this community"
        elif error_message == 'received 403 HTTP response':
            return 'SUBREDDIT_IS_PRIVATE'
        else:
            raise

    if len(submissions) == 0:
        return None
    # return oldest submission that matches search
    return submissions[-1]


def handle_existing_post(comment, existing_post):
    text = f'''\
    I found [this preexisting post]({existing_post.link_permalink}) in {existing_post.subreddit_name_prefixed} with the same link as the original post.'''
    text = textwrap.dedent(text)
    text += consts.POST_SUFFIX_TEXT
    return comment.reply(text)

def handle_comment(comment):
    if comment.score < COMMENT_SCORE_THRESHOLD:
        logging.info(f'comment score = {comment.score} < {COMMENT_SCORE_THRESHOLD} = threshold. Passing this comment.')
        return

    other_subreddit = listener.check_pattern(comment)
    result = get_existing_crosspost(comment, other_subreddit)
    if result is not None and type(result) is not type(''):
        existing_post = result
        logging.info('Existing crosspost found') 
        reply = handle_existing_post(comment, existing_post)
        logging.info(f'Replied to comment, link: {reply.link_permalink}')
        return
    elif result is not None and type(result) is type(''):
        crosspost_is_impossible_reason_string = result
        logging.info(f'Cannot crosspost to subreddit \'{other_subreddit}\' because {crosspost_is_impossible_reason_string}')
        return

    try:
        cross_post = comment.submission.crosspost(subreddit=other_subreddit, send_replies=False)
        logging.info(f'Crosspost succesful. link to post: www.reddit.com{cross_post.permalink}')
        #reply_to_crosspost_suggestion_comment(comment, cross_post, other_subreddit) # I commented this line out because people seem to negatively react to these comments.
        reply_to_crosspost(comment, cross_post, other_subreddit)
    except Exception as e:
        handled_with_grace = handle_crosspost_exception(e, comment, other_subreddit)
        if not handled_with_grace:
            logging.error(f'Crosspost failed due to a problem: {str(e)}' + '\n\n' 
            + f'This occured while attempting to crosspost based on this comment: {comment.permalink}')
            if environment.DEBUG:
                raise


def reply_to_crosspost_suggestion_comment(comment, cross_post, other_subreddit):
    text = f'''\
    It looks like you think this post also fits in r/{other_subreddit}:'


    So I took the liberty and crossposted this link there. Here's a [link](www.reddit.com{cross_post.permalink})'''
    text = textwrap.dedent(text)
    text += consts.POST_SUFFIX_TEXT
    return comment.reply(text)


def reply_to_crosspost(comment, cross_post, other_subreddit):
    text = f'''\
    I crossposted this from {comment.subreddit_name_prefixed} to r/{other_subreddit} after seeing [this decently upvoted comment]({comment.permalink}) (score={comment.score}) that seems to suggest that this post would be a good fit here too.
    
    If you think this was a mistake, go ahead and downvote; I'll remove posts with negative scores.
    '''
    text = textwrap.dedent(text)
    text += consts.POST_SUFFIX_TEXT
    cross_post.reply(text)
    return

def handle_crosspost_exception(e, comment, other_subreddit):
    """Attempts to handle exceptions that arise while crossposting. Returns True if the error was handled gracefully
    """
    if type(e) is praw.exceptions.RedditAPIException:
        if e.error_type == 'NO_CROSSPOSTS':
            logging.info(f'Crossposts are not allowed in /r/{other_subreddit}')
            return True
        elif e.error_type == 'INVALID_CROSSPOST_THING': #wtf does this even mean, reddit? why are some urls considered invalid for crossposting?
            logging.info(f'Got that weird unhelpful INVALID_CROSSPOST_THING message again: {e.message}')
            return True
        elif e.error_type == 'SUBREDDIT_NOTALLOWED':
            logging.info(f'Not allowed to post in /r/{other_subreddit}')
            return True
        else:
            # Unfamiliar reddit error
            return False

    return False


def check_comment_availability(comment):
    try:
        comment.score #access a property to trigger praw to retrieve the comment
        return True
    except Exception as e:
        if type(e) == praw.exceptions.ClientException:
            logging.info('This comment cannot be reached for some reason (maybe removed or banned)')
            return False
        else:
            raise