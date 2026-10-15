"""Renders the crosspost reply for every combination of the things that change its wording, and checks each one.

The wording depends on:
* the target subreddit's locale (one representative subreddit per locale, LOCALE_SUBREDDITS);
* whether the source comment's author still exists (/u/name, or "the user who commented");
* the waiting period, whose day count picks a plural form (1, 2, 5, 21 and 90 days cover every form of every
  locale, including the Hebrew dual and the Russian 1/21 vs 2-4 vs 5+ split);
* the image duplicate scan (IMAGE_SCANS): none (not an image post), complete with 0, 1, 2, 5 or 21 image posts
  checked, or cut short by Reddit's listing limit after 0, 1, 2 or 21.

`render_crosspost_reply` is also handy for looking at the replies by hand.
"""

import itertools
import re
import time
from unittest.mock import MagicMock, patch

import pytest

from racb import i18n
from racb.core.duplicate_detector import ImageRepostScan
from racb.phases import phase3

LOCALE_SUBREDDITS = {
    'en': 'pics',
    'en@totallynotrobots': 'totallynotrobots',
    'de': 'ich_iel',
    'es': 'yo_elvr',
    'fr': 'moi_dlvv',
    'he': 'israel',
    'ru': 'pikabu',
}
AUTHORS = ['someone', None]  # None: the comment's author deleted their account
DAY_COUNTS = [1, 2, 5, 21, 90]
IMAGE_SCANS = {
    'not an image post': None,
    'complete, 0 image posts': dict(image_posts_checked=0, complete=True),
    'complete, 1 image post': dict(image_posts_checked=1, complete=True),
    'complete, 2 image posts': dict(image_posts_checked=2, complete=True),
    'complete, 5 image posts': dict(image_posts_checked=5, complete=True),
    'complete, 21 image posts': dict(image_posts_checked=21, complete=True),
    'cut short, 0 image posts': dict(image_posts_checked=0, complete=False, oldest_days_ago=3),
    'cut short, 1 image post': dict(image_posts_checked=1, complete=False, oldest_days_ago=3),
    'cut short, 2 image posts': dict(image_posts_checked=2, complete=False, oldest_days_ago=3),
    'cut short, 21 image posts': dict(image_posts_checked=21, complete=False, oldest_days_ago=12),
}

COMBINATIONS = list(itertools.product(LOCALE_SUBREDDITS.items(), AUTHORS, DAY_COUNTS, IMAGE_SCANS))


def make_image_scan(scan_name, now):
    settings = IMAGE_SCANS[scan_name]
    if settings is None:
        return None
    settings = dict(settings)
    oldest_days_ago = settings.pop('oldest_days_ago', None)
    oldest_post_utc = now - oldest_days_ago * 86400 if oldest_days_ago is not None else None
    return ImageRepostScan(oldest_post_utc=oldest_post_utc, **settings)


def render_crosspost_reply(target_subreddit, author_name, days, scan_name):
    """Returns the reply AutoCrosspostBot would post under a crosspost made after `days` days."""
    now = time.time()
    comment = MagicMock()
    comment.subreddit.display_name = 'some_source'
    comment.author = MagicMock() if author_name else None
    if author_name:
        comment.author.name = author_name
    comment.permalink = '/r/some_source/comments/abc/title/def/'
    comment.score = 42
    comment.submission.id = 'abc'
    comment.created_utc = now - days * 86400
    cross_post = MagicMock()
    with patch.dict('os.environ', {'PHASE3_WAITING_PERIOD': f'{days} days'}):
        phase3.reply_to_crosspost(comment, cross_post, target_subreddit, image_scan=make_image_scan(scan_name, now))
    return cross_post.reply.call_args.args[0]


def shows_image_sentence(scan_name):
    settings = IMAGE_SCANS[scan_name]
    return settings is not None and (settings['complete'] or settings['image_posts_checked'] > 0)


@pytest.mark.unit
@pytest.mark.parametrize(
    'locale_and_subreddit, author_name, days, scan_name',
    COMBINATIONS,
    ids=[f'{locale}-{author or "deleted"}-{days}d-{scan}' for (locale, _), author, days, scan in COMBINATIONS],
)
def test_crosspost_reply_phrasing(locale_and_subreddit, author_name, days, scan_name):
    locale, target_subreddit = locale_and_subreddit
    assert i18n.get_language(target_subreddit) == locale
    reply = render_crosspost_reply(target_subreddit, author_name, days, scan_name)
    body = reply.split('\n---\n')[0]  # the paragraphs above the footnote

    # Every placeholder was filled, and nothing printed as a Python object
    assert not re.search(r'[{}]', reply), reply
    assert 'MagicMock' not in reply, reply

    # Both subreddits and the source comment are named and linked
    assert 'r/some_source' in body
    assert f'r/{target_subreddit}' in body
    assert '](https://reddit.com/r/some_source/comments/abc/title/def/)' in body

    # Exactly one footnote, the "human-made" one, naming the author or "the user who commented"
    assert body.count('^1') == 1
    assert reply.count('^(1 - ') == 1
    for removed_footnote in ['^2', '^(2', '^3', '^(3']:
        assert removed_footnote not in reply
    author_text = (
        f'/u/{author_name}' if author_name
        else i18n.get_translated_string('THE_USER_WHO_COMMENTED', target_subreddit, add_suffix=False)
    )
    footnote = re.search(r'\^\(1 - (.*)\)', reply).group(1)
    assert author_text in footnote

    # The waiting period is spelled with the right plural form
    assert i18n.format_duration_in_days(days * 86400, target_subreddit) in body

    # The "bad bot" request uses the phrase the inbox recognizes in this language
    bad_bot_phrase = i18n.get_translated_string('BAD_BOT_PHRASE', target_subreddit, add_suffix=False)
    assert bad_bot_phrase.lower() in body.lower()
    assert 'downvote' not in reply.lower()

    # The image sentence appears exactly when images were compared, links to the code, and gives the count
    assert (phase3.DUPLICATE_DETECTOR_SOURCE_URL in body) == shows_image_sentence(scan_name)
    count = (IMAGE_SCANS[scan_name] or {}).get('image_posts_checked', 0)
    if shows_image_sentence(scan_name) and count >= 2:
        assert re.search(rf'\b{count}\b', body)

    # Well-formed markdown: balanced links and superscripts, no stray spacing
    assert body.count('[') == body.count(']')
    assert all(target.startswith('https://') for target in re.findall(r'\]\(([^)]*)\)', reply))
    for line in reply.splitlines():
        if '^(' in line:
            assert line.count('(') == line.count(')'), line
    assert '  ' not in body.replace('\n', ' \n')
    assert ' .' not in body and ' ,' not in body

    # One language per reply, and the footer that every bot comment carries
    if locale not in ('en', 'en@totallynotrobots'):
        assert 'I crossposted' not in reply and 'To prevent reposts' not in reply
    if locale == 'en@totallynotrobots':
        assert 'I CROSSPOSTED' in body and 'BAD HUMAN' in body
    assert reply.rstrip().endswith('(https://www.botrank.net/bots/AutoCrosspostBot)')
