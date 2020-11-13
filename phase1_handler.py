"""Listens to incoming comments from reddit, checks if they match the 'subreddit recommendation' pattern, and saves them to the DB"""

import logging
import re

import consts
import racb_db


def handle_incoming_comment(comment):
    logging.debug(f'Handling a comment: {comment.permalink}')
    other_subreddit = check_pattern(comment)
    if other_subreddit is None:
        return

    if is_mod_post(comment):
        return

    if not is_top_level_comment(comment):
        return
        
    if other_subreddit.lower() == comment.subreddit.title.lower():
        return

    if title_contains_prohibited_phrases(comment):
        return

    if other_subreddit.lower() in consts.SUB_BLACKLIST or comment.subreddit.title.lower() in consts.SUB_BLACKLIST:
        return

    logging.info(f'Match found: {comment.permalink}')
    racb_db.add_comment(comment)


def is_mod_post(comment):
    return comment.distinguished == 'moderator'


def is_top_level_comment(comment):
    return comment.parent_id == comment.link_id
    
prohibited_phrases = ['sub', 'subs', 'subreddit', 'subreddits']
prohibited_phrases = [(r'\b' + x + r'\b') for x in prohibited_phrases]
prohibited_phrases = '|'.join(prohibited_phrases)
prohibited_phrases = re.compile(prohibited_phrases, flags=re.IGNORECASE)

def title_contains_prohibited_phrases(comment):
    title = comment.submission.title
    return prohibited_phrases.search(title) is not None

subreddit_regex = re.compile(r'^(/)?r/([a-zA-Z0-9-_]+)$')  # compile once

# Checks if the comment's body contains only a reference to a subreddit,
# and return the subreddit name if there's a match
def check_pattern(comment):
    search_result = subreddit_regex.search(comment.body)
    if search_result is None:
        return None

    groups = search_result.groups()

    target_subreddit = groups[1]
    return target_subreddit
