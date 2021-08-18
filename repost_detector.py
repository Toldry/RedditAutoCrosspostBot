"""Attempts to detect whether a submission has already been posted"""

import logging
from http import HTTPStatus

import requests

def get_reposts_in_sub(source_comment, target_subreddit):
    url = source_comment.submission.url
    posts = search_reposts(url)
    target_subreddit = target_subreddit.lower()
    posts_in_target_sub = [p for p in posts if p['subreddit'].lower() == target_subreddit]
    return posts_in_target_sub
    #the dict this function returns has the following structure:
    # [{'post_id': 'k6qn88', 'url': 'https://i.redd.it/0ih30efcs7361.jpg', 'shortlink': None, 'perma_link': '/r/gay_irl/comments/k6qn88/gayirl/', 'title': 'Gay🦸\\u200d♂️irl', 'dhash_v': 'ffff0000000010fe30263efdccc880009bff06ff16006110fad3988c0717fffd', 'dhash_h': '5331f0001a4f0e792a716b64cd5991d91349358d3d8d6dcfc5c735873b873f87', 'created_at': 1607106877.0, 'author': 'star37o', 'subreddit': 'gay_irl'}]


def search_reposts(url):
    parameters = {
        'filter':True,
        'url': url,
        'include_crossposts':True,
        'target_match_percent':90,
        'filter_author':False,
        # 'same_sub':'false',
        # 'only_older':'false',
        # 'meme_filter':'false',
        # 'filter_dead_matches':False,
    }
    try:
        response = requests.get('https://api.repostsleuth.com/image', params=parameters)
    except Exception as e:
        logging.warn(f'Encountered error while accessing api.repostsleuth.com: {e}')
        return []

    log_error = False
    if response.status_code == HTTPStatus.OK:
        content = response.json()
        posts = [x['post'] for x in content['matches']]
        return posts
    elif response.headers.get('content-type') == 'application/json':
        error_details = response.json()
        if error_details['title'] == 'Invalid URL':
            # This typically happens when the URL points to a video (rather than an image)
            pass
        elif error_details['title'] == 'Search API is not available.':
            pass
        else:
            log_error = True
    else:
        log_error = True
    if log_error:
        logging.info(f'Encountered a problem with repostsleuth.', str(response.status_code))
    return []

