"""Removes unwanted submissions according to score"""
import logging

import reddit_instantiator

MINIMUM_SCORE = 0 #TODO: move this to env ?


def get_latest_submissions(limit=40):
    reddit = reddit_instantiator.get_reddit_instance()
    return reddit.redditor('AutoCrosspostBot').submissions.new(limit=limit)


def delete_unwanted_submissions():
    logging.debug('Deleting unwanted submissions')
    latest_submissions = get_latest_submissions()
    for submission in latest_submissions:
        if submission.score < MINIMUM_SCORE:
            logging.info(f'Removing the submission {submission.permalink} due to score ({submission.score}) being '
                         f'below minimum threshold ({MINIMUM_SCORE})')
            submission.delete()
    logging.debug('Finished deleting unwanted submissions')
