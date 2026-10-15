"""Phase 1: Listens to incoming comments from Reddit, checks patterns, and saves qualified recommendations."""

import logging
import re

import prawcore

from racb.constants import SUB_BLACKLIST
from racb.core import db, reddit, duplicate_detector, sub_search
from racb import config, i18n

logger = logging.getLogger(__name__)


def handle_incoming_comment(comment, metrics=None):
    if metrics:
        metrics.record_comment()

    logger.debug(f'Handling a comment: {reddit.full_url(comment.permalink)}')
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
        logger.info(f'Found "same subreddit" comment ({reddit.full_url(comment.permalink)}). Replying via same_subreddit_bot.')
        reply = reply_to_source_equals_target_comment(comment)
        if metrics:
            metrics.record_aux()
        return reply

    result_obj = get_posts_with_same_content(comment, target_subreddit)
    if result_obj.posts_found:
        logger.info(f'Found post with duplicate content in r/{target_subreddit} ({reddit.full_url(comment.permalink)}). Replying via same_post_bot.')
        post_with_same_content = result_obj.posts[0]
        reply = reply_to_same_content_post_comment(comment, target_subreddit, post_with_same_content)
        if metrics:
            metrics.record_aux()
        return reply
    elif result_obj.unable_to_search and result_obj.unable_to_search_reason == 'SUBREDDIT_DOES_NOT_EXIST':
        logger.info(f'Found reference to non-existent subreddit r/{target_subreddit} ({reddit.full_url(comment.permalink)}). Replying via sub_doesnt_exist_bot.')
        reply = reply_to_nonexistent_target_subreddit_comment(comment, target_subreddit)
        if metrics:
            metrics.record_aux()
        return reply

    if not is_top_level_comment(comment):
        return None

    logger.info(f'Qualified recommendation match found: {reddit.full_url(comment.permalink)} -> r/{target_subreddit}')
    db.add_comment(comment, target_subreddit)
    if metrics:
        metrics.record_match()
    return True


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


def get_posts_with_same_content(comment, subreddit, media_scan_since=None,
                                media_scan_limit=duplicate_detector.QUICK_SCAN_NEWEST_LIMIT,
                                media_scan_top_limit=duplicate_detector.QUICK_SCAN_TOP_LIMIT):
    """Looks for posts in `subreddit` that link to the same URL or, for an image or video post, look the same.

    The picture comparison reads at most `media_scan_limit` of the newest posts, stopping at the first one older
    than `media_scan_since` (a Unix timestamp), plus the `media_scan_top_limit` top posts of all time. Its outcome is kept in `media_scan` (None if nothing was
    compared).
    """
    class Result:
        posts_found = False
        posts = []
        unable_to_search = False
        unable_to_search_reason = None
        media_scan = None

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
    except prawcore.exceptions.Redirect as e:
        # Reddit answers a search inside a nonexistent subreddit by redirecting to its community search
        if getattr(e, 'path', '').startswith('/subreddits/search'):
            result.unable_to_search = True
            result.unable_to_search_reason = 'SUBREDDIT_DOES_NOT_EXIST'
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

    result.media_scan = duplicate_detector.scan_subreddit_for_duplicates(
        comment.submission, subreddit, posted_after=media_scan_since, limit=media_scan_limit,
        top_limit=media_scan_top_limit,
    )
    if result.media_scan and result.media_scan.duplicates:
        result.posts_found = True
        result.posts = [duplicate['post'] for duplicate in result.media_scan.duplicates]

    return result


def reply_to_source_equals_target_comment(source_comment):
    source_subreddit = source_comment.subreddit.display_name
    text = i18n.get_translated_string(
        'THATS_WHERE_WE_ARE',
        source_subreddit,
        bot_name=reddit.SAME_SUBREDDIT_BOT_NAME,
    )
    return reply_as_bot(source_comment, text, reddit.SAME_SUBREDDIT_BOT_NAME)


def reply_to_nonexistent_target_subreddit_comment(source_comment, target_subreddit):
    source_subreddit = source_comment.subreddit.display_name

    matches = sub_search.get_matches(target_subreddit)
    suggestions = [describe_subreddit(sub) for sub in matches]

    # (key, separator before it) for each piece of the reply
    parts = [('NONEXISTENT_SUBREDDIT', '')]
    if len(suggestions) == 0:
        parts.append(('MAYBE_TYPO', ' '))
    else:
        parts.append(('ALTERNATE_SUBS_SUGGESTION', '\n\n'))
    if is_subreddit_name_length_valid(target_subreddit):
        parts.append(('PROMPT_NONEXISTENT_SUBREDDIT_CREATION', '\n\n'))

    # Every piece, the suggestion details and the suffix use one language, so a partly translated locale
    # gets an all-English reply rather than English sentences in the middle of a translated one
    keys = [key for key, _ in parts] + suggestion_message_keys(suggestions) + [i18n.SUFFIX_KEY]
    language = i18n.language_with_all(keys, source_subreddit)
    alternate_subreddits_string = '\n'.join(
        get_subreddit_suggestion_list_line(suggestion, language) for suggestion in suggestions
    )
    format_kwargs = {
        'target_subreddit': target_subreddit,
        'alternate_subreddits_string': alternate_subreddits_string,
    }
    text = ''
    for key, separator in parts:
        _, piece = i18n.lookup_in_languages(key, [language])
        text += separator + piece.format(**format_kwargs)

    _, suffix_text = i18n.lookup_in_languages(i18n.SUFFIX_KEY, [language])
    text += i18n.format_suffix(suffix_text, reddit.SUB_DOESNT_EXIST_BOT_NAME)
    return reply_as_bot(source_comment, text, reddit.SUB_DOESNT_EXIST_BOT_NAME)


def is_subreddit_name_length_valid(subreddit_name):
    NEW_SUBREDDIT_NAME_MINIMUM_LENGTH = 3
    NEW_SUBREDDIT_NAME_MAXIMUM_LENGTH = 24
    return NEW_SUBREDDIT_NAME_MINIMUM_LENGTH <= len(subreddit_name) <= NEW_SUBREDDIT_NAME_MAXIMUM_LENGTH


# Subreddit types that are worth pointing out in a suggestion. The state is shown as Reddit names it
# (untranslated), e.g. `* r/name (**private**)`.
NOTABLE_SUBREDDIT_TYPES = ('restricted', 'archived')
SUBSCRIBERS_KEY = 'SUGGESTION_SUBSCRIBERS'


def _error_reason(exception):
    """Returns the `reason` Reddit gives in a 403/404 body (e.g. 'private', 'banned'), or None."""
    try:
        return exception.response.json().get('reason')
    except Exception:
        return None


def describe_subreddit(subreddit_name):
    """Fetches a suggested subreddit once and returns what its suggestion line shows.

    Returns a dict with `name`, `state` (Reddit's own word, such as 'private', 'banned', 'quarantined' or
    'restricted', 'unavailable' when Reddit gives none, or None for a normal public subreddit),
    `subscribers` (None when unknown) and `over18`. A subreddit that cannot be fetched is
    still described, so the reply can list it with its state instead of silently dropping it.
    """
    info = {'name': subreddit_name, 'state': None, 'subscribers': None, 'over18': False}
    reddit_instance = reddit.get_reddit_instance()
    try:
        sub_obj = reddit_instance.subreddit(subreddit_name)
        info['subscribers'] = sub_obj.subscribers
        info['over18'] = bool(sub_obj.over18)
        subreddit_type = getattr(sub_obj, 'subreddit_type', 'public')
        if getattr(sub_obj, 'quarantine', False):
            info['state'] = 'quarantined'
        elif subreddit_type in NOTABLE_SUBREDDIT_TYPES:
            info['state'] = subreddit_type
    except prawcore.exceptions.Forbidden as e:
        info['state'] = _error_reason(e) or 'private'
    except prawcore.exceptions.NotFound as e:
        info['state'] = _error_reason(e) or 'unavailable'
    except prawcore.exceptions.Redirect:
        # Reddit redirects a name that does not exist (any more) to its search page
        info['state'] = 'unavailable'
    except Exception as e:
        # A network hiccup says nothing about the subreddit, so list it without details
        logger.warning(f'Could not fetch suggested subreddit r/{subreddit_name}: {e!r}')
    return info


def suggestion_message_keys(suggestions):
    """Returns the message keys the suggestion lines for `suggestions` will use."""
    if any(suggestion['subscribers'] is not None for suggestion in suggestions):
        return [SUBSCRIBERS_KEY]
    return []


def get_subreddit_suggestion_list_line(suggestion, language):
    """Formats a suggestion bullet such as `* r/name (**NSFW**, subscribers: 1,234)` in `language`."""
    details = []
    if suggestion['state'] is not None:
        details.append(f"**{suggestion['state']}**")
    if suggestion['over18']:
        details.append('**NSFW**')
    if suggestion['subscribers'] is not None:
        _, subscribers_text = i18n.lookup_in_languages(SUBSCRIBERS_KEY, [language])
        details.append(subscribers_text.format(subscribers=f"{suggestion['subscribers']:,}"))
    line = f"* r/{suggestion['name']}"
    if details:
        line += f" ({', '.join(details)})"
    return line


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
    return reply_as_bot(source_comment, text, reddit.SAME_POST_BOT_NAME)


def reply_as_bot(source_comment, text, bot_name):
    """Replies to `source_comment` as `bot_name`, or only logs the reply when DRY_RUN is enabled."""
    if config.is_dry_run():
        logger.info(f'[DRY RUN] Would reply as /u/{bot_name} to {reddit.full_url(source_comment.permalink)}:\n{text}')
        return None
    comment2 = get_comment_with_different_praw_instance(source_comment, bot_name)
    return comment2.reply(text)


def get_comment_with_different_praw_instance(comment, username):
    reddit2 = reddit.get_reddit_instance(username=username)
    comment2 = reddit2.comment(id=comment.id)
    return comment2
