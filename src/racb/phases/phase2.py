"""Phase 2: Retrieves aged comment entries from the DB and batch-validates them against Reddit API."""

import logging
import os
import praw
import prawcore
import pytimeparse

from racb.core import db, reddit
from racb.phases import phase1

BATCH_SIZE = 100


def filter_comments_from_db(verbose=False):
    logging.info('Running phase 2 comment filter')
    PHASE2_WAITING_PERIOD = os.environ.get('PHASE2_WAITING_PERIOD', '2 hours')
    waiting_period_seconds = pytimeparse.timeparse.timeparse(PHASE2_WAITING_PERIOD) or 7200
    comment_entries = db.get_unchecked_comments_older_than(waiting_period_seconds)
    logging.info(f'Found {len(comment_entries)} unchecked comments')

    if not comment_entries:
        logging.info('Finished running phase 2 comment filter (0 comments)')
        return

    reddit_instance = reddit.get_reddit_instance()

    # Process comments in batch requests of 100 to minimize API queries
    for chunk in chunk_list(comment_entries, BATCH_SIZE):
        id_to_entry = {}
        for ce in chunk:
            cid = extract_comment_id_from_permalink(ce['permalink'])
            if cid:
                id_to_entry[cid] = ce

        fullnames = [f't1_{cid}' for cid in id_to_entry.keys()]
        fetched_comments = {}
        try:
            for comment in reddit_instance.info(fullnames=fullnames):
                fetched_comments[comment.id] = comment
        except (prawcore.exceptions.PrawcoreException, Exception) as e:
            logging.error(f'Error fetching batch comment info: {e}')

        for cid, ce in id_to_entry.items():
            comment = fetched_comments.get(cid)
            result = run_filters(ce, comment=comment)

            if verbose:
                score = result.comment.score if result.comment else 'NA'
                logging.info(f'Passed filter={result.passes_filter}. Score={score}. Permalink={ce["permalink"]}')

            if result.passes_filter:
                db.set_comment_checked(ce)
            else:
                db.delete_comment(ce)

    logging.info('Finished running phase 2 comment filter')


def chunk_list(lst, chunk_size):
    """Yield successive chunks of size chunk_size from lst."""
    for i in range(0, len(lst), chunk_size):
        yield lst[i:i + chunk_size]


def extract_comment_id_from_permalink(permalink):
    parts = [p for p in permalink.strip('/').split('/') if p]
    if parts:
        return parts[-1]
    return None


def run_filters(comment_entry, comment=None):
    class Result:
        passes_filter = False
        reason = None
        comment = None
        target_subreddit = None
        post_with_same_content = None

    result = Result()

    if comment is None:
        comment = get_full_comment_from_reddit(comment_entry['permalink'])
        available = check_comment_availability(comment)
        if not available:
            result.reason = 'COMMENT_UNAVAILABLE'
            return result

    result.comment = comment

    # Check if comment is deleted or removed
    if not hasattr(comment, 'body') or comment.body in ('[deleted]', '[removed]'):
        result.reason = 'COMMENT_DELETED_OR_REMOVED'
        return result

    comment_score_threshold = int(os.environ.get('COMMENT_SCORE_THRESHOLD', '1'))
    if getattr(comment, 'score', 0) < comment_score_threshold:
        result.reason = 'COMMENT_SCORE_TOO_LOW'
        return result

    target_subreddit = phase1.check_pattern(comment)
    result.target_subreddit = target_subreddit
    if target_subreddit is None:
        # this can happen when the source comment was edited since it was scraped
        result.reason = 'TARGET_SUBREDDIT_NOT_FOUND'
        return result

    gpwsc_result = phase1.get_posts_with_same_content(comment, target_subreddit)
    if gpwsc_result.posts_found:
        result.reason = 'POST_WITH_SAME_CONTENT_FOUND'
        result.post_with_same_content = gpwsc_result.posts[0]
        return result
    elif gpwsc_result.unable_to_search:
        unable_to_search_reason = gpwsc_result.unable_to_search_reason
        result.reason = f'UNABLE_TO_SEARCH_TARGET_SUBREDDIT_BECAUSE__{unable_to_search_reason}'
        return result
    
    result.passes_filter = True
    return result


def get_full_comment_from_reddit(permalink_without_prefix):
    reddit_instance = reddit.get_reddit_instance()
    return reddit_instance.comment(url=r'https://www.reddit.com' + permalink_without_prefix)


def check_comment_availability(comment):
    try:
        _ = comment.score
        return True
    except (praw.exceptions.ClientException, prawcore.exceptions.PrawcoreException, Exception) as e:
        logging.info(f'Comment unavailable ({permalink_safe(comment)}): {e}')
        return False


def permalink_safe(comment):
    return getattr(comment, 'permalink', 'unknown')
