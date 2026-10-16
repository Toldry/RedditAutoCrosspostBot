"""Process-wide shutdown signal shared by the stream loop, the Phase 1 workers and the scheduled jobs.

A SIGTERM (`docker stop`), a SIGINT (Ctrl-C) or a fatal error on any thread calls `request_stop()`.
Every long wait goes through `wait()` instead of `time.sleep()`, and long loops (Phase 2, Phase 3)
check `stop_requested()` between entries, so all threads wind down within a few seconds.
"""

import logging
import threading

logger = logging.getLogger(__name__)

_stop_event = threading.Event()
_fatal_error_lock = threading.Lock()
_fatal_error = None


def request_stop():
    _stop_event.set()


def stop_requested():
    return _stop_event.is_set()


def wait(seconds):
    """Sleeps for `seconds`, waking early on shutdown. Returns True if a shutdown was requested."""
    return _stop_event.wait(seconds)


def fail(error, thread_name):
    """Records an error that escaped a worker or job thread and stops the process.

    Only the first error is kept; `main.start_bot()` re-raises it once every thread has stopped,
    so the process exits non-zero and Docker restarts it.
    """
    global _fatal_error
    with _fatal_error_lock:
        if _fatal_error is None:
            _fatal_error = error
    logger.critical(f'Thread {thread_name} crashed: {error!r}', exc_info=error)
    request_stop()


def fatal_error():
    return _fatal_error


def reset():
    """Clears the stop signal and any recorded error (for tests)."""
    global _fatal_error
    _stop_event.clear()
    with _fatal_error_lock:
        _fatal_error = None
