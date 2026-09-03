"""Unit tests for racb.phases.phase2 module."""

from unittest.mock import patch, MagicMock
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
