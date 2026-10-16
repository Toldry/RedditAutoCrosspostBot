"""Checks, against live Reddit, the assumptions the ID-range comment reader (comment_reader.py) relies on.

Read-only: it makes about 30 requests as /u/AutoCrosspostBot and writes nothing. It only needs
`racb.core.reddit`, so it also runs on a bot that does not have the comment reader yet. On the VM:

    cd /opt/RedditAutoCrosspostBot
    curl -sL https://raw.githubusercontent.com/Toldry/RedditAutoCrosspostBot/claude/project-thread-bf84xq/scripts/verify_comment_ids.py \\
        | docker compose exec -T bot python -

It checks:
1. Comment IDs follow creation time (the r/all listing's newest 100 comments, sorted by ID, are also
   sorted by time, give or take a few seconds).
2. Blocks of 100 consecutive IDs come back mostly full, as one request each, with the fields the r/all
   filter reads (over_18, quarantine, subreddit_type).
3. How many comment IDs Reddit hands out per second, and so how many requests per minute the reader needs.
4. How long a block request and an r/all listing request take, and how many bytes each returns.
"""

import statistics
import sys
import time

from racb.core import reddit

BLOCK_SIZE = 100
SAMPLE_BLOCKS = 10
# Sample blocks start this many IDs below the newest comment, so every ID in them exists already
SAMPLE_DEPTH_IDS = 5_000
RATE_SECONDS = 60
FILTER_FIELDS = ('over_18', 'quarantine', 'subreddit_type', 'created_utc')

response_bytes = []


def track_response_sizes(instance):
    """Records the compressed size of every response (as received over the network) in response_bytes."""
    try:
        session = instance._core._requestor._http
    except AttributeError:
        return
    session.hooks['response'].append(
        lambda response, *args, **kwargs: response_bytes.append(int(response.headers.get('content-length', 0)))
    )


def to_number(comment_id):
    return int(comment_id, 36)


def to_id(number):
    digits = '0123456789abcdefghijklmnopqrstuvwxyz'
    text = ''
    while number:
        number, remainder = divmod(number, 36)
        text = digits[remainder] + text
    return text or '0'


def newest_listing(instance, limit):
    started = time.monotonic()
    response_bytes.clear()
    comments = list(instance.subreddit('all').comments(limit=limit))
    return comments, time.monotonic() - started, sum(response_bytes)


def fetch_block(instance, start):
    started = time.monotonic()
    response_bytes.clear()
    fullnames = [f't1_{to_id(number)}' for number in range(start, start + BLOCK_SIZE)]
    comments = list(instance.info(fullnames=fullnames))
    return comments, time.monotonic() - started, sum(response_bytes)


def verdict(passed, text):
    print(f'  [{"PASS" if passed else "FAIL"}] {text}')
    return passed


def main():
    instance = reddit.get_reddit_instance()
    track_response_sizes(instance)
    results = []

    print('1. Do comment IDs follow creation time?')
    listing, listing_seconds, listing_bytes = newest_listing(instance, 100)
    by_id = sorted(listing, key=lambda comment: to_number(comment.id))
    times = [comment.created_utc for comment in by_id]
    backwards = max((earlier - later for earlier, later in zip(times, times[1:])), default=0)
    print(f'  newest r/all comment: {by_id[-1].id}; the newest 100 span {times[-1] - times[0]:.0f} s')
    results.append(verdict(backwards <= 10, f'sorted by ID, creation times never go back more than 10 s '
                                            f'(worst: {backwards:.0f} s)'))

    print(f'\n2. Are blocks of {BLOCK_SIZE} consecutive IDs mostly real comments?')
    head = to_number(by_id[-1].id)
    counts, seconds, sizes, missing_fields, shown, spans = [], [], [], set(), 0, []
    for index in range(SAMPLE_BLOCKS):
        start = head - SAMPLE_DEPTH_IDS - index * BLOCK_SIZE
        comments, elapsed, size = fetch_block(instance, start)
        counts.append(len(comments))
        seconds.append(elapsed)
        sizes.append(size)
        for comment in comments:
            fields = vars(comment)
            missing_fields.update(field for field in FILTER_FIELDS if field not in fields)
            hidden = (fields.get('over_18') or fields.get('quarantine')
                      or fields.get('subreddit_type') in ('private', 'user')
                      or str(fields.get('subreddit', '')).lower().startswith('u_'))
            shown += not hidden
        created = [comment.created_utc for comment in comments]
        if created:
            spans.append(max(created) - min(created))
    total = sum(counts)
    print(f'  comments per block: {counts}')
    results.append(verdict(total >= SAMPLE_BLOCKS * BLOCK_SIZE * 0.5,
                           f'{total} of {SAMPLE_BLOCKS * BLOCK_SIZE} IDs returned ({total / SAMPLE_BLOCKS:.0f}%)'))
    results.append(verdict(not missing_fields, 'every comment has ' + ', '.join(FILTER_FIELDS)
                           + (f' (missing: {sorted(missing_fields)})' if missing_fields else '')))
    print(f'  {shown} of {total} would be on r/all ({100 * shown / max(total, 1):.0f}%)')
    if spans:
        results.append(verdict(statistics.median(spans) < 120,
                               f'a block covers {statistics.median(spans):.0f} s of comments (median)'))

    print(f'\n3. How fast does Reddit hand out comment IDs? (waiting {RATE_SECONDS} s)')
    time.sleep(RATE_SECONDS)
    later, _, _ = newest_listing(instance, 1)
    ids_per_second = (to_number(later[0].id) - head) / max(RATE_SECONDS, 1)
    blocks_per_minute = ids_per_second * 60 / BLOCK_SIZE
    print(f'  {ids_per_second:.0f} IDs per second, so about {blocks_per_minute:.0f} block requests per minute')
    print(f'  (the listing reader saw about 14 comments per second by day)')

    print('\n4. What does each request cost?')
    print(f'  r/all listing, 100 comments: {listing_seconds:.2f} s, {listing_bytes / 1024:.0f} KiB')
    print(f'  ID block: median {statistics.median(seconds):.2f} s, max {max(seconds):.2f} s, '
          f'median {statistics.median(sizes) / 1024:.0f} KiB')
    if not any(sizes):
        print('  (sizes unknown: Reddit sent no content-length)')
    # Each block request and its acknowledgements send a few KiB out; an estimate, not a measurement
    print(f'  rough monthly egress at ~3 KiB out per request: '
          f'{blocks_per_minute * 3 * 60 * 24 * 30 / 1024 / 1024:.1f} GiB (the free tier allows 1 GiB)')

    print(f'\n{sum(results)} of {len(results)} checks passed')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
