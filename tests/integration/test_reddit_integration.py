"""Integration tests interacting directly with live Reddit APIs using test subreddits."""

import time
import uuid
import pytest

from racb.core import reddit
from racb.phases import phase1, phase3, cleanup

# Hardcoded test subreddits created for integration testing
TEST_SOURCE_SUBREDDIT = 'racb_test_1'
TEST_TARGET_SUBREDDIT = 'racb_test_2'


@pytest.fixture(autouse=True)
def configure_integration_test_environment(monkeypatch):
    """Configures shortened waiting periods for integration testing."""
    monkeypatch.setenv('PHASE2_WAITING_PERIOD', '0 seconds')
    monkeypatch.setenv('PHASE3_WAITING_PERIOD', '0 seconds')
    monkeypatch.setenv('COMMENT_SCORE_THRESHOLD', '1')


@pytest.mark.integration
def test_live_reddit_auth_all_bots():
    """Verifies all 4 bot accounts authenticate successfully against Reddit API."""
    bot_names = [
        reddit.AUTO_CROSSPOST_BOT_NAME,
        reddit.SUB_DOESNT_EXIST_BOT_NAME,
        reddit.SAME_SUBREDDIT_BOT_NAME,
        reddit.SAME_POST_BOT_NAME,
    ]
    for bot_name in bot_names:
        r = reddit.get_reddit_instance(bot_name)
        me = r.user.me()
        assert me is not None, f"Bot {bot_name} failed to authenticate"
        assert me.name.lower() == bot_name.lower()


@pytest.mark.integration
def test_live_subreddits_access():
    """Verifies that the bot can access the private test subreddits."""
    r = reddit.get_reddit_instance(reddit.AUTO_CROSSPOST_BOT_NAME)
    source_sub = r.subreddit(TEST_SOURCE_SUBREDDIT)
    target_sub = r.subreddit(TEST_TARGET_SUBREDDIT)

    assert source_sub.display_name.lower() == TEST_SOURCE_SUBREDDIT.lower()
    assert target_sub.display_name.lower() == TEST_TARGET_SUBREDDIT.lower()


@pytest.mark.integration
def test_live_same_subreddit_bot_trigger():
    """Tests that same_subreddit_bot replies when a comment links to the same subreddit."""
    r = reddit.get_reddit_instance(reddit.AUTO_CROSSPOST_BOT_NAME)
    sub = r.subreddit(TEST_SOURCE_SUBREDDIT)

    submission = sub.submit(
        title=f'[Test] Same Sub Test {uuid.uuid4().hex[:8]}',
        selftext='Test post body for same_subreddit_bot integration test',
    )

    try:
        raw_comment = submission.reply(f'r/{TEST_SOURCE_SUBREDDIT}')
        time.sleep(2)
        comment = r.comment(id=raw_comment.id)

        # Trigger same_subreddit_bot reply
        bot_reply = phase1.reply_to_source_equals_target_comment(comment)

        assert bot_reply is not None, f"Expected reply from {reddit.SAME_SUBREDDIT_BOT_NAME}"
        assert bot_reply.author.name.lower() == reddit.SAME_SUBREDDIT_BOT_NAME.lower()
        assert "Yes, that's where we are." in bot_reply.body

    finally:
        submission.delete()


@pytest.mark.integration
def test_live_sub_doesnt_exist_bot_trigger():
    """Tests that sub_doesnt_exist_bot replies when linking to a non-existent subreddit."""
    r = reddit.get_reddit_instance(reddit.AUTO_CROSSPOST_BOT_NAME)
    sub = r.subreddit(TEST_SOURCE_SUBREDDIT)

    fake_sub_name = f'racb_fake_{uuid.uuid4().hex[:10]}'
    submission = sub.submit(
        title=f'[Test] Nonexistent Sub Test {uuid.uuid4().hex[:8]}',
        selftext='Test post body for sub_doesnt_exist_bot integration test',
    )

    try:
        raw_comment = submission.reply(f'r/{fake_sub_name}')
        time.sleep(2)
        comment = r.comment(id=raw_comment.id)

        # Trigger sub_doesnt_exist_bot reply
        bot_reply = phase1.reply_to_nonexistent_target_subreddit_comment(comment, fake_sub_name)

        assert bot_reply is not None, f"Expected reply from {reddit.SUB_DOESNT_EXIST_BOT_NAME}"
        assert bot_reply.author.name.lower() == reddit.SUB_DOESNT_EXIST_BOT_NAME.lower()
        assert fake_sub_name in bot_reply.body
        assert "does not exist" in bot_reply.body

    finally:
        submission.delete()


@pytest.mark.integration
def test_live_same_post_bot_reply():
    """Tests that same_post_bot generates duplicate notice reply on Reddit."""
    r = reddit.get_reddit_instance(reddit.AUTO_CROSSPOST_BOT_NAME)
    source_sub = r.subreddit(TEST_SOURCE_SUBREDDIT)
    target_sub = r.subreddit(TEST_TARGET_SUBREDDIT)

    target_submission = target_sub.submit(
        title=f'[Test Prior Post] {uuid.uuid4().hex[:8]}',
        selftext='Target post content',
    )
    source_submission = source_sub.submit(
        title=f'[Test Current Post] {uuid.uuid4().hex[:8]}',
        selftext='Source post content',
    )

    try:
        raw_comment = source_submission.reply(f'r/{TEST_TARGET_SUBREDDIT}')
        time.sleep(2)
        comment = r.comment(id=raw_comment.id)

        # Trigger same_post_bot reply directly with target submission
        bot_reply = phase1.reply_to_same_content_post_comment(
            comment,
            TEST_TARGET_SUBREDDIT,
            target_submission,
        )

        assert bot_reply is not None
        assert bot_reply.author.name.lower() == reddit.SAME_POST_BOT_NAME.lower()
        assert TEST_TARGET_SUBREDDIT in bot_reply.body
        assert target_submission.permalink in bot_reply.body

    finally:
        source_submission.delete()
        target_submission.delete()


@pytest.mark.integration
def test_live_phase3_graceful_private_crosspost():
    """Tests that Phase 3 gracefully handles Reddit API rejection when crossposting private subreddits."""
    r = reddit.get_reddit_instance(reddit.AUTO_CROSSPOST_BOT_NAME)
    source_sub = r.subreddit(TEST_SOURCE_SUBREDDIT)

    source_submission = source_sub.submit(
        title=f'[Test Private Source] {uuid.uuid4().hex[:8]}',
        selftext='Source content in private sub',
    )

    try:
        raw_comment = source_submission.reply(f'r/{TEST_TARGET_SUBREDDIT}')
        time.sleep(2)
        comment = r.comment(id=raw_comment.id)

        res = phase3.exec_crosspost(comment, TEST_TARGET_SUBREDDIT, reply_to_crosspost_flag=False)
        assert res.success is False
        assert res.failure_reason in ('PRIVATE_SUBREDDIT_CROSSPOST', 'SUBREDDIT_NOTALLOWED', 'NO_CROSSPOSTS')

    finally:
        source_submission.delete()


@pytest.mark.integration
def test_live_cleanup_query():
    """Tests that cleanup routine queries user submissions on Reddit without error."""
    submissions = list(cleanup.get_latest_submissions(limit=5))
    assert isinstance(submissions, list)
