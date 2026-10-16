"""Integration tests interacting directly with live Reddit APIs using test subreddits."""

import logging
import os
import tempfile
import time
import uuid
from urllib.parse import urlparse
import pytest
import prawcore
from praw.models import PostMedia

from racb import comment_reader
from racb.core import reddit
from racb.phases import phase1, phase3, cleanup
from scripts.delete_test_artifacts import record_artifact

logger = logging.getLogger(__name__)

# Hardcoded test subreddits created for integration testing
TEST_SOURCE_SUBREDDIT = 'racb_test_1'
TEST_TARGET_SUBREDDIT = 'racb_test_2'

# Flag to disable cleanup of created submissions and comments across all integration tests.
# Set RACB_KEEP_TEST_ARTIFACTS=true to keep them on Reddit for debugging.
DISABLE_CLEANUP = os.environ.get('RACB_KEEP_TEST_ARTIFACTS', '').strip().lower() in ('true', '1', 'yes')


def track(reddit_object):
    """Records a post or comment this test created, then returns it.

    The CI step after the tests runs scripts/delete_test_artifacts.py, which deletes every recorded item
    that is still there, e.g. because a test was killed before its own cleanup ran.
    """
    if reddit_object is not None:
        record_artifact(reddit_object._reddit.config.username, reddit_object.fullname)
    return reddit_object


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
        except Exception as e:
            # Not fatal for the test, but a silent failure here hid leftover artifacts before
            logger.warning(f'Could not delete test artifact {getattr(obj, "fullname", obj)}: {e}')


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
def test_live_comment_reader_reads_recent_comments_by_id():
    """Reads 4 blocks of comment IDs just below r/all's newest comment, one per bot account.

    The comment reader relies on Reddit numbering comments in order across the whole site. If that ever
    stops being true, most of these IDs come back empty, this test fails, and CI does not deploy a bot
    that would read nothing.
    """
    block_count = len(reddit.BOT_NAMES)
    first_block = comment_reader.fetch_head() - 10 * comment_reader.BLOCK_SIZE
    comments = []
    for index, bot_name in enumerate(reddit.BOT_NAMES):
        comments += comment_reader.fetch_block(bot_name, first_block + index * comment_reader.BLOCK_SIZE)
    numbers = [comment_reader.comment_id_to_number(comment.id) for comment in comments]
    last_id = first_block + block_count * comment_reader.BLOCK_SIZE
    assert all(first_block <= number < last_id for number in numbers)
    # Comments in private subreddits are not returned, so not every ID comes back
    assert len(comments) >= block_count * comment_reader.BLOCK_SIZE // 2, f'only {len(comments)} comments'
    assert any(comment_reader.is_shown_on_r_all(comment) for comment in comments)


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

    submission = track(sub.submit(
        title=f'[Test] Same Sub Test {uuid.uuid4().hex[:8]}',
        selftext='Test post body for same_subreddit_bot integration test',
    ))
    raw_comment = None
    bot_reply = None

    try:
        raw_comment = track(submission.reply(f'r/{TEST_SOURCE_SUBREDDIT}'))
        time.sleep(2)
        comment = r.comment(id=raw_comment.id)

        # Trigger same_subreddit_bot reply
        bot_reply = track(phase1.reply_to_source_equals_target_comment(comment))

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
    submission = track(sub.submit(
        title=f'[Test] Nonexistent Sub Test {uuid.uuid4().hex[:8]}',
        selftext='Test post body for sub_doesnt_exist_bot integration test',
    ))
    raw_comment = None
    bot_reply = None

    try:
        raw_comment = track(submission.reply(f'r/{fake_sub_name}'))
        time.sleep(2)
        comment = r.comment(id=raw_comment.id)

        # Trigger sub_doesnt_exist_bot reply
        bot_reply = track(phase1.reply_to_nonexistent_target_subreddit_comment(comment, fake_sub_name))

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

    target_submission = track(target_sub.submit(
        title=f'[Test Prior Post] {uuid.uuid4().hex[:8]}',
        selftext='Target post content',
    ))
    source_submission = track(source_sub.submit(
        title=f'[Test Current Post] {uuid.uuid4().hex[:8]}',
        selftext='Source post content',
    ))
    raw_comment = None
    bot_reply = None

    try:
        raw_comment = track(source_submission.reply(f'r/{TEST_TARGET_SUBREDDIT}'))
        time.sleep(2)
        comment = r.comment(id=raw_comment.id)

        # Trigger same_post_bot reply directly with target submission
        bot_reply = track(phase1.reply_to_same_content_post_comment(
            comment,
            TEST_TARGET_SUBREDDIT,
            target_submission,
        ))

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

    source_submission = track(source_sub.submit(
        title=f'[Test Private Source] {uuid.uuid4().hex[:8]}',
        selftext='Source content in private sub',
    ))
    raw_comment = None

    try:
        raw_comment = track(source_submission.reply(f'r/{TEST_TARGET_SUBREDDIT}'))
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

    from PIL import Image

    asset_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'assets', 'test_sample.png')
    assert os.path.exists(asset_path), f"Asset missing at {asset_path}"

    # The asset is an RGBA PNG whose pixels are almost all (partly) transparent. The variations drop the alpha
    # channel, which reveals the colours hidden under the transparency, so they look nothing like the asset as
    # Reddit shows it (and as its preview images, which the detector compares, are rendered). Post an opaque
    # copy as the target, so that every variation really is a variation of it.
    opaque_dir = tempfile.TemporaryDirectory()
    base_image_path = os.path.join(opaque_dir.name, 'base.png')
    with Image.open(asset_path) as asset:
        asset.convert('RGB').save(base_image_path)

    target_submission = track(target_sub.submit(
        title=f'[Test Target Base Image] {uuid.uuid4().hex[:8]}',
        image=PostMedia(base_image_path),
        timeout=25,
    ))
    time.sleep(4.0)  # Allow Reddit listing index to propagate new post

    created_source_submissions = []
    created_comments = []

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            variations = generate_test_image_variations(base_image_path, tmp_dir)

            for var_name, var_path in variations.items():
                if var_name == 'rotate':
                    # Rotation is deliberately not matched: the per-region check that rejects
                    # same-template memes with different captions also rejects rotated images
                    continue
                source_submission = track(source_sub.submit(
                    title=f'[Test Variation {var_name}] {uuid.uuid4().hex[:8]}',
                    image=PostMedia(var_path),
                    timeout=25,
                ))
                created_source_submissions.append(source_submission)

                raw_comment = track(source_submission.reply(f'r/{TEST_TARGET_SUBREDDIT}'))
                created_comments.append(raw_comment)
                time.sleep(2)
                comment = r.comment(id=raw_comment.id)
                _ = comment.submission.title

                # Query duplicates with retry in case Reddit image processing index is slightly delayed
                duplicates = []
                for attempt in range(3):
                    scan = duplicate_detector.scan_subreddit_for_duplicates(
                        comment.submission, TEST_TARGET_SUBREDDIT, limit=15,
                    )
                    duplicates = scan.duplicates if scan else []
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
        opaque_dir.cleanup()
        cleanup_reddit_objects(created_comments, created_source_submissions, target_submission)




@pytest.mark.integration
def test_live_duplicate_detector_scans_gallery_posts():
    """An image post and a gallery holding the same picture are found as duplicates of each other, both ways."""
    from PIL import Image
    from racb.core import duplicate_detector

    r = reddit.get_reddit_instance(reddit.AUTO_CROSSPOST_BOT_NAME)
    # Both posts go to the target and both scans read it. Scanning r/racb_test_1 failed in 3 of 4 master runs
    # on 2026-10-16: the image post made there never showed up in the scan, not even after 30 s, while every
    # scan of r/racb_test_2 (here and in the other tests) found posts made seconds earlier.
    target_sub = r.subreddit(TEST_TARGET_SUBREDDIT)

    asset_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'assets', 'test_sample.png')
    image_dir = tempfile.TemporaryDirectory()
    shared_path = os.path.join(image_dir.name, 'shared.png')
    other_path = os.path.join(image_dir.name, 'other.png')
    with Image.open(asset_path) as asset:
        asset.convert('RGB').save(shared_path)  # Opaque, see test_live_duplicate_detector_query
    # Random noise looks nothing like the asset, so only the shared picture can match
    Image.frombytes('RGB', (256, 256), os.urandom(256 * 256 * 3)).save(other_path)

    gallery_submission = None
    image_submission = None
    try:
        # The shared picture second, so a match proves pictures after the first are compared
        gallery_submission = track(target_sub.submit(
            title=f'[Test Gallery] {uuid.uuid4().hex[:8]}',
            gallery=[PostMedia(other_path), PostMedia(shared_path)],
            timeout=25,
        ))
        image_submission = track(target_sub.submit(
            title=f'[Test Gallery Picture] {uuid.uuid4().hex[:8]}',
            image=PostMedia(shared_path),
            timeout=25,
        ))

        # Reddit fills in a gallery's media_metadata once it has processed the pictures
        gallery = None
        for attempt in range(10):
            gallery = r.submission(id=gallery_submission.id)
            if len(duplicate_detector.gallery_image_urls(gallery)) == 2:
                break
            time.sleep(3.0)
        assert duplicate_detector.media_kind(gallery) == duplicate_detector.IMAGE
        assert len(duplicate_detector.gallery_image_urls(gallery)) == 2
        assert len(duplicate_detector.fingerprint_post(gallery)) == 2

        image_post = r.submission(id=image_submission.id)
        for scanned, expected_id in ((image_post, gallery_submission.id), (gallery, image_submission.id)):
            # Reddit's "new" listing can take a while to show a post made seconds ago, so keep asking
            scan = None
            for attempt in range(10):
                scan = duplicate_detector.scan_subreddit_for_duplicates(scanned, TEST_TARGET_SUBREDDIT, limit=15)
                if scan and expected_id in [duplicate['post_id'] for duplicate in scan.duplicates]:
                    break
                time.sleep(3.0)
            assert scan is not None, f'{scanned.url} was not scanned'
            if expected_id not in [duplicate['post_id'] for duplicate in scan.duplicates]:
                # Says whether the post was missing from the listing or listed but not matched
                listed_ids = [post.id for post in duplicate_detector.list_newest_posts(TEST_TARGET_SUBREDDIT, 15)]
                pytest.fail(
                    f'r/{TEST_TARGET_SUBREDDIT} post {expected_id} not found as a duplicate of {scanned.url}: '
                    f'{scan.posts_checked} posts compared, newest posts listed: {listed_ids}'
                )

    finally:
        image_dir.cleanup()
        cleanup_reddit_objects(image_submission, gallery_submission)


def find_crosspost(reddit_instance, is_wanted, listing_limit=1000):
    """Returns a crosspost from r/all's "new" listing that satisfies `is_wanted`, or None.

    Prefers one whose own `url` is not the original's media URL (e.g. the original's permalink), the case the
    duplicate detector used to miss. The test subreddits are private, and Reddit refuses crossposts out of
    private subreddits, so the tests cannot make their own crossposts; public ones on r/all are common enough.
    """
    from racb.core import duplicate_detector

    # post_attribute, not getattr: PRAW answers a field a listed post lacks by downloading the post, and
    # one of those downloads can hit a post the bot may not read (HTTP 403)
    wanted = [
        post for post in reddit_instance.subreddit('all').new(limit=listing_limit)
        if duplicate_detector.post_attribute(post, 'crosspost_parent_list') and is_wanted(post)
    ]
    differing = [post for post in wanted if post.url != duplicate_detector.media_url(post)]
    return (differing or wanted or [None])[0]


@pytest.mark.integration
@pytest.mark.parametrize('original_kind', ['image', 'gallery'])
def test_live_duplicate_detector_reads_crossposts_through_the_original(original_kind):
    """A live crosspost of an image (or gallery) post is an image post, and its picture(s) can be fingerprinted."""
    from racb.core import duplicate_detector

    def is_wanted(post):
        original = duplicate_detector.crosspost_parent(post)
        if original_kind == 'gallery':
            return original.get('is_gallery') is True
        return original.get('is_gallery') is not True and urlparse(original.get('url') or '').netloc == 'i.redd.it'

    r = reddit.get_reddit_instance(reddit.AUTO_CROSSPOST_BOT_NAME)
    crosspost = find_crosspost(r, is_wanted)
    if crosspost is None:
        pytest.skip(f'No crosspost of a public {original_kind} post among the newest posts of r/all')

    logger.info(f'Checking crosspost {crosspost.permalink} (url {crosspost.url})')
    assert duplicate_detector.media_kind(crosspost) == duplicate_detector.IMAGE
    assert duplicate_detector.fingerprint_post(crosspost), f'Could not read the picture of {crosspost.permalink}'
