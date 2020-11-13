import logging
import os
import concurrent.futures

import praw

import racb_db
import reddit_instantiator
import repost_detector
import phase1_handler

def filter_comments_from_db():
    logging.info('Running phase 1 comment filter')
    PHASE2_WAITING_PERIOD_SECONDS = int(os.environ.get('PHASE2_WAITING_PERIOD_SECONDS'))
    comment_entries = racb_db.get_unchecked_comments_older_than(PHASE2_WAITING_PERIOD_SECONDS)
    logging.info(f'Found {len(comment_entries)} unchecked comments')
    if len(comment_entries) == 0:
        return
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        executor.map(process_comment_entry, comment_entries)
    logging.info('Finished running phase 1 comment filter')


def run_filters(comment_entry):
    """Returns False if the comment does not pass all the filters required for crossposting.
    otherwise returns a tuple with the comment itself (praw.models.Comment) and the target subreddit (str)
    """
    comment = get_full_comment_from_reddit(comment_entry)
    available = check_comment_availability(comment)
    if not available:
        return False

    comment_score_threshold = int(os.environ.get('COMMENT_SCORE_THRESHOLD'))
    if comment.score < comment_score_threshold:
        logging.debug(f'comment score = {comment.score} < {comment_score_threshold} = threshold. Passing this comment.')
        return False
    logging.debug(f'comment score = {comment.score} >= {comment_score_threshold} = threshold. Continuing.')

    target_subreddit = phase1_handler.check_pattern(comment)
    if target_subreddit is None:
        logging.debug('Re-checked the source comment for the target subreddit, and no match was found. The comment was probably edited since it was scraped. Passing.')
        return False

    logging.debug('Checking for existing crosspost in target subreddit')
    result = get_existing_crosspost(comment, target_subreddit)
    if result is not None and not isinstance(result, str):
        logging.debug('Existing crosspost found')
        return False
    elif result is not None and isinstance(result, str):
        crosspost_is_impossible_reason_string = result
        logging.debug(f'Cannot crosspost to subreddit \'{target_subreddit}\' because {crosspost_is_impossible_reason_string}')
        return False

    was_posted_before = repost_detector.check_if_posted_before(comment, target_subreddit)
    logging.debug(f'was_posted_before = {was_posted_before}')
    if was_posted_before:
        return False
    
    return (comment,target_subreddit,)

def get_full_comment_from_reddit(comment_info):
    reddit = reddit_instantiator.get_reddit_instance()
    return reddit.comment(url=r'https://www.reddit.com' + comment_info['permalink'])


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


def process_comment_entry(comment_entry):
    result = run_filters(comment_entry)
    passed = result != False
    if passed:
        racb_db.set_comment_checked(comment_entry)
    else:
        racb_db.delete_comment(comment_entry)