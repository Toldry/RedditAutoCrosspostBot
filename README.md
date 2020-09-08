# RedditAutoCrosspostBot

https://www.reddit.com/user/AutoCrosspostBot

This reddit bot listens to comments and looks for ones that match the pattern 

`/r/othersubreddit`

It then saves the link to that comment for later processing.
After some period of time (e.g. 3 hours, 2 months), the bot checks the same comment,  
and if it has accrued enough upvotes, the bot will crosspost the submission that the comment replied to, to the other subreddit.

# Known problem: Reposts
This bot has a very rudimentary method of preventing reposts; it checks whether the original post's link has been posted to /r/othersubreddit before, and if it has, then the bot won't crosspost the original post. This means that when the same image/video/whatever has been posted to /r/othersubreddit with a different link than the one the bot has, the bot will fail to detect the prior post, and will post the same media with a different link, resulting in a repost.

I considered using [MAGIC_EYE_BOT](https://www.reddit.com/r/MAGIC_EYE_BOT/comments/hanedl/feature_requests_utilizing_magic_eye_bot_in_my/) to prevent this, but [that turned out more complicated than I thought it would be](https://www.reddit.com/r/MAGIC_EYE_BOT/comments/hanedl/feature_requests_utilizing_magic_eye_bot_in_my/)

I am still looking for a way to solve this reposting problem. Suggestions are welcome.

# Requierments
Python 3.X

# How to install

`pip install -r requirements.txt`

TODO: I need to actually make that `requirements.txt` file some time

# How to run
Development:

`python3 RedditAutoCrosspostBot.py`

Production:

`python3 RedditAutoCrosspostBot.py --production`
