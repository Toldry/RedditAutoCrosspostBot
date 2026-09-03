"""Detects whether an image submission has already been posted using self-hosted perceptual image hashing."""

import io
import logging
from functools import lru_cache
from urllib.parse import urlparse

import imagehash
from PIL import Image
import prawcore
import requests

from racb.core.reddit import get_reddit_instance

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')
IMAGE_DOMAINS = ('i.redd.it', 'i.imgur.com', 'preview.redd.it', 'imgur.com')
DEFAULT_HASH_THRESHOLD = 5  # Maximum Hamming distance to consider two images identical


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


@lru_cache(maxsize=256)
def compute_image_hash(url):
    """Downloads an image from a URL and computes its 64-bit difference hash (dhash)."""
    if not is_image_url(url):
        return None

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) RACB/2.2'}
        response = requests.get(url, headers=headers, timeout=6, stream=True)
        if response.status_code != 200:
            return None

        image_bytes = io.BytesIO(response.content)
        with Image.open(image_bytes) as img:
            return imagehash.dhash(img)
    except Exception as e:
        logger.debug(f'Could not compute perceptual hash for {url}: {e}')
        return None


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

    source_hash = compute_image_hash(source_url)
    if source_hash is None:
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

            candidate_hash = compute_image_hash(candidate_url)
            if candidate_hash is None:
                continue

            distance = source_hash - candidate_hash
            if distance <= hash_threshold:
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
