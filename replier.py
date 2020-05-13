import praw
import time
import logging
import re

import reddit_instantiator
import racb_db
import environment
import listener


TIME_DELAY_SECONDS = 30*60 #30 min
COMMENT_SCORE_THRESHOLD = 4
POST_SUFFIX_TEXT = '''
---

^^🤖 ^^this ^^comment ^^was ^^written ^^by ^^a ^^bot. ^^beep ^^boop ^^🤖

^^if ^^there's ^^a ^^problem, ^^please ^^report ^^it ^^to ^^/u/orqa'''


def respond_to_saved_comments():
    logging.info('Responding to saved comments')
    comment_entries = racb_db.get_comments_before_timedelta(TIME_DELAY_SECONDS) #get comments that were scraped TIME_DELAY_SECONDS seconds ago
    for comment_entry in comment_entries:
        logging.info(f"Begin processing saved comment: {comment_entry['permalink']}")
        comment = get_full_comment_from_reddit(comment_entry)
        handle_comment(comment)
        racb_db.remove_comment(comment_entry) #remove entry after processing


def get_full_comment_from_reddit(comment_info):
    reddit = reddit_instantiator.get_reddit_instance()
    return reddit.comment(url= r'https://www.reddit.com' + comment_info['permalink'])

def get_existing_crosspost(comment, other_subreddit):
    """Attempts to find whether an existing crosspost already exists. Returns the oldest replica submission if found. 
    Returns `None` if no existing crosspost exists
    Returns a `string_reason` if it is impossible to crosspost to the other sub (due to it not existing, or being private, or otherwise)
    """
    reddit = reddit_instantiator.get_reddit_instance()
    try:
        submissions = [s for s in reddit.subreddit(other_subreddit).search(query = comment.submission.permalink, sort='new', time_filter='all')]
    except Exception as e:
        error_message = e.args[0]
        if error_message == 'Redirect to /submit': #when reddit tries redirecting a search query of a link to the submission page, that means 0 results were found for the search query
            return None
        elif error_message == 'Redirect to /subreddits/search': #when reddit redirects to /subreddits/search that means the subreddit `other_subreddit` doesn't exist
            return 'SUBREDDIT_DOES_NOT_EXIST'
        elif error_message == 'received 403 HTTP response': #this error is recieved when the other_subreddit is private "You must be invited to visit this community"
            return 'SUBREDDIT_IS_PRIVATE'
        else:
            raise

    if len(submissions) == 0:
        return None
    return submissions[-1] #return oldest submission that matches search

def handle_existing_post(comment, existing_post):
    text = f'I found [this preexisting post]({existing_post.link_permalink}) in /{existing_post.subreddit_name_prefixed} with the same link as the original post.'
    text += POST_SUFFIX_TEXT
    return comment.reply(text)

def handle_comment(comment):
    if comment.score < COMMENT_SCORE_THRESHOLD:
        logging.info(f'comment score = {comment.score} < {COMMENT_SCORE_THRESHOLD} = threshold. Passing this comment.')
        return

    other_subreddit = listener.check_pattern(comment)
    result = get_existing_crosspost(comment, other_subreddit)
    if result is not None and type(result) is not type(''):
        existing_post = result
        logging.info('Existing crosspost found') 
        reply = handle_existing_post(comment, existing_post)
        logging.info(f'Replied to comment, link: {reply.link_permalink}')
        return
    elif result is not None and type(result) is type(''):
        crosspost_is_impossible_reason_string = result
        logging.info(f'Cannot crosspost to subreddit \'{other_subreddit}\' because ${crosspost_is_impossible_reason_string}')
        return

    try:
        cross_post = comment.submission.crosspost(subreddit=other_subreddit, send_replies=False)
        logging.info(f'Crosspost succesful. link to post: www.reddit.com{cross_post.permalink}')
        reply_to_crosspost_suggestion_comment(comment, cross_post, other_subreddit)
    except Exception as e:
        handled_with_grace = handle_crosspost_exception(e, comment, other_subreddit)
        if not handled_with_grace:
            logging.error(f'Crosspost failed due to error: {str(e)}' + '\n\n' 
            + f'This occured while attempting to crosspost based on this comment: {comment.permalink}')
        if environment.DEBUG:
            raise


def reply_to_crosspost_suggestion_comment(comment, cross_post, other_subreddit):
    text = f'Crossposted to /r/{other_subreddit}:'
    text += '\n\n'
    text += 'www.reddit.com' + cross_post.permalink
    text += POST_SUFFIX_TEXT
    return comment.reply(text)


def handle_crosspost_exception(e, comment, other_subreddit):
    """Attempts to handle exceptions that arise while crossposting. Returns True if the error was handled gracefully
    """
    if type(e) is praw.exceptions.RedditAPIException:
        if e.error_type == 'NO_CROSSPOSTS':
            logging.info(f'Crossposts are not allowed in /r/{other_subreddit}')
            return True
        else:
            # Unfamiliar reddit error
            return False


    if len(e.args) == 0:
        return False

    error_message = e.args[0]
    
    ratelimit_error_regex = re.compile(r'you are doing that too much\. try again in (\d)+ minutes\.')
    search_ratelimit_error_message = ratelimit_error_regex.search(error_message)
    if search_ratelimit_error_message is not None:
        minutes_to_wait = int(search_ratelimit_error_message.groups()[0])
        logging.info(f'waiting {minutes_to_wait} minutes...')
        time.sleep(minutes_to_wait * 60)
        return True
    return False