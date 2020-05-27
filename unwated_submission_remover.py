import logging

import praw

from . import reddit_instantiator

MINIMUM_SCORE = 0

def get_latest_sbumissions(limit=40):
    reddit = reddit_instantiator.get_reddit_instance()
    return reddit.redditor('AutoCrosspostBot').submissions.new(limit=limit)

def delete_unwanted_submissions():
    latest_submissions = get_latest_sbumissions()
    for submission in latest_submissions:
        if submission.score < MINIMUM_SCORE:
            logging.info(f'Removing the submission {submission.permalink} due to score ({submission.score}) being below minimum threshold ({MINIMUM_SCORE})')
            submission.delete()