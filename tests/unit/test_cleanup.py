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


@pytest.mark.unit
def test_integration_cleanup_reddit_objects(monkeypatch):
    """Verifies that cleanup_reddit_objects deletes objects when enabled and skips when disabled."""
    from tests.integration.test_reddit_integration import cleanup_reddit_objects
    import tests.integration.test_reddit_integration as tri

    mock_sub = MagicMock()
    mock_comment = MagicMock()
    mock_nested = [MagicMock(), MagicMock()]

    # Test when cleanup is enabled (DISABLE_CLEANUP = False)
    monkeypatch.setattr(tri, 'DISABLE_CLEANUP', False)
    cleanup_reddit_objects(mock_sub, mock_comment, mock_nested, None)

    mock_sub.delete.assert_called_once()
    mock_comment.delete.assert_called_once()
    for item in mock_nested:
        item.delete.assert_called_once()

    # Test when cleanup is disabled (DISABLE_CLEANUP = True)
    mock_sub2 = MagicMock()
    monkeypatch.setattr(tri, 'DISABLE_CLEANUP', True)
    cleanup_reddit_objects(mock_sub2)
    mock_sub2.delete.assert_not_called()

