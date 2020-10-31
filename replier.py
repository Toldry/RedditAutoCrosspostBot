"""Replies to processed comment entries
"""

import logging
import textwrap
import os

import praw

import consts
import listener
import racb_db
import reddit_instantiator
import my_i18n as i18n
import repost_detector


def respond_to_saved_comments():
    logging.info('Responding to saved comments')
    waiting_period_months = int(os.environ.get('WAITING_PERIOD_MONTHS'))
    waiting_period_seconds = 60 * 60 * 24 * 30 * waiting_period_months
    comment_entries = racb_db.get_comments_older_than(waiting_period_seconds)
    for comment_entry in comment_entries:
        logging.info(
            f"Begin processing saved comment: {comment_entry['permalink']}")
        comment = get_full_comment_from_reddit(comment_entry)
        available = check_comment_availability(comment)
        if available:
            handle_comment(comment)
        racb_db.delete_comment(comment_entry)


def get_full_comment_from_reddit(comment_info):
    reddit = reddit_instantiator.get_reddit_instance()
    return reddit.comment(
        url=r'https://www.reddit.com' + comment_info['permalink'])


def get_existing_crosspost(source_comment, target_subreddit):
    """Attempts to find whether an existing crosspost
    already exists in target_subreddit.
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
        query = f'url:\"{source_comment.submission.url}\"'
        submissions = reddit.subreddit(target_subreddit).search(query=query,
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
        # the subreddit `target_subreddit` doesn't exist
        elif (error_message == 'Redirect to /subreddits/search'
              or error_message == 'received 404 HTTP response'):
            return 'SUBREDDIT_DOES_NOT_EXIST'
        # this error is recieved when the target_subreddit is private
        # "You must be invited to visit this community"
        elif error_message == 'received 403 HTTP response':
            return 'SUBREDDIT_IS_PRIVATE'
        else:
            raise

    if len(submissions) == 0:
        return None
    oldest_submission = submissions[-1]
    return oldest_submission


def handle_comment(source_comment):
    comment_score_threshold = int(os.environ.get('COMMENT_SCORE_THRESHOLD'))
    if source_comment.score < comment_score_threshold:
        logging.info(f'comment score = {source_comment.score} < {comment_score_threshold} = threshold. Passing this comment.')
        return
    else:
        logging.info(f'comment score = {source_comment.score} >= {comment_score_threshold} = threshold. Continuing.')


    target_subreddit = listener.check_pattern(source_comment)
    if target_subreddit is None:
        logging.info('Re-checked the source comment for the target subreddit, and no match was found. The comment was probably edited since it was scraped. Passing.')
        return

    logging.info('Checking for existing crosspost in target subreddit')
    result = get_existing_crosspost(source_comment, target_subreddit)
    if result is not None and not isinstance(result, str):
        existing_post = result
        logging.info('Existing crosspost found')
        return
    elif result is not None and isinstance(result, str):
        crosspost_is_impossible_reason_string = result
        logging.info(f'Cannot crosspost to subreddit \'{target_subreddit}\' because {crosspost_is_impossible_reason_string}')
        return

    was_posted_before = repost_detector.check_if_posted_before(source_comment, target_subreddit)
    logging.info(f'was_posted_before = {was_posted_before}')
    if was_posted_before:
        return

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
            debug = bool(os.environ.get('DEBUG'))
            if debug:
                raise


def reply_to_crosspost(source_comment, cross_post, target_subreddit):
    text = i18n.get_translated_string('REPLY_TO_CROSSPOST', target_subreddit)
    waiting_period_months = int(os.environ.get('WAITING_PERIOD_MONTHS'))
    text = text.format(source_subreddit_name_prefixed=source_comment.subreddit_name_prefixed,
                       target_subreddit=target_subreddit,
                       source_comment_permalink=source_comment.permalink,
                       source_comment_score=source_comment.score,
                       source_submission_id=source_comment.submission.id,
                       waiting_period_months=waiting_period_months,
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


def check_comment_availability(comment):
    try:
        comment.score  # access a property to trigger praw to retrieve the comment
        return True
    except Exception as e:
        if isinstance(e, praw.exceptions.ClientException):
            logging.info('This comment cannot be reached for some reason (maybe removed or banned)')
            return False
        else:
            raise
