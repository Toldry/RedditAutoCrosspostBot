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



@pytest.mark.unit
def test_delete_unwanted_submissions_dry_run():
    sub_negative = MagicMock(score=-2, permalink='/r/test/comments/1/')
    with patch.dict('os.environ', {'DRY_RUN': 'true'}):
        with patch('racb.phases.cleanup.get_latest_submissions', return_value=[sub_negative]):
            cleanup.delete_unwanted_submissions()
    sub_negative.delete.assert_not_called()


@pytest.mark.unit
def test_delete_unwanted_submissions_continues_after_delete_error():
    import prawcore
    failing = MagicMock(score=-1, permalink='/r/test/comments/1/')
    failing.delete.side_effect = prawcore.exceptions.Forbidden(MagicMock())
    other = MagicMock(score=-1, permalink='/r/test/comments/2/')
    with patch('racb.phases.cleanup.get_latest_submissions', return_value=[failing, other]):
        cleanup.delete_unwanted_submissions()
    other.delete.assert_called_once()


@pytest.mark.unit
def test_praw_delete_posts_to_api_del_without_trailing_slash():
    """Reddit 404s on `POST /api/del/`; importing racb.core.reddit must make PRAW use `api/del`."""
    import praw
    import racb.core.reddit  # noqa: F401  (applies the endpoint patch)

    reddit_instance = praw.Reddit(client_id='id', client_secret='secret', user_agent='test', username='u', password='p')
    with patch.object(reddit_instance, 'post') as mock_post:
        reddit_instance.submission('abc123').delete()
        reddit_instance.comment('def456').delete()

    assert [call.args[0] for call in mock_post.call_args_list] == ['api/del', 'api/del']
    assert mock_post.call_args_list[0].kwargs == {'data': {'id': 't3_abc123'}}
    assert mock_post.call_args_list[1].kwargs == {'data': {'id': 't1_def456'}}
