#!/usr/bin/env python3
"""Read-only probe: shows what each Reddit subreddit-search endpoint returns for one query.

Run from the repository root:
    uv run python probe_sub_search.py wtfdidijustread

It only reads; nothing is posted.
"""

import sys

sys.path.insert(0, 'src')
from racb.core import reddit  # noqa: E402


def snippet(text, width=70):
    flat = ' '.join((text or '').split())
    return flat if len(flat) <= width else flat[: width - 1] + '…'


def find_query(text, query):
    """Where the query (ignoring case and spaces) appears in a text, if anywhere."""
    squashed = ''.join((text or '').lower().split())
    return query.lower() in squashed


def main():
    query = sys.argv[1]
    r = reddit.get_reddit_instance()

    print(f'=== /subreddits/search (what get_matches() calls today), q={query!r} ===')
    raw = r.get('subreddits/search', params={'q': query, 'limit': 10, 'raw_json': 1})
    for position, sub in enumerate(raw, start=1):
        data = vars(sub)
        title = data.get('title', '')
        description = data.get('public_description', '')
        in_name = query.lower() in sub.display_name.lower()
        in_title = find_query(title, query)
        in_description = find_query(description, query)
        print(f'{position:>2}. {type(sub).__name__}  {sub.display_name}'
              f'  subs={data.get("subscribers")}  type={data.get("subreddit_type")}')
        print(f'    query in name={in_name} title={in_title} description={in_description}')
        print(f'    title: {snippet(title)}')
        print(f'    desc:  {snippet(description)}')

    print()
    print(f'=== /api/search_reddit_names (subreddits.search_by_name), q={query!r} ===')
    for prefix_length in (len(query), 8, 5):
        prefix = query[:prefix_length]
        names = [sub.display_name for sub in r.subreddits.search_by_name(prefix, include_nsfw=True)]
        print(f'prefix {prefix!r}: {names}')


if __name__ == '__main__':
    main()
