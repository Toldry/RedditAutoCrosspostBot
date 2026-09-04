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


def purge_bot_history(bot_name: str, limit: int = None, dry_run: bool = False):
    """Purges test submissions and comments for a given bot account."""
    logger.info(f'=== Checking history for /u/{bot_name} ===')
    try:
        r = reddit.get_reddit_instance(bot_name)
        me = r.user.me()
        if me is None:
            logger.warning(f'Could not authenticate as /u/{bot_name}, skipping.')
            return 0, 0
    except (prawcore.exceptions.OAuthException, Exception) as e:
        logger.warning(f'Failed to authenticate /u/{bot_name}: {e}, skipping.')
        return 0, 0

    deleted_submissions = 0
    deleted_comments = 0

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
                    except Exception as e:
                        logger.error(f'Failed to delete submission {submission.id}: {e}')
    except Exception as e:
        logger.error(f'Error fetching submissions for /u/{bot_name}: {e}')

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
                    except Exception as e:
                        logger.error(f'Failed to delete comment {comment.id}: {e}')
    except Exception as e:
        logger.error(f'Error fetching comments for /u/{bot_name}: {e}')

    logger.info(f'Finished /u/{bot_name}: {deleted_submissions} submissions, {deleted_comments} comments {"identified" if dry_run else "deleted"}.\n')
    return deleted_submissions, deleted_comments


def purge_all_bots(limit: int = None, dry_run: bool = False):
    """Purges test artifacts across all 4 bots."""
    bot_names = [
        reddit.AUTO_CROSSPOST_BOT_NAME,
        reddit.SUB_DOESNT_EXIST_BOT_NAME,
        reddit.SAME_SUBREDDIT_BOT_NAME,
        reddit.SAME_POST_BOT_NAME,
    ]

    total_submissions = 0
    total_comments = 0

    for bot_name in bot_names:
        subs, comms = purge_bot_history(bot_name, limit=limit, dry_run=dry_run)
        total_submissions += subs
        total_comments += comms

    action = 'Would delete' if dry_run else 'Successfully deleted'
    logger.info(f'=== Summary: {action} {total_submissions} test submissions and {total_comments} test comments across all bots ===')


def main():
    parser = argparse.ArgumentParser(description='Delete test submissions and comments from all 4 Reddit bots.')
    parser.add_argument('--limit', type=int, default=100, help='Maximum number of items to inspect per category (default: 100, 0 or negative for all).')
    parser.add_argument('--dry-run', action='store_true', help='Preview items that would be deleted without actually deleting them.')
    args = parser.parse_args()

    limit = None if args.limit <= 0 else args.limit
    purge_all_bots(limit=limit, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
