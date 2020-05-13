import schedule
import logging

import reddit_instantiator
import environment
import listener
import replier
from logging.handlers import RotatingFileHandler

#https://www.pythonforengineers.com/build-a-reddit-bot-part-1/


fileHandler = RotatingFileHandler("app.log", mode='a', maxBytes=5*1024*1024, backupCount=1, encoding=None, delay=0)
streamHandler = logging.StreamHandler() 

fileHandler.setLevel(logging.INFO)
streamHandler.setLevel(logging.DEBUG)

logging.getLogger("prawcore").disabled = True
logging.getLogger("urllib3.connectionpool").disabled = True

# Configure logging 
logging.basicConfig(#filename='app.log',
                    #filemode='w', 
                    format='%(asctime)-15s - %(name)s - %(levelname)s - %(message)s', 
                    level=logging.DEBUG,
                    handlers=[
                        fileHandler,
                        streamHandler
                    ])


def main():
    logging.info('Running RedditAutoCrosspostBot')
    reddit = reddit_instantiator.get_reddit_instance()

    replier.respond_to_saved_comments()
    schedule.every(6).minutes.do(replier.respond_to_saved_comments)
    scanned_subreddits = 'all'
    #scanned_subreddits = 'test+test9'
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


# TODO Add code to respond to 'Good bot' and 'Bad bot' replies
# TODO Monitor and delete comments (and their corresponding crosspost) whose score is negative
# TODO optional: before crossposting, check to make sure that this same link was not crossposted before (theoretically this should be solved already by the `get_existing_crosspost` function)