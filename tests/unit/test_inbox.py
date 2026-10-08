"""Unit tests for racb.phases.inbox module."""

from unittest.mock import patch, MagicMock
import pytest
import praw.models
import prawcore
from racb.phases import inbox


@pytest.mark.unit
def test_check_sentiment():
    assert inbox.check_sentiment('good bot') == 'positive'
    assert inbox.check_sentiment('Good bot!') == 'positive'
    assert inbox.check_sentiment('bad bot') == 'negative'
    assert inbox.check_sentiment('delete this!') == 'negative'
    assert inbox.check_sentiment('no') == 'negative'
    assert inbox.check_sentiment('Hello there!') is None


@pytest.mark.unit
def test_handle_comment_reply_marks_read():
    mock_comment = MagicMock(spec=praw.models.Comment)
    mock_comment.body = 'good bot'
    mock_reddit = MagicMock()

    with patch('racb.phases.inbox.get_reddit_instance', return_value=mock_reddit):
        inbox.handle_comment_reply(mock_comment)
        mock_reddit.inbox.mark_read.assert_called_once_with([mock_comment])


@pytest.mark.unit
def test_handle_comment_reply_forbidden_exception():
    mock_comment = MagicMock(spec=praw.models.Comment)
    mock_comment.body = 'good bot'
    mock_comment.subreddit_name_prefixed = 'r/banned_sub'
    mock_reddit = MagicMock()

    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.reason = 'Forbidden'
    mock_resp.__str__ = lambda self: 'Forbidden'
    mock_reddit.inbox.mark_read.return_value = None

    with patch('racb.phases.inbox.get_reddit_instance', return_value=mock_reddit):
        with patch('racb.phases.inbox.respond_to_positive_sentiment', side_effect=prawcore.exceptions.Forbidden(mock_resp)):
            inbox.handle_comment_reply(mock_comment)
            mock_reddit.inbox.mark_read.assert_called_with([mock_comment])


@pytest.mark.unit
def test_process_unread_inboxes_reads_all_bot_inboxes():
    reddit_instances = {name: MagicMock() for name in inbox.BOT_NAMES}
    for instance in reddit_instances.values():
        instance.inbox.unread.return_value = []

    with patch('racb.phases.inbox.get_reddit_instance', side_effect=lambda name: reddit_instances[name]):
        inbox.process_unread_inboxes()

    for instance in reddit_instances.values():
        instance.inbox.unread.assert_called_once()


@pytest.mark.unit
def test_feedback_is_marked_read_with_the_receiving_bots_instance():
    mock_comment = MagicMock(spec=praw.models.Comment)
    mock_comment.body = 'bad bot'
    mock_comment.type = 'comment_reply'
    receiving_reddit = MagicMock()
    receiving_reddit.inbox.unread.return_value = [mock_comment]
    other_reddit = MagicMock()
    other_reddit.inbox.unread.return_value = []

    def get_instance(name):
        return receiving_reddit if name == inbox.BOT_NAMES[1] else other_reddit

    with patch('racb.phases.inbox.get_reddit_instance', side_effect=get_instance):
        inbox.process_unread_inboxes()

    receiving_reddit.inbox.mark_read.assert_called_once_with([mock_comment])
    other_reddit.inbox.mark_read.assert_not_called()
