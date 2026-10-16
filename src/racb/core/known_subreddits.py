"""Remembers which subreddits exist, from the r/all comment stream, for "did you mean" suggestions.

Names are buffered in memory and written to the `known_subreddits` table in one batch by a scheduled
job, so the stream loop never waits on the database for this. The stream thread adds names while the
job's thread writes them, so the buffer is guarded by a lock.
"""

import logging
import threading

from racb.core import db

logger = logging.getLogger(__name__)

# Upper bound on buffered names, so a long database outage cannot grow the buffer without limit
MAX_PENDING_NAMES = 50_000

_pending_names = set()
_pending_names_lock = threading.Lock()


def record(subreddit_name):
    """Buffers a subreddit name seen in the stream. User profiles (`u_<name>`) are skipped."""
    if not subreddit_name or subreddit_name.lower().startswith('u_'):
        return
    with _pending_names_lock:
        if len(_pending_names) < MAX_PENDING_NAMES:
            _pending_names.add(subreddit_name)


def flush():
    """Writes the buffered names to the database. On failure they stay buffered for the next run."""
    with _pending_names_lock:
        names = sorted(_pending_names)
    if not names:
        return 0
    # The database write runs outside the lock, so the stream never waits for it
    db.record_known_subreddits(names)
    with _pending_names_lock:
        _pending_names.difference_update(names)
    logger.info(f'Recorded {len(names)} known subreddits')
    return len(names)


def find_similar(subreddit_name, max_results):
    """Returns known subreddit names resembling `subreddit_name` (trigram similarity), most similar first."""
    return db.find_similar_subreddits(subreddit_name, max_results)
