"""Checks the changelog fragments in changelog.d/ before they reach the release job.

A fragment towncrier cannot read (a misspelled type such as `+x.fix.md`) would only fail the release job
after merge, so catch it here instead.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.release_level import FRAGMENT_DIRECTORY, NON_FRAGMENT_FILES, PROJECT_ROOT

pytestmark = pytest.mark.unit


def fragment_paths() -> list[Path]:
    return sorted(
        path for path in FRAGMENT_DIRECTORY.iterdir()
        if path.is_file() and path.name not in NON_FRAGMENT_FILES and not path.name.startswith('.')
    )


def test_towncrier_accepts_every_fragment():
    result = subprocess.run(
        [sys.executable, '-m', 'towncrier', 'build', '--draft', '--version', '0.0.0'],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('fragment', fragment_paths(), ids=lambda path: path.name)
def test_fragment_is_one_paragraph(fragment):
    # towncrier renders each fragment as one list item; a blank line would end the item
    text = fragment.read_text(encoding='utf-8').strip()
    assert text, f'{fragment.name} is empty'
    assert '\n\n' not in text, f'{fragment.name} has more than one paragraph'
    assert not text.startswith(('- ', '* ')), f'{fragment.name}: towncrier adds the bullet itself'
