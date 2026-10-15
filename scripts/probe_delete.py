#!/usr/bin/env python3
"""One-off probe: shows exactly what Reddit answers when a bot deletes one of its own items.

Run from the repository root:
    uv run python scripts/probe_delete.py AutoCrosspostBot t3_1x66jwb

It deletes that one item if Reddit allows it (it is a leftover test item anyway).
"""

import os
import sys

import prawcore

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
from racb.core import reddit  # noqa: E402


def main():
    bot_name, fullname = sys.argv[1], sys.argv[2]
    r = reddit.get_reddit_instance(bot_name)
    me = r.user.me()
    print(f'Logged in as:      /u/{me.name} (asked for /u/{bot_name})')
    print(f'Token scopes:      {sorted(r.auth.scopes())}')

    item = next(iter(r.info(fullnames=[fullname])), None)
    if item is None:
        print(f'{fullname}: not returned by /api/info')
        return
    print(f'Item author:       {item.author}')
    print(f'Item subreddit:    r/{item.subreddit.display_name}')
    for attribute in ('archived', 'locked', 'removed_by_category', 'banned_by', 'can_mod_post'):
        print(f'Item {attribute + ":":<13}{vars(item).get(attribute, "n/a")}')

    try:
        result = r._core.request(method='POST', path='api/del/', data={'id': fullname, 'api_type': 'json'})
        print(f'POST /api/del/:    OK {result!r}')
    except prawcore.exceptions.ResponseException as e:
        response = e.response
        print(f'POST /api/del/:    {response.status_code}')
        print(f'Request URL:       {response.request.method} {response.request.url}')
        print(f'Request body:      {response.request.body}')
        print(f'Response headers:  {dict(response.headers)}')
        print(f'Response body:     {response.text[:1000]}')


if __name__ == '__main__':
    main()
