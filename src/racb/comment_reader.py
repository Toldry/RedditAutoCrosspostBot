"""Reads new Reddit comments by polling the r/all comment listing on several threads and accounts at once.

Why not one request at a time: the listing returns at most 100 comments per request, and each request takes
several seconds. On 2026-10-15/16 a single PRAW stream read about 850 comments a minute during the US day,
while r/all got several times that, so most comments were never seen.

Why not by ID range (3.0 to 3.1): that reader asked `/api/info` for 100 consecutive comment IDs at a time,
which worked while Reddit numbered comments in order. Since about 2026-10-21 every new comment gets a 64-bit,
13-character ID (e.g. `19rnei2yv8zk1`) that is not sequential: two comments seconds apart are about 2 * 10^9
IDs apart, so a block of 100 IDs holds no comment at all. Reddit announced it on r/redditdev
("Upcoming changes to the comment ID endpoint", rescheduled to the week of 2026-09-28); the ID reader then
read nothing from 2026-10-21 until it was replaced.

So now `STREAM_READERS` threads, spread over the 4 bot accounts, each ask the r/all listing for its newest 100
comments, over and over, staggered so their requests interleave. A comment seen before (by any thread) is
skipped, so each new comment is handled once. Every comment the listing returns is on r/all, but its NSFW and
quarantine state is still checked by Phase 1, by the submission (`phase1.is_hidden_from_r_all`).

A poll that returns only new comments means comments may have been missed between it and the previous poll
(a "gap"); the heartbeat counts them. Few gaps mean the readers keep up with r/all. Many mean more readers
would help (`STREAM_READERS`), as long as Reddit's rate limit allows.

Nothing is saved across restarts: the listing only reaches back about 100 comments, so whatever was posted
while the bot was down cannot be read anyway.
"""

import collections
import logging
import threading
import time

from racb import config
from racb.core import reddit, shutdown
from racb.workers import back_off_after_network_error

logger = logging.getLogger(__name__)

# Comments one listing request asks for: the most Reddit returns. Reddit filters the listing after applying
# the limit, so it often returns fewer, and sometimes none (seen on 2026-10-16)
LISTING_LIMIT = 100

# Each reader thread starts its next request at least this long after its previous one started. Every
# thread has its own PRAW instance, so PRAW's rate limiter does not see the other threads of the same
# account. Reddit allows 100 requests a minute per account; with 3 threads per account (the default
# STREAM_READERS) this keeps reading at no more than 60, leaving room for Phase 1 to 3 and the inbox
MIN_SECONDS_BETWEEN_POLLS = 3

# Fullnames of the comments seen most recently, to skip them when another poll returns them again. A poll
# only returns the newest 100, so this only needs to reach back a few polls
SEEN_CAPACITY = 20_000

# Seconds a reader waits after an unexpected error before it polls again
ERROR_RETRY_SECONDS = 10


def fetch_listing(bot_name):
    """Returns the newest comments on r/all, newest first (one request, as `bot_name`)."""
    instance = reddit.get_reddit_instance(bot_name)
    return list(instance.subreddit('all').comments(limit=LISTING_LIMIT))


class SeenComments:
    """The fullnames of the last `capacity` comments handled, oldest forgotten first. Thread safe."""

    def __init__(self, capacity=SEEN_CAPACITY):
        self.capacity = capacity
        self._fullnames = collections.OrderedDict()
        self._lock = threading.Lock()

    def __len__(self):
        with self._lock:
            return len(self._fullnames)

    def add_new(self, fullnames):
        """Remembers `fullnames` and returns the ones not seen before, in their order."""
        with self._lock:
            new = []
            for fullname in fullnames:
                if fullname in self._fullnames:
                    continue
                self._fullnames[fullname] = None
                new.append(fullname)
            while len(self._fullnames) > self.capacity:
                self._fullnames.popitem(last=False)
            return new


class CommentReader:
    """Polls the r/all comment listing on reader threads; see the module docstring.

    `handle_comment(comment)` runs on the reader threads, several at once, so it must be thread safe.
    `run()` occupies the calling thread (the main thread) until shutdown.
    """

    def __init__(self, handle_comment, reader_count, metrics=None, fetch_listing=fetch_listing,
                 min_seconds_between_polls=MIN_SECONDS_BETWEEN_POLLS):
        self.handle_comment = handle_comment
        self.metrics = metrics
        self.fetch_listing = fetch_listing
        self.min_seconds_between_polls = min_seconds_between_polls
        self.seen = SeenComments()
        # Whether any poll returned comments yet: the first one cannot tell whether it missed some
        self._started = False
        self._started_lock = threading.Lock()
        # Readers take turns between the accounts, so each account's rate limit carries a share. Each starts
        # a little after the previous one, so their requests interleave
        stagger_seconds = min_seconds_between_polls / max(1, reader_count)
        self.threads = [
            threading.Thread(
                target=self.read_listing,
                args=(reddit.BOT_NAMES[number % len(reddit.BOT_NAMES)], number * stagger_seconds),
                name=f'reader-{number + 1}',
                daemon=True,
            )
            for number in range(reader_count)
        ]

    def start(self):
        for thread in self.threads:
            thread.start()
        return self

    def run(self):
        """Waits until shutdown. The reader threads do the reading."""
        while not shutdown.stop_requested_within(1):
            pass

    # --- Reader threads ---

    def read_listing(self, bot_name, start_delay):
        try:
            if shutdown.stop_requested_within(start_delay):
                return
            while not shutdown.stop_requested():
                started = time.monotonic()
                if self.poll(bot_name):
                    return
                remaining = self.min_seconds_between_polls - (time.monotonic() - started)
                if remaining > 0 and shutdown.stop_requested_within(remaining):
                    return
        except BaseException as error:
            shutdown.fail(error, threading.current_thread().name)

    def poll(self, bot_name):
        """Reads the listing once and hands on the comments not seen before. Returns True on shutdown."""
        started = time.monotonic()
        try:
            comments = self.fetch_listing(bot_name)
        except reddit.TRANSIENT_NETWORK_ERRORS as error:
            if config.is_debug():
                raise
            return back_off_after_network_error(error)
        except Exception as error:
            if config.is_debug():
                raise
            logger.exception(f'Could not read the r/all comment listing: {error!r}')
            return shutdown.stop_requested_within(ERROR_RETRY_SECONDS)
        self.handle_listing(comments, time.monotonic() - started)
        return False

    def handle_listing(self, comments, seconds):
        """Hands on the comments of one listing that no poll returned before, oldest first."""
        by_fullname = {f't1_{comment.id}': comment for comment in comments}
        new = self.seen.add_new(reversed(list(by_fullname)))
        with self._started_lock:
            # Only new comments: whatever was posted between them and the comments seen before was missed
            gap = self._started and bool(new) and len(new) == len(by_fullname)
            if by_fullname:
                self._started = True
        if self.metrics:
            self.metrics.record_listing_poll(seconds, len(comments), len(new), gap)
        newest_created = None
        for fullname in new:
            comment = by_fullname[fullname]
            self.handle_comment(comment)
            created = vars(comment).get('created_utc')
            if created is not None and (newest_created is None or created > newest_created):
                newest_created = created
        if self.metrics and newest_created is not None:
            self.metrics.record_newest_created(newest_created)
