"""Centralized parsing of environment-based configuration values."""

import logging
import os
import re

import dotenv
from pytimeparse.timeparse import timeparse

logger = logging.getLogger(__name__)

TRUTHY_VALUES = ('true', '1', 'yes', 'y', 't')

SECONDS_PER_DAY = 86400

# pytimeparse has no notion of months or years, so those units are approximated here
CALENDAR_UNIT_SECONDS = {
    'month': 30 * SECONDS_PER_DAY,
    'year': 365 * SECONDS_PER_DAY,
}
calendar_duration_regex = re.compile(
    r'^\s*(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>month|year)s?\s*$',
    flags=re.IGNORECASE,
)

PHASE2_WAITING_PERIOD_DEFAULT = '2 hours'
PHASE3_WAITING_PERIOD_DEFAULT = '90 days'


def load_environment():
    """Loads `.env` into `os.environ` without overriding variables that are already set.

    Must run before anything reads configuration (including logging setup). Safe to call repeatedly.
    """
    dotenv.load_dotenv()


def env_bool(key, default=False):
    val = os.environ.get(key)
    if val is None:
        return default
    return str(val).strip().lower() in TRUTHY_VALUES


def is_debug():
    return env_bool('DEBUG', False)


def is_dry_run():
    """When true, the bots never write to Reddit (no replies, crossposts or deletions)."""
    return env_bool('DRY_RUN', False)


def is_listen_only():
    return env_bool('LISTEN_ONLY', False)


def parse_duration(text):
    """Parses a human-readable duration into seconds.

    Accepts everything `pytimeparse` understands (e.g. `2 hours`, `90 days`, `1w 2d`) plus
    `N month(s)` (30 days each) and `N year(s)` (365 days each). Returns None if unparseable.
    """
    if text is None:
        return None
    calendar_match = calendar_duration_regex.match(text)
    if calendar_match:
        amount = float(calendar_match.group('amount'))
        unit_seconds = CALENDAR_UNIT_SECONDS[calendar_match.group('unit').lower()]
        return int(amount * unit_seconds)
    seconds = timeparse(text)
    if seconds is None:
        return None
    return int(seconds)


def env_duration_seconds(key, default_text):
    """Reads a duration from the environment, warning loudly (instead of silently) on bad values."""
    raw_value = os.environ.get(key, default_text)
    seconds = parse_duration(raw_value)
    if seconds is None:
        logger.warning(f'Could not parse {key}={raw_value!r} as a duration; using default {default_text!r}')
        seconds = parse_duration(default_text)
    return seconds


def phase2_waiting_period_seconds():
    return env_duration_seconds('PHASE2_WAITING_PERIOD', PHASE2_WAITING_PERIOD_DEFAULT)


def phase3_waiting_period_seconds():
    return env_duration_seconds('PHASE3_WAITING_PERIOD', PHASE3_WAITING_PERIOD_DEFAULT)


def comment_score_threshold():
    return int(os.environ.get('COMMENT_SCORE_THRESHOLD', '1'))
