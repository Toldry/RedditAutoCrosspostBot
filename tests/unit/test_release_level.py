"""Unit tests for scripts/release_level.py."""

import pytest

from scripts.release_level import bump_level, unreleased_has_entries

pytestmark = pytest.mark.unit

CHANGELOG_HEADER = '# Changelog\n\nIntro text.\n\n'


def test_unreleased_with_entries_is_releasable():
    changelog = CHANGELOG_HEADER + '## [Unreleased]\n\n### Fixed\n- Something.\n\n## [2.3.0] - 2026-10-15\n\n- Old.\n'
    assert unreleased_has_entries(changelog)


def test_empty_unreleased_is_not_releasable():
    changelog = CHANGELOG_HEADER + '## [Unreleased]\n\n## [2.3.0] - 2026-10-15\n\n### Fixed\n- Old.\n'
    assert not unreleased_has_entries(changelog)


def test_empty_unreleased_at_end_of_file_is_not_releasable():
    assert not unreleased_has_entries(CHANGELOG_HEADER + '## [Unreleased]\n\n')


def test_missing_unreleased_heading_is_not_releasable():
    assert not unreleased_has_entries(CHANGELOG_HEADER + '## [2.3.0] - 2026-10-15\n\n- Old.\n')


def test_subsection_heading_does_not_end_unreleased_section():
    changelog = CHANGELOG_HEADER + '## [Unreleased]\n\n### Added\n- New thing.\n'
    assert unreleased_has_entries(changelog)


@pytest.mark.parametrize('messages, expected', [
    (['fix: handle 403'], 'patch'),
    (['docs: typo', 'chore: tidy'], 'patch'),
    (['Update README'], 'patch'),
    ([], 'patch'),
    (['fix: a', 'feat(i18n): add Spanish'], 'minor'),
    (['FEAT: shouting'], 'minor'),
    (['feat!: drop Python 3.11'], 'major'),
    (['refactor(db)!: rename columns'], 'major'),
    (['fix: a', 'feat: b\n\nBREAKING CHANGE: the env var X is gone'], 'major'),
    (['feat: b\n\nBREAKING-CHANGE: gone'], 'major'),
])
def test_bump_level(messages, expected):
    assert bump_level(messages) == expected


def test_breaking_change_mentioned_mid_line_is_not_a_footer():
    assert bump_level(['fix: avoid a BREAKING CHANGE: in the parser']) == 'patch'
