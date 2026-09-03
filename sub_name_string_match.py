"""Searches Reddit's live community directory for similar subreddits when a non-existent subreddit is linked."""

import logging
import prawcore
import reddit_instantiator

RESULTS_LIMIT = 4


def get_matches(subreddit_name):
    """Searches Reddit for existing communities similar to the given subreddit name."""
    reddit = reddit_instantiator.get_reddit_instance()
    results = []
    try:
        search_results = reddit.subreddits.search(subreddit_name, limit=10)
        for sub in search_results:
            name = sub.display_name
            if name.lower() != subreddit_name.lower() and name not in results:
                results.append(name)
            if len(results) >= RESULTS_LIMIT:
                break
    except (prawcore.exceptions.PrawcoreException, Exception) as e:
        logging.warning(f"Error searching subreddits for '{subreddit_name}': {e}")
    return results