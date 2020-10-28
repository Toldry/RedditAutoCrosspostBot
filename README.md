# RedditAutoCrosspostBot

https://www.reddit.com/user/AutoCrosspostBot

This reddit bot listens to comments and looks for ones that match the pattern 

`/r/othersubreddit`

It then saves the link to that comment for later processing.
After some waiting period  (e.g. 3 months), the bot checks the same comment, and if it has accrued enough upvotes, the bot will crosspost the submission that the comment replied to, to the other subreddit.

# Known problem: Reposts
This bot has two methods for preventing reposts. It first checks whether the source submission link was already posted to the target subreddit, and then it uses [repostsleuth.com](https://www.repostsleuth.com) to check whether anything similar to that link was already posted to the target subreddit.

As far as I know, RepostSleuth doesn't support videos or any other non-image file format, so this method will fail to detect video reposts.

I am looking for a way to solve this problem. Suggestions are welcome.

# Requierments
Python 3.X

# How to install

`pip install -r requirements.txt`

TODO: I need to actually make that `requirements.txt` file some time 🤦‍♀️

# How to run
Development:

`python3 reddit_auto_crosspost_bot.py`

Production:

`python3 reddit_auto_crosspost_bot.py --production`
