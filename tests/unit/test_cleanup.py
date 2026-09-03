"""Unit tests for racb.phases.cleanup module."""

from unittest.mock import patch, MagicMock
import pytest
from racb.phases import cleanup


@pytest.mark.unit
def test_delete_unwanted_submissions():
    sub_negative = MagicMock(score=-2, permalink='/r/test/comments/1/')
    sub_zero = MagicMock(score=0, permalink='/r/test/comments/2/')
    sub_positive = MagicMock(score=10, permalink='/r/test/comments/3/')

    with patch('racb.phases.cleanup.get_latest_submissions', return_value=[sub_negative, sub_zero, sub_positive]):
        cleanup.delete_unwanted_submissions()

        sub_negative.delete.assert_called_once()
        sub_zero.delete.assert_not_called()
        sub_positive.delete.assert_not_called()
