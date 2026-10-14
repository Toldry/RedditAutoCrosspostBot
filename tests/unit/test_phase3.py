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


@pytest.mark.unit
def test_exec_crosspost_dry_run_does_not_crosspost(make_mock_comment):
    comment = make_mock_comment()
    with patch.dict('os.environ', {'DRY_RUN': 'true'}):
        res = phase3.exec_crosspost(comment, 'target_sub')
    comment.submission.crosspost.assert_not_called()
    assert res.success is False
    assert res.failure_reason == 'DRY_RUN'


@pytest.mark.unit
def test_process_comment_entries_keeps_rows_in_dry_run():
    entries = [{'id': 1, 'permalink': '/r/a/comments/1/t/c/', 'target_subreddit': 'b'}]
    with patch.dict('os.environ', {'DRY_RUN': 'true'}), \
         patch('racb.phases.phase3.db.get_comments_older_than', return_value=entries), \
         patch('racb.phases.phase3.handle_comment'), \
         patch('racb.phases.phase3.db.delete_comment') as mock_delete:
        phase3.process_comment_entries()
    mock_delete.assert_not_called()


@pytest.mark.unit
def test_reply_to_crosspost_formats_waiting_period(make_mock_comment):
    comment = make_mock_comment(subreddit='test_source')
    cross_post = MagicMock()
    with patch.dict('os.environ', {'PHASE3_WAITING_PERIOD': '3 months'}):
        phase3.reply_to_crosspost(comment, cross_post, 'pics')
    reply_text = cross_post.reply.call_args.args[0]
    assert 'written 90 days ago' in reply_text
    assert '3 months' not in reply_text
    assert 'repostsleuth' not in reply_text.lower()


@pytest.mark.unit
@pytest.mark.parametrize('target_subreddit', ['pics', 'ich_iel', 'moi_dlvv', 'yo_elvr', 'israel', 'totallynotrobots'])
def test_reply_to_crosspost_omits_waiting_and_downvote_notes(make_mock_comment, target_subreddit):
    comment = make_mock_comment(subreddit='test_source')
    cross_post = MagicMock()
    phase3.reply_to_crosspost(comment, cross_post, target_subreddit)
    reply_text = cross_post.reply.call_args.args[0].lower()
    for removed_phrase in ['waited', 'arbitrar', 'downvote', 'gewartet', 'esperé', 'attendu', 'חיכיתי', 'באקראי']:
        assert removed_phrase not in reply_text



@pytest.mark.unit
def test_process_oldest_entry_handles_and_deletes_row():
    entry = {'id': 7, 'permalink': '/r/a/comments/1/t/c/', 'scraped_time': None, 'target_subreddit': 'b'}
    with patch.dict('os.environ', {'DRY_RUN': 'false'}), \
         patch('racb.phases.phase3.db.get_oldest_comment', return_value=entry), \
         patch('racb.phases.phase3.handle_comment') as mock_handle, \
         patch('racb.phases.phase3.db.delete_comment') as mock_delete:
        assert phase3.process_oldest_entry() is True
    mock_handle.assert_called_once_with(entry)
    mock_delete.assert_called_once_with(entry)


@pytest.mark.unit
def test_process_oldest_entry_keeps_row_in_dry_run():
    entry = {'id': 7, 'permalink': '/r/a/comments/1/t/c/', 'scraped_time': None, 'target_subreddit': 'b'}
    with patch.dict('os.environ', {'DRY_RUN': 'true'}), \
         patch('racb.phases.phase3.db.get_oldest_comment', return_value=entry), \
         patch('racb.phases.phase3.handle_comment') as mock_handle, \
         patch('racb.phases.phase3.db.delete_comment') as mock_delete:
        assert phase3.process_oldest_entry() is True
    mock_handle.assert_called_once_with(entry)
    mock_delete.assert_not_called()


@pytest.mark.unit
def test_process_oldest_entry_reports_empty_table():
    with patch('racb.phases.phase3.db.get_oldest_comment', return_value=None), \
         patch('racb.phases.phase3.handle_comment') as mock_handle:
        assert phase3.process_oldest_entry() is False
    mock_handle.assert_not_called()
