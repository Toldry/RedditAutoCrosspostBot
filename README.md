# RedditAutoCrosspostBot

https://www.reddit.com/user/AutoCrosspostBot

This reddit bot listens to comments and looks for ones that match the pattern 

`/r/othersubreddit`

It then saves the link to that comment for later processing.
After an hour, the bot checks the same comment,  
and if it has accrued enough upvotes, the bot will crosspost the submission that the comment replied to, to the other subreddit.

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
