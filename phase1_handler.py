"""Listens to incoming comments from reddit, checks if they match the 'subreddit recommendation' pattern, and saves them to the DB"""

import logging
import re
import json

import consts
import racb_db
import reddit_instantiator
import my_i18n as i18n

SUBREDDIT_NAME_LENGTH_LIMIT = 24

def handle_incoming_comment(comment):
    logging.debug(f'Handling a comment: {comment.permalink}')
    target_subreddit = check_pattern(comment)
    if target_subreddit is None:
        return

    if len(target_subreddit) > SUBREDDIT_NAME_LENGTH_LIMIT:
        return

    if is_mod_post(comment):
        return
        
    if title_contains_prohibited_phrases(comment):
        return

    source_subreddit = comment.subreddit.display_name.lower()
    if target_subreddit.lower() in consts.SUB_BLACKLIST or source_subreddit in consts.SUB_BLACKLIST:
        return
    
    if target_subreddit.lower() == source_subreddit:
        logging.info('Found "source_subreddit=target_subreddt" comment. Replying to source comment.')
        reply_to_source_equals_target_comment(comment, target_subreddit)
        return

    gec_result = get_existing_crosspost(comment, target_subreddit)
    if gec_result is not None and not isinstance(gec_result, str):
        logging.info('Found existing crosspost. Replying to source comment.')
        reply_to_existing_crosspost_comment(comment, target_subreddit, existing_crosspost=gec_result)
        return
    elif gec_result == 'SUBREDDIT_DOES_NOT_EXIST':
        logging.info('Found a reference to a subreddit that does not exist. Replying to source comment.')
        reply_to_nonexistent_target_subreddit_comment(comment, target_subreddit)
        return

    if not is_top_level_comment(comment):
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

def check_pattern(comment):
    '''Checks if the comment's body contains only a reference to a subreddit,
    and return the subreddit name if there's a match
    '''
    search_result = subreddit_regex.search(comment.body)
    if search_result is None:
        return None

    groups = search_result.groups()

    target_subreddit = groups[1]
    return target_subreddit

def get_existing_crosspost(source_comment, target_subreddit):
    '''Attempts to find whether an existing crosspost
    already exists in target_subreddit.
    Returns the oldest replica submission if found.
    Returns `None` if no existing crosspost exists.
    Returns a `string_reason` if it is impossible to
    crosspost to the other sub (due to it not existing,
    or being private, or otherwise)
    '''
    reddit = reddit_instantiator.get_reddit_instance()
    try:
        # See git isseue to understand why this is necessary
        # https://github.com/praw-dev/praw/issues/880
        query = f'url:\"{source_comment.submission.url}\"'
        submissions = reddit.subreddit(target_subreddit).search(query=query,
                                                               sort='new',
                                                               time_filter='all')
        # iterate over submissions to fetch them
        submissions = [s for s in submissions]
    except Exception as e:
        error_message = e.args[0]
        # when reddit tries redirecting a search query of a link to the submission page, that means 0 results were found for the search query
        if error_message == 'Redirect to /submit':
            return None
        # when reddit redirects to /subreddits/search that means the subreddit `target_subreddit` doesn't exist
        elif error_message in ['Redirect to /subreddits/search', 'received 404 HTTP response']:
            if e.response.text:
                try:
                    response_obj = json.loads(e.response.text)
                    if response_obj['reason'] == 'banned':
                        return 'SUBREDDIT_BANNED'
                except json.JSONDecodeError:
                    pass
            return 'SUBREDDIT_DOES_NOT_EXIST'
        # this error is recieved when the target_subreddit is private
        # "You must be invited to visit this community"
        elif error_message == 'received 403 HTTP response':
            return 'SUBREDDIT_IS_PRIVATE'
        else:
            raise

    if len(submissions) == 0:
        return None
    oldest_submission = submissions[-1]
    return oldest_submission

def reply_to_source_equals_target_comment(source_comment, target_subreddit):
    text = i18n.get_translated_string('THATS_WHERE_WE_ARE', target_subreddit)
    reddit_SSB = reddit_instantiator.get_reddit_instance(username = reddit_instantiator.SAME_SUBREDDIT_BOT_NAME)
    source_comment_SSB = reddit_SSB.comment(id=source_comment.id)
    source_comment_SSB.reply(text)
    return

def reply_to_nonexistent_target_subreddit_comment(source_comment, target_subreddit):
    text = i18n.get_translated_string('NONEXISTENT_SUBREDDIT', target_subreddit)
    text = text.format(target_subreddit=target_subreddit,)
    reddit_SDE = reddit_instantiator.get_reddit_instance(username = reddit_instantiator.SUB_DOESNT_EXIST_BOT_NAME)
    source_comment_SDE = reddit_SDE.comment(id=source_comment.id)
    source_comment_SDE.reply(text)
    return

def reply_to_existing_crosspost_comment(source_comment, target_subreddit, existing_crosspost):
    text = i18n.get_translated_string('FOUND_EXISTING_CROSSPOST', target_subreddit)
    text = text.format(original_post_url=existing_crosspost.permalink,
                        target_subreddit=target_subreddit,)
    source_comment.reply(text)
    return