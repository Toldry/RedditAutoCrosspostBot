"""Checks whether an image or video has already been posted in a subreddit, using perceptual image hashing.

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

Videos are compared the same way, through the still preview image Reddit makes for every video post
(Reddit-hosted, YouTube, ...). That catches re-uploads of the same clip, but a trimmed or re-edited copy
usually gets a different preview frame and is missed. Decoding the videos themselves would catch more, but
is far too heavy for the 1 GB VM the bot runs on.

Transparent images (PNGs, GIFs, WebPs with see-through areas) are first laid over a white background, so what
is compared is what a viewer sees. Without that, the hidden colour stored behind the transparent pixels would
decide the hash, and two copies of the same picture saved by different programs would not match.

A gallery post counts as an image post. Its first MAX_GALLERY_IMAGES pictures are fingerprinted, and two posts
match when any picture of one matches any picture of the other.

Which posts are compared
------------------------
`scan_subreddit_for_duplicates` walks the subreddit's "new" listing from the newest post backwards and
compares the image (or video) with every image (or video) post it meets, stopping at the first post older
than `posted_after`. Reddit's listings end after about REDDIT_LISTING_LIMIT posts, so in a busy subreddit the
scan cannot reach that far back; the returned `RepostScan` says whether it did (`complete`).

The quick check that runs when a comment is first seen (and answers through u/same_post_bot) reads only the
QUICK_SCAN_NEWEST_LIMIT newest posts, plus the QUICK_SCAN_TOP_LIMIT top posts of all time (`top_limit`),
because reposts of a subreddit's most famous posts are common and those are often years old. The full scan
made just before crossposting also reads the top posts of the past year (`top_time_filters`).

To keep a scan cheap on a small VM, posts are compared through the small preview images Reddit generates
(`preview_image_url`), and several previews are downloaded at a time. When a preview cannot be read, the
full image is tried instead, so a post is only left out of the count when neither can be read.
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
from praw.models.reddit.base import RedditBase

from racb.core.reddit import get_reddit_instance
from racb.version import __version__

logger = logging.getLogger(__name__)

# --- What counts as an image or video post ---------------------------------------------------------
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')
IMAGE_DOMAINS = ('i.redd.it', 'i.imgur.com', 'preview.redd.it', 'external-preview.redd.it', 'imgur.com')
VIDEO_DOMAINS = ('v.redd.it', 'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be', 'streamable.com')
IMAGE = 'image'
VIDEO = 'video'

# --- When two fingerprints match --------------------------------------------------------------------
DEFAULT_HASH_THRESHOLD = 5  # Maximum whole-image Hamming distance (out of 64 bits)
REGION_GRID_SIZE = 4
REGION_PIXELS = 32
DEFAULT_REGION_HASH_THRESHOLD = 10  # Maximum Hamming distance of any single grid cell

# --- Which posts are compared -----------------------------------------------------------------------
QUICK_SCAN_NEWEST_LIMIT = 100  # Newest posts read by the quick check made when a comment is first seen
QUICK_SCAN_TOP_LIMIT = 100  # Top posts of all time the quick check also reads
DEFAULT_SCAN_LIMIT = QUICK_SCAN_NEWEST_LIMIT
REDDIT_LISTING_LIMIT = 1000  # Reddit stops listing a subreddit's posts after about this many

MAX_GALLERY_IMAGES = 10  # Pictures of a gallery post compared; bounds the downloads a gallery costs

# --- Downloading --------------------------------------------------------------------------------------
PREVIEW_MIN_WIDTH = 320  # Smallest preview width used; plenty for a 128x128 fingerprint
MAX_PARALLEL_DOWNLOADS = 8
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # The VM has 1 GB of RAM; never buffer arbitrarily large downloads
DOWNLOAD_TIMEOUT_SECONDS = 6
DOWNLOAD_CHUNK_BYTES = 64 * 1024
HTTP_HEADERS = {'User-Agent': f'RACB/{__version__} (+https://github.com/Toldry/RedditAutoCrosspostBot)'}

ImageFingerprint = namedtuple('ImageFingerprint', ['global_hash', 'region_hashes'])


@dataclass
class RepostScan:
    """The outcome of comparing one image or video post with the posts of the same kind in a subreddit."""

    media: str = IMAGE  # IMAGE or VIDEO: which kind of post was compared
    duplicates: list = field(default_factory=list)  # {'post_id', 'post', 'subreddit', 'distance'} per match
    posts_checked: int = 0
    # True if every such post made since `posted_after` was compared. False if the listing ran out first.
    complete: bool = False
    # created_utc of the oldest post the scan read, or None if the subreddit had no posts
    oldest_post_utc: float | None = None
    # How many of the posts_checked came only from the top listings, not from the newest posts
    top_posts_checked: int = 0


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


def flatten_transparency(img):
    """Returns the image laid over a white background if it has transparent areas, else the image itself.

    Hashing works on grayscale, which ignores transparency, so a transparent pixel would otherwise count as
    whatever colour happens to be stored behind it (often black, sometimes leftovers of an edit).
    """
    if not img.has_transparency_data:
        return img
    background = Image.new('RGBA', img.size, 'white')
    return Image.alpha_composite(background, img.convert('RGBA')).convert('RGB')


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


class ImageUnavailable(Exception):
    """An image could not be downloaded or read."""


def compute_image_fingerprint(url):
    """Downloads an image and returns its ImageFingerprint, or None if it cannot be downloaded or read."""
    if not is_image_url(url):
        return None
    try:
        return fingerprint_image_at(url)
    except ImageUnavailable:
        return None


@lru_cache(maxsize=2048)
def fingerprint_image_at(url):
    """Like compute_image_fingerprint, but raises ImageUnavailable instead of returning None.

    lru_cache does not cache exceptions, so a failed download (a timeout, or a preview Reddit has not made yet)
    is tried again next time instead of being remembered as unreadable.
    """
    try:
        image_bytes = download_image_bytes(url)
        if image_bytes is None:
            raise ImageUnavailable(url)
        with Image.open(io.BytesIO(image_bytes)) as img:
            flattened = flatten_transparency(img)
            return ImageFingerprint(imagehash.dhash(flattened), compute_region_hashes(flattened))
    except ImageUnavailable:
        raise
    except Exception as e:
        logger.debug(f'Could not compute perceptual hash for {url}: {e}')
        raise ImageUnavailable(url) from e


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

def post_attribute(submission, name):
    """Returns one of a post's fields, or None if Reddit did not send it, without asking Reddit for it.

    A PRAW post read from a listing holds every field Reddit sent for it, but reading a field it lacks (such as
    `crosspost_parent_list`, which only crossposts have) makes PRAW download the whole post again: one request
    per post, so a scan of 1000 posts would run into Reddit's rate limit. So the fields are read from the
    post's own `__dict__`. A post created from just its id (like a comment's `submission`) holds no fields yet;
    it is fetched once.
    """
    if not isinstance(submission, RedditBase):
        return getattr(submission, name, None)  # A plain object, as in the unit tests
    if not submission._fetched and 'created_utc' not in vars(submission):
        submission._fetch()
    return vars(submission).get(name)


def media_field(submission, name):
    """Returns a field describing a post's media, read from the original post when `submission` is a crosspost.

    A crosspost's own `url` is often just the original post's permalink, and its `is_video`, `preview`,
    `is_gallery`, `media_metadata` and `gallery_data` are often missing; the original post's fields are in
    `crosspost_parent_list`, in the entry whose `name` is the crosspost's `crosspost_parent` (the first entry if
    none is). When the list is empty (the original was deleted), the crosspost's own fields are used.
    """
    parent = crosspost_parent(submission)
    if parent is not None:
        return parent.get(name)
    return post_attribute(submission, name)


def crosspost_parent(submission):
    """Returns the original post's fields (a dict) when `submission` is a crosspost, else None."""
    parents = post_attribute(submission, 'crosspost_parent_list')
    if not isinstance(parents, list):
        return None
    parents = [parent for parent in parents if isinstance(parent, dict)]
    if not parents:
        return None
    parent_name = post_attribute(submission, 'crosspost_parent')
    named = [parent for parent in parents if parent_name is not None and parent.get('name') == parent_name]
    return (named or parents)[0]


def media_url(submission):
    """The URL of a post's media: the post's own `url`, or the original post's for a crosspost."""
    return media_field(submission, 'url')


def media_kind(submission):
    """Returns IMAGE for an image or gallery post, VIDEO for a video post, or None for anything else.

    A crosspost has the kind of the post it crossposts.
    """
    if media_field(submission, 'is_video') is True:
        return VIDEO
    if gallery_image_urls(submission):
        return IMAGE
    url = media_url(submission)
    if is_image_url(url):
        return IMAGE
    if url and urlparse(url.lower()).netloc in VIDEO_DOMAINS:
        return VIDEO
    return None


def preview_image_url(submission):
    """Returns the URL of the smallest preview of a post that is at least PREVIEW_MIN_WIDTH wide.

    For an image post, falls back to the full image when Reddit has no preview (yet). A video post without a
    preview cannot be compared, so it returns None. Preview URLs come HTML-escaped.
    """
    fallback = media_url(submission) if media_kind(submission) == IMAGE else None
    try:
        resolutions = media_field(submission, 'preview')['images'][0]['resolutions']
    except (AttributeError, KeyError, IndexError, TypeError):
        return fallback
    wide_enough = [resolution for resolution in resolutions if resolution.get('width', 0) >= PREVIEW_MIN_WIDTH]
    if not wide_enough:
        return fallback
    smallest = min(wide_enough, key=lambda resolution: resolution['width'])
    return html.unescape(smallest['url'])


def gallery_metadata(submission):
    """Returns (media_metadata, gallery_data) of a gallery post (or a crosspost of one), else (None, None)."""
    if media_field(submission, 'is_gallery') is not True:
        return None, None
    media_metadata, gallery_data = media_field(submission, 'media_metadata'), media_field(submission, 'gallery_data')
    if isinstance(media_metadata, dict) and isinstance(gallery_data, dict):
        return media_metadata, gallery_data
    return None, None


def gallery_image_urls(submission):
    """Returns preview URLs of the first MAX_GALLERY_IMAGES pictures of a gallery post, or [] if it is not one.

    For each picture, the smallest preview at least PREVIEW_MIN_WIDTH wide, else the full picture. Animated
    items and items Reddit has not finished processing are skipped.
    """
    media_metadata, gallery_data = gallery_metadata(submission)
    if media_metadata is None:
        return []
    urls = []
    for item in gallery_data.get('items') or []:
        metadata = media_metadata.get(item.get('media_id')) if isinstance(item, dict) else None
        if not isinstance(metadata, dict) or metadata.get('status') != 'valid' or metadata.get('e') != 'Image':
            continue
        wide_enough = [preview for preview in metadata.get('p') or [] if preview.get('x', 0) >= PREVIEW_MIN_WIDTH]
        if wide_enough:
            url = min(wide_enough, key=lambda preview: preview['x']).get('u')
        else:
            url = (metadata.get('s') or {}).get('u')
        if url:
            urls.append(html.unescape(url))
        if len(urls) == MAX_GALLERY_IMAGES:
            break
    return urls


def fingerprint_post(submission):
    """Returns the fingerprints of a post's pictures: one per readable gallery picture, else at most one.

    The preview is tried first; if it cannot be read, an image post's full image is tried instead.
    """
    gallery_urls = gallery_image_urls(submission)
    if gallery_urls:
        fingerprints = [compute_image_fingerprint(url) for url in gallery_urls]
        return [fingerprint for fingerprint in fingerprints if fingerprint is not None]
    preview_url = preview_image_url(submission)
    fingerprint = compute_image_fingerprint(preview_url)
    full_image_url = media_url(submission)
    if fingerprint is None and media_kind(submission) == IMAGE and full_image_url != preview_url:
        fingerprint = compute_image_fingerprint(full_image_url)
    return [fingerprint] if fingerprint is not None else []


def best_match(source_fingerprints, fingerprints, hash_threshold):
    """Compares every picture of one post with every picture of another; returns (is_match, smallest distance)."""
    results = [
        fingerprints_match(source_fingerprint, fingerprint, hash_threshold)
        for source_fingerprint in source_fingerprints
        for fingerprint in fingerprints
    ]
    matches = [distance for is_match, distance in results if is_match]
    if matches:
        return True, min(matches)
    return False, min(distance for _, distance in results)


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


def list_top_posts(subreddit_name, limit, time_filter='all'):
    """Returns up to `limit` of the subreddit's top posts of `time_filter` ('all', 'year', ...), best first."""
    return list(get_reddit_instance().subreddit(subreddit_name).top(time_filter=time_filter, limit=limit))


def fingerprint_all(posts):
    """Fingerprints several posts (`fingerprint_post`), working on up to MAX_PARALLEL_DOWNLOADS at a time."""
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_DOWNLOADS) as executor:
        return list(executor.map(fingerprint_post, posts))


def scan_subreddit_for_duplicates(submission, subreddit_name, posted_after=None, limit=DEFAULT_SCAN_LIMIT,
                                  top_limit=0, top_time_filters=('all',), hash_threshold=DEFAULT_HASH_THRESHOLD):
    """Compares an image (or video) post with the newest image (or video) posts of a subreddit.

    Reads at most `limit` posts, newest first, and stops at the first one created before `posted_after`
    (a Unix timestamp). With `top_limit`, also compares the subreddit's `top_limit` top posts of each period in
    `top_time_filters` (all time by default); they do not change `complete` or `oldest_post_utc`, which describe
    the newest posts only, and are also counted in `top_posts_checked`. Returns a RepostScan, or None if `submission` is neither an image nor a video post, or
    its picture cannot be read, in which case nothing was compared.
    """
    media = media_kind(submission)
    if media is None:
        return None
    source_fingerprints = fingerprint_post(submission)
    if not source_fingerprints:
        return None

    scan = RepostScan(media=media)
    try:
        posts = list_newest_posts(subreddit_name, limit)
    except (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException) as e:
        logger.warning(f'Could not list the posts of r/{subreddit_name} to look for duplicate {media}s: {e!r}')
        return scan

    candidates = []
    for post in posts:
        created_utc = post_attribute(post, 'created_utc')
        if posted_after is not None and created_utc is not None and created_utc < posted_after:
            scan.complete = True
            break
        if created_utc is not None:
            scan.oldest_post_utc = created_utc
        if media_kind(post) == media:
            candidates.append(post)
    else:
        # The listing ended without reaching an older post: complete only if Reddit had no more to give
        scan.complete = len(posts) < limit

    top_only_ids = set()
    if top_limit:
        seen_ids = {post.id for post in candidates}
        for time_filter in top_time_filters:
            try:
                top_posts = list_top_posts(subreddit_name, top_limit, time_filter)
            except (prawcore.exceptions.PrawcoreException, praw.exceptions.PRAWException) as e:
                logger.warning(f'Could not list the top posts ({time_filter}) of r/{subreddit_name}: {e!r}')
                continue
            for post in top_posts:
                if media_kind(post) == media and post.id not in seen_ids:
                    seen_ids.add(post.id)
                    top_only_ids.add(post.id)
                    candidates.append(post)

    unreadable_count = 0
    for post, fingerprints in zip(candidates, fingerprint_all(candidates)):
        if media_url(post) == media_url(submission):
            is_match, distance = True, 0
        elif fingerprints:
            is_match, distance = best_match(source_fingerprints, fingerprints, hash_threshold)
        else:
            unreadable_count += 1
            logger.debug(f'Could not read the picture of {media_url(post)}; it does not count as checked')
            continue
        scan.posts_checked += 1
        if post.id in top_only_ids:
            scan.top_posts_checked += 1
        if is_match:
            logger.info(
                f'Duplicate {media} found in r/{subreddit_name}: {media_url(submission)} vs {media_url(post)} '
                f'(distance={distance})'
            )
            scan.duplicates.append({
                'post_id': post.id,
                'post': post,
                'subreddit': subreddit_name.lower(),
                'distance': distance,
            })
    if unreadable_count:
        logger.warning(f'Could not read {unreadable_count} of {len(candidates)} {media} posts in r/{subreddit_name}')
    return scan
