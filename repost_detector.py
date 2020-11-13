import logging
from http import HTTPStatus

import requests

def check_if_posted_before(source_comment, target_subreddit):
    post_id = source_comment.submission.id
    posts = search_reposts(post_id)
    subreddits = [post['subreddit'] for post in posts]
    was_posted_before = target_subreddit.upper() in [sub.upper() for sub in subreddits]
    return was_posted_before


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
        logging.warn(f'Encountered error while accessing api.repostsleuth.com: {e}')
        return []

    if response.status_code != HTTPStatus.OK:
        logging.info(f'Encountered error while accessing api.repostsleuth.com: {response.reason}, most likely because the source submission is a video.')
        return []
    
    content = response.json()
    posts = [x['post'] for x in content['matches']]
    return posts

