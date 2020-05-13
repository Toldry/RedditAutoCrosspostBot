import praw
import logging

reddit = None

def _instantiate_reddit():
    username = 'AutoCrosspostBot'
    clientname = username
    #password = 'REDACTED'
    app_client_id = 'UwKgkrvtl9fpUw'
    #app_client_secret = 'REDACTED'
    version = '0.2'
    developername = 'orqa'
    useragent =  f'{clientname}/{version} by /u/{developername}'

    password, app_client_secret = _read_credentials()

    logging.info('Connecting to reddit via praw to instantiate connection instance')
    global reddit
    reddit = praw.Reddit(client_id=app_client_id,
                         client_secret=app_client_secret,
                         user_agent=useragent,
                         username=username,
                         password=password)

def _read_credentials():
    lines = None
    with open('.credentials') as f:
        lines = [line.rstrip('\n') for line in f]
    return lines


def get_reddit_instance():
    if not reddit:
        _instantiate_reddit()
    return reddit


__all__ = ['get_reddit_instance']        