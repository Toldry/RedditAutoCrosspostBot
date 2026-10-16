"""Unit tests for scripts/release_level.py."""

import pytest

from scripts.release_level import bump_level, has_pending_fragments

pytestmark = pytest.mark.unit


def test_fragment_is_releasable(tmp_path):
    (tmp_path / 'README.md').write_text('How to write a fragment.\n')
    (tmp_path / '+something.fixed.md').write_text('**Something:** fixed.\n')
    assert has_pending_fragments(tmp_path)


def test_readme_alone_is_not_releasable(tmp_path):
    (tmp_path / 'README.md').write_text('How to write a fragment.\n')
    assert not has_pending_fragments(tmp_path)


def test_dotfiles_and_subdirectories_are_not_fragments(tmp_path):
    (tmp_path / '.DS_Store').write_bytes(b'')
    (tmp_path / 'nested').mkdir()
    assert not has_pending_fragments(tmp_path)


def test_missing_fragment_directory_is_not_releasable(tmp_path):
    assert not has_pending_fragments(tmp_path / 'changelog.d')


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
