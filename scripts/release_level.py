#!/usr/bin/env python3
"""Decide which version bump, if any, the commits merged since the last release call for.

Run by the `release` job in .github/workflows/tests.yml after every push to master. Prints
`major`, `minor`, `patch` or `none`:

- `none` when CHANGELOG.md has nothing under `## [Unreleased]` (nothing to release).
- `major` when a commit since the last `v*` tag is a breaking Conventional Commit
  (`feat!: ...`, `fix(scope)!: ...`, or a `BREAKING CHANGE:` footer).
- `minor` when one is a `feat`.
- `patch` otherwise, including commits that do not follow Conventional Commits.
"""

import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG_PATH = PROJECT_ROOT / 'CHANGELOG.md'

UNRELEASED_HEADING = '## [Unreleased]'
# `type(scope)!: subject`; the scope and the `!` are optional
CONVENTIONAL_SUBJECT = re.compile(r'^(?P<type>[a-zA-Z]+)(\([^)]*\))?(?P<breaking>!)?:')
BREAKING_FOOTER = re.compile(r'^BREAKING[ -]CHANGE:', re.MULTILINE)
# Commits made by a previous run of the release job
RELEASE_COMMIT_PREFIX = 'chore(release):'


def unreleased_has_entries(changelog_text: str) -> bool:
    """Return True if anything but blank lines sits between `## [Unreleased]` and the next heading."""
    _, found, after_heading = changelog_text.partition(UNRELEASED_HEADING)
    if not found:
        return False
    unreleased_section = re.split(r'^## ', after_heading, maxsplit=1, flags=re.MULTILINE)[0]
    return unreleased_section.strip() != ''


def bump_level(commit_messages: list[str]) -> str:
    """Return the SemVer part to bump for these full commit messages (subject and body)."""
    level = 'patch'
    for message in commit_messages:
        subject = message.strip().splitlines()[0] if message.strip() else ''
        match = CONVENTIONAL_SUBJECT.match(subject)
        if (match and match.group('breaking')) or BREAKING_FOOTER.search(message):
            return 'major'
        if match and match.group('type').lower() == 'feat':
            level = 'minor'
    return level


def commit_messages_since_last_release() -> list[str]:
    """Full messages of the non-merge commits after the latest `v*` tag (all commits if untagged)."""
    describe = subprocess.run(
        ['git', 'describe', '--tags', '--abbrev=0', '--match', 'v*'],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    revision_range = f'{describe.stdout.strip()}..HEAD' if describe.returncode == 0 else 'HEAD'
    log = subprocess.run(
        ['git', 'log', '--no-merges', '--format=%B%x00', revision_range],
        capture_output=True, text=True, check=True, cwd=PROJECT_ROOT,
    )
    messages = [message.strip() for message in log.stdout.split('\0')]
    return [message for message in messages if message and not message.startswith(RELEASE_COMMIT_PREFIX)]


def main() -> int:
    if not unreleased_has_entries(CHANGELOG_PATH.read_text(encoding='utf-8')):
        print('none')
        return 0
    print(bump_level(commit_messages_since_last_release()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
