"""Removes unwanted submissions made by the bot according to community score."""

import logging

import praw
import prawcore

from racb import config
from racb.core.reddit import get_reddit_instance, AUTO_CROSSPOST_BOT_NAME

logger = logging.getLogger(__name__)

MINIMUM_SCORE = 0


def get_latest_submissions(limit=40):
    reddit = get_reddit_instance()
    return reddit.redditor(AUTO_CROSSPOST_BOT_NAME).submissions.new(limit=limit)


def delete_unwanted_submissions():
    logger.debug('Executing scheduled cleanup of downvoted submissions...')
    latest_submissions = get_latest_submissions()
    removed_count = 0
    for submission in latest_submissions:
        if submission.score < MINIMUM_SCORE:
            logger.info(
                f'Removing submission {submission.permalink} due to negative score ({submission.score})'
            )
            if config.is_dry_run():
                logger.info(f'[DRY RUN] Would delete {submission.permalink}')
                continue
            try:
                submission.delete()
            except (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException) as e:
                logger.warning(f'Could not delete {submission.permalink}: {e!r}')
                continue
            removed_count += 1
    logger.debug(f'Finished cleanup of downvoted submissions ({removed_count} removed)')
