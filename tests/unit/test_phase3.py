"""Unit tests for racb.phases.phase3 module."""

from unittest.mock import patch, MagicMock
import pytest
import praw.exceptions
from racb.phases import phase3


@pytest.mark.unit
def test_get_crosspost_title_for_crosspost(make_mock_submission):
    sub = make_mock_submission(title='Look at this normal human behavior')
    assert phase3.get_crosspost_title_for_crosspost(sub, 'totallynotrobots') == 'LOOK AT THIS NORMAL HUMAN BEHAVIOR'
    assert phase3.get_crosspost_title_for_crosspost(sub, 'funny') is None


@pytest.mark.unit
def test_handle_crosspost_exception_familiar():
    api_exc = praw.exceptions.RedditAPIException([['NO_CROSSPOSTS', 'No crossposts allowed', 'subreddit']])
    res = phase3.handle_crosspost_exception(api_exc, None, 'some_sub')
    assert res.handled_with_grace is True
    assert res.error_type == 'NO_CROSSPOSTS'


@pytest.mark.unit
def test_handle_crosspost_exception_unfamiliar():
    api_exc = praw.exceptions.RedditAPIException([['COMPLETELY_UNEXPECTED_ERROR', 'Unexpected', 'field']])
    res = phase3.handle_crosspost_exception(api_exc, None, 'some_sub')
    assert res.handled_with_grace is False
    assert res.error_type is None


@pytest.mark.unit
def test_exec_crosspost_success(make_mock_comment):
    comment = make_mock_comment()
    mock_crosspost = MagicMock(permalink='/r/target_sub/comments/crosspost_123/')
    comment.submission.crosspost.return_value = mock_crosspost

    with patch('racb.phases.phase3.reply_to_crosspost') as mock_reply:
        res = phase3.exec_crosspost(comment, 'target_sub', reply_to_crosspost_flag=True)
        assert res.success is True
        assert res.crosspost == mock_crosspost
        mock_reply.assert_called_once_with(comment, mock_crosspost, 'target_sub')


@pytest.mark.unit
def test_exec_crosspost_graceful_failure(make_mock_comment):
    comment = make_mock_comment()
    api_exc = praw.exceptions.RedditAPIException([['SUBREDDIT_NOTALLOWED', 'Not allowed', 'subreddit']])
    comment.submission.crosspost.side_effect = api_exc

    res = phase3.exec_crosspost(comment, 'target_sub', reply_to_crosspost_flag=False)
    assert res.success is False
    assert res.failure_reason == 'SUBREDDIT_NOTALLOWED'
