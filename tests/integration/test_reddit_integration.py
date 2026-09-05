"""Integration tests interacting directly with live Reddit APIs using test subreddits."""

import os
import tempfile
import time
import uuid
import pytest
import prawcore
from praw.models import PostMedia

from racb.core import reddit
from racb.phases import phase1, phase3, cleanup

# Hardcoded test subreddits created for integration testing
TEST_SOURCE_SUBREDDIT = 'racb_test_1'
TEST_TARGET_SUBREDDIT = 'racb_test_2'

# Flag to disable cleanup of created submissions and comments across all integration tests
DISABLE_CLEANUP = True


def cleanup_reddit_objects(*objects):
    """Deletes Reddit submissions, comments, or collections of them unless cleanup is disabled."""
    if DISABLE_CLEANUP:
        return
    for obj in objects:
        if obj is None:
            continue
        if isinstance(obj, (list, tuple, set)):
            for item in obj:
                cleanup_reddit_objects(item)
            continue
        try:
            obj.delete()
        except Exception:
            pass


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
def test_live_authenticate_all_bots_function():
    """Verifies that the startup authenticate_all_bots function executes without errors on live Reddit."""
    # Should complete without raising any exception if credentials in .env are valid
    reddit.authenticate_all_bots()


@pytest.mark.integration
def test_live_main_startup_flow(monkeypatch):
    """Verifies that main() startup authentication check succeeds with live Reddit before dispatching."""
    from unittest.mock import patch
    from racb.main import main

    monkeypatch.setattr('sys.argv', ['racb'])
    with patch('racb.main.start_bot') as mock_start_bot, \
         patch('racb.main.configure_logging'):
        main()
        mock_start_bot.assert_called_once()


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
    raw_comment = None
    bot_reply = None

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
        cleanup_reddit_objects(bot_reply, raw_comment, submission)


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
    raw_comment = None
    bot_reply = None

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
        cleanup_reddit_objects(bot_reply, raw_comment, submission)


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
    raw_comment = None
    bot_reply = None

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
        cleanup_reddit_objects(bot_reply, raw_comment, source_submission, target_submission)


@pytest.mark.integration
def test_live_phase3_graceful_private_crosspost():
    """Tests that Phase 3 gracefully handles Reddit API rejection when crossposting private subreddits."""
    r = reddit.get_reddit_instance(reddit.AUTO_CROSSPOST_BOT_NAME)
    source_sub = r.subreddit(TEST_SOURCE_SUBREDDIT)

    source_submission = source_sub.submit(
        title=f'[Test Private Source] {uuid.uuid4().hex[:8]}',
        selftext='Source content in private sub',
    )
    raw_comment = None

    try:
        raw_comment = source_submission.reply(f'r/{TEST_TARGET_SUBREDDIT}')
        time.sleep(2)
        comment = r.comment(id=raw_comment.id)

        res = phase3.exec_crosspost(comment, TEST_TARGET_SUBREDDIT, reply_to_crosspost_flag=False)
        assert res.success is False
        assert res.failure_reason in ('PRIVATE_SUBREDDIT_CROSSPOST', 'SUBREDDIT_NOTALLOWED', 'NO_CROSSPOSTS')

    finally:
        cleanup_reddit_objects(raw_comment, source_submission)


@pytest.mark.integration
def test_live_cleanup_query():
    """Tests that cleanup routine queries user submissions on Reddit without error."""
    submissions = list(cleanup.get_latest_submissions(limit=5))
    assert isinstance(submissions, list)


def generate_test_image_variations(base_image_path, target_dir):
    """Generates subtle variations of the base image asset."""
    from PIL import Image, ImageEnhance

    base_img = Image.open(base_image_path).convert('RGB')
    variations = {}

    # 1. Resize (-5%)
    v1_path = os.path.join(target_dir, 'var_resize.png')
    v1_img = base_img.resize((int(base_img.width * 0.95), int(base_img.height * 0.95)))
    v1_img.save(v1_path)
    variations['resize'] = v1_path

    # 2. Add corner pixels / watermark dots
    v2_path = os.path.join(target_dir, 'var_pixels.png')
    v2_img = base_img.copy()
    for x in range(10):
        for y in range(10):
            v2_img.putpixel((x, y), (255, 0, 0))
    v2_img.save(v2_path)
    variations['pixels'] = v2_path

    # 3. Brightness shift (+8%)
    v3_path = os.path.join(target_dir, 'var_brightness.png')
    enhancer = ImageEnhance.Brightness(base_img)
    v3_img = enhancer.enhance(1.08)
    v3_img.save(v3_path)
    variations['brightness'] = v3_path

    # 4. Slight rotation (1.5 deg)
    v4_path = os.path.join(target_dir, 'var_rotate.png')
    v4_img = base_img.rotate(1.5, resample=Image.BICUBIC)
    v4_img.save(v4_path)
    variations['rotate'] = v4_path

    return variations


@pytest.mark.integration
def test_live_duplicate_detector_query():
    """Tests that perceptual hash duplicate detector identifies all subtle variations of an uploaded image."""
    from racb.core import duplicate_detector

    r = reddit.get_reddit_instance(reddit.AUTO_CROSSPOST_BOT_NAME)
    source_sub = r.subreddit(TEST_SOURCE_SUBREDDIT)
    target_sub = r.subreddit(TEST_TARGET_SUBREDDIT)

    base_image_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'assets', 'test_sample.png')
    assert os.path.exists(base_image_path), f"Asset missing at {base_image_path}"

    target_submission = target_sub.submit(
        title=f'[Test Target Base Image] {uuid.uuid4().hex[:8]}',
        image=PostMedia(base_image_path),
        timeout=25,
    )
    time.sleep(4.0)  # Allow Reddit listing index to propagate new post

    created_source_submissions = []
    created_comments = []

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            variations = generate_test_image_variations(base_image_path, tmp_dir)

            for var_name, var_path in variations.items():
                source_submission = source_sub.submit(
                    title=f'[Test Variation {var_name}] {uuid.uuid4().hex[:8]}',
                    image=PostMedia(var_path),
                    timeout=25,
                )
                created_source_submissions.append(source_submission)

                raw_comment = source_submission.reply(f'r/{TEST_TARGET_SUBREDDIT}')
                created_comments.append(raw_comment)
                time.sleep(2)
                comment = r.comment(id=raw_comment.id)
                _ = comment.submission.title

                # Query duplicates with retry in case Reddit image processing index is slightly delayed
                duplicates = []
                for attempt in range(3):
                    duplicates = duplicate_detector.get_reposts_in_sub(comment, TEST_TARGET_SUBREDDIT, limit=15)
                    if duplicates and target_submission.id in [d['post_id'] for d in duplicates]:
                        break
                    time.sleep(2.0)

                assert isinstance(duplicates, list)
                assert len(duplicates) > 0, f"Expected duplicate detection for variation '{var_name}'"
                matching_ids = [d['post_id'] for d in duplicates]
                assert target_submission.id in matching_ids, f"Target post not detected for variation '{var_name}'"
                matched = next(d for d in duplicates if d['post_id'] == target_submission.id)
                assert matched['distance'] <= duplicate_detector.DEFAULT_HASH_THRESHOLD
                assert matched['subreddit'].lower() == TEST_TARGET_SUBREDDIT.lower()

    finally:
        cleanup_reddit_objects(created_comments, created_source_submissions, target_submission)




