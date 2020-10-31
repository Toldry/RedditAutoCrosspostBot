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
PostgreSQL
Heroku (for production)

# How to install for local development

`pip install -r requirements.txt`

# How to run
Development:

`python3 reddit_auto_crosspost_bot.py`

Production:
`python3 reddit_auto_crosspost_bot.py --production`


# Heroku
I use Heroku to host/run this app
TODO write more about this later

```
git clone https://github.com/Toldry/RedditAutoCrosspostBot.git
cd reddit_auto_crosspost_bot
heroku login
heroku ps:scale worker=1
```


# PostgreSQL
This bot stores its data in a postgres database
TODO write more about this later

# Environment variables
```
PASSWORD=redacted
APP_CLIENT_SECRET=redacted
DATABASE_URL=redacted
```
TODO write more about this later