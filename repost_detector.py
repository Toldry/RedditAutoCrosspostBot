"""Attempts to detect whether a submission has already been posted"""

import logging
from http import HTTPStatus

import requests

def get_reposts_in_sub(source_comment, target_subreddit):
    post_id = source_comment.submission.id
    posts = search_reposts(post_id)
    posts_in_target_sub = [p for p in posts if p['subreddit'].lower() == target_subreddit.lower()]
    return posts_in_target_sub
    #the dict this function returns has the following structure:
    # [{'post_id': 'k6qn88', 'url': 'https://i.redd.it/0ih30efcs7361.jpg', 'shortlink': None, 'perma_link': '/r/gay_irl/comments/k6qn88/gayirl/', 'title': 'Gay🦸\\u200d♂️irl', 'dhash_v': 'ffff0000000010fe30263efdccc880009bff06ff16006110fad3988c0717fffd', 'dhash_h': '5331f0001a4f0e792a716b64cd5991d91349358d3d8d6dcfc5c735873b873f87', 'created_at': 1607106877.0, 'author': 'star37o', 'subreddit': 'gay_irl'}]


def search_reposts(post_id):
    logging.info(f'Using api.repostsleuth.com with post_id={post_id} to check ')
    parameters = {
        'filter':True,
        'post_id':post_id,
        'include_crossposts':True,
        'image_match_percent':65,
        # 'same_sub':'false',
        # 'filter_author':'true',
        # 'only_older':'false',
        # 'meme_filter':'false',
        # 'filter_dead_matches':False,
    }
    try:
        response = requests.get('https://api.repostsleuth.com/image', params=parameters)
    except Exception as e:
        # logging.warn(f'Encountered error while accessing api.repostsleuth.com: {e}')
        return []

    if response.status_code != HTTPStatus.OK:
        # logging.info(f'Encountered error while accessing api.repostsleuth.com: {response.reason}, most likely because the source submission is a video.')
        return []
    
    content = response.json()
    posts = [x['post'] for x in content['matches']]
    return posts

