"""Listens to incoming comments from reddit"""

import logging
import re

import racb_db


def handle_incoming_comment(comment):
    logging.debug(f'Handling a comment: {comment.permalink}')
    if is_mod_post(comment):
        return

    if not is_top_level_comment(comment):
        return

    other_subreddit = check_pattern(comment)
    if other_subreddit is None:
        return

    if other_subreddit.lower() == comment.subreddit.title.lower():
        return

    logging.debug(f'Match found: {comment.permalink}')
    racb_db.add_comment(comment)


def is_mod_post(comment):
    return comment.distinguished == 'moderator'


def is_top_level_comment(comment):
    return comment.parent_id == comment.link_id


subreddit_regex = re.compile(r'^(/)?r/([a-zA-Z0-9-_]+)$')  # compile once


# Checks if the comment's body contains only a reference to a subreddit,
# and return the subreddit name if there's a match
def check_pattern(comment):
    search_result = subreddit_regex.search(comment.body)
    if search_result is None:
        return None

    groups = search_result.groups()

    other_subreddit = groups[1]
    return other_subreddit
