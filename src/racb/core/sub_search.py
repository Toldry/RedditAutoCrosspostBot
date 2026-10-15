"""Finds existing subreddits with similar names when a non-existent subreddit is linked."""

import logging
from difflib import SequenceMatcher

import prawcore
import psycopg2
from racb.core import db, known_subreddits
from racb.core.reddit import get_reddit_instance

logger = logging.getLogger(__name__)

RESULTS_LIMIT = 4
SEARCH_LIMIT = 25
KNOWN_SUBREDDITS_LIMIT = 25
# /subreddits/search matches on topic (title and description), not on the name, so a search for
# "wtfdidijustread" also returns r/nocontext. Only names at least this similar to the query are kept.
MIN_NAME_SIMILARITY = 0.6


def name_similarity(first_name, second_name):
    """Returns a 0..1 similarity score between two subreddit names, ignoring case."""
    return SequenceMatcher(None, first_name.lower(), second_name.lower()).ratio()


def _candidates_from_known_subreddits(subreddit_name):
    """Names from our own known_subreddits table (filled from the comment stream)."""
    try:
        return known_subreddits.find_similar(subreddit_name, KNOWN_SUBREDDITS_LIMIT)
    except Exception as e:
        if isinstance(e, (psycopg2.OperationalError, psycopg2.InterfaceError)):
            db.reset_connection()
        logger.warning(f"Error looking up known subreddits similar to '{subreddit_name}': {e!r}")
        return []


def _candidates_from_reddit_search(subreddit_name):
    """Names from Reddit's community search, which matches on topic as well as name."""
    reddit = get_reddit_instance()
    try:
        return [sub.display_name for sub in reddit.subreddits.search(subreddit_name, limit=SEARCH_LIMIT)]
    except (prawcore.exceptions.PrawcoreException, Exception) as e:
        logger.warning(f"Error searching subreddits for '{subreddit_name}': {e}")
        return []


def get_matches(subreddit_name):
    """Finds existing communities whose names are similar to the given subreddit name.

    Candidates come from the known_subreddits table and from Reddit's search. Both are scored the same
    way, and the results are ordered from most to least similar name.
    """
    candidates = _candidates_from_known_subreddits(subreddit_name) + _candidates_from_reddit_search(subreddit_name)
    scored_names = {}
    for name in candidates:
        if name.lower() == subreddit_name.lower() or name.lower() in scored_names:
            continue
        similarity = name_similarity(name, subreddit_name)
        if similarity >= MIN_NAME_SIMILARITY:
            scored_names[name.lower()] = (similarity, name)
    ranked = sorted(scored_names.values(), key=lambda pair: pair[0], reverse=True)
    return [name for _, name in ranked[:RESULTS_LIMIT]]
