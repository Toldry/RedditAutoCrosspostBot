"""Shared fixtures and test helpers for RACB test suite."""

from unittest.mock import MagicMock
import pytest


class MockRedditor:
    """Mock PRAW Redditor object."""

    def __init__(self, name='test_user'):
        self.name = name

    def __str__(self):
        return self.name


class MockSubreddit:
    """Mock PRAW Subreddit object."""

    def __init__(self, display_name='test_sub', subscribers=1000, over18=False):
        self.display_name = display_name
        self.subscribers = subscribers
        self.over18 = over18

    def __str__(self):
        return self.display_name


class MockSubmission:
    """Mock PRAW Submission object."""

    def __init__(
        self,
        submission_id='sub_123',
        title='Test submission title',
        url='https://example.com/image.png',
        permalink='/r/test_source/comments/sub_123/test_submission/',
        subreddit='test_source',
        score=10,
        author='submission_author',
        over_18=False,
    ):
        self.id = submission_id
        self.title = title
        self.url = url
        self.permalink = permalink
        self.subreddit = MockSubreddit(subreddit) if isinstance(subreddit, str) else subreddit
        self.score = score
        self.author = MockRedditor(author) if isinstance(author, str) else author
        self.over_18 = over_18
        self.crosspost = MagicMock()
        self.delete = MagicMock()


class MockComment:
    """Mock PRAW Comment object."""

    def __init__(
        self,
        comment_id='c_456',
        body='r/test_target',
        permalink='/r/test_source/comments/sub_123/test_submission/c_456/',
        subreddit='test_source',
        score=50,
        author='comment_author',
        parent_id='t3_sub_123',
        link_id='t3_sub_123',
        distinguished=None,
        submission=None,
    ):
        self.id = comment_id
        self.body = body
        self.permalink = permalink
        self.subreddit = MockSubreddit(subreddit) if isinstance(subreddit, str) else subreddit
        self.score = score
        self.author = MockRedditor(author) if isinstance(author, str) else author
        self.parent_id = parent_id
        self.link_id = link_id
        self.distinguished = distinguished
        self.submission = submission or MockSubmission(subreddit=subreddit)
        self.reply = MagicMock()


@pytest.fixture
def make_mock_comment():
    """Factory fixture to create customized mock comments."""
    def _factory(**kwargs):
        return MockComment(**kwargs)
    return _factory


@pytest.fixture
def make_mock_submission():
    """Factory fixture to create customized mock submissions."""
    def _factory(**kwargs):
        return MockSubmission(**kwargs)
    return _factory
