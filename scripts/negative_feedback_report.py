#!/usr/bin/env python3
"""Writes an HTML report of the written feedback people left in "bad bot" exchanges with the bots.

The bots answer "bad bot" style replies with a comment asking why the post or comment was inappropriate (the
RESPOND_TO_NEGATIVE_SENTIMENT text, sent by `respond_to_negative_sentiment` in `racb.phases.inbox`). Only
/u/AutoCrosspostBot sent it from December 2019 until February 2023, when it was switched off; since it was
switched back on, every bot sends it. A "bad bot" reply that already explains itself ("Bad bot, this sub
bans memes", which the crosspost reply asks for) gets no answer; it is found through the bot's inbox instead.
"Bad bot" counts in every supported language.

An exchange is reported only if it contains written feedback: a reply anywhere below the bot's question that
says more than a bare "bad bot" / "good bot", or a "bad bot" comment that itself says more than that. The
report then shows the post and the whole exchange as a tree, from the bot comment that got the "bad bot" reply
down to the last reply, with the feedback highlighted.

Each report covers only feedback that no earlier report in the reports directory covered: every report embeds
the ids of the feedback comments it shows, and the next run reads them back. An exchange that gets new
feedback is shown again in full, with only the new feedback marked as new. Each report links to the earlier ones.

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
from racb.phases.inbox import BOT_NAMES, check_sentiment

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
# Bodies Reddit shows in place of deleted, removed or otherwise hidden comments (compared lowercased)
DELETED_BODIES = {'[deleted]', '[removed]', '[unavailable]'}
INFO_BATCH_SIZE = 100
# Upper bound on "load more" requests per thread, so one huge thread cannot stall the run
MAX_MORE_COMMENTS_REQUESTS = 50
# Number of parent comments Reddit shows above a linked comment; large enough for any thread
COMMENT_LINK_CONTEXT = 10000


@dataclasses.dataclass
class CommentNode:
    """One comment of a feedback thread, with its replies."""

    comment_id: str
    author: str
    body: str
    score: int
    created_utc: float
    permalink: str
    is_bot: bool = False
    is_feedback: bool = False
    is_new: bool = False
    children: list = dataclasses.field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


@dataclasses.dataclass
class FeedbackThread:
    """A "bad bot" exchange that drew written feedback: the post, the bot's comment and the whole reply tree."""

    question_created_utc: float
    subreddit: str
    submission_title: str
    submission_author: str
    submission_permalink: str
    submission_text: str
    submission_url: str | None
    # The bot comment that got the "bad bot" reply, or the trigger itself when it replied to the post
    tree: CommentNode

    def feedback_ids(self):
        return [node.comment_id for node in self.tree.walk() if node.is_feedback]

    def new_feedback_ids(self):
        return [node.comment_id for node in self.tree.walk() if node.is_new]


@dataclasses.dataclass
class PreviousReport:
    file_name: str
    generated_at: str
    feedback_ids: list


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


def find_explained_bad_bot_replies(reddit_instance, limit=None):
    """Finds "bad bot" replies that already explain themselves, which the bots do not answer, through the inbox."""
    found = {}
    for item in _iter_listing('inbox', reddit_instance.inbox.comment_replies(limit=limit)):
        if check_sentiment(getattr(item, 'body', '') or '') == 'explained_negative':
            found[item.id] = item
    return found


def is_bot_account(thing):
    name = author_name(thing)
    return name is not None and name.lower() in {bot_name.lower() for bot_name in BOT_NAMES}


def is_written_feedback(comment):
    """Checks that a comment says more than a bare "bad bot" / "good bot" and is not by a bot or deleted."""
    body = (getattr(comment, 'body', '') or '').strip()
    if not body or body.lower() in DELETED_BODIES or is_bot_account(comment):
        return False
    # A "bad bot" followed by an explanation is written feedback; a bare "bad bot" / "good bot" is not
    return check_sentiment(body) not in ('negative', 'positive')


def collect_subtree(root_comment):
    """Returns every comment below `root_comment`, loading the ones hidden behind "load more" links.

    `CommentForest.replace_more()` is avoided on purpose: on a refreshed comment it raises
    `DuplicateReplaceException` when a "load more" link returns comments the tree already holds. Here the
    "load more" results are read without writing them into PRAW's tree, and duplicates are dropped by id.
    """
    comments = {}
    pending = list(root_comment.replies)
    more_requests = 0
    while pending:
        item = pending.pop(0)
        if isinstance(item, praw.models.MoreComments):
            if more_requests >= MAX_MORE_COMMENTS_REQUESTS:
                logger.warning(f'Stopped loading more replies under {reddit.full_url(root_comment.permalink)}')
                continue
            more_requests += 1
            try:
                pending.extend(item.comments(update=False))
            except (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException) as e:
                logger.warning(f'Could not load more replies under {reddit.full_url(root_comment.permalink)}: {e!r}')
        elif item.id not in comments:
            comments[item.id] = item
            pending.extend(item.replies)
    return list(comments.values())


def comment_url(comment):
    """Links a comment with all of its parent comments shown, not just the comment itself."""
    return f'{reddit.full_url(comment.permalink)}?context={COMMENT_LINK_CONTEXT}'


def to_node(comment):
    return CommentNode(
        comment_id=comment.id,
        author=author_name(comment) or '[deleted]',
        body=comment.body,
        score=comment.score,
        created_utc=comment.created_utc,
        permalink=comment_url(comment),
        is_bot=is_bot_account(comment),
    )


def build_tree(root_comment, descendants):
    """Links `root_comment` and its `descendants` into CommentNodes, oldest reply first."""
    nodes = {comment.id: to_node(comment) for comment in [root_comment, *descendants]}
    parents = {comment.id: comment.parent_id for comment in descendants}
    for comment_id, parent_id in parents.items():
        parent = nodes.get(parent_id.split('_', 1)[1])
        if parent is not None:
            parent.children.append(nodes[comment_id])
    for node in nodes.values():
        node.children.sort(key=lambda child: child.created_utc)
    return nodes[root_comment.id], nodes


def mark_feedback(trigger, question_node, nodes, comments_by_id, covered_ids):
    """Marks the written feedback: the trigger itself if it says more than "bad bot", and any reply below the question."""
    candidates = [trigger] + [comments_by_id[node.comment_id] for node in question_node.walk()][1:]
    for comment in candidates:
        if is_written_feedback(comment):
            node = nodes[comment.id]
            node.is_feedback = True
            node.is_new = comment.id not in covered_ids


def load_thread(reddit_instance, trigger, question, covered_ids):
    """Builds the thread around one "bad bot" comment, or returns None if it has no new written feedback.

    `question` is the bot's RESPOND_TO_NEGATIVE_SENTIMENT answer, or None for a "bad bot" that explained itself
    and got no answer; then every reply below the "bad bot" comment counts.
    """
    trigger.refresh()
    descendants = collect_subtree(trigger)
    trigger_node, nodes = build_tree(trigger, descendants)
    if question is None:
        question_node = trigger_node
    else:
        question_node = nodes.get(question.id)
        if question_node is None:
            logger.warning(f'Could not find {reddit.full_url(question.permalink)} below the comment it answered')
            return None
    comments_by_id = {comment.id: comment for comment in [trigger, *descendants]}
    mark_feedback(trigger, question_node, nodes, comments_by_id, covered_ids)
    if not any(node.is_new for node in trigger_node.walk()):
        return None

    tree = trigger_node
    if trigger.parent_id.startswith('t1_'):
        original = reddit_instance.comment(trigger.parent_id.split('_', 1)[1])
        tree = to_node(original)
        tree.children.append(trigger_node)

    submission = trigger.submission
    return FeedbackThread(
        question_created_utc=(question or trigger).created_utc,
        subreddit=submission.subreddit.display_name,
        submission_title=submission.title,
        submission_author=author_name(submission) or '[deleted]',
        submission_permalink=reddit.full_url(submission.permalink),
        submission_text=submission.selftext if submission.is_self else '',
        submission_url=None if submission.is_self else submission.url,
        tree=tree,
    )


def gather_feedback(bot_name, covered_ids, limit=None):
    reddit_instance = reddit.get_reddit_instance(bot_name)
    markers = message_markers()
    redditor = reddit_instance.redditor(bot_name)

    questions = find_in_comment_history(redditor, markers, limit=limit)
    for comment_id, comment in find_via_inbox(reddit_instance, bot_name, markers, limit=limit).items():
        questions.setdefault(comment_id, comment)
    logger.info(f'/u/{bot_name}: found {len(questions)} RESPOND_TO_NEGATIVE_SENTIMENT comments')

    triggers = fetch_by_fullnames(reddit_instance, [question.parent_id for question in questions.values()])
    threads = []
    seen_trigger_ids = set()
    for question in questions.values():
        trigger = triggers.get(question.parent_id)
        if not isinstance(trigger, praw.models.Comment) or trigger.id in seen_trigger_ids:
            continue
        seen_trigger_ids.add(trigger.id)
        try:
            thread = load_thread(reddit_instance, trigger, question, covered_ids)
        except (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException) as e:
            logger.warning(f'Could not read the replies to {reddit.full_url(question.permalink)}: {e!r}')
            continue
        if thread is not None:
            threads.append(thread)

    explained = find_explained_bad_bot_replies(reddit_instance, limit=limit)
    logger.info(f'/u/{bot_name}: found {len(explained)} "bad bot" replies that explain themselves')
    for trigger in explained.values():
        if trigger.id in seen_trigger_ids:
            continue
        seen_trigger_ids.add(trigger.id)
        try:
            thread = load_thread(reddit_instance, trigger, None, covered_ids)
        except (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException) as e:
            logger.warning(f'Could not read the replies to {reddit.full_url(trigger.permalink)}: {e!r}')
            continue
        if thread is not None:
            threads.append(thread)
    logger.info(f'/u/{bot_name}: {len(threads)} threads with new written feedback')
    return sorted(threads, key=lambda thread: thread.question_created_utc, reverse=True)


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
            logger.warning(f'{file_name} has no embedded report data; its feedback may be reported again')
            continue
        data = json.loads(match.group('data'))
        reports.append(PreviousReport(
            file_name=file_name,
            generated_at=data.get('generated_at', ''),
            # Reports written before the tree layout stored `reply_ids`
            feedback_ids=data.get('feedback_ids', data.get('reply_ids', [])),
        ))
    return reports


def format_utc(timestamp):
    return datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def _quote(body):
    return f'<blockquote>{html.escape(body)}</blockquote>'


def _link(url, text):
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(text)}</a>'


def render_comment(node, show_badges=True):
    classes = ' '.join(['comment'] + [name for name, flag in (
        ('bot', node.is_bot), ('feedback', node.is_feedback), ('new', node.is_new)) if flag])
    badges = ''
    if show_badges:
        if node.is_bot:
            badges += ' <span class="badge">bot</span>'
        if node.is_new:
            badges += ' <span class="badge new">new feedback</span>'
        elif node.is_feedback:
            badges += ' <span class="badge">earlier feedback</span>'
    return f'''<div class="{classes}">
  <p class="meta">{_link(node.permalink, f"u/{node.author}")}{badges} &middot; {format_utc(node.created_utc)} &middot; score {node.score}</p>
  <div class="body">{html.escape(node.body)}</div>
</div>'''


def render_node(node):
    children = ''
    if node.children:
        children = '<ul class="tree">' + ''.join(f'<li>{render_node(child)}</li>' for child in node.children) + '</ul>'
    return render_comment(node) + children


def render_thread(thread):
    """Shows the thread's new feedback, with the post and the whole conversation collapsed below it."""
    post_body = ''
    if thread.submission_text:
        post_body = f'<div class="body">{html.escape(thread.submission_text)}</div>'
    elif thread.submission_url:
        post_body = f'<p class="meta">{_link(thread.submission_url, thread.submission_url)}</p>'
    new_feedback = ''.join(render_comment(node, show_badges=False) for node in thread.tree.walk() if node.is_new)
    return f'''
    <article>
      <h2>{_link(thread.submission_permalink, thread.submission_title)}</h2>
      <p class="meta">r/{html.escape(thread.subreddit)} &middot; posted by u/{html.escape(thread.submission_author)}</p>
      <div class="new-feedback">{new_feedback}</div>
      <details class="context">
        <summary>Show full conversation</summary>
        {post_body}
        <ul class="tree root"><li>{render_node(thread.tree)}</li></ul>
      </details>
    </article>'''


def render_report(threads, previous_reports, generated_at, bot_names):
    feedback_ids = [comment_id for thread in threads for comment_id in thread.feedback_ids()]
    new_count = sum(len(thread.new_feedback_ids()) for thread in threads)
    report_data = json.dumps({'generated_at': generated_at, 'bots': bot_names, 'feedback_ids': feedback_ids})
    # A "</" inside the JSON would end the <script> element early
    report_data = report_data.replace('</', '<\\/')

    if previous_reports:
        previous_items = '\n'.join(
            f'<li>{_link(report.file_name, report.generated_at or report.file_name)}'
            f' &middot; {len(report.feedback_ids)} feedback comments</li>'
            for report in previous_reports
        )
        previous_section = f'<ul class="previous">{previous_items}</ul>'
    else:
        previous_section = '<p>This is the first report.</p>'

    if threads:
        body = '\n'.join(render_thread(thread) for thread in threads)
    else:
        body = '<p>No new written feedback since the previous report.</p>'

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
    --border: #d6d3d1; --accent: #c2410c; --bot: #e7e5e4; --highlight: #fff7ed;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --background: #1c1917; --surface: #292524; --text: #f5f5f4; --muted: #a8a29e;
      --border: #44403c; --accent: #fb923c; --bot: #1c1917; --highlight: #431407;
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
  .body {{ white-space: pre-wrap; overflow-wrap: anywhere; margin: 0.25rem 0; }}
  ul.tree {{ list-style: none; margin: 0; padding: 0 0 0 1rem; border-left: 2px solid var(--border); }}
  ul.tree.root {{ border-left: none; padding-left: 0; margin-top: 0.75rem; }}
  ul.tree li {{ margin-top: 0.5rem; }}
  .comment {{ padding: 0.25rem 0.5rem; border-radius: 0.375rem; }}
  .comment.bot {{ background: var(--bot); }}
  .comment.feedback {{ border-left: 3px solid var(--muted); }}
  .comment.new {{ border-left-color: var(--accent); background: var(--highlight); }}
  .badge {{
    border: 1px solid var(--border); border-radius: 0.25rem; font-size: 0.75rem; margin-left: 0.25rem;
    padding: 0 0.25rem;
  }}
  .badge.new {{ border-color: var(--accent); color: var(--accent); }}
  .new-feedback .comment + .comment {{ margin-top: 0.5rem; }}
  details.context {{ margin-top: 0.75rem; }}
  details.context > summary {{
    display: inline-block; cursor: pointer; list-style: none; color: var(--accent);
    border: 1px solid var(--accent); border-radius: 0.375rem; padding: 0.125rem 0.625rem; font-size: 0.875rem;
  }}
  details.context > summary::-webkit-details-marker {{ display: none; }}
  details.context > summary::before {{ content: "\\25B8  "; }}
  details.context[open] > summary::before {{ content: "\\25BE  "; }}
  details.context[open] > summary {{ margin-bottom: 0.5rem; }}
</style>
</head>
<body>
<header>
  <h1>Negative feedback report</h1>
  <p class="meta">Generated {html.escape(generated_at)} &middot; {new_count} new feedback comments
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
    parser = argparse.ArgumentParser(description='Write an HTML report of new written feedback on the bots\' "bad bot" exchanges.')
    parser.add_argument('--reports-dir', default=DEFAULT_REPORTS_DIR, help=f'Where reports are read and written (default: {DEFAULT_REPORTS_DIR}).')
    parser.add_argument('--bot', action='append', dest='bot_names', help='Bot account to scan; repeatable (default: all 4 bots).')
    parser.add_argument('--limit', type=int, default=0, help='Maximum items to read per listing (default: 0, meaning as many as Reddit returns).')
    parser.add_argument('--write-empty', action='store_true', help='Write a report even when there is no new written feedback.')
    args = parser.parse_args()

    bot_names = args.bot_names or [
        reddit.AUTO_CROSSPOST_BOT_NAME,
        reddit.SUB_DOESNT_EXIST_BOT_NAME,
        reddit.SAME_SUBREDDIT_BOT_NAME,
        reddit.SAME_POST_BOT_NAME,
    ]
    limit = None if args.limit <= 0 else args.limit
    previous_reports = read_previous_reports(args.reports_dir)
    covered_ids = {comment_id for report in previous_reports for comment_id in report.feedback_ids}
    logger.info(f'{len(previous_reports)} earlier reports cover {len(covered_ids)} feedback comments')

    threads = []
    for bot_name in bot_names:
        threads.extend(gather_feedback(bot_name, covered_ids, limit=limit))

    if not threads and not args.write_empty:
        logger.info('No new written feedback; no report written')
        return

    now = datetime.datetime.now(datetime.timezone.utc)
    html_text = render_report(threads, previous_reports, now.strftime('%Y-%m-%d %H:%M UTC'), bot_names)
    path = write_report(args.reports_dir, html_text, now)
    logger.info(f'Wrote {path}')
    logger.info(report_file_url(path))


if __name__ == '__main__':
    main()
