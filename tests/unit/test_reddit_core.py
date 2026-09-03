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
