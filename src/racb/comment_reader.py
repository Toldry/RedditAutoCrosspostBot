"""Reads every new Reddit comment by ID range, instead of polling the r/all comment listing.

Why: the r/all comment listing returns at most 100 comments per request, and each request takes several
seconds. On 2026-10-15/16 the bot read about 850 comments a minute through it during the US day and up
to 2,500 a minute at night, when Reddit answers faster. So the listing, not r/all, set the count, and most
comments were never seen.

Comment IDs are base-36 numbers that Reddit hands out in order across the whole site. So instead the bot
asks for 100 consecutive IDs at a time (`/api/info`), on several threads and accounts at once, without
gaps or overlap:

- The main thread asks the r/all listing for its newest comment (the "head") and queues the blocks of
  100 IDs up to it.
- `STREAM_READERS` threads, spread over the 4 bot accounts, each fetch one block at a time and pass every
  comment that r/all would show (`is_shown_on_r_all`) to `handle_comment`.
- The ID below which every block is done (the "watermark") and the blocks already done above it are
  saved to the database every `CURSOR_SAVE_SECONDS` and at shutdown, so a restart resumes where the previous
  run stopped instead of skipping what was posted meanwhile.

`/api/info` also returns comments that r/all leaves out (private subreddits, user profiles), so those are
dropped. Its comments say nothing about NSFW or quarantine (no `over_18` or `quarantine` field, checked live
on 2026-10-16), so Phase 1 drops those by their submission (`phase1.is_hidden_from_r_all`), which it fetches
anyway, and only for regex matches. Subreddits that only opted out of r/all cannot be told apart from a
comment, so their comments are now read too.
"""

import logging
import queue
import threading
import time

from racb import config
from racb.core import db, reddit, shutdown
from racb.workers import POLL_SECONDS, back_off_after_network_error

logger = logging.getLogger(__name__)

# /api/info returns at most 100 items per request
BLOCK_SIZE = 100

# Comments the head poll asks for. Reddit filters the r/all listing after applying the limit, so asking for
# one comment sometimes returns none (seen live on 2026-10-16); the newest of a few is the head
HEAD_LISTING_LIMIT = 25

# Seconds to wait between two head polls (each poll itself takes a few seconds). It only sets how soon a new
# comment is read, not how many: every block up to the head is queued at once. Fewer polls, fewer requests.
HEAD_POLL_SECONDS = 10

# At most this many IDs may wait to be read (about half an hour of Reddit comments, at a guessed 100 per
# second). Further behind, the reader skips ahead to the head and logs how many IDs it skipped, so it never
# works on comments too old to be worth a reply. The same limit decides whether a restart resumes.
MAX_BACKLOG_IDS = 200_000

# Tries per block before its comments are given up on
MAX_BLOCK_ATTEMPTS = 3

CURSOR_SAVE_SECONDS = 30
CURSOR_NAME = 'comments'

# Subreddit types whose comments never appear on r/all
HIDDEN_SUBREDDIT_TYPES = frozenset({'private', 'user', 'employees_only', 'gold_only'})

BASE36_DIGITS = '0123456789abcdefghijklmnopqrstuvwxyz'


def comment_id_to_number(comment_id):
    """`'k3l2x9a'` -> its number. Also accepts a fullname (`'t1_k3l2x9a'`)."""
    if comment_id.startswith('t1_'):
        comment_id = comment_id[3:]
    return int(comment_id, 36)


def number_to_comment_id(number):
    """The inverse of `comment_id_to_number`, without the `t1_` prefix."""
    if number == 0:
        return '0'
    digits = []
    while number:
        number, remainder = divmod(number, 36)
        digits.append(BASE36_DIGITS[remainder])
    return ''.join(reversed(digits))


def block_fullnames(block_start):
    """The 100 comment fullnames of the block that starts at ID `block_start`."""
    return [f't1_{number_to_comment_id(number)}' for number in range(block_start, block_start + BLOCK_SIZE)]


def is_shown_on_r_all(comment):
    """Whether r/all could list this comment: not in a private subreddit or on a user profile.

    NSFW and quarantined comments pass: `/api/info` comments carry no field for either, so Phase 1 checks
    their submission instead (`phase1.is_hidden_from_r_all`).
    """
    # vars(), not getattr: reading a field the comment lacks would make PRAW fetch it, one request each
    fields = vars(comment)
    if fields.get('subreddit_type') in HIDDEN_SUBREDDIT_TYPES:
        return False
    subreddit = fields.get('subreddit')
    subreddit_name = getattr(subreddit, 'display_name', subreddit) or ''
    return not str(subreddit_name).lower().startswith('u_')


def fetch_head():
    """Returns the number of the newest comment on r/all (one request, as /u/AutoCrosspostBot)."""
    newest = list(reddit.get_reddit_instance().subreddit('all').comments(limit=HEAD_LISTING_LIMIT))
    if not newest:
        raise RuntimeError('The r/all comment listing returned no comments')
    return max(comment_id_to_number(comment.id) for comment in newest)


def fetch_block(bot_name, block_start):
    """Returns the comments of one block that still exist and are visible (one request, as `bot_name`)."""
    return list(reddit.get_reddit_instance(bot_name).info(fullnames=block_fullnames(block_start)))


class CommentReader:
    """Reads every new comment by ID block on reader threads; see the module docstring.

    `handle_comment(comment)` runs on the reader threads, several at once, so it must be thread safe.
    `run()` occupies the calling thread (the main thread) until shutdown.
    """

    def __init__(self, handle_comment, reader_count, metrics=None, handle_exception=None,
                 fetch_head=fetch_head, fetch_block=fetch_block, cursor_store=db):
        self.handle_comment = handle_comment
        self.metrics = metrics
        # Decides what to do about an error from a head poll: returns True to re-raise (see main.handle_exception)
        self.handle_exception = handle_exception or (lambda error: True)
        self.fetch_head = fetch_head
        self.fetch_block = fetch_block
        self.cursor_store = cursor_store
        self.blocks = queue.Queue()
        self._lock = threading.Lock()
        # The next ID to queue; None until the start position is known
        self.next_id = None
        # Starts of the blocks queued but not done yet, and of the blocks done above the watermark
        self.in_flight = set()
        self.done_above = set()
        self.last_saved = time.monotonic()
        # Readers take turns between the accounts, so each account's rate limit carries a share
        self.threads = [
            threading.Thread(
                target=self.read_blocks,
                args=(reddit.BOT_NAMES[number % len(reddit.BOT_NAMES)],),
                name=f'reader-{number + 1}',
                daemon=True,
            )
            for number in range(reader_count)
        ]

    def start(self):
        for thread in self.threads:
            thread.start()
        return self

    # --- Main thread ---

    def run(self):
        """Polls the head and queues blocks until shutdown, then saves the cursor."""
        try:
            while not shutdown.stop_requested():
                try:
                    self.poll_head()
                except Exception as error:
                    if self.handle_exception(error):
                        raise
                self.save_cursor_if_due()
                if shutdown.stop_requested_within(HEAD_POLL_SECONDS):
                    break
        finally:
            self.save_cursor()

    def poll_head(self):
        started = time.monotonic()
        head = self.fetch_head()
        if self.metrics:
            self.metrics.record_head_poll(time.monotonic() - started)
        with self._lock:
            if self.next_id is None:
                self._choose_start(head)
            self._skip_ahead_if_too_far_behind(head)
            self._queue_blocks_up_to(head)
            lag_ids = head + 1 - self._watermark()
        if self.metrics:
            self.metrics.set_lag_ids(lag_ids)

    def _choose_start(self, head):
        """Resumes from the saved cursor if it is recent enough, otherwise starts at the head."""
        saved = self._load_cursor()
        if saved is not None and 0 <= head + 1 - saved['next_comment_id'] <= MAX_BACKLOG_IDS:
            self.next_id = saved['next_comment_id']
            self.done_above = {start for start in saved['done_blocks'] or [] if start >= self.next_id}
            logger.info(
                f'Resuming the comment reader {head + 1 - self.next_id:,} IDs behind the newest comment '
                f'(cursor saved at {saved["updated_at"]})'
            )
            return
        if saved is not None:
            logger.warning(
                f'Saved comment cursor is {head + 1 - saved["next_comment_id"]:,} IDs from the newest comment '
                f'(more than {MAX_BACKLOG_IDS:,}, or ahead of it); starting at the newest comment instead'
            )
        self.next_id = head + 1
        logger.info(f'Comment reader starts at the newest comment, ID {number_to_comment_id(head)}')

    def _load_cursor(self):
        try:
            return self.cursor_store.get_stream_cursor(CURSOR_NAME)
        except Exception as error:
            if config.is_debug():
                raise
            logger.warning(f'Could not read the saved comment cursor ({error!r}); starting at the newest comment')
            return None

    def _skip_ahead_if_too_far_behind(self, head):
        behind = head + 1 - self.next_id
        if behind <= MAX_BACKLOG_IDS:
            return
        logger.warning(f'Comment reader is {behind:,} IDs behind the newest comment; skipping them')
        if self.metrics:
            self.metrics.record_skipped_ids(behind)
        self.next_id = head + 1
        self.done_above.clear()

    def _queue_blocks_up_to(self, head):
        """Queues every complete block up to `head`, as long as fewer than MAX_BACKLOG_IDS wait to be read."""
        # Only complete blocks: the newest IDs of a partial block may not exist yet
        while self.next_id + BLOCK_SIZE - 1 <= head and len(self.in_flight) * BLOCK_SIZE < MAX_BACKLOG_IDS:
            start = self.next_id
            self.next_id += BLOCK_SIZE
            if start in self.done_above:
                continue
            self.in_flight.add(start)
            self.blocks.put(start)

    def _watermark(self):
        """Every ID below this has been read. Call with the lock held."""
        return min(self.in_flight) if self.in_flight else self.next_id

    def save_cursor_if_due(self):
        if time.monotonic() - self.last_saved >= CURSOR_SAVE_SECONDS:
            self.save_cursor()

    def save_cursor(self):
        """Saves the watermark and the blocks done above it. Errors are logged; the next save retries."""
        self.last_saved = time.monotonic()
        with self._lock:
            if self.next_id is None:
                return
            watermark = self._watermark()
            self.done_above = {start for start in self.done_above if start >= watermark}
            done_blocks = sorted(self.done_above)
        try:
            self.cursor_store.save_stream_cursor(CURSOR_NAME, watermark, done_blocks)
        except Exception as error:
            if config.is_debug():
                raise
            if isinstance(error, db.CONNECTION_ERRORS):
                db.reset_connection()
            logger.warning(f'Could not save the comment cursor: {error!r}')

    # --- Reader threads ---

    def read_blocks(self, bot_name):
        try:
            while not shutdown.stop_requested():
                try:
                    block_start = self.blocks.get(timeout=POLL_SECONDS)
                except queue.Empty:
                    continue
                self.read_block(bot_name, block_start)
        except BaseException as error:
            shutdown.fail(error, threading.current_thread().name)

    def read_block(self, bot_name, block_start):
        """Fetches one block, retrying network errors, and hands its comments on."""
        comments = None
        for attempt in range(1, MAX_BLOCK_ATTEMPTS + 1):
            started = time.monotonic()
            try:
                comments = self.fetch_block(bot_name, block_start)
                break
            except reddit.TRANSIENT_NETWORK_ERRORS as error:
                if config.is_debug():
                    raise
                # A block left unfinished at shutdown is read again after the restart
                if back_off_after_network_error(error):
                    return
                if attempt == MAX_BLOCK_ATTEMPTS:
                    logger.warning(f'Giving up on comment IDs {self._describe(block_start)} after {attempt} tries')
            except Exception as error:
                if config.is_debug():
                    raise
                logger.exception(f'Could not read comment IDs {self._describe(block_start)}: {error!r}')
                break
        if comments is None:
            if self.metrics:
                self.metrics.record_failed_block()
        else:
            if self.metrics:
                self.metrics.record_request(time.monotonic() - started, len(comments))
            newest_created = None
            for comment in comments:
                if is_shown_on_r_all(comment):
                    self.handle_comment(comment)
                created = vars(comment).get('created_utc')
                if created is not None and (newest_created is None or created > newest_created):
                    newest_created = created
            if self.metrics and newest_created is not None:
                self.metrics.record_newest_created(newest_created)
        self.finish(block_start)

    def finish(self, block_start):
        with self._lock:
            self.in_flight.discard(block_start)
            if block_start >= self._watermark():
                self.done_above.add(block_start)

    def pending(self):
        """Number of blocks queued or being read."""
        with self._lock:
            return len(self.in_flight)

    @staticmethod
    def _describe(block_start):
        return f'{number_to_comment_id(block_start)}-{number_to_comment_id(block_start + BLOCK_SIZE - 1)}'
