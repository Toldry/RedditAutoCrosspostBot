"""Background threads: the scheduled jobs and the Phase 1 worker pool.

The main thread only reads the r/all comment stream (see `main.py`). Everything slow runs here, so a long
Phase 3 run, an inbox pass or a Phase 1 duplicate search no longer stops the stream from reading.
Each thread gets its own PRAW instances (`reddit.get_reddit_instance`) and database connection
(`db.get_connection`) the first time it uses them.
"""

import logging
import queue
import threading

import prawcore

from racb import config
from racb.core import db, reddit, shutdown

logger = logging.getLogger(__name__)

TOO_MANY_REQUESTS_DELAY_SECONDS = 60
NETWORK_RETRY_DELAY_SECONDS = 30

# How often a thread waiting for work or for a lock checks for shutdown
POLL_SECONDS = 1

# Matching comments waiting for a Phase 1 worker. When all workers are busy for this long,
# new matches are dropped (and counted in the heartbeat) instead of blocking the stream.
PHASE1_QUEUE_SIZE = 1000


def run_job_safely(job_func):
    """Runs a scheduled job, logging (instead of propagating) its errors.

    A job that raises would otherwise end its thread, and the job would never run again.
    """
    try:
        return job_func()
    except Exception as e:
        if config.is_debug():
            raise
        if isinstance(e, db.CONNECTION_ERRORS):
            db.reset_connection()
        logger.exception(f'Scheduled job {job_func.__name__} failed: {e!r}')
        return None


class PeriodicJob(threading.Thread):
    """Runs `job_func` on its own thread every `interval_seconds`, counted from the end of the previous run.

    The first run is one interval after start (`run_at_start` runs it immediately). A run never overlaps
    the previous run of the same job. Jobs that share a `lock` never run at the same time as each other.
    """

    def __init__(self, name, interval_seconds, job_func, lock=None, run_at_start=False):
        super().__init__(name=name, daemon=True)
        self.interval_seconds = interval_seconds
        self.job_func = job_func
        self.lock = lock
        self.run_at_start = run_at_start

    def run(self):
        try:
            if not self.run_at_start and shutdown.wait(self.interval_seconds):
                return
            while not shutdown.stop_requested():
                self.run_once()
                if shutdown.wait(self.interval_seconds):
                    return
        except BaseException as e:
            shutdown.fail(e, self.name)

    def run_once(self):
        if self.lock is None:
            run_job_safely(self.job_func)
            return
        while not self.lock.acquire(timeout=POLL_SECONDS):
            if shutdown.stop_requested():
                return
        try:
            run_job_safely(self.job_func)
        finally:
            self.lock.release()


def back_off_after_network_error(error, thread_name):
    """Waits after a rate limit or network error. Returns True if a shutdown was requested meanwhile."""
    if isinstance(error, prawcore.exceptions.TooManyRequests):
        delay = TOO_MANY_REQUESTS_DELAY_SECONDS
        logger.warning(f'{thread_name}: rate limited by Reddit ({error}). Waiting {delay}s.')
    else:
        delay = NETWORK_RETRY_DELAY_SECONDS
        logger.info(f'{thread_name}: network error {error!r}. Waiting {delay}s.')
    return shutdown.wait(delay)


class Phase1Workers:
    """A pool of threads that run `handle_comment_id(comment_id)` for matching stream comments.

    The stream thread calls `submit()` with comment IDs, not PRAW objects: a comment from the stream is
    bound to the stream thread's PRAW instance, so each worker fetches it again through its own.
    """

    def __init__(self, handle_comment_id, worker_count, metrics=None, queue_size=PHASE1_QUEUE_SIZE):
        self.handle_comment_id = handle_comment_id
        self.metrics = metrics
        self.queue = queue.Queue(maxsize=queue_size)
        self.threads = [
            threading.Thread(target=self.work, name=f'phase1-{number}', daemon=True)
            for number in range(1, worker_count + 1)
        ]

    def start(self):
        for thread in self.threads:
            thread.start()
        return self

    def submit(self, comment_id):
        """Queues a comment for Phase 1 without blocking. Returns False if the queue is full."""
        try:
            self.queue.put_nowait(comment_id)
            return True
        except queue.Full:
            logger.warning(f'Phase 1 queue is full ({self.queue.maxsize}); dropping comment {comment_id}')
            if self.metrics:
                self.metrics.record_dropped()
            return False

    def pending(self):
        return self.queue.qsize()

    def work(self):
        thread_name = threading.current_thread().name
        try:
            while not shutdown.stop_requested():
                try:
                    comment_id = self.queue.get(timeout=POLL_SECONDS)
                except queue.Empty:
                    continue
                try:
                    self.handle_comment_id(comment_id)
                except reddit.TRANSIENT_NETWORK_ERRORS as e:
                    if config.is_debug():
                        raise
                    # The comment is skipped, as when the single-threaded loop rebuilt the stream
                    logger.info(f'Skipping comment {comment_id} after network error')
                    if back_off_after_network_error(e, thread_name):
                        return
        except BaseException as e:
            shutdown.fail(e, thread_name)
