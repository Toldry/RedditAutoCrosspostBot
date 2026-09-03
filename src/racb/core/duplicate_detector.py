"""Attempts to detect whether a submission has already been posted using RepostSleuth API."""

import logging
from http import HTTPStatus

import requests

logger = logging.getLogger(__name__)


def get_reposts_in_sub(source_comment, target_subreddit):
    url = source_comment.submission.url
    posts = search_reposts(url)
    target_subreddit = target_subreddit.lower()
    posts_in_target_sub = [p for p in posts if p.get('subreddit', '').lower() == target_subreddit]
    return posts_in_target_sub


def search_reposts(url):
    parameters = {
        'filter': True,
        'url': url,
        'include_crossposts': True,
        'target_match_percent': 90,
        'filter_author': False,
    }
    try:
        response = requests.get('https://api.repostsleuth.com/image', params=parameters, timeout=10)
    except Exception as e:
        logger.warning(f'Encountered error while accessing api.repostsleuth.com: {e}')
        return []

    log_error = False
    if response.status_code == HTTPStatus.OK:
        try:
            content = response.json()
            posts = [x['post'] for x in content.get('matches', []) if 'post' in x]
            return posts
        except Exception:
            return []
    elif response.headers.get('content-type') == 'application/json':
        try:
            error_details = response.json()
            if error_details.get('title') in ('Invalid URL', 'Search API is not available.'):
                pass
            else:
                log_error = True
        except Exception:
            log_error = True
    else:
        log_error = True

    if log_error:
        logger.info(f'Encountered a problem with repostsleuth: status={response.status_code}, reason={response.reason}')
    return []
