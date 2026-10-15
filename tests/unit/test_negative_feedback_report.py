"""Unit tests for scripts/negative_feedback_report.py."""

import os
from unittest.mock import MagicMock

import praw
import pytest

from scripts.negative_feedback_report import (
    FeedbackReply,
    FeedbackThread,
    collect_new_replies,
    find_in_comment_history,
    find_via_inbox,
    is_negative_sentiment_comment,
    iter_direct_replies,
    message_markers,
    read_previous_reports,
    render_report,
    write_report,
)

BOT_NAME = 'AutoCrosspostBot'
CURRENT_TEXT = (
    'Thanks for the feedback, would you mind detailing why this post/comment was inappropriate?\n\n'
    'The creator of this bot will look at the responses and try to change the code to reduce incidences like these.'
)
FIRST_VERSION_TEXT = 'Thanks for the feedback, would you mind detailing why this crosspost was inappropriate? \n'


def _comment(comment_id, body, author=BOT_NAME, parent_id='t1_parent', created_utc=1_600_000_000.0):
    comment = MagicMock()
    comment.id = comment_id
    comment.fullname = f't1_{comment_id}'
    comment.body = body
    comment.author = author
    comment.parent_id = parent_id
    comment.score = 3
    comment.created_utc = created_utc
    comment.permalink = f'/r/pics/comments/abc/title/{comment_id}/'
    return comment


def _thread(reply_ids, trigger_body='bad bot'):
    return FeedbackThread(
        bot_comment_id='bot1',
        bot_comment_permalink='https://reddit.com/r/pics/comments/abc/title/bot1/',
        bot_comment_created_utc=1_600_000_000.0,
        subreddit='pics',
        submission_title='A <b>title</b>',
        submission_permalink='https://reddit.com/r/pics/comments/abc/title/',
        trigger_author='someone',
        trigger_body=trigger_body,
        trigger_permalink='https://reddit.com/r/pics/comments/abc/title/trig/',
        replies=[
            FeedbackReply(reply_id, 'replier', f'reply {reply_id} </script> <i>x</i>', 2, 1_600_000_100.0,
                          f'https://reddit.com/r/pics/comments/abc/title/{reply_id}/')
            for reply_id in reply_ids
        ],
    )


@pytest.mark.unit
def test_markers_match_current_and_legacy_wordings():
    markers = message_markers()
    assert is_negative_sentiment_comment(CURRENT_TEXT + '\n\n^footer', markers)
    assert is_negative_sentiment_comment(FIRST_VERSION_TEXT, markers)
    assert not is_negative_sentiment_comment('I crossposted this from r/a to r/b', markers)
    assert not is_negative_sentiment_comment('', markers)


@pytest.mark.unit
def test_comment_history_is_read_in_every_sort_and_deduplicated():
    match = _comment('m1', CURRENT_TEXT)
    other = _comment('o1', 'I crossposted this')
    redditor = MagicMock()
    redditor.comments.new.return_value = [match, other]
    redditor.comments.top.return_value = [match]
    redditor.comments.controversial.return_value = [_comment('m2', FIRST_VERSION_TEXT)]

    found = find_in_comment_history(redditor, message_markers())

    assert set(found) == {'m1', 'm2'}


@pytest.mark.unit
def test_inbox_parents_by_the_bot_with_the_message_are_found():
    reddit_instance = MagicMock()
    reddit_instance.inbox.comment_replies.return_value = [
        _comment('r1', 'because', author='user', parent_id='t1_m1'),
        _comment('r2', 'meh', author='user', parent_id='t3_post'),
    ]
    reddit_instance.info.return_value = [
        _comment('m1', CURRENT_TEXT),
    ]

    found = find_via_inbox(reddit_instance, BOT_NAME, message_markers())

    assert set(found) == {'m1'}
    reddit_instance.info.assert_called_once_with(fullnames=['t1_m1'])


@pytest.mark.unit
def test_inbox_parent_by_someone_else_is_ignored():
    reddit_instance = MagicMock()
    reddit_instance.inbox.comment_replies.return_value = [_comment('r1', 'x', author='user', parent_id='t1_m1')]
    reddit_instance.info.return_value = [_comment('m1', CURRENT_TEXT, author='impostor')]

    assert find_via_inbox(reddit_instance, BOT_NAME, message_markers()) == {}


@pytest.mark.unit
def test_collect_new_replies_skips_covered_deleted_and_bot_replies():
    bot_comment = _comment('bot1', CURRENT_TEXT)
    bot_comment.replies = [
        _comment('new2', 'later', author='b', parent_id='t1_bot1', created_utc=200.0),
        _comment('new1', 'it was a repost', author='a', parent_id='t1_bot1', created_utc=100.0),
        _comment('old', 'seen already', author='c', parent_id='t1_bot1'),
        _comment('gone', '[deleted]', author=None, parent_id='t1_bot1'),
        _comment('self', 'bot talking', author=BOT_NAME, parent_id='t1_bot1'),
    ]

    replies = collect_new_replies(bot_comment, BOT_NAME, covered_reply_ids={'old'})

    assert [reply.reply_id for reply in replies] == ['new1', 'new2']
    assert replies[0].permalink == 'https://reddit.com/r/pics/comments/abc/title/new1/'
    bot_comment.refresh.assert_called_once_with()


def _more_comments(parent_id, loaded):
    more = MagicMock(spec=praw.models.MoreComments)
    more.parent_id = parent_id
    more.comments.return_value = loaded
    return more


@pytest.mark.unit
def test_direct_replies_behind_load_more_are_loaded_without_touching_the_tree():
    bot_comment = _comment('bot1', CURRENT_TEXT)
    nested_more = _more_comments('t1_r1', [])
    more = _more_comments('t1_bot1', [
        _comment('r2', 'hidden reply', author='a', parent_id='t1_bot1'),
        _comment('deep', 'reply to a reply', author='b', parent_id='t1_r1'),
        _comment('r1', 'duplicate of a loaded reply', author='a', parent_id='t1_bot1'),
    ])
    bot_comment.replies = [
        _comment('r1', 'loaded reply', author='a', parent_id='t1_bot1'),
        nested_more,
        more,
    ]

    replies = list(iter_direct_replies(bot_comment))

    assert [reply.id for reply in replies] == ['r1', 'r2']
    more.comments.assert_called_once_with(update=False)
    nested_more.comments.assert_not_called()


@pytest.mark.unit
def test_report_round_trip_records_covered_ids_and_links_earlier_reports(tmp_path):
    reports_dir = str(tmp_path)
    first_html = render_report([_thread(['r1', 'r2'])], [], '2026-10-01 10:00 UTC', [BOT_NAME])
    with open(os.path.join(reports_dir, 'negative-feedback-20261001-100000.html'), 'w', encoding='utf-8') as f:
        f.write(first_html)
    with open(os.path.join(reports_dir, 'notes.html'), 'w', encoding='utf-8') as f:
        f.write('not a report')

    previous = read_previous_reports(reports_dir)

    assert [report.file_name for report in previous] == ['negative-feedback-20261001-100000.html']
    assert previous[0].reply_ids == ['r1', 'r2']
    assert previous[0].generated_at == '2026-10-01 10:00 UTC'

    second_html = render_report([_thread(['r3'])], previous, '2026-10-15 10:00 UTC', [BOT_NAME])
    assert 'href="negative-feedback-20261001-100000.html"' in second_html
    assert '2 responses' in second_html


@pytest.mark.unit
def test_report_escapes_reddit_text():
    report_html = render_report([_thread(['r1'])], [], '2026-10-15 10:00 UTC', [BOT_NAME])

    assert '<b>title</b>' not in report_html
    assert '&lt;i&gt;x&lt;/i&gt;' in report_html
    # Only the report's own data block may close a <script> element
    assert report_html.count('</script>') == 1


@pytest.mark.unit
def test_empty_report_says_so_and_mentions_first_report():
    report_html = render_report([], [], '2026-10-15 10:00 UTC', [BOT_NAME])

    assert 'No new responses' in report_html
    assert 'This is the first report.' in report_html


@pytest.mark.unit
def test_newest_report_is_listed_first(tmp_path):
    reports_dir = str(tmp_path)
    for stamp in ('20260101-000000', '20260301-000000', '20260201-000000'):
        report_html = render_report([], [], stamp, [BOT_NAME])
        with open(os.path.join(reports_dir, f'negative-feedback-{stamp}.html'), 'w', encoding='utf-8') as f:
            f.write(report_html)

    names = [report.file_name for report in read_previous_reports(reports_dir)]

    assert names == [
        'negative-feedback-20260301-000000.html',
        'negative-feedback-20260201-000000.html',
        'negative-feedback-20260101-000000.html',
    ]


@pytest.mark.unit
def test_missing_reports_dir_means_no_earlier_reports(tmp_path):
    assert read_previous_reports(str(tmp_path / 'missing')) == []


@pytest.mark.unit
def test_write_report_creates_the_directory(tmp_path):
    import datetime

    now = datetime.datetime(2026, 10, 15, 8, 30, 5, tzinfo=datetime.timezone.utc)
    path = write_report(str(tmp_path / 'reports'), '<html></html>', now)

    assert path.endswith('negative-feedback-20261015-083005.html')
    assert os.path.isfile(path)
