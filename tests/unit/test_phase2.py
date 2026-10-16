"""Unit tests for racb.phases.phase2 module."""

from unittest.mock import patch, MagicMock, PropertyMock
import prawcore
import pytest
from racb.phases import phase2


@pytest.mark.unit
def test_chunk_list():
    items = list(range(10))
    chunks = list(phase2.chunk_list(items, 3))
    assert len(chunks) == 4
    assert chunks[0] == [0, 1, 2]
    assert chunks[1] == [3, 4, 5]
    assert chunks[2] == [6, 7, 8]
    assert chunks[3] == [9]


@pytest.mark.unit
def test_extract_comment_id_from_permalink():
    assert phase2.extract_comment_id_from_permalink('/r/test/comments/123/title/c456/') == 'c456'
    assert phase2.extract_comment_id_from_permalink('/r/test/comments/123/title/c456') == 'c456'
    assert phase2.extract_comment_id_from_permalink('') is None


@pytest.mark.unit
def test_run_filters_deleted_comment(make_mock_comment):
    comment_entry = {'permalink': '/r/test/comments/1/2/3/'}
    deleted_comment = make_mock_comment(body='[deleted]')
    res = phase2.run_filters(comment_entry, comment=deleted_comment)
    assert res.passes_filter is False
    assert res.reason == 'COMMENT_DELETED_OR_REMOVED'


@pytest.mark.unit
def test_run_filters_low_score(make_mock_comment):
    comment_entry = {'permalink': '/r/test/comments/1/2/3/'}
    low_score_comment = make_mock_comment(body='r/target_sub', score=-5)
    with patch.dict('os.environ', {'COMMENT_SCORE_THRESHOLD': '10'}):
        res = phase2.run_filters(comment_entry, comment=low_score_comment)
        assert res.passes_filter is False
        assert res.reason == 'COMMENT_SCORE_TOO_LOW'


@pytest.mark.unit
def test_run_filters_target_pattern_not_found(make_mock_comment):
    comment_entry = {'permalink': '/r/test/comments/1/2/3/'}
    edited_comment = make_mock_comment(body='I edited this comment and removed the sub name', score=100)
    with patch.dict('os.environ', {'COMMENT_SCORE_THRESHOLD': '10'}):
        res = phase2.run_filters(comment_entry, comment=edited_comment)
        assert res.passes_filter is False
        assert res.reason == 'TARGET_SUBREDDIT_NOT_FOUND'


@pytest.mark.unit
def test_run_filters_passes(make_mock_comment):
    comment_entry = {'permalink': '/r/test/comments/1/2/3/'}
    valid_comment = make_mock_comment(body='r/target_sub', score=100)

    mock_gpwsc = MagicMock()
    mock_gpwsc.posts_found = False
    mock_gpwsc.unable_to_search = False

    with patch.dict('os.environ', {'COMMENT_SCORE_THRESHOLD': '10'}):
        with patch('racb.phases.phase1.get_posts_with_same_content', return_value=mock_gpwsc):
            res = phase2.run_filters(comment_entry, comment=valid_comment)
            assert res.passes_filter is True
            assert res.target_subreddit == 'target_sub'


@pytest.mark.unit
def test_run_filters_rejects_comment_edited_to_point_elsewhere(make_mock_comment):
    comment_entry = {'permalink': '/r/test/comments/1/2/3/', 'target_subreddit': 'aww'}
    edited_comment = make_mock_comment(body='r/some_other_sub', score=500)
    with patch.dict('os.environ', {'COMMENT_SCORE_THRESHOLD': '10'}):
        with patch('racb.phases.phase1.get_posts_with_same_content') as mock_search:
            res = phase2.run_filters(comment_entry, comment=edited_comment)
            mock_search.assert_not_called()
    assert res.passes_filter is False
    assert res.reason == 'TARGET_SUBREDDIT_CHANGED'


@pytest.mark.unit
def test_run_filters_target_comparison_is_case_insensitive(make_mock_comment):
    comment_entry = {'permalink': '/r/test/comments/1/2/3/', 'target_subreddit': 'AWW'}
    comment = make_mock_comment(body='r/aww', score=500)
    mock_gpwsc = MagicMock(posts_found=False, unable_to_search=False)
    with patch.dict('os.environ', {'COMMENT_SCORE_THRESHOLD': '10'}):
        with patch('racb.phases.phase1.get_posts_with_same_content', return_value=mock_gpwsc):
            res = phase2.run_filters(comment_entry, comment=comment)
    assert res.passes_filter is True


@pytest.mark.unit
def test_full_media_scan_reads_back_to_the_comment_and_the_top_posts(make_mock_comment):
    comment_entry = {'permalink': '/r/test/comments/1/2/3/'}
    comment = make_mock_comment(body='r/target_sub', score=100)
    mock_gpwsc = MagicMock(posts_found=False, unable_to_search=False)
    with patch.dict('os.environ', {'COMMENT_SCORE_THRESHOLD': '10'}):
        with patch('racb.phases.phase1.get_posts_with_same_content', return_value=mock_gpwsc) as mock_search:
            phase2.run_filters(comment_entry, comment=comment, full_media_scan=True)
    assert mock_search.call_args.kwargs['media_scan_limit'] == 1000
    assert mock_search.call_args.kwargs['media_scan_top_limit'] == 100
    assert mock_search.call_args.kwargs['media_scan_top_time_filters'] == ('all', 'year')
    assert mock_search.call_args.kwargs['search_titles'] is True


def run_filters_on(comment, target_subreddit=None):
    comment_entry = {'permalink': '/r/test/comments/1/2/3/', 'target_subreddit': target_subreddit}
    mock_gpwsc = MagicMock(posts_found=False, unable_to_search=False)
    with patch.dict('os.environ', {'COMMENT_SCORE_THRESHOLD': '10'}):
        with patch('racb.phases.phase1.get_posts_with_same_content', return_value=mock_gpwsc) as mock_search:
            result = phase2.run_filters(comment_entry, comment=comment)
    return result, mock_search


@pytest.mark.unit
@pytest.mark.parametrize('source, target', [('pics', 'reviewsbyretards'), ('ReviewsByRetards', 'pics')])
def test_run_filters_rejects_rows_for_blacklisted_subreddits(make_mock_comment, source, target):
    comment = make_mock_comment(body=f'r/{target}', subreddit=source, score=100)
    result, mock_search = run_filters_on(comment, target)
    assert result.passes_filter is False
    assert result.reason == 'CROSSPOST_BLOCKED'
    mock_search.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize('source_state, reason', [
    ({'removed_by_category': 'moderator'}, 'SOURCE_DELETED_OR_REMOVED'),
    ({'removed_by_category': 'deleted'}, 'SOURCE_DELETED_OR_REMOVED'),
    ({'selftext': '[deleted]'}, 'SOURCE_DELETED_OR_REMOVED'),
    ({'selftext': '[removed]'}, 'SOURCE_DELETED_OR_REMOVED'),
    ({'locked': True}, 'SOURCE_LOCKED'),
    ({'archived': True}, 'SOURCE_ARCHIVED'),
])
def test_run_filters_rejects_dead_source_posts(make_mock_comment, make_mock_submission, source_state, reason):
    comment = make_mock_comment(body='r/target_sub', score=100, submission=make_mock_submission(**source_state))
    result, mock_search = run_filters_on(comment)
    assert result.passes_filter is False
    assert result.reason == reason
    mock_search.assert_not_called()


@pytest.mark.unit
def test_run_filters_rejects_a_source_post_it_cannot_read(make_mock_comment):
    comment = make_mock_comment(body='r/target_sub', score=100)
    comment.submission = MagicMock()
    type(comment.submission).removed_by_category = PropertyMock(
        side_effect=prawcore.exceptions.Forbidden(MagicMock(status_code=403)))
    result, _ = run_filters_on(comment)
    assert result.reason == 'SOURCE_UNAVAILABLE'


@pytest.mark.unit
def test_run_filters_passes_a_live_source_post_and_searches_titles(make_mock_comment):
    comment = make_mock_comment(body='r/target_sub', score=100)
    result, mock_search = run_filters_on(comment)
    assert result.passes_filter is True
    assert mock_search.call_args.kwargs['search_titles'] is True
