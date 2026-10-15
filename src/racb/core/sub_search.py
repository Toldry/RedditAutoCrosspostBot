"""Searches Reddit's live community directory for similar subreddits when a non-existent subreddit is linked."""

import logging
from difflib import SequenceMatcher

import prawcore
from racb.core.reddit import get_reddit_instance

logger = logging.getLogger(__name__)

RESULTS_LIMIT = 4
SEARCH_LIMIT = 25
# /subreddits/search matches on topic (title and description), not on the name, so a search for
# "wtfdidijustread" also returns r/nocontext. Only names at least this similar to the query are kept.
MIN_NAME_SIMILARITY = 0.6


def name_similarity(first_name, second_name):
    """Returns a 0..1 similarity score between two subreddit names, ignoring case."""
    return SequenceMatcher(None, first_name.lower(), second_name.lower()).ratio()


def get_matches(subreddit_name):
    """Searches Reddit for existing communities whose names are similar to the given subreddit name.

    Results are ordered from most to least similar name.
    """
    reddit = get_reddit_instance()
    scored_names = {}
    try:
        search_results = reddit.subreddits.search(subreddit_name, limit=SEARCH_LIMIT)
        for sub in search_results:
            name = sub.display_name
            if name.lower() == subreddit_name.lower() or name.lower() in scored_names:
                continue
            similarity = name_similarity(name, subreddit_name)
            if similarity >= MIN_NAME_SIMILARITY:
                scored_names[name.lower()] = (similarity, name)
    except (prawcore.exceptions.PrawcoreException, Exception) as e:
        logger.warning(f"Error searching subreddits for '{subreddit_name}': {e}")
    ranked = sorted(scored_names.values(), key=lambda pair: pair[0], reverse=True)
    return [name for _, name in ranked[:RESULTS_LIMIT]]
