"""Phase 2: Retrieves aged comment entries from the DB and batch-validates them against Reddit API."""

import logging
import praw
import prawcore

from racb import config
from racb.constants import is_crosspost_blocked
from racb.core import db, reddit, duplicate_detector
from racb.phases import phase1

logger = logging.getLogger(__name__)

BATCH_SIZE = 100


def filter_comments_from_db(verbose=False):
    logger.info('Running phase 2 comment filter pass...')
    waiting_period_seconds = config.phase2_waiting_period_seconds()
    comment_entries = db.get_unchecked_comments_older_than(waiting_period_seconds)
    logger.info(f'Found {len(comment_entries)} unchecked comment(s) older than {waiting_period_seconds}s')

    if not comment_entries:
        logger.info('Finished phase 2 comment filter (0 comments to process)')
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
            logger.error(f'Error fetching batch comment info: {e}')

        for cid, ce in id_to_entry.items():
            comment = fetched_comments.get(cid)
            result = run_filters(ce, comment=comment)

            if verbose:
                score = result.comment.score if result.comment else 'NA'
                logger.info(f'Passed filter={result.passes_filter}. Score={score}. Permalink={reddit.full_url(ce["permalink"])}')

            if result.passes_filter:
                db.set_comment_checked(ce)
            else:
                db.delete_comment(ce)

    logger.info('Finished running phase 2 comment filter pass')


def chunk_list(lst, chunk_size):
    """Yield successive chunks of size chunk_size from lst."""
    for i in range(0, len(lst), chunk_size):
        yield lst[i:i + chunk_size]


def extract_comment_id_from_permalink(permalink):
    parts = [p for p in permalink.strip('/').split('/') if p]
    if parts:
        return parts[-1]
    return None


def run_filters(comment_entry, comment=None, full_media_scan=False):
    """Re-checks a scraped comment before it is crossposted.

    With `full_media_scan`, an image (or video) post is compared with every image (or video) post the target
    subreddit got since the comment was written (as far back as Reddit lists), instead of with the newest few
    only, and with the target's top posts of all time and of the past year. Phase 3 uses it, since its reply
    tells readers how many posts were checked.
    """
    class Result:
        passes_filter = False
        reason = None
        comment = None
        target_subreddit = None
        post_with_same_content = None
        media_scan = None

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

    comment_score_threshold = config.comment_score_threshold()
    if getattr(comment, 'score', 0) < comment_score_threshold:
        result.reason = 'COMMENT_SCORE_TOO_LOW'
        return result

    target_subreddit = phase1.check_pattern(comment)
    result.target_subreddit = target_subreddit
    if target_subreddit is None:
        # this can happen when the source comment was edited since it was scraped
        result.reason = 'TARGET_SUBREDDIT_NOT_FOUND'
        return result

    # Guards against bait-and-switch edits: a comment that gathered upvotes for one subreddit
    # must not be able to redirect the crosspost to another. Rows scraped before the
    # target_subreddit column existed have no stored target and skip this check.
    stored_target_subreddit = comment_entry.get('target_subreddit')
    if stored_target_subreddit and stored_target_subreddit.lower() != target_subreddit.lower():
        result.reason = 'TARGET_SUBREDDIT_CHANGED'
        return result

    # Rows scraped before a subreddit or pair was blocked must not be crossposted either
    if is_crosspost_blocked(comment.subreddit.display_name, target_subreddit):
        result.reason = 'CROSSPOST_BLOCKED'
        return result

    source_problem = get_source_submission_problem(comment.submission)
    if source_problem:
        result.reason = source_problem
        return result

    if full_media_scan:
        gpwsc_result = phase1.get_posts_with_same_content(
            comment,
            target_subreddit,
            media_scan_since=getattr(comment, 'created_utc', None),
            media_scan_limit=duplicate_detector.REDDIT_LISTING_LIMIT,
            # The reply leaves the top posts out of its count (RepostScan.top_posts_checked)
            media_scan_top_limit=duplicate_detector.QUICK_SCAN_TOP_LIMIT,
            media_scan_top_time_filters=('all', 'year'),
            search_titles=True,
        )
    else:
        gpwsc_result = phase1.get_posts_with_same_content(comment, target_subreddit, search_titles=True)
    result.media_scan = gpwsc_result.media_scan
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


def get_source_submission_problem(submission):
    """Returns why the post a comment is under should not be crossposted any more, or None if it still can be.

    A deleted or removed post would be crossposted empty, and a locked or archived one can no longer be
    discussed where it was posted.
    """
    try:
        # Logged so a post that looks live in a browser can be told apart from one Reddit reports as removed
        removed_by_category = getattr(submission, 'removed_by_category', None)
        if isinstance(removed_by_category, str):
            logger.info(f'Source post {permalink_safe(submission)} has removed_by_category={removed_by_category!r}')
            return 'SOURCE_DELETED_OR_REMOVED'
        selftext = getattr(submission, 'selftext', None)
        if selftext in ('[deleted]', '[removed]'):
            logger.info(f'Source post {permalink_safe(submission)} has selftext={selftext!r}')
            return 'SOURCE_DELETED_OR_REMOVED'
        if getattr(submission, 'locked', False) is True:
            return 'SOURCE_LOCKED'
        if getattr(submission, 'archived', False) is True:
            return 'SOURCE_ARCHIVED'
    except (praw.exceptions.PRAWException, prawcore.exceptions.PrawcoreException) as e:
        logger.info(f'Source post unavailable ({permalink_safe(submission)}): {e!r}')
        return 'SOURCE_UNAVAILABLE'
    return None


def get_full_comment_from_reddit(permalink_without_prefix):
    reddit_instance = reddit.get_reddit_instance()
    return reddit_instance.comment(url=r'https://www.reddit.com' + permalink_without_prefix)


def check_comment_availability(comment):
    try:
        _ = comment.score
        return True
    except (praw.exceptions.ClientException, prawcore.exceptions.PrawcoreException, Exception) as e:
        logger.info(f'Comment unavailable ({permalink_safe(comment)}): {e}')
        return False


def permalink_safe(comment):
    return reddit.full_url(getattr(comment, 'permalink', 'unknown'))
