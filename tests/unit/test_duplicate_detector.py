"""Unit tests for racb.core.duplicate_detector module using self-hosted perceptual hashing."""

import io
from unittest.mock import patch, MagicMock
import pytest
from PIL import Image
import imagehash
import requests

from racb.core import duplicate_detector


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
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = img_bytes

    with patch('requests.get', return_value=mock_resp):
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
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        if 'source' in url:
            mock_resp.content = img_bytes_1
        else:
            mock_resp.content = img_bytes_2
        return mock_resp

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
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        if 'source' in url:
            mock_resp.content = buf_source.getvalue()
        else:
            mock_resp.content = buf_candidate.getvalue()
        return mock_resp

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

