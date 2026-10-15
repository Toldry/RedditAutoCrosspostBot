"""Unit tests for scripts/negative_feedback_report.py."""

import os
from unittest.mock import MagicMock

import praw
import pytest

from scripts.negative_feedback_report import (
    CommentNode,
    FeedbackThread,
    collect_subtree,
    find_in_comment_history,
    find_via_inbox,
    is_negative_sentiment_comment,
    is_written_feedback,
    load_thread,
    message_markers,
    read_previous_reports,
    render_report,
    report_file_url,
    write_report,
)

BOT_NAME = 'AutoCrosspostBot'
CURRENT_TEXT = (
    'Thanks for the feedback, would you mind detailing why this post/comment was inappropriate?\n\n'
    'The creator of this bot will look at the responses and try to change the code to reduce incidences like these.'
)
FIRST_VERSION_TEXT = 'Thanks for the feedback, would you mind detailing why this crosspost was inappropriate? \n'


def _comment(comment_id, body, author=BOT_NAME, parent_id='t1_parent', created_utc=1_600_000_000.0, replies=()):
    comment = MagicMock(spec=praw.models.Comment)
    comment.id = comment_id
    comment.fullname = f't1_{comment_id}'
    comment.body = body
    comment.author = author
    comment.parent_id = parent_id
    comment.score = 3
    comment.created_utc = created_utc
    comment.permalink = f'/r/pics/comments/abc/title/{comment_id}/'
    comment.replies = list(replies)
    return comment


def _node(comment_id, body, author='someone', is_bot=False, is_feedback=False, is_new=False, children=()):
    return CommentNode(
        comment_id=comment_id,
        author=author,
        body=body,
        score=2,
        created_utc=1_600_000_100.0,
        permalink=f'https://reddit.com/r/pics/comments/abc/title/{comment_id}/',
        is_bot=is_bot,
        is_feedback=is_feedback,
        is_new=is_new,
        children=list(children),
    )


def _thread(feedback_ids, new=True):
    feedback = [
        _node(comment_id, f'reply {comment_id} </script> <i>x</i>', is_feedback=True, is_new=new)
        for comment_id in feedback_ids
    ]
    question = _node('q1', CURRENT_TEXT, author=BOT_NAME, is_bot=True, children=feedback)
    trigger = _node('trig', 'bad bot', children=[question])
    return FeedbackThread(
        question_created_utc=1_600_000_000.0,
        subreddit='pics',
        submission_title='A <b>title</b>',
        submission_author='poster',
        submission_permalink='https://reddit.com/r/pics/comments/abc/title/',
        submission_text='',
        submission_url='https://i.redd.it/x.png',
        tree=_node('orig', 'I crossposted this', author=BOT_NAME, is_bot=True, children=[trigger]),
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


def _more_comments(loaded):
    more = MagicMock(spec=praw.models.MoreComments)
    more.comments.return_value = loaded
    return more


@pytest.mark.unit
def test_written_feedback_needs_more_than_a_sentiment_phrase():
    assert not is_written_feedback(_comment('a', 'Bad bot!', author='user'))
    assert not is_written_feedback(_comment('b', 'good bot', author='user'))
    assert not is_written_feedback(_comment('c', '[deleted]', author=None))
    assert not is_written_feedback(_comment('d', 'I am explaining things', author='same_post_bot'))
    assert is_written_feedback(_comment('e', 'bad bot, this is a repost of a repost', author='user'))


@pytest.mark.unit
def test_subtree_loads_load_more_without_touching_the_tree_and_drops_duplicates():
    r1 = _comment('r1', 'loaded reply', author='a', parent_id='t1_q1')
    question = _comment('q1', CURRENT_TEXT, parent_id='t1_trig', replies=[r1])
    more = _more_comments([
        _comment('r2', 'hidden reply', author='b', parent_id='t1_q1'),
        _comment('r1', 'duplicate', author='a', parent_id='t1_q1'),
    ])
    trigger = _comment('trig', 'bad bot', author='user', replies=[question, more])

    comments = collect_subtree(trigger)

    assert sorted(comment.id for comment in comments) == ['q1', 'r1', 'r2']
    more.comments.assert_called_once_with(update=False)


def _exchange(question_replies):
    question = _comment('q1', CURRENT_TEXT, parent_id='t1_trig', created_utc=110.0, replies=question_replies)
    sibling = _comment('s1', 'lol', author='lurker', parent_id='t1_trig', created_utc=105.0)
    trigger = _comment('trig', 'bad bot', author='user_a', parent_id='t1_orig', created_utc=100.0,
                       replies=[question, sibling])
    trigger.submission.subreddit.display_name = 'pics'
    trigger.submission.title = 'A post'
    trigger.submission.author = 'poster'
    trigger.submission.permalink = '/r/pics/comments/abc/a_post/'
    trigger.submission.is_self = True
    trigger.submission.selftext = 'post text'
    reddit_instance = MagicMock()
    reddit_instance.comment.return_value = _comment('orig', 'I crossposted this', parent_id='t3_abc', created_utc=50.0)
    return reddit_instance, trigger, question


@pytest.mark.unit
def test_thread_with_written_feedback_is_shown_as_a_full_tree():
    reply_b = _comment('b1', 'It was a repost', author='user_b', parent_id='t1_q1', created_utc=200.0)
    reply_c = _comment('c1', 'Wrong sub entirely', author='user_c', parent_id='t1_q1', created_utc=300.0)
    bare = _comment('d1', 'bad bot', author='user_d', parent_id='t1_q1', created_utc=150.0)
    reddit_instance, trigger, question = _exchange([reply_c, bare, reply_b])

    thread = load_thread(reddit_instance, trigger, question, covered_ids={'c1'})

    trigger.refresh.assert_called_once_with()
    reddit_instance.comment.assert_called_once_with('orig')
    assert thread.tree.comment_id == 'orig'
    trigger_node = thread.tree.children[0]
    assert [child.comment_id for child in trigger_node.children] == ['s1', 'q1']
    question_node = trigger_node.children[1]
    assert [child.comment_id for child in question_node.children] == ['d1', 'b1', 'c1']
    assert sorted(thread.feedback_ids()) == ['b1', 'c1']
    assert thread.new_feedback_ids() == ['b1']
    assert thread.submission_text == 'post text'
    assert question_node.children[1].permalink == 'https://reddit.com/r/pics/comments/abc/title/b1/?context=10000'


@pytest.mark.unit
def test_thread_with_only_bare_sentiment_replies_is_skipped():
    bare = _comment('d1', 'Bad bot.', author='user_d', parent_id='t1_q1')
    reddit_instance, trigger, question = _exchange([bare])

    assert load_thread(reddit_instance, trigger, question, covered_ids=set()) is None


@pytest.mark.unit
def test_thread_whose_feedback_was_all_reported_is_skipped():
    reply_b = _comment('b1', 'It was a repost', author='user_b', parent_id='t1_q1')
    reddit_instance, trigger, question = _exchange([reply_b])

    assert load_thread(reddit_instance, trigger, question, covered_ids={'b1'}) is None


@pytest.mark.unit
def test_detailed_trigger_counts_as_feedback():
    reddit_instance, trigger, question = _exchange([])
    trigger.body = 'bad bot, the target sub bans crossposts'

    thread = load_thread(reddit_instance, trigger, question, covered_ids=set())

    assert thread.new_feedback_ids() == ['trig']


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
    assert previous[0].feedback_ids == ['r1', 'r2']
    assert previous[0].generated_at == '2026-10-01 10:00 UTC'

    second_html = render_report([_thread(['r3'])], previous, '2026-10-15 10:00 UTC', [BOT_NAME])
    assert 'href="negative-feedback-20261001-100000.html"' in second_html
    assert '2 feedback comments' in second_html


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

    assert 'No new written feedback' in report_html
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


@pytest.mark.unit
def test_report_file_url_is_an_absolute_file_url(tmp_path):
    path = tmp_path / 'negative-feedback-20261015-083005.html'

    url = report_file_url(str(path))

    assert url.startswith('file:///')
    assert url.endswith('/negative-feedback-20261015-083005.html')


@pytest.mark.unit
def test_reports_from_before_the_tree_layout_still_count_as_covered(tmp_path):
    old_report = '<script type="application/json" id="racb-report-data">{"generated_at": "x", "reply_ids": ["r1"]}</script>'
    with open(tmp_path / 'negative-feedback-20261001-100000.html', 'w', encoding='utf-8') as f:
        f.write(old_report)

    assert read_previous_reports(str(tmp_path))[0].feedback_ids == ['r1']


@pytest.mark.unit
def test_report_marks_new_feedback_and_includes_the_whole_exchange():
    report_html = render_report([_thread(['r1'])], [], '2026-10-15 10:00 UTC', [BOT_NAME])

    assert 'new feedback' in report_html
    assert 'u/AutoCrosspostBot' in report_html
    assert 'I crossposted this' in report_html
    assert report_html.count('class="tree') == 4  # post > bot comment > bad bot > question > feedback
