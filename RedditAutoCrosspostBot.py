"""Main entry point of the program
"""

import logging
from logging.handlers import RotatingFileHandler

import schedule

from . import environment
from . import inbox_responder
from . import listener
from . import reddit_instantiator
from . import replier
from . import unwated_submission_remover

# https://www.pythonforengineers.com/build-a-reddit-bot-part-1/

configure_logging()


def configure_logging():
    file_handler = RotatingFileHandler("app.log", mode='a', maxBytes=5 * 1024 * 1024, backupCount=1, encoding=None,
                                       delay=0)
    stream_handler = logging.StreamHandler()

    file_handler.setLevel(logging.INFO)
    stream_handler.setLevel(logging.DEBUG)

    logging_blacklist = ['prawcore', 'urllib3.connectionpool', 'schedule']
    for item in logging_blacklist:
        logging.getLogger(item).disabled = True

    logging.basicConfig(format='%(asctime)-15s - %(name)s - %(levelname)s - %(message)s',
                        level=logging.DEBUG,
                        handlers=[
                            file_handler,
                            stream_handler
                        ])


def main():
    logging.info('Running RedditAutoCrosspostBot')
    reddit = reddit_instantiator.get_reddit_instance()

    replier.respond_to_saved_comments()
    unwated_submission_remover.delete_unwanted_submissions()
    inbox_responder.respond_to_inbox()

    schedule.every(7).minutes.do(unwated_submission_remover.delete_unwanted_submissions)
    schedule.every(20).seconds.do(inbox_responder.respond_to_inbox)
    schedule.every(6).minutes.do(replier.respond_to_saved_comments)

    scanned_subreddits = 'all'
    # scanned_subreddits = 'test+test9'
    subreddit_object = reddit.subreddit(scanned_subreddits)

    # infinite stream of comments from reddit
    logging.info('Listening to comment stream...')
    for comment in subreddit_object.stream.comments(skip_existing=True):
        try:
            listener.handle_incoming_comment(comment)
            schedule.run_pending()
        except Exception as e:
            logging.exception(e)
            if environment.DEBUG:
                raise


if __name__ == '__main__':
    # execute only if run as a script
    main()
