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

import datetime
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
# one comment sometimes returns none (seen live on 2026-10-16); the newest of a few is the head. Even 25 came
# back empty on about every other poll on 2026-10-16, so ask for the most one request can return
HEAD_LISTING_LIMIT = 100

# Empty head polls in a row before a WARNING is logged. A few are normal and cost nothing (the next poll
# catches up), so they are only counted in the heartbeat; a long streak means no new comments are being queued
EMPTY_HEAD_POLLS_BEFORE_WARNING = 6

# Seconds to wait between two head polls (each poll itself takes a few seconds). It only sets how soon a new
# comment is read, not how many: every block up to the head is queued at once. Fewer polls, fewer requests.
HEAD_POLL_SECONDS = 10

# At most this many IDs may wait to be read (about half an hour of Reddit comments, at a guessed 100 per
# second). Further behind, the reader skips ahead to the head and logs how many IDs it skipped, so it never
# works on comments too old to be worth a reply. The same limit decides whether a restart resumes.
MAX_BACKLOG_IDS = 200_000

# How fast the head may plausibly move. Reddit hands out roughly 100 comment IDs a second
# (scripts/verify_comment_ids.py); this allows 20 times that. On 2026-10-17 the r/all listing started returning
# a comment whose ID was about 6 * 10^18, while real IDs were about 8 * 10^10: the reader jumped to it, never
# saw a real head above its position again, and read nothing for hours. A head further ahead of the last
# accepted one than MAX_BACKLOG_IDS plus this rate allows is now rejected instead of followed
MAX_COMMENT_IDS_PER_SECOND = 2_000

# Head polls in a row whose head is far below the reader's position before the reader trusts the head again.
# A position that far ahead of every head can only come from a bogus ID, and it would stall reading for good
HEADS_BELOW_POSITION_BEFORE_RESET = 6

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


def saved_timestamp(updated_at):
    """The Unix time of a cursor's `updated_at`, a naive timestamp in the database's time zone (UTC)."""
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=datetime.timezone.utc)
    return updated_at.timestamp()


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
    """Returns the number of the newest comment on r/all (one request, as /u/AutoCrosspostBot).

    Returns None if the listing came back empty, which Reddit sometimes does even with HEAD_LISTING_LIMIT.
    """
    newest = list(reddit.get_reddit_instance().subreddit('all').comments(limit=HEAD_LISTING_LIMIT))
    return newest_consistent_id([comment.id for comment in newest])


def newest_consistent_id(comment_ids):
    """Returns the highest of `comment_ids` (as a number) that lies near most of the others, or None.

    The listing's comments were all written in the last few seconds, so their IDs lie within a few thousand
    of each other. An ID far from the median (another numbering, or a malformed ID) is not a head to follow.
    """
    numbers = []
    for comment_id in comment_ids:
        try:
            numbers.append(comment_id_to_number(comment_id))
        except (AttributeError, TypeError, ValueError):
            logger.debug(f'Ignoring comment ID {comment_id!r} in the r/all listing: not base 36')
    if not numbers:
        return None
    median = sorted(numbers)[len(numbers) // 2]
    consistent = [number for number in numbers if abs(number - median) <= MAX_BACKLOG_IDS]
    if len(consistent) < len(numbers):
        outliers = sorted(set(numbers) - set(consistent))
        logger.warning(
            f'Ignoring {len(numbers) - len(consistent)} of {len(numbers)} comment IDs in the r/all listing far '
            f'from the others (median {number_to_comment_id(median)}, e.g. {number_to_comment_id(outliers[-1])})'
        )
    return max(consistent)


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
        # Head polls in a row that got an empty listing, so a long streak is logged once, not every poll
        self.empty_head_polls = 0
        # The last head accepted (or the saved cursor) and when (time.time()), to reject implausible jumps
        self.reference_id = None
        self.reference_time = None
        # Head polls in a row rejected as too far ahead, and in a row far below the reader's position
        self.rejected_heads = 0
        self.heads_below_position = 0
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
            self.metrics.record_head_poll(time.monotonic() - started, empty=head is None)
        if head is None:
            # Nothing is lost: the next poll queues every block up to the head it finds
            self.empty_head_polls += 1
            if self.empty_head_polls == EMPTY_HEAD_POLLS_BEFORE_WARNING:
                logger.warning(
                    f'The r/all comment listing returned no comments {self.empty_head_polls} polls in a row; '
                    'no new comments are queued until it does'
                )
            return
        if self.empty_head_polls >= EMPTY_HEAD_POLLS_BEFORE_WARNING:
            logger.info(f'The r/all comment listing has comments again after {self.empty_head_polls} empty poll(s)')
        self.empty_head_polls = 0
        with self._lock:
            if self.next_id is None:
                self._choose_start(head)
            if not self._accept_head(head):
                return
            self._reset_if_far_ahead_of(head)
            self._skip_ahead_if_too_far_behind(head)
            self._queue_blocks_up_to(head)
            lag_ids = head + 1 - self._watermark()
        if self.metrics:
            self.metrics.set_lag_ids(lag_ids)

    def _max_plausible_head(self, now):
        """The highest head that could be real, given the last accepted one; None if there is none yet."""
        if self.reference_id is None:
            return None
        elapsed = max(0.0, now - self.reference_time)
        return self.reference_id + MAX_BACKLOG_IDS + int(MAX_COMMENT_IDS_PER_SECOND * elapsed)

    def _accept_head(self, head):
        """False, with a WARNING once per streak, if `head` is too far ahead to be real. Call with the lock held."""
        now = time.time()
        max_head = self._max_plausible_head(now)
        if max_head is not None and head > max_head:
            self.rejected_heads += 1
            if self.metrics:
                self.metrics.record_rejected_head()
            if self.rejected_heads == 1:
                logger.warning(
                    f'Ignoring head comment ID {number_to_comment_id(head)} ({head:,}): more than '
                    f'{max_head - self.reference_id:,} IDs past the last one accepted, '
                    f'{number_to_comment_id(self.reference_id)}'
                )
            return False
        if self.rejected_heads:
            logger.info(f'Accepted a head comment ID again after {self.rejected_heads} rejected one(s)')
            self.rejected_heads = 0
        if self.reference_id is None or head >= self.reference_id:
            self.reference_id = head
            self.reference_time = now
        return True

    def _reset_if_far_ahead_of(self, head):
        """Moves the position back to `head` if every recent head was far below it. Call with the lock held."""
        if self.next_id - (head + 1) <= MAX_BACKLOG_IDS:
            self.heads_below_position = 0
            return
        self.heads_below_position += 1
        if self.heads_below_position < HEADS_BELOW_POSITION_BEFORE_RESET:
            return
        logger.warning(
            f'Comment reader position {number_to_comment_id(self.next_id)} is {self.next_id - head - 1:,} IDs '
            f'ahead of the newest comment for {self.heads_below_position} polls; restarting at the newest comment'
        )
        self.next_id = head + 1
        self.done_above.clear()
        # The next head becomes the reference, in case this one was a bogus low ID
        self.reference_id = None
        self.reference_time = None
        self.heads_below_position = 0

    def _choose_start(self, head):
        """Resumes from the saved cursor if it is recent enough, otherwise starts at the head.

        A saved cursor also bounds how far ahead the first head may plausibly be (see `_accept_head`), unless
        it is ahead of the head itself, which only a bogus ID can have caused.
        """
        saved = self._load_cursor()
        if saved is not None and saved['next_comment_id'] <= head + 1 and saved.get('updated_at'):
            self.reference_id = saved['next_comment_id']
            self.reference_time = saved_timestamp(saved['updated_at'])
            max_head = self._max_plausible_head(time.time())
            if head > max_head:
                # The first head is bogus: resume from the cursor and wait for a real head
                logger.warning(
                    f'Head comment ID {number_to_comment_id(head)} is implausibly far past the saved cursor '
                    f'{number_to_comment_id(self.reference_id)}; resuming from the cursor'
                )
                self.next_id = saved['next_comment_id']
                self.done_above = {start for start in saved['done_blocks'] or [] if start >= self.next_id}
                return
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
