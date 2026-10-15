#!/usr/bin/env python3
"""Cleanup script to query the comment and submission history of all 4 RACB bots and purge test artifacts."""

import argparse
import logging
import os
import sys

# Ensure src/ directory is on sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(PROJECT_ROOT, 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import praw
import prawcore
from racb.core import reddit

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)-8s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

TEST_SUBREDDITS = {'racb_test_1', 'racb_test_2'}
TEST_SUBREDDIT_PREFIXES = ('racb_test_', 'racb_fake_')
TEST_PATTERNS = ('[test', 'racb_test_', 'racb_fake_')


def is_test_subreddit(sub_name: str) -> bool:
    """Checks if a subreddit name belongs to test environments."""
    if not sub_name:
        return False
    sub_lower = sub_name.lower()
    if sub_lower in TEST_SUBREDDITS:
        return True
    return any(sub_lower.startswith(prefix) for prefix in TEST_SUBREDDIT_PREFIXES)


def is_test_submission(submission) -> bool:
    """Checks if a submission was created for integration testing."""
    try:
        sub_name = submission.subreddit.display_name.lower()
        if is_test_subreddit(sub_name):
            return True
    except Exception:
        pass

    try:
        title = (submission.title or '').lower()
        if any(pattern in title for pattern in TEST_PATTERNS):
            return True
    except Exception:
        pass

    return False


def is_test_comment(comment) -> bool:
    """Checks if a comment was created during integration testing."""
    try:
        sub_name = comment.subreddit.display_name.lower()
        if is_test_subreddit(sub_name):
            return True
    except Exception:
        pass

    try:
        body = (comment.body or '').lower()
        if any(pattern in body for pattern in TEST_PATTERNS):
            return True
    except Exception:
        pass

    try:
        sub_title = (comment.submission.title or '').lower()
        if any(pattern in sub_title for pattern in TEST_PATTERNS):
            return True
    except Exception:
        pass

    return False


def _is_already_deleted(thing) -> bool:
    """Checks whether a submission or comment fetched from Reddit was deleted by its author."""
    text = getattr(thing, 'body', None)
    if text is None:
        text = getattr(thing, 'selftext', None)
    return thing.author is None and text == '[deleted]'


def _subreddit_status(reddit_instance, sub_name: str) -> str:
    """Describes whether the authenticated account can still open a subreddit."""
    try:
        subreddit_type = reddit_instance.subreddit(sub_name).subreddit_type
    except prawcore.exceptions.Forbidden:
        return f'r/{sub_name} is private and this account cannot see it (not an approved user?)'
    except (prawcore.exceptions.NotFound, prawcore.exceptions.Redirect):
        return f'r/{sub_name} is banned or no longer exists'
    except (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException) as e:
        return f'r/{sub_name} could not be checked ({e})'
    return f'r/{sub_name} is reachable ({subreddit_type})'


def _account_status(reddit_instance, bot_name: str) -> str:
    """Describes whether a bot account is visible to logged-out readers.

    Shadowbanned and suspended accounts can still log in, but Reddit hides their profile and
    content from everyone else, which also explains 404s on their own items.
    """
    previous_read_only = reddit_instance.read_only
    try:
        # A script app may switch to an app-only token, which sees Reddit like a logged-out reader
        reddit_instance.read_only = True
        if getattr(reddit_instance.redditor(bot_name), 'is_suspended', False):
            return f'/u/{bot_name} is suspended'
    except prawcore.exceptions.NotFound:
        return f'/u/{bot_name} is invisible to logged-out readers (shadowbanned or suspended)'
    except (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException) as e:
        return f'/u/{bot_name} visibility could not be checked ({e})'
    finally:
        reddit_instance.read_only = previous_read_only
    return f'/u/{bot_name} is visible to logged-out readers'


def diagnose_not_found(reddit_instance, thing, bot_name: str, account_status_cache: dict) -> bool:
    """Logs why Reddit answered 404 to deleting `thing`.

    Returns:
        True if the item turns out to be deleted already, so the caller can count it as deleted.
    """
    try:
        refetched = next(iter(reddit_instance.info(fullnames=[thing.fullname])), None)
    except (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException) as e:
        logger.warning(f'  Could not re-fetch {thing.fullname}: {e}')
        refetched = None
    else:
        if refetched is None:
            logger.warning(f'  Reddit no longer returns {thing.fullname} at all')
        elif _is_already_deleted(refetched):
            logger.info(f'  {thing.fullname} was already deleted; counting it as deleted')
            return True
        elif getattr(refetched, 'removed_by_category', None):
            logger.warning(f'  {thing.fullname} was removed (removed_by_category={refetched.removed_by_category})')

    try:
        sub_name = thing.subreddit.display_name
    except Exception:
        sub_name = None
    if sub_name:
        logger.warning(f'  {_subreddit_status(reddit_instance, sub_name)}')

    if bot_name not in account_status_cache:
        account_status_cache[bot_name] = _account_status(reddit_instance, bot_name)
    logger.warning(f'  {account_status_cache[bot_name]}')
    return False


def purge_bot_history(bot_name: str, limit: int = None, dry_run: bool = False, errors: list = None):
    """Purges test submissions and comments for a given bot account.

    Every failure that may leave test artifacts behind is also appended to `errors`, when given.
    """
    if errors is None:
        errors = []
    logger.info(f'=== Checking history for /u/{bot_name} ===')
    try:
        r = reddit.get_reddit_instance(bot_name)
        me = r.user.me()
        if me is None:
            logger.warning(f'Could not authenticate as /u/{bot_name}, skipping.')
            errors.append(f'could not authenticate as /u/{bot_name}')
            return 0, 0
    except (prawcore.exceptions.OAuthException, Exception) as e:
        logger.warning(f'Failed to authenticate /u/{bot_name}: {e}, skipping.')
        errors.append(f'failed to authenticate /u/{bot_name}: {e}')
        return 0, 0

    deleted_submissions = 0
    deleted_comments = 0
    # The account check is the same for every item, so it runs at most once per bot
    account_status_cache = {}

    # 1. Clean up Submissions
    try:
        for submission in me.submissions.new(limit=limit):
            if is_test_submission(submission):
                sub_desc = f'r/{submission.subreddit.display_name} "{submission.title}" (id: {submission.id})'
                if dry_run:
                    logger.info(f'[DRY RUN] Would delete submission in {sub_desc}')
                    deleted_submissions += 1
                else:
                    try:
                        submission.delete()
                        logger.info(f'Deleted submission in {sub_desc}')
                        deleted_submissions += 1
                    except prawcore.exceptions.NotFound as e:
                        logger.error(f'Failed to delete submission {submission.id}: {e}')
                        if diagnose_not_found(r, submission, bot_name, account_status_cache):
                            deleted_submissions += 1
                        else:
                            errors.append(f'could not delete submission {submission.id}: {e}')
                    except Exception as e:
                        logger.error(f'Failed to delete submission {submission.id}: {e}')
                        errors.append(f'could not delete submission {submission.id}: {e}')
    except Exception as e:
        logger.error(f'Error fetching submissions for /u/{bot_name}: {e}')
        errors.append(f'could not list the submissions of /u/{bot_name}: {e}')

    # 2. Clean up Comments
    try:
        for comment in me.comments.new(limit=limit):
            if is_test_comment(comment):
                comment_desc = f'r/{comment.subreddit.display_name} "{comment.body[:40]}" (id: {comment.id})'
                if dry_run:
                    logger.info(f'[DRY RUN] Would delete comment in {comment_desc}')
                    deleted_comments += 1
                else:
                    try:
                        comment.delete()
                        logger.info(f'Deleted comment in {comment_desc}')
                        deleted_comments += 1
                    except prawcore.exceptions.NotFound as e:
                        logger.error(f'Failed to delete comment {comment.id}: {e}')
                        if diagnose_not_found(r, comment, bot_name, account_status_cache):
                            deleted_comments += 1
                        else:
                            errors.append(f'could not delete comment {comment.id}: {e}')
                    except Exception as e:
                        logger.error(f'Failed to delete comment {comment.id}: {e}')
                        errors.append(f'could not delete comment {comment.id}: {e}')
    except Exception as e:
        logger.error(f'Error fetching comments for /u/{bot_name}: {e}')
        errors.append(f'could not list the comments of /u/{bot_name}: {e}')

    logger.info(f'Finished /u/{bot_name}: {deleted_submissions} submissions, {deleted_comments} comments {"identified" if dry_run else "deleted"}.\n')
    return deleted_submissions, deleted_comments


def purge_all_bots(limit: int = None, dry_run: bool = False) -> list:
    """Purges test artifacts across all 4 bots and returns the failures that may have left some behind."""
    bot_names = [
        reddit.AUTO_CROSSPOST_BOT_NAME,
        reddit.SUB_DOESNT_EXIST_BOT_NAME,
        reddit.SAME_SUBREDDIT_BOT_NAME,
        reddit.SAME_POST_BOT_NAME,
    ]

    total_submissions = 0
    total_comments = 0
    errors = []

    for bot_name in bot_names:
        subs, comms = purge_bot_history(bot_name, limit=limit, dry_run=dry_run, errors=errors)
        total_submissions += subs
        total_comments += comms

    action = 'Would delete' if dry_run else 'Successfully deleted'
    logger.info(f'=== Summary: {action} {total_submissions} test submissions and {total_comments} test comments across all bots ===')
    return errors


def main():
    parser = argparse.ArgumentParser(description='Delete test submissions and comments from all 4 Reddit bots.')
    parser.add_argument('--limit', type=int, default=100, help='Maximum number of items to inspect per category (default: 100, 0 or negative for all).')
    parser.add_argument('--dry-run', action='store_true', help='Preview items that would be deleted without actually deleting them.')
    parser.add_argument('--strict', action='store_true', help='Exit with code 1 if any bot could not be checked or any test item could not be deleted (used by CI before deploying).')
    args = parser.parse_args()

    limit = None if args.limit <= 0 else args.limit
    errors = purge_all_bots(limit=limit, dry_run=args.dry_run)
    if errors:
        logger.error(f'{len(errors)} problem(s) may have left test artifacts behind:')
        for error in errors:
            logger.error(f'  - {error}')
        if args.strict:
            sys.exit(1)


if __name__ == '__main__':
    main()
