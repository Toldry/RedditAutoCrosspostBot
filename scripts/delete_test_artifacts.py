#!/usr/bin/env python3
"""Deletes the posts and comments that the live integration tests created, and nothing else.

Each integration test records every post and comment it creates in a manifest file as soon as it exists
(`record_artifact`), one JSON line per item with the bot that wrote it. This script reads the manifest,
deletes whatever is still there as the bot that wrote it, and removes the manifest once everything is gone.
It is fast because it only looks up the recorded items, unlike scripts/cleanup_test_history.py, which walks
the recent history of all 4 bots. CI runs it after the integration tests, even when they failed or were
cancelled, so items a killed test never deleted itself are removed too.

The manifest is $RACB_TEST_ARTIFACTS_FILE, or racb-test-artifacts.jsonl in the system temp directory.
"""

import argparse
import json
import logging
import os
import sys
import tempfile

# Ensure src/ directory is on sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(PROJECT_ROOT, 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from racb.core import reddit

logger = logging.getLogger(__name__)

MANIFEST_ENV_VAR = 'RACB_TEST_ARTIFACTS_FILE'
DEFAULT_MANIFEST_NAME = 'racb-test-artifacts.jsonl'
# reddit.info() accepts at most 100 fullnames per request
INFO_BATCH_SIZE = 100


def manifest_path() -> str:
    """Returns the path of the manifest the integration tests write to."""
    return os.environ.get(MANIFEST_ENV_VAR) or os.path.join(tempfile.gettempdir(), DEFAULT_MANIFEST_NAME)


def record_artifact(bot_name: str, fullname: str, path: str = None):
    """Appends one created post or comment to the manifest, flushed at once so a killed test run keeps it."""
    with open(path or manifest_path(), 'a', encoding='utf-8') as manifest:
        manifest.write(json.dumps({'bot': bot_name, 'fullname': fullname}) + '\n')


def read_manifest(path: str) -> dict:
    """Returns {bot name: [fullnames in recording order]} from the manifest, or {} if there is none."""
    fullnames_by_bot = {}
    if not os.path.exists(path):
        return fullnames_by_bot
    with open(path, encoding='utf-8') as manifest:
        for line_number, line in enumerate(manifest, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                bot_fullnames = fullnames_by_bot.setdefault(entry['bot'], [])
                if entry['fullname'] not in bot_fullnames:
                    bot_fullnames.append(entry['fullname'])
            except (ValueError, KeyError, TypeError):
                # A run killed mid-write can leave a truncated last line
                logger.warning(f'Ignoring malformed line {line_number} of {path}: {line.strip()!r}')
    return fullnames_by_bot


def delete_bot_artifacts(bot_name: str, fullnames: list, dry_run: bool, errors: list) -> int:
    """Deletes the recorded items of one bot that still exist. Returns how many were (or would be) deleted."""
    deleted_count = 0
    try:
        bot_reddit = reddit.get_reddit_instance(bot_name)
    except KeyError:
        errors.append(f'{len(fullnames)} item(s) recorded for unknown bot {bot_name!r}')
        return 0

    for start in range(0, len(fullnames), INFO_BATCH_SIZE):
        batch = fullnames[start:start + INFO_BATCH_SIZE]
        try:
            items = list(bot_reddit.info(fullnames=batch))
        except Exception as e:
            errors.append(f'could not look up {len(batch)} item(s) of /u/{bot_name}: {e}')
            continue
        for item in items:
            # A deleted post or comment loses its author; one that does not exist is missing from info()
            if item.author is None:
                continue
            if dry_run:
                logger.info(f'[DRY RUN] Would delete {item.fullname} by /u/{bot_name}')
                deleted_count += 1
                continue
            try:
                item.delete()
                logger.info(f'Deleted {item.fullname} by /u/{bot_name}')
                deleted_count += 1
            except Exception as e:
                errors.append(f'could not delete {item.fullname} by /u/{bot_name}: {e}')
    return deleted_count


def delete_recorded_artifacts(path: str = None, dry_run: bool = False) -> list:
    """Deletes every recorded item that still exists and returns the problems that may have left some behind.

    The manifest is removed when nothing went wrong, so the next test run starts with an empty one.
    """
    path = path or manifest_path()
    fullnames_by_bot = read_manifest(path)
    if not fullnames_by_bot:
        logger.info(f'No test artifacts recorded in {path}')
        return []

    errors = []
    deleted_count = 0
    for bot_name, fullnames in fullnames_by_bot.items():
        deleted_count += delete_bot_artifacts(bot_name, fullnames, dry_run, errors)

    recorded_count = sum(len(fullnames) for fullnames in fullnames_by_bot.values())
    action = 'Would delete' if dry_run else 'Deleted'
    logger.info(f'{action} {deleted_count} of {recorded_count} recorded test artifacts; the rest were already gone')
    if not errors and not dry_run:
        os.remove(path)
    return errors


def main():
    parser = argparse.ArgumentParser(description='Delete the posts and comments the integration tests recorded.')
    parser.add_argument('--manifest', help=f'Manifest to read (default: ${MANIFEST_ENV_VAR}, or {DEFAULT_MANIFEST_NAME} in the temp directory).')
    parser.add_argument('--dry-run', action='store_true', help='Only log what would be deleted.')
    parser.add_argument('--strict', action='store_true', help='Exit with code 1 if any recorded item could not be checked or deleted (used by CI).')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)-8s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    errors = delete_recorded_artifacts(args.manifest, dry_run=args.dry_run)
    if errors:
        logger.error(f'{len(errors)} problem(s) may have left test artifacts behind:')
        for error in errors:
            logger.error(f'  - {error}')
        if args.strict:
            sys.exit(1)


if __name__ == '__main__':
    main()
