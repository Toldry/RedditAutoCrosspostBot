"""Unit tests for racb.phases.phase1 module."""

from unittest.mock import patch, MagicMock
import pytest
from racb.phases import phase1


@pytest.mark.unit
def test_check_pattern(make_mock_comment):
    # Valid patterns
    assert phase1.check_pattern(make_mock_comment(body='r/funny')) == 'funny'
    assert phase1.check_pattern(make_mock_comment(body='/r/funny')) == 'funny'
    assert phase1.check_pattern(make_mock_comment(body='r/ProgrammerHumor')) == 'ProgrammerHumor'
    assert phase1.check_pattern(make_mock_comment(body='r/sub_with_underscores_1')) == 'sub_with_underscores_1'

    # Invalid patterns
    assert phase1.check_pattern(make_mock_comment(body='check out r/funny')) is None
    assert phase1.check_pattern(make_mock_comment(body='r/')) is None
    assert phase1.check_pattern(make_mock_comment(body='r/a')) is None  # < 2 chars
    assert phase1.check_pattern(make_mock_comment(body='r/this_subreddit_name_is_way_too_long_for_reddit')) is None
    assert phase1.check_pattern(make_mock_comment(body='just some text')) is None


@pytest.mark.unit
def test_is_mod_post(make_mock_comment):
    mod_comment = make_mock_comment(distinguished='moderator')
    regular_comment = make_mock_comment(distinguished=None)
    assert phase1.is_mod_post(mod_comment) is True
    assert phase1.is_mod_post(regular_comment) is False


@pytest.mark.unit
def test_is_top_level_comment(make_mock_comment):
    top_level = make_mock_comment(parent_id='t3_sub123', link_id='t3_sub123')
    nested = make_mock_comment(parent_id='t1_c999', link_id='t3_sub123')
    assert phase1.is_top_level_comment(top_level) is True
    assert phase1.is_top_level_comment(nested) is False


@pytest.mark.unit
def test_title_contains_prohibited_phrases(make_mock_comment, make_mock_submission):
    bad_title_sub = make_mock_submission(title='What is your favorite sub to browse?')
    good_title_sub = make_mock_submission(title='A very interesting cat photo')

    assert phase1.title_contains_prohibited_phrases(make_mock_comment(submission=bad_title_sub)) is True
    assert phase1.title_contains_prohibited_phrases(make_mock_comment(submission=good_title_sub)) is False


@pytest.mark.unit
def test_is_subreddit_name_length_valid():
    assert phase1.is_subreddit_name_length_valid('abc') is True
    assert phase1.is_subreddit_name_length_valid('a' * 24) is True
    assert phase1.is_subreddit_name_length_valid('ab') is False
    assert phase1.is_subreddit_name_length_valid('a' * 25) is False


@pytest.mark.unit
def test_handle_incoming_comment_routes_same_sub(make_mock_comment):
    comment = make_mock_comment(body='r/test_source', subreddit='test_source')
    with patch('racb.phases.phase1.reply_to_source_equals_target_comment') as mock_reply:
        phase1.handle_incoming_comment(comment)
        mock_reply.assert_called_once_with(comment)


@pytest.mark.unit
def test_handle_incoming_comment_routes_duplicate_post(make_mock_comment):
    comment = make_mock_comment(body='r/target_sub', subreddit='test_source')
    mock_duplicate_res = MagicMock()
    mock_duplicate_res.posts_found = True
    mock_duplicate_res.posts = [MagicMock(permalink='/r/target_sub/comments/dup1/')]

    with patch('racb.phases.phase1.get_posts_with_same_content', return_value=mock_duplicate_res):
        with patch('racb.phases.phase1.reply_to_same_content_post_comment') as mock_reply:
            phase1.handle_incoming_comment(comment)
            mock_reply.assert_called_once()


@pytest.mark.unit
def test_handle_incoming_comment_routes_nonexistent_sub(make_mock_comment):
    comment = make_mock_comment(body='r/nonexistent_sub_xyz', subreddit='test_source')
    mock_res = MagicMock()
    mock_res.posts_found = False
    mock_res.unable_to_search = True
    mock_res.unable_to_search_reason = 'SUBREDDIT_DOES_NOT_EXIST'

    with patch('racb.phases.phase1.get_posts_with_same_content', return_value=mock_res):
        with patch('racb.phases.phase1.reply_to_nonexistent_target_subreddit_comment') as mock_reply:
            phase1.handle_incoming_comment(comment)
            mock_reply.assert_called_once_with(comment, 'nonexistent_sub_xyz')


@pytest.mark.unit
def test_handle_incoming_comment_adds_qualified_recommendation(make_mock_comment):
    comment = make_mock_comment(body='r/target_sub', subreddit='test_source')
    mock_res = MagicMock()
    mock_res.posts_found = False
    mock_res.unable_to_search = False

    with patch('racb.phases.phase1.get_posts_with_same_content', return_value=mock_res):
        with patch('racb.core.db.add_comment') as mock_db_add:
            phase1.handle_incoming_comment(comment)
            mock_db_add.assert_called_once_with(comment)
