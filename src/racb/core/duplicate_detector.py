"""Checks whether an image has already been posted in a subreddit, using perceptual image hashing.

How two images are compared
---------------------------
Each image is reduced to a fingerprint (`compute_image_fingerprint`):

* a 64-bit difference hash ("dhash") of the whole image. A dhash records, for a tiny grayscale copy of the
  image, whether each pixel is brighter than its right-hand neighbour. Resizing, recompressing or slightly
  recoloring an image barely changes it;
* one dhash per cell of a REGION_GRID_SIZE x REGION_GRID_SIZE grid laid over the image.

Two images match (`fingerprints_match`) when the whole-image hashes differ in at most
DEFAULT_HASH_THRESHOLD bits AND every grid cell differs in at most DEFAULT_REGION_HASH_THRESHOLD bits.
The whole-image hash alone cannot tell apart two memes made from the same template, because their
different captions are a small part of the image; the grid can. The price is that rotated reposts
(even by ~1.5 degrees) no longer match, since rotation moves the edges most.

Which posts are compared
------------------------
`scan_subreddit_for_image` walks the subreddit's "new" listing from the newest post backwards and
compares the image with every image post it meets, stopping at the first post older than `posted_after`.
Reddit's listings end after about REDDIT_LISTING_LIMIT posts, so in a busy subreddit the scan cannot reach
that far back; the returned `ImageRepostScan` says whether it did (`complete`).

To keep a scan cheap on a small VM, images are compared through the small preview Reddit generates for
every image post (`preview_image_url`), and several previews are downloaded at a time.
"""

import html
import io
import logging
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlparse

import imagehash
import praw
import prawcore
import requests
from PIL import Image

from racb.core.reddit import get_reddit_instance
from racb.version import __version__

logger = logging.getLogger(__name__)

# --- What counts as an image post -------------------------------------------------------------------
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')
IMAGE_DOMAINS = ('i.redd.it', 'i.imgur.com', 'preview.redd.it', 'imgur.com')

# --- When two fingerprints match --------------------------------------------------------------------
DEFAULT_HASH_THRESHOLD = 5  # Maximum whole-image Hamming distance (out of 64 bits)
REGION_GRID_SIZE = 4
REGION_PIXELS = 32
DEFAULT_REGION_HASH_THRESHOLD = 10  # Maximum Hamming distance of any single grid cell

# --- Which posts are compared -----------------------------------------------------------------------
DEFAULT_SCAN_LIMIT = 50  # Posts read by the quick check made when a comment is first seen
REDDIT_LISTING_LIMIT = 1000  # Reddit stops listing a subreddit's posts after about this many

# --- Downloading --------------------------------------------------------------------------------------
PREVIEW_MIN_WIDTH = 320  # Smallest preview width used; plenty for a 128x128 fingerprint
MAX_PARALLEL_DOWNLOADS = 8
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # The VM has 1 GB of RAM; never buffer arbitrarily large downloads
DOWNLOAD_TIMEOUT_SECONDS = 6
DOWNLOAD_CHUNK_BYTES = 64 * 1024
HTTP_HEADERS = {'User-Agent': f'RACB/{__version__} (+https://github.com/Toldry/RedditAutoCrosspostBot)'}

ImageFingerprint = namedtuple('ImageFingerprint', ['global_hash', 'region_hashes'])


@dataclass
class ImageRepostScan:
    """The outcome of comparing one image with the image posts of a subreddit."""

    duplicates: list = field(default_factory=list)  # {'post_id', 'post', 'subreddit', 'distance'} per match
    image_posts_checked: int = 0
    # True if every image post made since `posted_after` was compared. False if the listing ran out first.
    complete: bool = False
    # created_utc of the oldest post the scan read, or None if the subreddit had no posts
    oldest_post_utc: float | None = None


# --- Fingerprints ---------------------------------------------------------------------------------------

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


@lru_cache(maxsize=2048)
def compute_image_fingerprint(url):
    """Downloads an image and returns its ImageFingerprint, or None if it cannot be downloaded or read."""
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


# --- Posts -------------------------------------------------------------------------------------------------

def is_image_post(submission):
    return is_image_url(getattr(submission, 'url', None))


def preview_image_url(submission):
    """Returns the URL of the smallest preview of an image post that is at least PREVIEW_MIN_WIDTH wide.

    Falls back to the full image when Reddit has no preview (yet). Preview URLs come HTML-escaped.
    """
    try:
        resolutions = submission.preview['images'][0]['resolutions']
    except (AttributeError, KeyError, IndexError, TypeError):
        return submission.url
    wide_enough = [resolution for resolution in resolutions if resolution.get('width', 0) >= PREVIEW_MIN_WIDTH]
    if not wide_enough:
        return submission.url
    smallest = min(wide_enough, key=lambda resolution: resolution['width'])
    return html.unescape(smallest['url'])


def list_newest_posts(subreddit_name, limit):
    """Returns up to `limit` of the subreddit's posts, newest first.

    Some accounts get an empty "new" listing for the private test subreddits; there, fall back to "hot" and
    then to the bot's own posts in that subreddit, so the integration tests have something to compare with.
    """
    reddit = get_reddit_instance()
    subreddit = reddit.subreddit(subreddit_name)
    posts = list(subreddit.new(limit=limit))
    if not posts:
        posts = list(subreddit.hot(limit=limit))
    if not posts:
        bot_account = reddit.user.me()
        if bot_account:
            posts = [
                post for post in reddit.redditor(bot_account.name).submissions.new(limit=limit)
                if post.subreddit.display_name.lower() == subreddit_name.lower()
            ]
    return posts


def fingerprint_all(urls):
    """Fingerprints several images, downloading up to MAX_PARALLEL_DOWNLOADS at a time."""
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_DOWNLOADS) as executor:
        return list(executor.map(compute_image_fingerprint, urls))


def scan_subreddit_for_image(submission, subreddit_name, posted_after=None, limit=DEFAULT_SCAN_LIMIT,
                             hash_threshold=DEFAULT_HASH_THRESHOLD):
    """Compares an image post with the newest image posts of a subreddit.

    Reads at most `limit` posts, newest first, and stops at the first one created before `posted_after`
    (a Unix timestamp). Returns an ImageRepostScan, or None if `submission` is not an image post or its
    image cannot be read, in which case nothing was compared.
    """
    if not is_image_post(submission):
        return None
    source_fingerprint = compute_image_fingerprint(preview_image_url(submission))
    if source_fingerprint is None:
        return None

    scan = ImageRepostScan()
    try:
        posts = list_newest_posts(subreddit_name, limit)
    except (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException) as e:
        logger.warning(f'Could not list the posts of r/{subreddit_name} to look for duplicate images: {e!r}')
        return scan

    candidates = []
    for post in posts:
        created_utc = getattr(post, 'created_utc', None)
        if posted_after is not None and created_utc is not None and created_utc < posted_after:
            scan.complete = True
            break
        if created_utc is not None:
            scan.oldest_post_utc = created_utc
        if is_image_post(post):
            candidates.append(post)
    else:
        # The listing ended without reaching an older post: complete only if Reddit had no more to give
        scan.complete = len(posts) < limit

    fingerprints = fingerprint_all([preview_image_url(post) for post in candidates])
    for post, fingerprint in zip(candidates, fingerprints):
        if post.url == submission.url:
            is_match, distance = True, 0
        elif fingerprint is not None:
            is_match, distance = fingerprints_match(source_fingerprint, fingerprint, hash_threshold)
        else:
            continue  # The image could not be read, so it does not count as checked
        scan.image_posts_checked += 1
        if is_match:
            logger.info(
                f'Duplicate image found in r/{subreddit_name}: {submission.url} vs {post.url} (distance={distance})'
            )
            scan.duplicates.append({
                'post_id': post.id,
                'post': post,
                'subreddit': subreddit_name.lower(),
                'distance': distance,
            })
    return scan
