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

    with patch('racb.phases.inbox.get_reddit_instance', return_value=mock_reddit), \
            patch('racb.phases.inbox.respond_to_positive_sentiment'):
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

    with patch('racb.phases.inbox.get_reddit_instance', side_effect=get_instance), \
            patch('racb.phases.inbox.respond_to_negative_sentiment') as respond:
        inbox.process_unread_inboxes()

    respond.assert_called_once_with(mock_comment, inbox.BOT_NAMES[1])
    receiving_reddit.inbox.mark_read.assert_called_once_with([mock_comment])
    other_reddit.inbox.mark_read.assert_not_called()


def _feedback_comment(comment_id, body, created_utc=100.0, parent=None):
    comment = MagicMock(spec=praw.models.Comment)
    comment.id = comment_id
    comment.body = body
    comment.created_utc = created_utc
    comment.permalink = f'/r/pics/comments/abc/title/{comment_id}/'
    comment.subreddit = MagicMock(display_name='pics')
    comment.parent.return_value = parent
    return comment


def _bot_comment(replies):
    bot_comment = MagicMock(spec=praw.models.Comment)
    bot_comment.replies = replies
    return bot_comment


@pytest.fixture
def no_dry_run(monkeypatch):
    monkeypatch.delenv('DRY_RUN', raising=False)


@pytest.mark.unit
def test_negative_feedback_gets_the_question_with_the_bots_suffix(no_dry_run):
    comment = _feedback_comment('c1', 'bad bot')

    inbox.respond_to_negative_sentiment(comment, 'same_post_bot')

    text = comment.reply.call_args.args[0]
    assert 'would you mind detailing why' in text
    assert 'same_post_bot' in text


@pytest.mark.unit
def test_first_good_bot_is_thanked(no_dry_run):
    comment = _feedback_comment('c2', 'Good bot!', created_utc=200.0)
    parent = _bot_comment([
        _feedback_comment('c1', 'bad bot', created_utc=100.0),
        comment,
        _feedback_comment('c3', 'good bot', created_utc=300.0),
    ])
    comment.parent.return_value = parent

    inbox.respond_to_positive_sentiment(comment, 'AutoCrosspostBot')

    parent.refresh.assert_called_once_with()
    text = comment.reply.call_args.args[0]
    assert text.startswith('Thank you (◕ᴗ◕✿)')
    assert 'AutoCrosspostBot' in text


@pytest.mark.unit
def test_later_good_bot_is_not_thanked(no_dry_run):
    comment = _feedback_comment('c2', 'good bot', created_utc=200.0)
    comment.parent.return_value = _bot_comment([
        _feedback_comment('c1', 'Good bot.', created_utc=100.0),
        comment,
    ])

    inbox.respond_to_positive_sentiment(comment, 'AutoCrosspostBot')

    comment.reply.assert_not_called()


@pytest.mark.unit
def test_feedback_reply_respects_dry_run(monkeypatch):
    monkeypatch.setenv('DRY_RUN', 'True')
    comment = _feedback_comment('c1', 'bad bot')

    inbox.respond_to_negative_sentiment(comment, 'AutoCrosspostBot')

    comment.reply.assert_not_called()


@pytest.mark.unit
def test_reply_rejected_by_reddit_is_marked_read(no_dry_run):
    comment = _feedback_comment('c1', 'bad bot')
    comment.reply.side_effect = praw.exceptions.RedditAPIException([['THREAD_LOCKED', 'locked', None]])
    reddit_instance = MagicMock(spec=praw.Reddit)
    reddit_instance.inbox = MagicMock()

    inbox.handle_comment_reply(comment, reddit_instance=reddit_instance, bot_name='AutoCrosspostBot')

    reddit_instance.inbox.mark_read.assert_called_once_with([comment])


@pytest.mark.unit
def test_feedback_is_logged_with_a_real_praw_instance(no_dry_run):
    # The PRAW instance has no `full_url`; the log line must use the racb.core.reddit helper
    comment = _feedback_comment('c1', 'bad bot')
    reddit_instance = MagicMock(spec=praw.Reddit)
    reddit_instance.inbox = MagicMock()

    inbox.handle_comment_reply(comment, reddit_instance=reddit_instance, bot_name='AutoCrosspostBot')

    comment.reply.assert_called_once()
    reddit_instance.inbox.mark_read.assert_called_once_with([comment])
