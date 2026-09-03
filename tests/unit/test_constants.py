"""Unit tests for racb.constants module."""

import pytest
from racb import constants


@pytest.mark.unit
def test_sub_blacklist_structure():
    assert isinstance(constants.SUB_BLACKLIST, list)
    assert len(constants.SUB_BLACKLIST) > 0
    assert 'all' in constants.SUB_BLACKLIST
    assert 'suddenlysexoffender' in constants.SUB_BLACKLIST
