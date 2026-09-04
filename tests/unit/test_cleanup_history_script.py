"""Unit tests for scripts/cleanup_test_history.py."""

from unittest.mock import MagicMock, patch
import pytest

from scripts.cleanup_test_history import (
    is_test_subreddit,
    is_test_submission,
    is_test_comment,
    purge_bot_history,
    purge_all_bots,
)


@pytest.mark.unit
def test_is_test_subreddit():
    assert is_test_subreddit('racb_test_1') is True
    assert is_test_subreddit('racb_test_2') is True
    assert is_test_subreddit('RACB_TEST_1') is True
    assert is_test_subreddit('racb_test_custom') is True
    assert is_test_subreddit('racb_fake_1234') is True
    assert is_test_subreddit('askreddit') is False
    assert is_test_subreddit('all') is False
    assert is_test_subreddit('') is False
    assert is_test_subreddit(None) is False


@pytest.mark.unit
def test_is_test_submission():
    # Test subreddit match
    sub1 = MagicMock()
    sub1.subreddit.display_name = 'racb_test_1'
    sub1.title = 'Normal title'
    assert is_test_submission(sub1) is True

    # Test title match
    sub2 = MagicMock()
    sub2.subreddit.display_name = 'AskReddit'
    sub2.title = '[Test] Same Sub Test 1234'
    assert is_test_submission(sub2) is True

    # Non-test submission
    sub3 = MagicMock()
    sub3.subreddit.display_name = 'funny'
    sub3.title = 'A funny meme'
    assert is_test_submission(sub3) is False


@pytest.mark.unit
def test_is_test_comment():
    # Test subreddit match
    c1 = MagicMock()
    c1.subreddit.display_name = 'racb_test_2'
    c1.body = 'regular comment'
    c1.submission.title = 'regular post'
    assert is_test_comment(c1) is True

    # Test body match
    c2 = MagicMock()
    c2.subreddit.display_name = 'funny'
    c2.body = 'r/racb_test_1'
    c2.submission.title = 'funny post'
    assert is_test_comment(c2) is True

    # Test parent submission title match
    c3 = MagicMock()
    c3.subreddit.display_name = 'funny'
    c3.body = 'normal comment'
    c3.submission.title = '[Test Variation pixels] 12345'
    assert is_test_comment(c3) is True

    # Non-test comment
    c4 = MagicMock()
    c4.subreddit.display_name = 'funny'
    c4.body = 'regular response'
    c4.submission.title = 'regular post'
    assert is_test_comment(c4) is False


@pytest.mark.unit
def test_purge_bot_history_live_and_dry_run():
    mock_reddit = MagicMock()
    mock_me = MagicMock()
    mock_reddit.user.me.return_value = mock_me

    # Submissions: 1 test, 1 non-test
    test_sub = MagicMock()
    test_sub.subreddit.display_name = 'racb_test_1'
    test_sub.title = '[Test] 123'
    test_sub.id = 'sub_1'

    non_test_sub = MagicMock()
    non_test_sub.subreddit.display_name = 'AskReddit'
    non_test_sub.title = 'Legit discussion'
    non_test_sub.id = 'sub_2'

    mock_me.submissions.new.return_value = [test_sub, non_test_sub]

    # Comments: 1 test, 1 non-test
    test_comment = MagicMock()
    test_comment.subreddit.display_name = 'racb_test_1'
    test_comment.body = 'r/racb_test_2'
    test_comment.submission.title = '[Test] 123'
    test_comment.id = 'c_1'

    non_test_comment = MagicMock()
    non_test_comment.subreddit.display_name = 'AskReddit'
    non_test_comment.body = 'Legit reply'
    non_test_comment.submission.title = 'Legit discussion'
    non_test_comment.id = 'c_2'

    mock_me.comments.new.return_value = [test_comment, non_test_comment]

    with patch('scripts.cleanup_test_history.reddit.get_reddit_instance', return_value=mock_reddit):
        # Dry run test
        del_subs, del_comms = purge_bot_history('AutoCrosspostBot', dry_run=True)
        assert del_subs == 1
        assert del_comms == 1
        test_sub.delete.assert_not_called()
        test_comment.delete.assert_not_called()

        # Actual purge test
        del_subs, del_comms = purge_bot_history('AutoCrosspostBot', dry_run=False)
        assert del_subs == 1
        assert del_comms == 1
        test_sub.delete.assert_called_once()
        test_comment.delete.assert_called_once()
        non_test_sub.delete.assert_not_called()
        non_test_comment.delete.assert_not_called()
