"""Detects whether an image submission has already been posted using self-hosted perceptual image hashing."""

import io
import logging
from collections import namedtuple
from functools import lru_cache
from urllib.parse import urlparse

import imagehash
from PIL import Image
import prawcore
import requests

from racb.core.reddit import get_reddit_instance
from racb.version import __version__

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')
IMAGE_DOMAINS = ('i.redd.it', 'i.imgur.com', 'preview.redd.it', 'imgur.com')
DEFAULT_HASH_THRESHOLD = 5  # Maximum Hamming distance to consider two images identical

# A whole-image 64-bit dhash cannot tell apart two memes made from the same template, because the
# differing captions are a small part of the image. Each image is therefore also split into a
# REGION_GRID_SIZE x REGION_GRID_SIZE grid, and every region must match too.
# Trade-off: rotated reposts (even by ~1.5 degrees) no longer match, since rotation moves the edges most.
REGION_GRID_SIZE = 4
REGION_PIXELS = 32
DEFAULT_REGION_HASH_THRESHOLD = 10

MAX_IMAGE_BYTES = 20 * 1024 * 1024  # The VM has 1 GB of RAM; never buffer arbitrarily large downloads
DOWNLOAD_TIMEOUT_SECONDS = 6
DOWNLOAD_CHUNK_BYTES = 64 * 1024
HTTP_HEADERS = {'User-Agent': f'RACB/{__version__} (+https://github.com/Toldry/RedditAutoCrosspostBot)'}

ImageFingerprint = namedtuple('ImageFingerprint', ['global_hash', 'region_hashes'])


def is_image_url(url):
    """Checks whether the given URL is an image based on path extension and domain."""
    if not url:
        return False
    parsed = urlparse(url.lower())
    if any(parsed.path.endswith(ext) for ext in IMAGE_EXTENSIONS):
        return True
    if parsed.netloc in IMAGE_DOMAINS:
        return True
    return False


def download_image_bytes(url):
    """Downloads an image, giving up (returning None) if it is larger than MAX_IMAGE_BYTES."""
    with requests.get(url, headers=HTTP_HEADERS, timeout=DOWNLOAD_TIMEOUT_SECONDS, stream=True) as response:
        if response.status_code != 200:
            return None
        declared_length = response.headers.get('Content-Length', '')
        if declared_length.isdigit() and int(declared_length) > MAX_IMAGE_BYTES:
            logger.debug(f'Skipping {url}: declared size {declared_length} bytes exceeds limit')
            return None
        buffer = bytearray()
        for chunk in response.iter_content(DOWNLOAD_CHUNK_BYTES):
            buffer.extend(chunk)
            if len(buffer) > MAX_IMAGE_BYTES:
                logger.debug(f'Skipping {url}: size exceeds {MAX_IMAGE_BYTES} bytes')
                return None
        return bytes(buffer)


def compute_region_hashes(img):
    """Computes a dhash for every cell of a REGION_GRID_SIZE x REGION_GRID_SIZE grid over the image."""
    side = REGION_GRID_SIZE * REGION_PIXELS
    grayscale = img.convert('L').resize((side, side))
    region_hashes = []
    for row in range(REGION_GRID_SIZE):
        for column in range(REGION_GRID_SIZE):
            box = (
                column * REGION_PIXELS,
                row * REGION_PIXELS,
                (column + 1) * REGION_PIXELS,
                (row + 1) * REGION_PIXELS,
            )
            region_hashes.append(imagehash.dhash(grayscale.crop(box)))
    return tuple(region_hashes)


@lru_cache(maxsize=256)
def compute_image_fingerprint(url):
    """Downloads an image from a URL and computes its whole-image dhash plus per-region dhashes."""
    if not is_image_url(url):
        return None

    try:
        image_bytes = download_image_bytes(url)
        if image_bytes is None:
            return None
        with Image.open(io.BytesIO(image_bytes)) as img:
            return ImageFingerprint(imagehash.dhash(img), compute_region_hashes(img))
    except Exception as e:
        logger.debug(f'Could not compute perceptual hash for {url}: {e}')
        return None


def compute_image_hash(url):
    """Downloads an image from a URL and computes its 64-bit difference hash (dhash)."""
    fingerprint = compute_image_fingerprint(url)
    return fingerprint.global_hash if fingerprint else None


def max_region_distance(fingerprint_a, fingerprint_b):
    return max(
        region_a - region_b
        for region_a, region_b in zip(fingerprint_a.region_hashes, fingerprint_b.region_hashes)
    )


def fingerprints_match(fingerprint_a, fingerprint_b, hash_threshold=DEFAULT_HASH_THRESHOLD,
                       region_hash_threshold=DEFAULT_REGION_HASH_THRESHOLD):
    """Returns (is_match, whole_image_distance)."""
    distance = fingerprint_a.global_hash - fingerprint_b.global_hash
    if distance > hash_threshold:
        return False, distance
    return max_region_distance(fingerprint_a, fingerprint_b) <= region_hash_threshold, distance


def get_reposts_in_sub(source_comment, target_subreddit, hash_threshold=DEFAULT_HASH_THRESHOLD, limit=50):
    """Searches target subreddit for visual duplicate images using perceptual hashing."""
    submission = getattr(source_comment, 'submission', None)
    if not submission:
        return []

    # Ensure lazy PRAW Submission is populated if needed
    source_url = getattr(submission, 'url', None)
    if not source_url or not is_image_url(source_url):
        try:
            _ = getattr(submission, 'title', None)
            source_url = getattr(submission, 'url', None)
        except Exception:
            pass

    if not source_url or not is_image_url(source_url):
        return []

    source_fingerprint = compute_image_fingerprint(source_url)
    if source_fingerprint is None:
        return []

    target_subreddit = target_subreddit.lower()
    reddit = get_reddit_instance()
    duplicates = []

    try:
        target_sub = reddit.subreddit(target_subreddit)
        candidates = list(target_sub.new(limit=limit))
        if not candidates:
            candidates = list(target_sub.hot(limit=limit))
        if not candidates:
            try:
                me = reddit.user.me()
                if me:
                    candidates = [
                        s for s in reddit.redditor(me.name).submissions.new(limit=limit)
                        if getattr(s, 'subreddit', None) and s.subreddit.display_name.lower() == target_subreddit
                    ]
            except Exception:
                pass
        for candidate in candidates:
            candidate_url = getattr(candidate, 'url', None)
            if not candidate_url or not is_image_url(candidate_url):
                try:
                    _ = getattr(candidate, 'title', None)
                    candidate_url = getattr(candidate, 'url', None)
                except Exception:
                    pass

            if not candidate_url or not is_image_url(candidate_url):
                continue
            if candidate_url == source_url:
                duplicates.append({
                    'post_id': candidate.id,
                    'post': candidate,
                    'subreddit': target_subreddit,
                    'distance': 0,
                })
                continue

            candidate_fingerprint = compute_image_fingerprint(candidate_url)
            if candidate_fingerprint is None:
                continue

            is_match, distance = fingerprints_match(source_fingerprint, candidate_fingerprint, hash_threshold)
            if is_match:
                logger.info(
                    f'Perceptual image duplicate found in r/{target_subreddit}: '
                    f'Source={source_url} vs Candidate={candidate_url} (distance={distance})'
                )
                duplicates.append({
                    'post_id': candidate.id,
                    'post': candidate,
                    'subreddit': target_subreddit,
                    'distance': distance,
                })
    except (prawcore.exceptions.PrawcoreException, Exception) as e:
        logger.warning(f'Error querying r/{target_subreddit} for duplicate image search: {e}')

    return duplicates
