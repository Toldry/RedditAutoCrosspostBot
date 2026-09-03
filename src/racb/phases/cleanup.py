"""Removes unwanted submissions made by the bot according to community score."""

import logging
from racb.core.reddit import get_reddit_instance

logger = logging.getLogger(__name__)

MINIMUM_SCORE = 0


def get_latest_submissions(limit=40):
    reddit = get_reddit_instance()
    return reddit.redditor('AutoCrosspostBot').submissions.new(limit=limit)


def delete_unwanted_submissions():
    logger.debug('Executing scheduled cleanup of downvoted submissions...')
    latest_submissions = get_latest_submissions()
    removed_count = 0
    for submission in latest_submissions:
        if submission.score < MINIMUM_SCORE:
            logger.info(
                f'Removing submission {submission.permalink} due to negative score ({submission.score})'
            )
            submission.delete()
            removed_count += 1
    logger.debug(f'Finished cleanup of downvoted submissions ({removed_count} removed)')
