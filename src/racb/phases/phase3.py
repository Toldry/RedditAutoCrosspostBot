"""Phase 3: Final validation and execution of crossposts with localized replies."""

import logging
import time

import praw

from racb.core import db, reddit, shutdown
from racb.phases import phase2
from racb import config, i18n

logger = logging.getLogger(__name__)

# Linked from the crosspost reply, so readers can see how the image duplicate check works
DUPLICATE_DETECTOR_SOURCE_URL = (
    'https://github.com/Toldry/RedditAutoCrosspostBot/blob/master/src/racb/core/duplicate_detector.py'
)
SECONDS_PER_DAY = 86400


def process_comment_entries():
    logger.info('Beginning phase 3 processing...')
    waiting_period_seconds = config.phase3_waiting_period_seconds()

    comment_entries = db.get_comments_older_than(waiting_period_seconds)
    logger.info(f'Found {len(comment_entries)} mature comment(s) older than {waiting_period_seconds}s for Phase 3')

    for comment_entry in comment_entries:
        if shutdown.stop_requested():
            # Between entries, so no entry is left crossposted but not deleted
            logger.info('Stopping phase 3 early: shutting down')
            return
        if not handle_comment_or_defer(comment_entry):
            # Reddit is rate limiting or failing; the rest of the batch would fail the same way
            break
        if config.is_dry_run():
            # Keep the row so it is still crossposted once DRY_RUN is turned off
            continue
        db.delete_comment(comment_entry)
    logger.info('Finished phase 3 processing')


def process_oldest_entry():
    """Runs Phase 3 on the oldest DB row only, for testing the bot in production one entry at a time.

    The row is processed even if it is younger than PHASE3_WAITING_PERIOD.
    As in the scheduled run, the row is deleted afterwards (kept with DRY_RUN).
    Returns False if the table is empty.
    """
    comment_entry = db.get_oldest_comment()
    if comment_entry is None:
        logger.info('The scraped_comments table is empty; nothing to process')
        return False
    logger.info(f'Processing the oldest entry (id={comment_entry["id"]}, scraped at {comment_entry["scraped_time"]})')

    if not handle_comment_or_defer(comment_entry):
        return True
    if config.is_dry_run():
        logger.info(f'[DRY RUN] Keeping entry id={comment_entry["id"]} in the database')
    else:
        db.delete_comment(comment_entry)
        logger.info(f'Deleted entry id={comment_entry["id"]} from the database')
    return True


def handle_comment_or_defer(comment_entry):
    """Runs handle_comment, unless Reddit is rate limiting or failing: then logs it and returns False.

    The row is then kept, so a later run tries it again, instead of the whole run crashing.
    """
    try:
        handle_comment(comment_entry)
    except reddit.TRANSIENT_NETWORK_ERRORS as e:
        if config.is_debug():
            raise
        logger.warning(
            f'Reddit could not be reached while processing entry id={comment_entry["id"]} ({e!r}); '
            'keeping it for the next run'
        )
        return False
    return True


def handle_comment(comment_entry):
    logger.info(f'Begin processing comment entry: {reddit.full_url(comment_entry["permalink"])}')
    result = phase2.run_filters(comment_entry, full_media_scan=True)
    if not result.passes_filter:
        logger.info(f'Comment {reddit.full_url(comment_entry["permalink"])} did not pass final filter: {result.reason}')
        return

    source_comment = result.comment
    target_subreddit = result.target_subreddit
    exec_crosspost(source_comment, target_subreddit, media_scan=result.media_scan)


def exec_crosspost(source_comment, target_subreddit, reply_to_crosspost_flag=True, media_scan=None):
    class Result:
        success = False
        crosspost = None
        failure_reason = None

    result = Result()
    crosspost_title = get_crosspost_title_for_crosspost(source_comment.submission, target_subreddit)

    if config.is_dry_run():
        logger.info(f'[DRY RUN] Would crosspost {reddit.full_url(source_comment.submission.permalink)} to r/{target_subreddit}')
        result.failure_reason = 'DRY_RUN'
        return result

    try:
        cross_post = source_comment.submission.crosspost(
            subreddit=target_subreddit,
            title=crosspost_title,
            send_replies=False,
        )
        logger.info(f'Crosspost successful: {reddit.full_url(cross_post.permalink)}')
        result.success = True
        result.crosspost = cross_post
        if reply_to_crosspost_flag:
            reply_to_crosspost(source_comment, cross_post, target_subreddit, media_scan=media_scan)
    except Exception as e:
        hce_res = handle_crosspost_exception(e, source_comment, target_subreddit)
        if hce_res.handled_with_grace:
            result.failure_reason = hce_res.error_type
            logger.info(f'Crosspost was rejected gracefully ({hce_res.error_type}) for comment {reddit.full_url(source_comment.permalink)}')
        else:
            logger.error(
                f'Crosspost failed unexpectedly: {e} | Source comment: {reddit.full_url(source_comment.permalink)}',
                exc_info=True,
            )
            if config.is_debug():
                raise

    return result


def get_crosspost_title_for_crosspost(submission, target_subreddit):
    if target_subreddit.lower() == 'totallynotrobots':
        return submission.title.upper()
    else:
        # Will use this submission’s title if None (default: None).
        return None


def reply_to_crosspost(source_comment, cross_post, target_subreddit, media_scan=None):
    source_subreddit = source_comment.subreddit.display_name
    text = i18n.get_translated_string(
        'REPLY_TO_CROSSPOST',
        target_subreddit,
        bot_name=reddit.AUTO_CROSSPOST_BOT_NAME,
    )
    # The time since the comment was written, not the configured waiting period: --process-one skips the wait
    comment_created_utc = getattr(source_comment, 'created_utc', None)
    if comment_created_utc is None:
        comment_age_seconds = config.phase3_waiting_period_seconds()
    else:
        comment_age_seconds = time.time() - comment_created_utc
    timedelta_string = i18n.format_duration_in_days(comment_age_seconds, target_subreddit)
    if source_comment.author:
        source_comment_author_name = f'/u/{source_comment.author.name}'
    else:
        source_comment_author_name = i18n.get_translated_string('THE_USER_WHO_COMMENTED', target_subreddit, add_suffix=False)

    text = text.format(
        source_subreddit=source_subreddit,
        target_subreddit=target_subreddit,
        source_comment_permalink=f'https://reddit.com{source_comment.permalink}',
        source_comment_score=getattr(source_comment, 'score', 0),
        source_submission_id=source_comment.submission.id,
        timedelta_string=timedelta_string,
        source_comment_author_name=source_comment_author_name,
        image_check=describe_media_scan(media_scan, source_comment, target_subreddit),
    )
    reply = cross_post.reply(text)
    reddit.log_posted_reply(reddit.AUTO_CROSSPOST_BOT_NAME, source_comment, target_subreddit, reply)
    return reply


def describe_media_scan(media_scan, source_comment, target_subreddit):
    """Returns the sentence saying which image (or video) posts were compared with this one, or '' if none were."""
    if media_scan is None:
        return ''
    media = media_scan.media.upper()  # IMAGE or VIDEO, as in the message keys
    # Top posts are compared too, but the sentence is about the posts made since the comment was written
    count = media_scan.posts_checked - media_scan.top_posts_checked
    if not count:
        # Nothing to compare with: too rare to be worth its own sentence
        return ''
    if media_scan.complete:
        # Every image (or video) post made since the comment was written
        string_key = f'REPLY_TO_CROSSPOST_{media}_CHECK'
        period_start = getattr(source_comment, 'created_utc', None)
    else:
        # Reddit's listing ended first, so only the newest ones were compared
        string_key = f'REPLY_TO_CROSSPOST_{media}_CHECK_PARTIAL'
        period_start = media_scan.oldest_post_utc
    if period_start is None:
        return ''

    scan_period = i18n.format_duration_in_days(max(time.time() - period_start, SECONDS_PER_DAY), target_subreddit)
    # Only the cut-short sentence has plural forms; "all 1 image posts" is too rare to be worth one
    _, text = i18n.lookup(string_key, target_subreddit)
    return text.format(
        post_count=count,
        target_subreddit=target_subreddit,
        scan_period=scan_period,
        duplicate_detector_url=DUPLICATE_DETECTOR_SOURCE_URL,
    )


def handle_crosspost_exception(e, comment, target_subreddit):
    class Result:
        handled_with_grace = False
        error_type = None
    result = Result()

    if not isinstance(e, praw.exceptions.RedditAPIException):
        return result

    familiar_error_types = [
        'NO_CROSSPOSTS',
        'INVALID_CROSSPOST_THING',
        'SUBREDDIT_NOTALLOWED',
        'SUBREDDIT_NOTALLOWED_BANNED',
        'NO_IMAGES',
        'NO_LINKS',
        'NO_SELFS',
        'NO_VIDEOS',
        'OVER18_SUBREDDIT_CROSSPOST',
        'PRIVATE_SUBREDDIT_CROSSPOST',
        'THREAD_LOCKED',
        'IMAGES_NOTALLOWED',
        'SUBMIT_VALIDATION_BODY_BLACKLISTED_STRING',
        'SUBMIT_VALIDATION_TITLE_BLACKLISTED_STRING',
        'SUBMIT_VALIDATION_MIN_LENGTH',
        'SUBMIT_VALIDATION_MAX_LENGTH',
        'SUBMIT_VALIDATION_BODY_REQUIRED',
        'SUBMIT_VALIDATION_BODY_NOT_ALLOWED',
        'SUBMIT_VALIDATION_LINK_WHITELIST',
        'SUBMIT_VALIDATION_LINK_BLACKLIST',
        'SUBMIT_VALIDATION_REPOST',
        'SUBMIT_VALIDATION_FLAIR_REQUIRED',
        'SUBMIT_VALIDATION_TITLE_REGEX_REQUIREMENT',
        'SUBMIT_VALIDATION_TITLE_REQUIREMENT',
        'BANNED_FROM_SUBREDDIT',
    ]

    error_types = []
    if hasattr(e, 'items') and e.items:
        error_types = [item.error_type for item in e.items]
    elif hasattr(e, 'error_type'):
        error_types = [e.error_type]

    for err_type in error_types:
        if err_type in familiar_error_types:
            result.handled_with_grace = True
            result.error_type = err_type
            break

    return result
