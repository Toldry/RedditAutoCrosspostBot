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
    duplicate_detector.compute_image_fingerprint.cache_clear()
    yield
    duplicate_detector.compute_image_fingerprint.cache_clear()


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
def test_get_reposts_in_sub_matches_identical_image(make_mock_comment):
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
            duplicates = duplicate_detector.get_reposts_in_sub(source_comment, 'target_sub')
            assert len(duplicates) == 1
            assert duplicates[0]['post_id'] == 'candidate_123'
            assert duplicates[0]['distance'] <= duplicate_detector.DEFAULT_HASH_THRESHOLD


@pytest.mark.unit
def test_get_reposts_in_sub_ignores_different_images(make_mock_comment):
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
            duplicates = duplicate_detector.get_reposts_in_sub(source_comment, 'target_sub', hash_threshold=2)
            assert len(duplicates) == 0


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

