"""Unit tests for racb.core.sub_search module."""

from unittest.mock import patch, MagicMock
import pytest
import prawcore
from racb.core import sub_search


@pytest.mark.unit
def test_get_matches_filters_exact_match_and_limits():
    mock_sub_search_results = [
        MagicMock(display_name='targetsub'),      # Same as query (should be excluded)
        MagicMock(display_name='TargetSub_1'),    # Match 1
        MagicMock(display_name='TargetSub_2'),    # Match 2
        MagicMock(display_name='targetsub_2'),    # Duplicate display name
        MagicMock(display_name='TargetSub_3'),    # Match 3
        MagicMock(display_name='TargetSub_4'),    # Match 4
        MagicMock(display_name='TargetSub_5'),    # Exceeds RESULTS_LIMIT (4)
    ]

    mock_reddit = MagicMock()
    mock_reddit.subreddits.search.return_value = mock_sub_search_results

    with patch('racb.core.sub_search.get_reddit_instance', return_value=mock_reddit):
        results = sub_search.get_matches('targetsub')
        assert len(results) == 4
        assert 'TargetSub_1' in results
        assert 'TargetSub_2' in results
        assert 'TargetSub_3' in results
        assert 'TargetSub_4' in results
        assert 'TargetSub_5' not in results
        assert 'targetsub' not in results


@pytest.mark.unit
def test_get_matches_drops_topic_only_matches_and_ranks_by_name_similarity():
    # Real /subreddits/search output for 'wtfdidijustread' (2026-10-15): it matches on topic, not name.
    mock_sub_search_results = [
        MagicMock(display_name='DaFuqDidIJustRead'),
        MagicMock(display_name='evenwithcontext'),
        MagicMock(display_name='nocontext'),
        MagicMock(display_name='HilariousComments'),
        MagicMock(display_name='guessthereddit'),
        MagicMock(display_name='IGTHFT_Mods'),
        MagicMock(display_name='wtfdidijustread76'),
    ]

    mock_reddit = MagicMock()
    mock_reddit.subreddits.search.return_value = mock_sub_search_results

    with patch('racb.core.sub_search.get_reddit_instance', return_value=mock_reddit):
        results = sub_search.get_matches('wtfdidijustread')
        assert results == ['wtfdidijustread76', 'DaFuqDidIJustRead']


@pytest.mark.unit
def test_get_matches_returns_empty_when_nothing_is_similar():
    mock_reddit = MagicMock()
    mock_reddit.subreddits.search.return_value = [MagicMock(display_name='nocontext')]

    with patch('racb.core.sub_search.get_reddit_instance', return_value=mock_reddit):
        assert sub_search.get_matches('wtfdidijustread') == []


@pytest.mark.unit
def test_get_matches_handles_exception():
    mock_reddit = MagicMock()
    mock_reddit.subreddits.search.side_effect = prawcore.exceptions.ServerError(MagicMock())

    with patch('racb.core.sub_search.get_reddit_instance', return_value=mock_reddit):
        results = sub_search.get_matches('nonexistent')
        assert results == []
