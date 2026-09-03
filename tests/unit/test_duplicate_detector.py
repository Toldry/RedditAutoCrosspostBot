"""Unit tests for racb.core.duplicate_detector module."""

from unittest.mock import patch, MagicMock
import pytest
import requests
from racb.core import duplicate_detector


@pytest.mark.unit
def test_search_reposts_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        'matches': [
            {'post': {'post_id': 'abc123', 'subreddit': 'funny'}},
            {'post': {'post_id': 'def456', 'subreddit': 'memes'}},
        ]
    }

    with patch('requests.get', return_value=mock_response):
        posts = duplicate_detector.search_reposts('https://i.redd.it/example.png')
        assert len(posts) == 2
        assert posts[0]['post_id'] == 'abc123'


@pytest.mark.unit
def test_search_reposts_network_error():
    with patch('requests.get', side_effect=requests.exceptions.RequestException('Timeout')):
        posts = duplicate_detector.search_reposts('https://i.redd.it/example.png')
        assert posts == []


@pytest.mark.unit
def test_search_reposts_non_200_json_error():
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.headers = {'content-type': 'application/json'}
    mock_response.json.return_value = {'title': 'Invalid URL'}

    with patch('requests.get', return_value=mock_response):
        posts = duplicate_detector.search_reposts('https://i.redd.it/bad.png')
        assert posts == []


@pytest.mark.unit
def test_get_reposts_in_sub(make_mock_comment):
    comment = make_mock_comment()
    mock_posts = [
        {'post_id': 'p1', 'subreddit': 'target_sub'},
        {'post_id': 'p2', 'subreddit': 'other_sub'},
        {'post_id': 'p3', 'subreddit': 'TARGET_SUB'},
    ]

    with patch('racb.core.duplicate_detector.search_reposts', return_value=mock_posts):
        filtered = duplicate_detector.get_reposts_in_sub(comment, 'Target_Sub')
        assert len(filtered) == 2
        assert filtered[0]['post_id'] == 'p1'
        assert filtered[1]['post_id'] == 'p3'
