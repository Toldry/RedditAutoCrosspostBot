"""Searches Reddit's live community directory for similar subreddits when a non-existent subreddit is linked."""

import logging
import prawcore
from racb.core.reddit import get_reddit_instance

logger = logging.getLogger(__name__)

RESULTS_LIMIT = 4


def get_matches(subreddit_name):
    """Searches Reddit for existing communities similar to the given subreddit name."""
    reddit = get_reddit_instance()
    results = []
    try:
        search_results = reddit.subreddits.search(subreddit_name, limit=10)
        for sub in search_results:
            name = sub.display_name
            if name.lower() != subreddit_name.lower() and name.lower() not in [r.lower() for r in results]:
                results.append(name)
            if len(results) >= RESULTS_LIMIT:
                break
    except (prawcore.exceptions.PrawcoreException, Exception) as e:
        logger.warning(f"Error searching subreddits for '{subreddit_name}': {e}")
    return results
