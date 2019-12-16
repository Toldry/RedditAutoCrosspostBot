import praw
from pprint import pprint
import re
import sys

reddit = None

def main():
    print('running...')
    instantiate_reddit()

    #for submission in reddit.subreddit('learnpython').hot(limit=10):
    #    print(submission.title)

    scanned_subreddits = get_scanned_subreddits_string()
    test_subreddits = 'test+test9'

    subreddit_object = reddit.subreddit(scanned_subreddits)

    i = 0
    # infinite stream of comments from reddit
    for comment in subreddit_object.stream.comments(skip_existing=True):
        print(i)
        i += 1
        handle_comment(comment)

def instantiate_reddit():
    username = 'AutoCrosspostBot'
    clientname = username
    #password = 'TzUQA0GhvfUhWzYoi7lm'
    app_client_id = 'zWq7Ze5tZ1LhWA'
    #app_client_secret = 'jwdF-bqlpWXa41GfKCqLLLHxK_c'
    version = '0.1'
    developername = 'orqa'
    useragent =  f'{clientname}/{version} by /u/{developername}'

    creds = read_credentials()
    password = creds[0]
    app_client_secret = creds[1]

    global reddit
    reddit = praw.Reddit(client_id=app_client_id,
                         client_secret=app_client_secret,
                         user_agent=useragent,
                         username=username,
                         password=password)
def read_credentials():
    lines = None
    with open('.credentials') as f:
        lines = [line.rstrip('\n') for line in f]
    return lines

def get_scanned_subreddits_string():
    scanned_subreddits_list = ['announcements','funny','AskReddit','gaming','pics','science','aww','worldnews','movies','todayilearned','Music','videos','IAmA','news','gifs','EarthPorn','Showerthoughts','askscience','Jokes','blog','explainlikeimfive','books','food','LifeProTips','DIY','mildlyinteresting','Art','sports','space','gadgets','nottheonion','television','photoshopbattles','Documentaries','GetMotivated','listentothis','UpliftingNews','tifu','InternetIsBeautiful','history','Futurology','philosophy','OldSchoolCool','WritingPrompts','personalfinance','dataisbeautiful','nosleep','creepy','TwoXChromosomes','technology','AdviceAnimals','Fitness','memes','WTF','wholesomememes','politics','bestof','interestingasfuck','BlackPeopleTwitter','oddlysatisfying','leagueoflegends','travel','lifehacks','facepalm','dankmemes','pcmasterrace','me_irl','NatureIsFuckingLit','Tinder','nba','woahdude','PS4','AnimalsBeingBros','Whatcouldgowrong','AnimalsBeingJerks','relationships','tattoos','Overwatch','FoodPorn','reactiongifs','trippinthroughtime','atheism','BikiniBottomTwitter','Unexpected','gonewild','PewdiepieSubmissions','programming','gameofthrones','relationship_advice','boardgames','europe','malefashionadvice','Minecraft','gardening','pokemongo','instant_regret','photography','dadjokes','mildlyinfuriating','Game',]
    return '+'.join(scanned_subreddits_list)

def get_existing_crosspost(comment, other_subreddit):
    return None #TODO

def get_existing_repost(link_url, other_subreddit):
    return None #TODO

def handle_existing_post(comment, existing_post):
    return None #TODO

#TODO find a more elegant way than a global variable
moderators = {}
def get_all_moderators():
    global moderators
    if bool(moderators): #if the moderators dict is not empty
        return moderators

    scanned_subreddits = get_scanned_subreddits_string()
    print('retrieving all moderators...')
    subreddits = scanned_subreddits.split('+')
    for sub in subreddits:
        print(f'getting moderators of /r/{sub}')
        moderators[sub] = reddit.subreddit(sub).moderator()
    print('finished retrieving all moderators:')
    pprint(moderators)
    return moderators

def handle_comment(comment):
    link_id_regex = re.compile(r'^(.+)_(.+)$')
    
    link_url = comment.link_url
    link_id = comment.link_id

    if is_mod_post(comment):
        print('this is a moderator post. skipping.')
        return

    other_subreddit = check_pattern(comment)
    if not other_subreddit:
        return

    existing_post = None
    link_to_xpost = get_existing_crosspost(comment, other_subreddit)
    if link_to_xpost is not None:
        existing_post = link_to_xpost
    else: 
        existing_repost = get_existing_repost(link_url, other_subreddit)
        if existing_repost is not None:
            existing_post = existing_repost

    #TODO if there is already a crosspost that was 
    # made before this comment was posted
    # then link to it as a reply to the comment
    if existing_post is not None:
        handle_existing_post(comment, existing_post)
        return

    #pprint(vars(comment))
    cleaned_link_id = link_id_regex.search(link_id).groups()[1]
    print(cleaned_link_id)
    submission = reddit.submission(id=cleaned_link_id) #TODO

    try:
        cross_post = submission.crosspost(subreddit=other_subreddit,
                                          send_replies=False)
        print('crosspost succesful')
    except:
        e = sys.exc_info()[0]
        print(f'crosspost failed: {str(e)}')
        pass

def is_mod_post(comment):
    moderators = get_all_moderators()
    return comment.author in moderators[comment.subreddit]  #TODO check if this is the correct attribute

def check_pattern(comment):
    #TODO singleton regex
    subreddit_regex = re.compile(r'^(\/)?r\/([a-zA-Z0-9-_]+)$')

    searchResult = subreddit_regex.search(comment.body)
    if searchResult is None:
        print('no match')
        return None

    groups = searchResult.groups()
    print(groups)

    other_subreddit = groups[1]
    print(other_subreddit)
    return other_subreddit

if __name__ == '__main__':
    # execute only if run as a script
    main()