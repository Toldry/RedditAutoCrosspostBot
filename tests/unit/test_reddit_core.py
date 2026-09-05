"""Unit tests for racb.core.reddit module."""

from unittest.mock import patch, MagicMock
import pytest
from racb.core import reddit


@pytest.mark.unit
def test_bot_details_configuration():
    assert reddit.AUTO_CROSSPOST_BOT_NAME in reddit.bot_details
    assert reddit.SUB_DOESNT_EXIST_BOT_NAME in reddit.bot_details
    assert reddit.SAME_SUBREDDIT_BOT_NAME in reddit.bot_details
    assert reddit.SAME_POST_BOT_NAME in reddit.bot_details

    for bot_name, details in reddit.bot_details.items():
        assert 'app_client_id' in details
        assert 'env_password_key' in details
        assert 'env_app_client_secret_key' in details
        assert 'version' in details


@pytest.mark.unit
def test_instantiate_praw():
    with patch('praw.Reddit') as mock_praw_class:
        mock_instance = MagicMock()
        mock_praw_class.return_value = mock_instance

        res = reddit._instantiate_praw(
            username='TestBot',
            app_client_id='client_123',
            password='pass',
            app_client_secret='secret_123',
            version='2.0.0',
        )

        mock_praw_class.assert_called_once_with(
            client_id='client_123',
            client_secret='secret_123',
            user_agent='linux:TestBot:v2.0.0 (by /u/orqa)',
            username='TestBot',
            password='pass',
            ratelimit_seconds=300,
        )
        assert res == mock_instance


@pytest.mark.unit
def test_get_reddit_instance_singleton():
    with patch('racb.core.reddit._instantiate_praw') as mock_instantiate:
        mock_instantiate.side_effect = lambda username, **kwargs: MagicMock(username=username)
        reddit.praw_instances = None  # Reset singleton cache

        inst1 = reddit.get_reddit_instance(reddit.AUTO_CROSSPOST_BOT_NAME)
        inst2 = reddit.get_reddit_instance(reddit.AUTO_CROSSPOST_BOT_NAME)

        assert inst1 is inst2
        assert mock_instantiate.call_count == 4  # 4 bots initialized on first call


@pytest.mark.unit
def test_authenticate_all_bots_success():
    mock_instances = {}
    bot_names = [
        reddit.AUTO_CROSSPOST_BOT_NAME,
        reddit.SUB_DOESNT_EXIST_BOT_NAME,
        reddit.SAME_SUBREDDIT_BOT_NAME,
        reddit.SAME_POST_BOT_NAME,
    ]
    for name in bot_names:
        mock_r = MagicMock()
        mock_r.user.me.return_value = MagicMock(name=name)
        # MagicMock str/name attribute for Redditor
        mock_r.user.me.return_value.name = name
        mock_instances[name] = mock_r

    with patch('racb.core.reddit.get_reddit_instance', side_effect=lambda name: mock_instances[name]):
        reddit.authenticate_all_bots()


@pytest.mark.unit
def test_authenticate_all_bots_none_user_raises():
    mock_r = MagicMock()
    mock_r.user.me.return_value = None

    with patch('racb.core.reddit.get_reddit_instance', return_value=mock_r):
        with pytest.raises(RuntimeError) as excinfo:
            reddit.authenticate_all_bots()
        assert 'is not authenticated' in str(excinfo.value)


@pytest.mark.unit
def test_authenticate_all_bots_username_mismatch_raises():
    mock_r = MagicMock()
    mock_r.user.me.return_value = MagicMock(name='WrongUser')
    mock_r.user.me.return_value.name = 'WrongUser'

    with patch('racb.core.reddit.get_reddit_instance', return_value=mock_r):
        with pytest.raises(RuntimeError) as excinfo:
            reddit.authenticate_all_bots()
        assert 'unexpected user' in str(excinfo.value)


@pytest.mark.unit
def test_authenticate_all_bots_exception_raises():
    mock_r = MagicMock()
    mock_r.user.me.side_effect = Exception('Invalid credentials or token expired')

    with patch('racb.core.reddit.get_reddit_instance', return_value=mock_r):
        with pytest.raises(RuntimeError) as excinfo:
            reddit.authenticate_all_bots()
        assert 'Authentication failed for 4 bot(s)' in str(excinfo.value)
        assert 'Invalid credentials or token expired' in str(excinfo.value)


@pytest.mark.unit
def test_authenticate_all_bots_attempts_all_before_raising():
    calls = []

    def mock_get_bot(name):
        calls.append(name)
        mock_r = MagicMock()
        if name == reddit.AUTO_CROSSPOST_BOT_NAME:
            mock_r.user.me.side_effect = Exception('AutoCrosspostBot auth error')
        elif name == reddit.SAME_SUBREDDIT_BOT_NAME:
            mock_r.user.me.return_value = None  # read-only
        else:
            mock_r.user.me.return_value = MagicMock(name=name)
            mock_r.user.me.return_value.name = name
        return mock_r

    with patch('racb.core.reddit.get_reddit_instance', side_effect=mock_get_bot):
        with pytest.raises(RuntimeError) as excinfo:
            reddit.authenticate_all_bots()

        # Verify all 4 bots were attempted despite early failures
        assert len(calls) == 4
        assert reddit.AUTO_CROSSPOST_BOT_NAME in calls
        assert reddit.SUB_DOESNT_EXIST_BOT_NAME in calls
        assert reddit.SAME_SUBREDDIT_BOT_NAME in calls
        assert reddit.SAME_POST_BOT_NAME in calls

        error_message = str(excinfo.value)
        assert 'Authentication failed for 2 bot(s)' in error_message
        assert 'AutoCrosspostBot' in error_message
        assert 'same_subreddit_bot' in error_message
