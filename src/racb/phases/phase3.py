"""Phase 3: Final validation and execution of crossposts with localized replies."""

import logging

import praw

from racb.core import db, reddit
from racb.phases import phase2
from racb import config, i18n

logger = logging.getLogger(__name__)


def process_comment_entries():
    logger.info('Beginning phase 3 processing...')
    waiting_period_seconds = config.phase3_waiting_period_seconds()

    comment_entries = db.get_comments_older_than(waiting_period_seconds)
    logger.info(f'Found {len(comment_entries)} mature comment(s) older than {waiting_period_seconds}s for Phase 3')

    for comment_entry in comment_entries:
        handle_comment(comment_entry)
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

    handle_comment(comment_entry)
    if config.is_dry_run():
        logger.info(f'[DRY RUN] Keeping entry id={comment_entry["id"]} in the database')
    else:
        db.delete_comment(comment_entry)
        logger.info(f'Deleted entry id={comment_entry["id"]} from the database')
    return True


def handle_comment(comment_entry):
    logger.info(f'Begin processing comment entry: {comment_entry["permalink"]}')
    result = phase2.run_filters(comment_entry)
    if not result.passes_filter:
        logger.info(f'Comment {comment_entry["permalink"]} did not pass final filter: {result.reason}')
        return

    source_comment = result.comment
    target_subreddit = result.target_subreddit
    exec_crosspost(source_comment, target_subreddit)


def exec_crosspost(source_comment, target_subreddit, reply_to_crosspost_flag=True):
    class Result:
        success = False
        crosspost = None
        failure_reason = None

    result = Result()
    crosspost_title = get_crosspost_title_for_crosspost(source_comment.submission, target_subreddit)

    if config.is_dry_run():
        logger.info(f'[DRY RUN] Would crosspost {source_comment.submission.permalink} to r/{target_subreddit}')
        result.failure_reason = 'DRY_RUN'
        return result

    try:
        cross_post = source_comment.submission.crosspost(
            subreddit=target_subreddit,
            title=crosspost_title,
            send_replies=False,
        )
        logger.info(f'Crosspost successful: https://www.reddit.com{cross_post.permalink}')
        result.success = True
        result.crosspost = cross_post
        if reply_to_crosspost_flag:
            reply_to_crosspost(source_comment, cross_post, target_subreddit)
    except Exception as e:
        hce_res = handle_crosspost_exception(e, source_comment, target_subreddit)
        if hce_res.handled_with_grace:
            result.failure_reason = hce_res.error_type
            logger.info(f'Crosspost was rejected gracefully ({hce_res.error_type}) for comment {source_comment.permalink}')
        else:
            logger.error(
                f'Crosspost failed unexpectedly: {e} | Source comment: {source_comment.permalink}',
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


def reply_to_crosspost(source_comment, cross_post, target_subreddit):
    source_subreddit = source_comment.subreddit.display_name
    text = i18n.get_translated_string(
        'REPLY_TO_CROSSPOST',
        target_subreddit,
        bot_name=reddit.AUTO_CROSSPOST_BOT_NAME,
    )
    timedelta_string = i18n.format_duration_in_days(config.phase3_waiting_period_seconds(), target_subreddit)
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
    )
    return cross_post.reply(text)


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
