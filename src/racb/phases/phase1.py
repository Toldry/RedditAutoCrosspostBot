"""Phase 1: Listens to incoming comments from Reddit, checks patterns, and saves qualified recommendations."""

import logging
import re

import prawcore

from racb.constants import SUB_BLACKLIST
from racb.core import db, reddit, duplicate_detector, sub_search
from racb import i18n

logger = logging.getLogger(__name__)


def handle_incoming_comment(comment, metrics=None):
    if metrics:
        metrics.record_comment()

    logger.debug(f'Handling a comment: {comment.permalink}')
    target_subreddit = check_pattern(comment)
    if target_subreddit is None:
        return

    if is_mod_post(comment):
        return

    if title_contains_prohibited_phrases(comment):
        return

    source_subreddit = comment.subreddit.display_name.lower()
    if target_subreddit.lower() in SUB_BLACKLIST or source_subreddit in SUB_BLACKLIST:
        return

    if target_subreddit.lower() == source_subreddit:
        logger.info(f'Found "same subreddit" comment ({comment.permalink}). Replying via same_subreddit_bot.')
        reply_to_source_equals_target_comment(comment)
        if metrics:
            metrics.record_aux()
        return

    result_obj = get_posts_with_same_content(comment, target_subreddit)
    if result_obj.posts_found:
        logger.info(f'Found post with duplicate content in r/{target_subreddit} ({comment.permalink}). Replying via same_post_bot.')
        post_with_same_content = result_obj.posts[0]
        reply_to_same_content_post_comment(comment, target_subreddit, post_with_same_content)
        if metrics:
            metrics.record_aux()
        return
    elif result_obj.unable_to_search and result_obj.unable_to_search_reason == 'SUBREDDIT_DOES_NOT_EXIST':
        logger.info(f'Found reference to non-existent subreddit r/{target_subreddit} ({comment.permalink}). Replying via sub_doesnt_exist_bot.')
        reply_to_nonexistent_target_subreddit_comment(comment, target_subreddit)
        if metrics:
            metrics.record_aux()
        return

    if not is_top_level_comment(comment):
        return

    logger.info(f'Qualified recommendation match found: {comment.permalink} -> r/{target_subreddit}')
    db.add_comment(comment)
    if metrics:
        metrics.record_match()


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


# subreddit name length must be between 2 and 24 characters
subreddit_regex = re.compile(r'^(/)?r/(?P<sub_name>[a-zA-Z0-9_]{2,24})$')


def check_pattern(comment):
    search_result = subreddit_regex.search(comment.body)
    if search_result is None:
        return None

    target_subreddit = search_result.group('sub_name')
    return target_subreddit


def get_posts_with_same_content(comment, subreddit):
    class Result:
        posts_found = False
        posts = []
        unable_to_search = False
        unable_to_search_reason = None

    result = Result()
    reddit_instance = reddit.get_reddit_instance()

    try:
        query = f'url:\"{comment.submission.url}\"'
        submissions = reddit_instance.subreddit(subreddit).search(query=query, sort='new', time_filter='all')
        submissions = list(submissions)
    except prawcore.exceptions.NotFound:
        result.unable_to_search = True
        result.unable_to_search_reason = 'SUBREDDIT_DOES_NOT_EXIST'
        return result
    except prawcore.exceptions.Forbidden:
        result.unable_to_search = True
        result.unable_to_search_reason = 'SUBREDDIT_IS_PRIVATE'
        return result
    except prawcore.exceptions.Redirect:
        return result
    except Exception as e:
        error_message = str(e)
        if 'Redirect to /submit' in error_message:
            return result
        elif 'Redirect to /subreddits/search' in error_message or '404' in error_message:
            result.unable_to_search = True
            result.unable_to_search_reason = 'SUBREDDIT_DOES_NOT_EXIST'
            return result
        elif '403' in error_message:
            result.unable_to_search = True
            result.unable_to_search_reason = 'SUBREDDIT_IS_PRIVATE'
            return result
        else:
            raise

    if len(submissions) > 0:
        result.posts_found = True
        result.posts = submissions
        return result

    prior_posts = duplicate_detector.get_reposts_in_sub(comment, subreddit)
    if prior_posts:
        result.posts_found = True
        result.posts = [reddit_instance.submission(id=p['post_id']) for p in prior_posts]

    return result


def reply_to_source_equals_target_comment(source_comment):
    source_subreddit = source_comment.subreddit.display_name
    text = i18n.get_translated_string(
        'THATS_WHERE_WE_ARE',
        source_subreddit,
        bot_name=reddit.SAME_SUBREDDIT_BOT_NAME,
    )
    comment2 = get_comment_with_different_praw_instance(source_comment, reddit.SAME_SUBREDDIT_BOT_NAME)
    comment2.reply(text)


def reply_to_nonexistent_target_subreddit_comment(source_comment, target_subreddit):
    source_subreddit = source_comment.subreddit.display_name
    text = i18n.get_translated_string(
        'NONEXISTENT_SUBREDDIT',
        source_subreddit,
        add_suffix=False,
    )
    text = text.format(target_subreddit=target_subreddit)

    matches = sub_search.get_matches(target_subreddit)
    filtered_matches = [sub for sub in matches if is_subreddit_available(sub)]

    if len(filtered_matches) == 0:
        text2 = i18n.get_translated_string(
            'MAYBE_TYPO',
            source_subreddit,
            add_suffix=False,
        )
        text = f'{text} {text2}'
    else:
        text2 = i18n.get_translated_string(
            'ALTERNATE_SUBS_SUGGESTION',
            source_subreddit,
            add_suffix=False,
        )
        formatted_matches = [get_subreddit_suggestion_list_line(sub) for sub in filtered_matches]
        alternate_subreddits_string = '\n'.join(formatted_matches)
        text2 = text2.format(alternate_subreddits_string=alternate_subreddits_string)
        text = f'{text}\n\n{text2}'

    if is_subreddit_name_length_valid(target_subreddit):
        text3 = i18n.get_translated_string(
            'PROMPT_NONEXISTENT_SUBREDDIT_CREATION',
            source_subreddit,
            add_suffix=False,
            bot_name=reddit.SUB_DOESNT_EXIST_BOT_NAME,
        )
        text3 = text3.format(target_subreddit=target_subreddit)
        text = f'{text}\n\n{text3}'

    text += i18n.get_suffix(
        subreddit=target_subreddit,
        bot_name=reddit.SUB_DOESNT_EXIST_BOT_NAME,
    )
    comment2 = get_comment_with_different_praw_instance(source_comment, reddit.SUB_DOESNT_EXIST_BOT_NAME)
    comment2.reply(text)


def is_subreddit_name_length_valid(subreddit_name):
    NEW_SUBREDDIT_NAME_MINIMUM_LENGTH = 3
    NEW_SUBREDDIT_NAME_MAXIMUM_LENGTH = 24
    return NEW_SUBREDDIT_NAME_MINIMUM_LENGTH <= len(subreddit_name) <= NEW_SUBREDDIT_NAME_MAXIMUM_LENGTH


def is_subreddit_available(subreddit_name):
    reddit_instance = reddit.get_reddit_instance()
    try:
        sub_obj = reddit_instance.subreddit(subreddit_name)
        _ = sub_obj.subscribers
        return True
    except (prawcore.exceptions.NotFound, prawcore.exceptions.Forbidden, prawcore.exceptions.PrawcoreException, Exception):
        return False


def get_subreddit_suggestion_list_line(subreddit_name):
    ret_val = f'* r/{subreddit_name}'
    reddit_instance = reddit.get_reddit_instance()
    try:
        sub_obj = reddit_instance.subreddit(subreddit_name)
        nsfw_str = '**NSFW**, ' if sub_obj.over18 else ''
        ret_val += f' ({nsfw_str}subscribers: {sub_obj.subscribers:,})'
    except Exception:
        pass
    return ret_val


def reply_to_same_content_post_comment(source_comment, target_subreddit, post_with_same_content):
    source_subreddit = source_comment.subreddit.display_name
    text = i18n.get_translated_string(
        'FOUND_POST_WITH_SAME_CONTENT',
        source_subreddit,
        bot_name=reddit.SAME_POST_BOT_NAME,
    )
    text = text.format(
        same_content_post_url=post_with_same_content.permalink,
        target_subreddit=target_subreddit,
    )
    comment2 = get_comment_with_different_praw_instance(source_comment, reddit.SAME_POST_BOT_NAME)
    comment2.reply(text)


def get_comment_with_different_praw_instance(comment, username):
    reddit2 = reddit.get_reddit_instance(username=username)
    comment2 = reddit2.comment(id=comment.id)
    return comment2
