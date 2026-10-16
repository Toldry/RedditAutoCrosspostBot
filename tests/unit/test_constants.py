"""Unit tests for racb.constants module."""

import pytest
from racb import constants


@pytest.mark.unit
def test_sub_blacklist_structure():
    assert isinstance(constants.SUB_BLACKLIST, list)
    assert len(constants.SUB_BLACKLIST) > 0
    assert 'all' in constants.SUB_BLACKLIST
    assert 'suddenlysexoffender' in constants.SUB_BLACKLIST


@pytest.mark.unit
@pytest.mark.parametrize('source, target, blocked', [
    ('pics', 'aww', False),
    ('disneyvacation', 'notdisneyvacation', False),
    ('ReviewsByRetards', 'pics', True),
    ('reviewsbyretards', 'pics', True),  # SUB_BLACKLIST entries with capitals match too
    ('pics', 'reviewsbyretards', True),
])
def test_is_crosspost_blocked(source, target, blocked):
    assert constants.is_crosspost_blocked(source, target) is blocked
