#!/usr/bin/env python3
"""Writes an HTML report of the replies people left under the bot's RESPOND_TO_NEGATIVE_SENTIMENT comments.

From December 2019 until February 2023, the bot answered "bad bot" style replies with a comment asking why the
post or comment was inappropriate (the RESPOND_TO_NEGATIVE_SENTIMENT text). The answers to that question are
the feedback this report collects. The bot no longer sends that comment (`respond_to_negative_sentiment` in
`racb.phases.inbox` is a no-op), so new feedback only appears if someone replies to one of the old comments.

Each report covers only replies that no earlier report in the reports directory covered: every report embeds
the ids of the replies it shows, and the next run reads them back. Each report also links to the earlier ones.

Reddit lists at most about 1000 items per listing, so the script reads the bot's comment history in several
sort orders and also checks the parents of the replies in its inbox. Comments that fall outside all of these
windows cannot be found through the API.
"""

import argparse
import dataclasses
import datetime
import html
import json
import logging
import os
import pathlib
import re
import sys

# Ensure src/ directory is on sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(PROJECT_ROOT, 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import praw
import prawcore
from racb import i18n
from racb.core import reddit

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)-8s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

DEFAULT_REPORTS_DIR = os.path.join(PROJECT_ROOT, 'reports', 'negative_feedback')
REPORT_FILE_PREFIX = 'negative-feedback-'
REPORT_FILE_PATTERN = re.compile(rf'^{REPORT_FILE_PREFIX}\d{{8}}-\d{{6}}\.html$')
REPORT_DATA_ELEMENT_ID = 'racb-report-data'
REPORT_DATA_PATTERN = re.compile(
    rf'<script type="application/json" id="{REPORT_DATA_ELEMENT_ID}">(?P<data>.*?)</script>',
    re.DOTALL,
)
MESSAGE_KEY = 'RESPOND_TO_NEGATIVE_SENTIMENT'
# Earlier wordings of the message, which only ever went out in English. The first version (2019) said
# "why this crosspost was inappropriate", later ones "why this post/comment was inappropriate".
LEGACY_MESSAGE_PREFIXES = ('Thanks for the feedback, would you mind detailing why this',)
DELETED_BODIES = {'[deleted]', '[removed]'}
INFO_BATCH_SIZE = 100


@dataclasses.dataclass
class FeedbackThread:
    """One RESPOND_TO_NEGATIVE_SENTIMENT comment and the new replies to it."""

    bot_comment_id: str
    bot_comment_permalink: str
    bot_comment_created_utc: float
    subreddit: str
    submission_title: str
    submission_permalink: str
    trigger_author: str | None = None
    trigger_body: str | None = None
    trigger_permalink: str | None = None
    replies: list = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class FeedbackReply:
    reply_id: str
    author: str
    body: str
    score: int
    created_utc: float
    permalink: str


@dataclasses.dataclass
class PreviousReport:
    file_name: str
    generated_at: str
    reply_ids: list


def normalize_text(text):
    return re.sub(r'\s+', ' ', text or '').strip().lower()


def message_markers():
    """Returns normalized text fragments that identify a RESPOND_TO_NEGATIVE_SENTIMENT comment in any locale."""
    markers = {normalize_text(prefix) for prefix in LEGACY_MESSAGE_PREFIXES}
    for locale in i18n.available_locales():
        try:
            _, text = i18n.lookup_in_languages(MESSAGE_KEY, [locale])
        except KeyError:
            continue
        first_line = next((line for line in text.splitlines() if line.strip()), '')
        if first_line:
            markers.add(normalize_text(first_line))
    return markers


def is_negative_sentiment_comment(body, markers):
    normalized_body = normalize_text(body)
    return any(marker in normalized_body for marker in markers)


def author_name(thing):
    author = getattr(thing, 'author', None)
    return str(author) if author is not None else None


def is_by(thing, bot_name):
    name = author_name(thing)
    return name is not None and name.lower() == bot_name.lower()


def _iter_listing(listing_name, listing):
    try:
        yield from listing
    except (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException) as e:
        logger.warning(f'Could not read the {listing_name} listing completely: {e!r}')


def find_in_comment_history(redditor, markers, limit=None):
    """Searches the bot's own comments, in every sort order that reaches a different window of its history."""
    listings = {
        'new': redditor.comments.new(limit=limit),
        'top': redditor.comments.top(time_filter='all', limit=limit),
        'controversial': redditor.comments.controversial(time_filter='all', limit=limit),
    }
    found = {}
    for listing_name, listing in listings.items():
        scanned = 0
        for comment in _iter_listing(listing_name, listing):
            scanned += 1
            if comment.id not in found and is_negative_sentiment_comment(comment.body, markers):
                found[comment.id] = comment
        logger.info(f'Scanned {scanned} comments sorted by {listing_name}; {len(found)} matching so far')
    return found


def fetch_by_fullnames(reddit_instance, fullnames):
    """Fetches comments and submissions by fullname, 100 per request."""
    fullnames = list(dict.fromkeys(fullnames))
    things = {}
    for start in range(0, len(fullnames), INFO_BATCH_SIZE):
        batch = fullnames[start:start + INFO_BATCH_SIZE]
        try:
            for thing in reddit_instance.info(fullnames=batch):
                things[thing.fullname] = thing
        except (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException) as e:
            logger.warning(f'Could not fetch {len(batch)} items by id: {e!r}')
    return things


def find_via_inbox(reddit_instance, bot_name, markers, limit=None):
    """Finds RESPOND_TO_NEGATIVE_SENTIMENT comments that someone replied to, through the replies in the inbox."""
    parent_fullnames = []
    for item in _iter_listing('inbox', reddit_instance.inbox.comment_replies(limit=limit)):
        parent_id = getattr(item, 'parent_id', '') or ''
        if parent_id.startswith('t1_'):
            parent_fullnames.append(parent_id)
    logger.info(f'Checking {len(set(parent_fullnames))} commented-on bot comments from the inbox')
    found = {}
    for parent in fetch_by_fullnames(reddit_instance, parent_fullnames).values():
        if is_by(parent, bot_name) and is_negative_sentiment_comment(getattr(parent, 'body', ''), markers):
            found[parent.id] = parent
    return found


def iter_direct_replies(bot_comment):
    """Yields every direct reply to `bot_comment`, loading the ones hidden behind "load more" links.

    `CommentForest.replace_more()` is avoided on purpose: on a refreshed comment it raises
    `DuplicateReplaceException` when a "load more" link returns comments the tree already holds. Only the
    "load more" links among the direct replies are expanded here, without writing into the tree.
    """
    seen_ids = set()
    pending = list(bot_comment.replies)
    while pending:
        item = pending.pop(0)
        if item.parent_id != bot_comment.fullname:
            continue
        if isinstance(item, praw.models.MoreComments):
            try:
                pending.extend(item.comments(update=False))
            except (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException) as e:
                logger.warning(f'Could not load more replies to {reddit.full_url(bot_comment.permalink)}: {e!r}')
        elif item.id not in seen_ids:
            seen_ids.add(item.id)
            yield item


def collect_new_replies(bot_comment, bot_name, covered_reply_ids):
    """Returns the direct replies to `bot_comment` that are not by the bot and not covered yet."""
    bot_comment.refresh()
    replies = []
    for reply in iter_direct_replies(bot_comment):
        if reply.id in covered_reply_ids or is_by(reply, bot_name):
            continue
        if (reply.body or '').strip() in DELETED_BODIES:
            continue
        replies.append(FeedbackReply(
            reply_id=reply.id,
            author=author_name(reply) or '[deleted]',
            body=reply.body,
            score=reply.score,
            created_utc=reply.created_utc,
            permalink=reddit.full_url(reply.permalink),
        ))
    return sorted(replies, key=lambda feedback_reply: feedback_reply.created_utc)


def build_thread(bot_comment, replies):
    submission_title = getattr(bot_comment, 'link_title', None)
    submission_permalink = getattr(bot_comment, 'link_permalink', None)
    if submission_title is None or submission_permalink is None:
        submission = bot_comment.submission
        submission_title = submission.title
        submission_permalink = reddit.full_url(submission.permalink)
    return FeedbackThread(
        bot_comment_id=bot_comment.id,
        bot_comment_permalink=reddit.full_url(bot_comment.permalink),
        bot_comment_created_utc=bot_comment.created_utc,
        subreddit=bot_comment.subreddit.display_name,
        submission_title=submission_title,
        submission_permalink=submission_permalink,
        replies=replies,
    )


def add_trigger_comments(reddit_instance, threads, bot_comments):
    """Adds the "bad bot" comment that each RESPOND_TO_NEGATIVE_SENTIMENT comment answered."""
    parent_ids = {thread.bot_comment_id: bot_comments[thread.bot_comment_id].parent_id for thread in threads}
    parents = fetch_by_fullnames(reddit_instance, [fullname for fullname in parent_ids.values() if fullname.startswith('t1_')])
    for thread in threads:
        parent = parents.get(parent_ids[thread.bot_comment_id])
        if parent is None:
            continue
        thread.trigger_author = author_name(parent) or '[deleted]'
        thread.trigger_body = parent.body
        thread.trigger_permalink = reddit.full_url(parent.permalink)


def gather_feedback(bot_name, covered_reply_ids, limit=None):
    reddit_instance = reddit.get_reddit_instance(bot_name)
    markers = message_markers()
    redditor = reddit_instance.redditor(bot_name)

    bot_comments = find_in_comment_history(redditor, markers, limit=limit)
    for comment_id, comment in find_via_inbox(reddit_instance, bot_name, markers, limit=limit).items():
        bot_comments.setdefault(comment_id, comment)
    logger.info(f'/u/{bot_name}: found {len(bot_comments)} RESPOND_TO_NEGATIVE_SENTIMENT comments')

    threads = []
    for bot_comment in bot_comments.values():
        try:
            replies = collect_new_replies(bot_comment, bot_name, covered_reply_ids)
            if replies:
                threads.append(build_thread(bot_comment, replies))
        except (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException) as e:
            logger.warning(f'Could not read the replies to {reddit.full_url(bot_comment.permalink)}: {e!r}')
    add_trigger_comments(reddit_instance, threads, bot_comments)
    return sorted(threads, key=lambda thread: thread.bot_comment_created_utc, reverse=True)


def read_previous_reports(reports_dir):
    """Returns the earlier reports in `reports_dir`, newest first."""
    if not os.path.isdir(reports_dir):
        return []
    reports = []
    for file_name in sorted(os.listdir(reports_dir), reverse=True):
        if not REPORT_FILE_PATTERN.match(file_name):
            continue
        with open(os.path.join(reports_dir, file_name), encoding='utf-8') as report_file:
            match = REPORT_DATA_PATTERN.search(report_file.read())
        if match is None:
            logger.warning(f'{file_name} has no embedded report data; its replies may be reported again')
            continue
        data = json.loads(match.group('data'))
        reports.append(PreviousReport(
            file_name=file_name,
            generated_at=data.get('generated_at', ''),
            reply_ids=data.get('reply_ids', []),
        ))
    return reports


def format_utc(timestamp):
    return datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def _quote(body):
    return f'<blockquote>{html.escape(body)}</blockquote>'


def _link(url, text):
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(text)}</a>'


def render_thread(thread):
    trigger = ''
    if thread.trigger_body is not None:
        trigger = f'''
      <p class="label">{_link(thread.trigger_permalink, f"u/{thread.trigger_author}")} said</p>
      {_quote(thread.trigger_body)}'''
    replies = '\n'.join(f'''
      <li class="reply">
        <p class="meta">{_link(reply.permalink, f"u/{reply.author}")} &middot; {format_utc(reply.created_utc)} &middot; score {reply.score}</p>
        {_quote(reply.body)}
      </li>''' for reply in thread.replies)
    return f'''
    <article>
      <h2>{_link(thread.submission_permalink, thread.submission_title)}</h2>
      <p class="meta">r/{html.escape(thread.subreddit)} &middot; bot asked for feedback
        {_link(thread.bot_comment_permalink, format_utc(thread.bot_comment_created_utc))}</p>{trigger}
      <p class="label">Responses</p>
      <ul class="replies">{replies}
      </ul>
    </article>'''


def render_report(threads, previous_reports, generated_at, bot_names):
    reply_ids = [reply.reply_id for thread in threads for reply in thread.replies]
    report_data = json.dumps({'generated_at': generated_at, 'bots': bot_names, 'reply_ids': reply_ids})
    # A "</" inside the JSON would end the <script> element early
    report_data = report_data.replace('</', '<\\/')

    if previous_reports:
        previous_items = '\n'.join(
            f'<li>{_link(report.file_name, report.generated_at or report.file_name)}'
            f' &middot; {len(report.reply_ids)} responses</li>'
            for report in previous_reports
        )
        previous_section = f'<ul class="previous">{previous_items}</ul>'
    else:
        previous_section = '<p>This is the first report.</p>'

    if threads:
        body = '\n'.join(render_thread(thread) for thread in threads)
    else:
        body = '<p>No new responses since the previous report.</p>'

    bots = ', '.join(f'u/{name}' for name in bot_names)
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Negative Feedback Report</title>
<style>
  :root {{
    --background: #ffffff; --surface: #f5f5f4; --text: #1c1917; --muted: #57534e;
    --border: #d6d3d1; --accent: #c2410c;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --background: #1c1917; --surface: #292524; --text: #f5f5f4; --muted: #a8a29e;
      --border: #44403c; --accent: #fb923c;
    }}
  }}
  body {{
    background: var(--background); color: var(--text); margin: 0 auto; max-width: 52rem;
    padding: 1.5rem 1rem; font: 16px/1.5 system-ui, sans-serif;
  }}
  a {{ color: var(--accent); }}
  h1 {{ margin-bottom: 0.25rem; }}
  h2 {{ font-size: 1.1rem; margin: 0 0 0.25rem; overflow-wrap: anywhere; }}
  .meta, .label {{ color: var(--muted); font-size: 0.875rem; margin: 0.25rem 0; }}
  .label {{ font-weight: 600; margin-top: 0.75rem; }}
  article {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 0.5rem;
    margin: 1rem 0; padding: 1rem;
  }}
  blockquote {{
    border-left: 3px solid var(--border); margin: 0.25rem 0; padding: 0 0 0 0.75rem;
    white-space: pre-wrap; overflow-wrap: anywhere;
  }}
  ul.replies {{ list-style: none; margin: 0; padding: 0; }}
  li.reply + li.reply {{ border-top: 1px solid var(--border); margin-top: 0.5rem; padding-top: 0.5rem; }}
</style>
</head>
<body>
<header>
  <h1>Negative feedback report</h1>
  <p class="meta">Generated {html.escape(generated_at)} &middot; {len(reply_ids)} new responses
    in {len(threads)} threads &middot; {html.escape(bots)}</p>
</header>
<main>
{body}
</main>
<section>
  <h2>Earlier reports</h2>
  {previous_section}
</section>
<script type="application/json" id="{REPORT_DATA_ELEMENT_ID}">{report_data}</script>
</body>
</html>
'''


def write_report(reports_dir, html_text, now):
    os.makedirs(reports_dir, exist_ok=True)
    path = os.path.join(reports_dir, f'{REPORT_FILE_PREFIX}{now:%Y%m%d-%H%M%S}.html')
    with open(path, 'w', encoding='utf-8') as report_file:
        report_file.write(html_text)
    return path


def report_file_url(path):
    """Returns a file:/// URL for `path`, which terminals show as a clickable link."""
    return pathlib.Path(path).resolve().as_uri()


def main():
    parser = argparse.ArgumentParser(description='Write an HTML report of new replies to the bot\'s requests for feedback.')
    parser.add_argument('--reports-dir', default=DEFAULT_REPORTS_DIR, help=f'Where reports are read and written (default: {DEFAULT_REPORTS_DIR}).')
    parser.add_argument('--bot', action='append', dest='bot_names', help=f'Bot account to scan; repeatable (default: {reddit.AUTO_CROSSPOST_BOT_NAME}, the only one that ever sent the message).')
    parser.add_argument('--limit', type=int, default=0, help='Maximum items to read per listing (default: 0, meaning as many as Reddit returns).')
    parser.add_argument('--write-empty', action='store_true', help='Write a report even when there are no new responses.')
    args = parser.parse_args()

    bot_names = args.bot_names or [reddit.AUTO_CROSSPOST_BOT_NAME]
    limit = None if args.limit <= 0 else args.limit
    previous_reports = read_previous_reports(args.reports_dir)
    covered_reply_ids = {reply_id for report in previous_reports for reply_id in report.reply_ids}
    logger.info(f'{len(previous_reports)} earlier reports cover {len(covered_reply_ids)} responses')

    threads = []
    for bot_name in bot_names:
        threads.extend(gather_feedback(bot_name, covered_reply_ids, limit=limit))

    if not threads and not args.write_empty:
        logger.info('No new responses; no report written')
        return

    now = datetime.datetime.now(datetime.timezone.utc)
    html_text = render_report(threads, previous_reports, now.strftime('%Y-%m-%d %H:%M UTC'), bot_names)
    path = write_report(args.reports_dir, html_text, now)
    logger.info(f'Wrote {path}')
    logger.info(report_file_url(path))


if __name__ == '__main__':
    main()
