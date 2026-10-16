"""Unit tests for racb.phases.phase3 module."""

import time
from unittest.mock import patch, MagicMock
import pytest
import praw.exceptions
from racb import i18n
from racb.core import duplicate_detector
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
        mock_reply.assert_called_once_with(comment, mock_crosspost, 'target_sub', media_scan=None)


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
@pytest.mark.parametrize('target_subreddit', ['pics', 'ich_iel', 'moi_dlvv', 'yo_elvr', 'israel', 'pikabu', 'totallynotrobots'])
def test_reply_to_crosspost_asks_for_bad_bot_feedback_in_its_language(make_mock_comment, target_subreddit):
    comment = make_mock_comment(subreddit='test_source')
    cross_post = MagicMock()
    phase3.reply_to_crosspost(comment, cross_post, target_subreddit)
    reply_text = cross_post.reply.call_args.args[0]
    bad_bot_phrase = i18n.get_translated_string('BAD_BOT_PHRASE', target_subreddit, add_suffix=False)
    assert bad_bot_phrase.lower() in reply_text.lower()
    assert 'downvote' not in reply_text.lower()
    assert '^1' in reply_text
    for removed_footnote in ['^2', '^(2', '^3', '^(3']:
        assert removed_footnote not in reply_text


def make_media_scan(posts_checked=0, complete=True, oldest_post_utc=None, top_posts_checked=0):
    return duplicate_detector.RepostScan(
        posts_checked=posts_checked, complete=complete, oldest_post_utc=oldest_post_utc,
        top_posts_checked=top_posts_checked,
    )


@pytest.mark.unit
def test_reply_to_crosspost_without_image_scan_has_no_image_sentence(make_mock_comment):
    comment = make_mock_comment(subreddit='test_source')
    cross_post = MagicMock()
    phase3.reply_to_crosspost(comment, cross_post, 'pics', media_scan=None)
    reply_text = cross_post.reply.call_args.args[0]
    assert 'To prevent reposts, I looked through r/pics for posts with the same URL.\n' in reply_text
    assert 'duplicate_detector.py' not in reply_text


@pytest.mark.unit
def test_reply_to_crosspost_reports_complete_image_scan(make_mock_comment):
    comment = make_mock_comment(subreddit='test_source')
    comment.created_utc = time.time() - 90 * 86400
    cross_post = MagicMock()
    phase3.reply_to_crosspost(comment, cross_post, 'pics', media_scan=make_media_scan(412))
    reply_text = cross_post.reply.call_args.args[0]
    assert (
        'I also looked through all 412 image posts made in r/pics in the last 90 days and '
        f'[checked for duplicates]({phase3.DUPLICATE_DETECTOR_SOURCE_URL}) of this image. None were found.'
    ) in reply_text


@pytest.mark.unit
def test_reply_to_crosspost_reports_partial_image_scan(make_mock_comment):
    comment = make_mock_comment(subreddit='test_source')
    comment.created_utc = time.time() - 90 * 86400
    scan = make_media_scan(870, complete=False, oldest_post_utc=time.time() - 12 * 86400)
    cross_post = MagicMock()
    phase3.reply_to_crosspost(comment, cross_post, 'pics', media_scan=scan)
    reply_text = cross_post.reply.call_args.args[0]
    assert 'the 870 newest image posts in r/pics, going back 12 days' in reply_text
    assert "Reddit doesn't list older posts" not in reply_text


@pytest.mark.unit
def test_reply_to_crosspost_says_nothing_when_there_were_no_posts_to_compare(make_mock_comment):
    comment = make_mock_comment(subreddit='test_source')
    comment.created_utc = time.time() - 90 * 86400
    cross_post = MagicMock()
    phase3.reply_to_crosspost(comment, cross_post, 'pics', media_scan=make_media_scan(0))
    reply_text = cross_post.reply.call_args.args[0]
    assert phase3.DUPLICATE_DETECTOR_SOURCE_URL not in reply_text


@pytest.mark.unit
@pytest.mark.parametrize('target_subreddit', ['pics', 'ich_iel', 'moi_dlvv', 'yo_elvr', 'israel', 'pikabu', 'totallynotrobots'])
@pytest.mark.parametrize('scan', [
    make_media_scan(1), make_media_scan(2), make_media_scan(5),
    make_media_scan(1, complete=False, oldest_post_utc=0), make_media_scan(21, complete=False, oldest_post_utc=0),
])
def test_image_scan_sentence_formats_in_every_language(make_mock_comment, target_subreddit, scan):
    comment = make_mock_comment(subreddit='test_source')
    comment.created_utc = time.time() - 90 * 86400
    sentence = phase3.describe_media_scan(scan, comment, target_subreddit)
    assert sentence.startswith(' ')
    assert phase3.DUPLICATE_DETECTOR_SOURCE_URL in sentence
    assert '{' not in sentence


@pytest.mark.unit
@pytest.mark.parametrize('target_subreddit', ['pics', 'ich_iel', 'moi_dlvv', 'yo_elvr', 'israel', 'pikabu', 'totallynotrobots'])
def test_reply_to_crosspost_marks_source_comment_as_human_made(make_mock_comment, target_subreddit):
    comment = make_mock_comment(subreddit='test_source')
    cross_post = MagicMock()
    phase3.reply_to_crosspost(comment, cross_post, target_subreddit)
    reply_text = cross_post.reply.call_args.args[0]
    assert '**^1' in reply_text
    assert '^(1 - ' in reply_text
    assert f'/u/{comment.author.name}' in reply_text.split('^(1 - ')[1].split(')')[0]



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


@pytest.mark.unit
def test_image_scan_sentence_counts_only_posts_since_the_comment(make_mock_comment):
    comment = make_mock_comment(subreddit='test_source')
    comment.created_utc = time.time() - 90 * 86400
    sentence = phase3.describe_media_scan(make_media_scan(150, top_posts_checked=130), comment, 'pics')
    assert '20' in sentence
    assert '150' not in sentence
    assert phase3.describe_media_scan(make_media_scan(30, top_posts_checked=30), comment, 'pics') == ''
