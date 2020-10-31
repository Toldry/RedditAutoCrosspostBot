import logging
import os

import reddit_instantiator
import replier
import racb_db

def delete_comments_with_low_score():
    logging.info('Removing comments with low score')
    waiting_period_for_low_score_comment_removal_hours = int(os.environ.get('WAITING_PERIOD_FOR_LOW_SCORE_COMMENT_REMOVAL_HOURS'))
    waiting_period_seconds = 60*60*waiting_period_for_low_score_comment_removal_hours
    comment_entries = racb_db.get_unchecked_comments_older_than(waiting_period_seconds)
    logging.info(f'Found {len(comment_entries)} unchecked comments.')
    if len(comment_entries) == 0:
        return
    
    for comment_entry in comment_entries:
        comment = replier.get_full_comment_from_reddit(comment_entry)
        available = replier.check_comment_availability(comment)
        comment_score_threshold = int(os.environ.get('COMMENT_SCORE_THRESHOLD'))
        passed = available and comment.score >= comment_score_threshold
        if passed:
            racb_db.set_comment_checked(comment_entry)
        else:
            racb_db.delete_comment(comment_entry)