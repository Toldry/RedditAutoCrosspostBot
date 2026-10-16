"""Unit tests for racb.core.duplicate_detector module using self-hosted perceptual hashing."""

import io
from unittest.mock import patch, MagicMock
import pytest
from PIL import Image
import imagehash
import requests

from racb.core import duplicate_detector


@pytest.fixture(autouse=True)
def clear_fingerprint_cache():
    """Fingerprints are cached per URL; tests reuse URLs with different image content."""
    duplicate_detector.fingerprint_image_at.cache_clear()
    yield
    duplicate_detector.fingerprint_image_at.cache_clear()


def make_http_response(content, status_code=200, headers=None):
    """Builds a real streamed `requests.Response` serving `content`."""
    response = requests.Response()
    response.status_code = status_code
    response.raw = io.BytesIO(content)
    response.headers.update(headers or {})
    return response


def create_test_image_bytes(color='red', size=(100, 100)):
    """Generates an in-memory PNG image."""
    img = Image.new('RGB', size, color=color)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


@pytest.mark.unit
def test_is_image_url():
    assert duplicate_detector.is_image_url('https://i.redd.it/example.png') is True
    assert duplicate_detector.is_image_url('https://i.redd.it/example.jpg') is True
    assert duplicate_detector.is_image_url('https://i.imgur.com/photo.webp') is True
    assert duplicate_detector.is_image_url('https://example.com/page.html') is False
    assert duplicate_detector.is_image_url('https://reddit.com/r/funny/comments/123/') is False
    assert duplicate_detector.is_image_url('') is False
    assert duplicate_detector.is_image_url(None) is False


@pytest.mark.unit
def test_compute_image_hash_success():
    img_bytes = create_test_image_bytes(color='blue')
    with patch('requests.get', return_value=make_http_response(img_bytes)):
        h = duplicate_detector.compute_image_hash('https://i.redd.it/blue.png')
        assert h is not None
        assert isinstance(h, imagehash.ImageHash)


@pytest.mark.unit
def test_compute_image_hash_non_image_url():
    h = duplicate_detector.compute_image_hash('https://example.com/article')
    assert h is None


@pytest.mark.unit
def test_compute_image_hash_network_error():
    with patch('requests.get', side_effect=requests.exceptions.RequestException('Timeout')):
        h = duplicate_detector.compute_image_hash('https://i.redd.it/error.png')
        assert h is None


@pytest.mark.unit
def test_scan_matches_a_resized_copy_of_the_image(make_mock_comment):
    img_bytes_1 = create_test_image_bytes(color='green', size=(200, 200))
    img_bytes_2 = create_test_image_bytes(color='green', size=(100, 100))  # Resized version of same image

    source_comment = make_mock_comment()
    source_comment.submission.url = 'https://i.redd.it/source.png'

    candidate_sub = MagicMock()
    candidate_sub.id = 'candidate_123'
    candidate_sub.url = 'https://i.redd.it/candidate.png'

    mock_target_sub = MagicMock()
    mock_target_sub.new.return_value = [candidate_sub]

    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value = mock_target_sub

    def mock_requests_get(url, **kwargs):
        return make_http_response(img_bytes_1 if 'source' in url else img_bytes_2)

    with patch('racb.core.duplicate_detector.get_reddit_instance', return_value=mock_reddit):
        with patch('requests.get', side_effect=mock_requests_get):
            scan = duplicate_detector.scan_subreddit_for_duplicates(source_comment.submission, 'target_sub')
            duplicates = scan.duplicates
            assert scan.posts_checked == 1
            assert len(duplicates) == 1
            assert duplicates[0]['post_id'] == 'candidate_123'
            assert duplicates[0]['distance'] <= duplicate_detector.DEFAULT_HASH_THRESHOLD


@pytest.mark.unit
def test_scan_ignores_different_images(make_mock_comment):
    img_source = Image.new('RGB', (100, 100), color='white')
    # Create distinct patterns for source and candidate
    for x in range(50):
        for y in range(50):
            img_source.putpixel((x, y), (0, 0, 0))

    img_candidate = Image.new('RGB', (100, 100), color='white')
    for x in range(50, 100):
        for y in range(50, 100):
            img_candidate.putpixel((x, y), (0, 0, 0))

    buf_source = io.BytesIO()
    img_source.save(buf_source, format='PNG')
    buf_candidate = io.BytesIO()
    img_candidate.save(buf_candidate, format='PNG')

    source_comment = make_mock_comment()
    source_comment.submission.url = 'https://i.redd.it/source.png'

    candidate_sub = MagicMock()
    candidate_sub.id = 'candidate_different'
    candidate_sub.url = 'https://i.redd.it/candidate_different.png'

    mock_target_sub = MagicMock()
    mock_target_sub.new.return_value = [candidate_sub]

    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value = mock_target_sub

    def mock_requests_get(url, **kwargs):
        return make_http_response(buf_source.getvalue() if 'source' in url else buf_candidate.getvalue())

    with patch('racb.core.duplicate_detector.get_reddit_instance', return_value=mock_reddit):
        with patch('requests.get', side_effect=mock_requests_get):
            scan = duplicate_detector.scan_subreddit_for_duplicates(
                source_comment.submission, 'target_sub', hash_threshold=2,
            )
            assert scan.posts_checked == 1
            assert len(scan.duplicates) == 0


@pytest.mark.unit
def test_image_variations_hash_distance_within_threshold():
    """Verifies that subtle transformations on the sample asset stay within DEFAULT_HASH_THRESHOLD."""
    from PIL import ImageEnhance
    import os

    asset_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'assets', 'test_sample.png')
    assert os.path.exists(asset_path), f"Asset missing at {asset_path}"

    base_img = Image.open(asset_path).convert('RGB')
    base_hash = imagehash.dhash(base_img)

    # Variation 1: Resize (-5%)
    v1 = base_img.resize((int(base_img.width * 0.95), int(base_img.height * 0.95)))
    assert (base_hash - imagehash.dhash(v1)) <= duplicate_detector.DEFAULT_HASH_THRESHOLD

    # Variation 2: Add pixels / watermark dot
    v2 = base_img.copy()
    for x in range(10):
        for y in range(10):
            v2.putpixel((x, y), (255, 0, 0))
    assert (base_hash - imagehash.dhash(v2)) <= duplicate_detector.DEFAULT_HASH_THRESHOLD

    # Variation 3: Brightness shift (+8%)
    enhancer = ImageEnhance.Brightness(base_img)
    v3 = enhancer.enhance(1.08)
    assert (base_hash - imagehash.dhash(v3)) <= duplicate_detector.DEFAULT_HASH_THRESHOLD

    # Variation 4: Slight rotation (1.5 deg)
    v4 = base_img.rotate(1.5, resample=Image.BICUBIC)
    assert (base_hash - imagehash.dhash(v4)) <= duplicate_detector.DEFAULT_HASH_THRESHOLD


def load_sample_image():
    import os
    asset_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'assets', 'test_sample.png')
    return Image.open(asset_path).convert('RGB')


def fingerprint_of(img):
    return duplicate_detector.ImageFingerprint(imagehash.dhash(img), duplicate_detector.compute_region_hashes(img))


def add_meme_captions(img, top_text, bottom_text):
    from PIL import ImageDraw, ImageFont
    captioned = img.copy()
    draw = ImageDraw.Draw(captioned)
    width, height = captioned.size
    try:
        font = ImageFont.truetype('DejaVuSans-Bold.ttf', height // 10)
    except OSError:
        font = ImageFont.load_default(size=height // 10)
    for text, y in ((top_text, height * 0.03), (bottom_text, height * 0.85)):
        draw.text((width * 0.05, y), text, fill='white', font=font, stroke_width=3, stroke_fill='black')
    return captioned


@pytest.mark.unit
def test_fingerprints_match_reposted_variations():
    from PIL import ImageEnhance
    base_img = load_sample_image()
    width, height = base_img.size
    variations = {
        'resize': base_img.resize((int(width * 0.95), int(height * 0.95))),
        'brightness': ImageEnhance.Brightness(base_img).enhance(1.08),
        'low_resolution': base_img.resize((width // 3, height // 3)).resize((width, height)),
    }
    for name, variation in variations.items():
        is_match, _ = duplicate_detector.fingerprints_match(fingerprint_of(base_img), fingerprint_of(variation))
        assert is_match, f'Expected variation {name!r} to match'


@pytest.mark.unit
def test_fingerprints_reject_same_meme_template_with_different_captions():
    base_img = load_sample_image()
    meme_a = add_meme_captions(base_img, 'NOBODY:', 'ABSOLUTELY NOBODY:')
    meme_b = add_meme_captions(base_img, 'ME AT 3AM', 'FIXING PROD')

    # With DejaVu Sans these two are only ~4 bits apart on the whole-image dhash (a false positive
    # under the old check); the per-region check must tell them apart regardless of font
    is_match, _ = duplicate_detector.fingerprints_match(fingerprint_of(meme_a), fingerprint_of(meme_b))
    assert not is_match


@pytest.mark.unit
def test_download_image_bytes_rejects_oversized_images():
    oversized = b'x' * (duplicate_detector.MAX_IMAGE_BYTES + 1)
    with patch('requests.get', return_value=make_http_response(oversized)):
        assert duplicate_detector.download_image_bytes('https://i.redd.it/huge.png') is None

    declared_oversized = make_http_response(b'x', headers={'Content-Length': str(10 ** 10)})
    with patch('requests.get', return_value=declared_oversized):
        assert duplicate_detector.download_image_bytes('https://i.redd.it/huge.png') is None



def make_post(post_id, url, created_utc, preview_resolutions=None, is_video=False):
    post = MagicMock()
    post.id = post_id
    post.url = url
    post.created_utc = created_utc
    post.is_video = is_video
    if preview_resolutions is None:
        del post.preview
    else:
        post.preview = {'images': [{'resolutions': preview_resolutions}]}
    return post


def scan_listing(posts, posted_after=None, limit=1000, image_bytes_by_url=None, source=None, top_posts=None,
                 top_limit=0, top_posts_by_period=None, top_time_filters=('all',)):
    """Runs scan_subreddit_for_duplicates over a fake "new" listing; every image is plain green unless given."""
    source = source or make_post('source', 'https://i.redd.it/source.png', created_utc=0)
    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value.new.return_value = posts[:limit]
    mock_reddit.subreddit.return_value.top.return_value = (top_posts or [])[:top_limit]
    if top_posts_by_period is not None:
        mock_reddit.subreddit.return_value.top.side_effect = (
            lambda time_filter, limit: top_posts_by_period.get(time_filter, [])[:limit]
        )
    image_bytes_by_url = image_bytes_by_url or {}
    downloaded_urls = []

    def mock_requests_get(url, **kwargs):
        downloaded_urls.append(url)
        return make_http_response(image_bytes_by_url.get(url, create_test_image_bytes(color='green')))

    with patch('racb.core.duplicate_detector.get_reddit_instance', return_value=mock_reddit), \
            patch('requests.get', side_effect=mock_requests_get):
        scan = duplicate_detector.scan_subreddit_for_duplicates(
            source, 'target_sub', posted_after=posted_after, limit=limit, top_limit=top_limit,
            top_time_filters=top_time_filters,
        )
    return scan, downloaded_urls


@pytest.mark.unit
def test_scan_returns_none_for_a_post_that_is_not_an_image():
    link_post = make_post('link', 'https://example.com/article', created_utc=0)
    assert duplicate_detector.scan_subreddit_for_duplicates(link_post, 'target_sub') is None


@pytest.mark.unit
def test_scan_stops_at_the_first_post_older_than_posted_after():
    posts = [
        make_post('new_image', 'https://i.redd.it/a.png', created_utc=300),
        make_post('new_link', 'https://example.com/b', created_utc=250),
        make_post('old_image', 'https://i.redd.it/c.png', created_utc=100),
    ]
    scan, downloaded_urls = scan_listing(posts, posted_after=200)
    assert scan.complete is True
    assert scan.posts_checked == 1  # the link post is skipped, the old image is never read
    assert scan.oldest_post_utc == 250
    assert 'https://i.redd.it/c.png' not in downloaded_urls
    assert [duplicate['post_id'] for duplicate in scan.duplicates] == ['new_image']


@pytest.mark.unit
def test_scan_is_incomplete_when_the_listing_runs_out_first():
    posts = [make_post(f'p{i}', f'https://i.redd.it/{i}.png', created_utc=1000 - i) for i in range(3)]
    scan, _ = scan_listing(posts, posted_after=0, limit=3)
    assert scan.complete is False
    assert scan.posts_checked == 3
    assert scan.oldest_post_utc == 998


@pytest.mark.unit
def test_scan_is_complete_when_the_subreddit_has_fewer_posts_than_the_limit():
    posts = [make_post('only', 'https://i.redd.it/only.png', created_utc=500)]
    scan, _ = scan_listing(posts, posted_after=0, limit=1000)
    assert scan.complete is True


@pytest.mark.unit
def test_scan_does_not_count_images_it_could_not_read():
    posts = [make_post('broken', 'https://i.redd.it/broken.png', created_utc=500)]
    scan, _ = scan_listing(posts, image_bytes_by_url={'https://i.redd.it/broken.png': b'not an image'})
    assert scan.posts_checked == 0
    assert scan.duplicates == []


@pytest.mark.unit
def test_scan_compares_small_previews_instead_of_full_images():
    resolutions = [
        {'url': 'https://preview.redd.it/a.png?width=108&amp;s=1', 'width': 108},
        {'url': 'https://preview.redd.it/a.png?width=320&amp;s=2', 'width': 320},
        {'url': 'https://preview.redd.it/a.png?width=640&amp;s=3', 'width': 640},
    ]
    posts = [make_post('with_preview', 'https://i.redd.it/a.png', created_utc=500, preview_resolutions=resolutions)]
    _, downloaded_urls = scan_listing(posts)
    assert 'https://preview.redd.it/a.png?width=320&s=2' in downloaded_urls
    assert 'https://i.redd.it/a.png' not in downloaded_urls


def png_bytes(img):
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    return buffer.getvalue()


def video_preview(name):
    return [{'url': f'https://external-preview.redd.it/{name}.jpg?width=320&amp;s=x', 'width': 320, 'height': 180}]


@pytest.mark.unit
@pytest.mark.parametrize('url, is_video, expected', [
    ('https://i.redd.it/cat.png', False, duplicate_detector.IMAGE),
    ('https://v.redd.it/abc123', True, duplicate_detector.VIDEO),
    ('https://v.redd.it/abc123', False, duplicate_detector.VIDEO),
    ('https://www.youtube.com/watch?v=abc', False, duplicate_detector.VIDEO),
    ('https://youtu.be/abc', False, duplicate_detector.VIDEO),
    ('https://streamable.com/abc', False, duplicate_detector.VIDEO),
    ('https://example.com/article', False, None),
    ('https://www.reddit.com/r/pics/comments/abc/title/', False, None),
])
def test_media_kind(url, is_video, expected):
    assert duplicate_detector.media_kind(make_post('p', url, created_utc=0, is_video=is_video)) == expected


@pytest.mark.unit
def test_video_without_a_preview_cannot_be_compared():
    video = make_post('v', 'https://v.redd.it/abc123', created_utc=0, is_video=True)
    assert duplicate_detector.preview_image_url(video) is None
    assert scan_listing([], source=video)[0] is None


@pytest.mark.unit
def test_video_scan_compares_preview_frames_of_videos_only():
    source = make_post('source', 'https://v.redd.it/source', created_utc=0, preview_resolutions=video_preview('source'),
                       is_video=True)
    posts = [
        make_post('image', 'https://i.redd.it/image.png', created_utc=300),
        make_post('copy', 'https://youtu.be/copy', created_utc=200, preview_resolutions=video_preview('copy')),
        make_post('other', 'https://v.redd.it/other', created_utc=100, preview_resolutions=video_preview('other'),
                  is_video=True),
    ]
    frame = load_sample_image()
    different_frame = frame.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    scan, downloaded_urls = scan_listing(posts, source=source, image_bytes_by_url={
        'https://external-preview.redd.it/source.jpg?width=320&s=x': png_bytes(frame),
        'https://external-preview.redd.it/copy.jpg?width=320&s=x': png_bytes(frame),
        'https://external-preview.redd.it/other.jpg?width=320&s=x': png_bytes(different_frame),
    })
    assert scan.media == duplicate_detector.VIDEO
    assert scan.posts_checked == 2
    assert [duplicate['post_id'] for duplicate in scan.duplicates] == ['copy']
    assert 'https://i.redd.it/image.png' not in downloaded_urls


@pytest.mark.unit
def test_scan_also_compares_the_top_posts_of_all_time():
    newest = [make_post(f'new{i}', f'https://i.redd.it/new{i}.png', created_utc=1000 - i) for i in range(3)]
    old_famous = make_post('famous', 'https://i.redd.it/famous.png', created_utc=5)
    top_posts = [old_famous, newest[0]]  # The newest post is also a top post; it must be compared once
    scan, downloaded_urls = scan_listing(
        newest, limit=3, top_posts=top_posts, top_limit=100,
        image_bytes_by_url={'https://i.redd.it/source.png': png_bytes(load_sample_image()),
                            'https://i.redd.it/famous.png': png_bytes(load_sample_image())},
    )
    assert scan.posts_checked == 4
    assert [duplicate['post_id'] for duplicate in scan.duplicates] == ['famous']
    assert downloaded_urls.count('https://i.redd.it/new0.png') == 1
    # The top posts say nothing about how far back the newest posts reach
    assert scan.complete is False
    assert scan.oldest_post_utc == 998


@pytest.mark.unit
def test_scan_reads_no_top_posts_by_default():
    old_famous = make_post('famous', 'https://i.redd.it/famous.png', created_utc=5)
    scan, _ = scan_listing([], top_posts=[old_famous], top_limit=0)
    assert scan.posts_checked == 0


@pytest.mark.unit
def test_scan_compares_the_top_posts_of_each_period_once():
    newest = [make_post('new0', 'https://i.redd.it/new0.png', created_utc=1000)]
    all_time = make_post('all_time', 'https://i.redd.it/all_time.png', created_utc=5)
    this_year = make_post('this_year', 'https://i.redd.it/this_year.png', created_utc=500)
    scan, downloaded_urls = scan_listing(
        newest, limit=1000, top_limit=100, top_time_filters=('all', 'year'),
        top_posts_by_period={'all': [all_time, newest[0]], 'year': [this_year, all_time]},
        image_bytes_by_url={'https://i.redd.it/source.png': png_bytes(load_sample_image()),
                            'https://i.redd.it/this_year.png': png_bytes(load_sample_image())},
    )
    assert scan.posts_checked == 3
    assert scan.top_posts_checked == 2  # new0 came from the newest posts, so it is not counted as a top post
    assert [duplicate['post_id'] for duplicate in scan.duplicates] == ['this_year']
    assert downloaded_urls.count('https://i.redd.it/all_time.png') == 1


@pytest.mark.unit
def test_failed_downloads_are_retried_instead_of_cached():
    url = 'https://i.redd.it/not_ready_yet.png'
    responses = [make_http_response(b'', status_code=404), make_http_response(create_test_image_bytes())]
    with patch('requests.get', side_effect=responses) as mock_get:
        assert duplicate_detector.compute_image_fingerprint(url) is None
        assert duplicate_detector.compute_image_fingerprint(url) is not None
        assert duplicate_detector.compute_image_fingerprint(url) is not None
    assert mock_get.call_count == 2  # The failure was retried; the success was cached


def make_noise_image(seed):
    """A random 64x64 grayscale image; two different seeds give fingerprints far apart."""
    import random
    generator = random.Random(seed)
    img = Image.new('L', (64, 64))
    img.putdata([generator.randrange(256) for _ in range(64 * 64)])
    return img.convert('RGB')


def make_gallery_post(post_id, image_names, created_utc=500):
    """A gallery post whose pictures have 320-wide previews at https://preview.redd.it/<name>.jpg."""
    post = make_post(post_id, f'https://www.reddit.com/gallery/{post_id}', created_utc)
    post.is_video = False
    post.is_gallery = True
    post.gallery_data = {'items': [{'media_id': name} for name in image_names]}
    post.media_metadata = {
        name: {
            'status': 'valid',
            'e': 'Image',
            'p': [
                {'x': 108, 'u': f'https://preview.redd.it/{name}.jpg?width=108&amp;s=1'},
                {'x': 320, 'u': f'https://preview.redd.it/{name}.jpg?width=320&amp;s=2'},
            ],
            's': {'x': 1080, 'u': f'https://i.redd.it/{name}.jpg'},
        }
        for name in image_names
    }
    return post


def gallery_preview(name):
    return f'https://preview.redd.it/{name}.jpg?width=320&s=2'


@pytest.mark.unit
def test_gallery_posts_are_image_posts():
    gallery = make_gallery_post('g', ['a', 'b'])
    assert duplicate_detector.media_kind(gallery) == duplicate_detector.IMAGE
    assert duplicate_detector.gallery_image_urls(gallery) == [gallery_preview('a'), gallery_preview('b')]


@pytest.mark.unit
def test_crosspost_of_a_gallery_is_an_image_post():
    crosspost = make_post('x', '/r/source/comments/g/title/', created_utc=0)
    crosspost.is_gallery = None
    gallery = make_gallery_post('g', ['a'])
    crosspost.crosspost_parent_list = [{
        'is_gallery': True, 'media_metadata': gallery.media_metadata, 'gallery_data': gallery.gallery_data,
    }]
    assert duplicate_detector.gallery_image_urls(crosspost) == [gallery_preview('a')]


@pytest.mark.unit
def test_gallery_pictures_are_capped():
    names = [f'n{i}' for i in range(duplicate_detector.MAX_GALLERY_IMAGES + 5)]
    urls = duplicate_detector.gallery_image_urls(make_gallery_post('g', names))
    assert len(urls) == duplicate_detector.MAX_GALLERY_IMAGES


@pytest.mark.unit
def test_scan_counts_galleries_and_matches_any_of_their_pictures():
    red, blue = png_bytes(make_noise_image(10)), png_bytes(make_noise_image(11))
    images = {
        'https://i.redd.it/source.png': png_bytes(make_noise_image(1)),
        gallery_preview('match_a'): red,
        gallery_preview('match_b'): png_bytes(make_noise_image(1)),
        gallery_preview('other_a'): red,
        gallery_preview('other_b'): blue,
    }
    posts = [
        make_gallery_post('has_copy', ['match_a', 'match_b']),
        make_gallery_post('different', ['other_a', 'other_b']),
    ]
    scan, _ = scan_listing(posts, image_bytes_by_url=images)
    assert scan.posts_checked == 2
    assert [duplicate['post_id'] for duplicate in scan.duplicates] == ['has_copy']


@pytest.mark.unit
def test_scan_from_a_gallery_post():
    images = {
        gallery_preview('src_a'): png_bytes(make_noise_image(12)),
        gallery_preview('src_b'): png_bytes(make_noise_image(2)),
        'https://i.redd.it/copy.png': png_bytes(make_noise_image(2)),
        'https://i.redd.it/other.png': png_bytes(make_noise_image(3)),
    }
    posts = [
        make_post('copy', 'https://i.redd.it/copy.png', created_utc=500),
        make_post('other', 'https://i.redd.it/other.png', created_utc=400),
    ]
    scan, _ = scan_listing(posts, image_bytes_by_url=images, source=make_gallery_post('source', ['src_a', 'src_b']))
    assert scan.posts_checked == 2
    assert [duplicate['post_id'] for duplicate in scan.duplicates] == ['copy']


@pytest.mark.unit
def test_scan_falls_back_to_the_full_image_when_the_preview_cannot_be_read():
    resolutions = [{'url': 'https://preview.redd.it/a.png?width=320&amp;s=2', 'width': 320}]
    posts = [make_post('p', 'https://i.redd.it/a.png', created_utc=500, preview_resolutions=resolutions)]
    scan, downloaded_urls = scan_listing(
        posts, image_bytes_by_url={'https://preview.redd.it/a.png?width=320&s=2': b'not an image'},
    )
    assert 'https://i.redd.it/a.png' in downloaded_urls
    assert scan.posts_checked == 1


def make_crosspost(post_id, original_fields, created_utc=500):
    """A crosspost as Reddit lists it: its url is the original's permalink, its media fields are on the original."""
    crosspost = make_post(post_id, '/r/source/comments/orig/title/', created_utc)
    crosspost.is_video = False
    crosspost.is_gallery = None
    crosspost.crosspost_parent_list = [original_fields]
    return crosspost


@pytest.mark.unit
def test_crosspost_of_an_image_is_compared_through_the_original_image():
    crosspost = make_crosspost('x', {
        'url': 'https://i.redd.it/orig.jpeg',
        'is_video': False,
        'preview': {'images': [{'resolutions': [
            {'url': 'https://preview.redd.it/orig.jpeg?width=320&amp;s=2', 'width': 320},
        ]}]},
    })
    assert duplicate_detector.media_kind(crosspost) == duplicate_detector.IMAGE
    images = {
        'https://i.redd.it/source.png': png_bytes(make_noise_image(20)),
        'https://preview.redd.it/orig.jpeg?width=320&s=2': png_bytes(make_noise_image(20)),
    }
    scan, _ = scan_listing([crosspost], image_bytes_by_url=images)
    assert scan.posts_checked == 1
    assert [duplicate['post_id'] for duplicate in scan.duplicates] == ['x']


@pytest.mark.unit
def test_crosspost_of_a_video_is_a_video_post():
    crosspost = make_crosspost('x', {'url': 'https://v.redd.it/abc', 'is_video': True})
    assert duplicate_detector.media_kind(crosspost) == duplicate_detector.VIDEO


@pytest.mark.unit
def test_crosspost_of_a_text_post_is_not_compared():
    crosspost = make_crosspost('x', {'url': 'https://www.reddit.com/r/source/comments/orig/title/'})
    assert duplicate_detector.media_kind(crosspost) is None


@pytest.mark.unit
def test_crosspost_whose_original_was_deleted_uses_its_own_fields():
    crosspost = make_crosspost('x', {})
    crosspost.crosspost_parent_list = []
    crosspost.url = 'https://i.redd.it/kept.png'
    assert duplicate_detector.media_kind(crosspost) == duplicate_detector.IMAGE


@pytest.mark.unit
def test_crosspost_reads_the_parent_that_crosspost_parent_names():
    crosspost = make_crosspost('x', {'name': 't3_middle', 'url': 'https://example.com/article'})
    crosspost.crosspost_parent_list.append({'name': 't3_orig', 'url': 'https://i.redd.it/orig.png'})
    crosspost.crosspost_parent = 't3_orig'
    assert duplicate_detector.media_url(crosspost) == 'https://i.redd.it/orig.png'


@pytest.mark.unit
def test_crosspost_reads_the_first_parent_when_none_is_named():
    crosspost = make_crosspost('x', {'name': 't3_orig', 'url': 'https://i.redd.it/orig.png'})
    crosspost.crosspost_parent = None
    assert duplicate_detector.media_url(crosspost) == 'https://i.redd.it/orig.png'
