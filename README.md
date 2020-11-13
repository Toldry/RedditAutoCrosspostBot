# RedditAutoCrosspostBot
https://www.reddit.com/user/AutoCrosspostBot

This reddit bot listens to comments and looks for ones that match the pattern 

`/r/target_subreddit`

It then saves the link to that comment for later processing.
After some waiting period  (e.g. 3 months), the bot checks the same comment, and if it has accrued enough upvotes, the bot will crosspost the submission that the comment replied to, to the target subreddit.

# Known problem: Reposts
This bot has two methods for preventing reposts. It first checks whether the source submission link was already posted to the target subreddit, and then it uses [repostsleuth.com](https://www.repostsleuth.com) to check whether anything similar to that link was already posted to the target subreddit.

As far as I know, RepostSleuth doesn't support videos or any other non-image file format, so this method will fail to detect video reposts.

I am looking for a way to solve this problem. Suggestions are welcome.

# Requierments
Python 3.X
PostgreSQL
Heroku (for production)

# How to install 
`pip install -r requirements.txt`

# How to run
`python reddit_auto_crosspost_bot.py`


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

# Donate
If you like the bot, you're welcome to donate to help me maintain and improve it ❤
<form action="https://www.paypal.com/donate" method="post" target="_top">
<input type="hidden" name="hosted_button_id" value="FBQP2PLKZJ988" />
<input type="image" src="https://www.paypalobjects.com/en_US/IL/i/btn/btn_donateCC_LG.gif" border="0" name="submit" title="PayPal - The safer, easier way to pay online!" alt="Donate with PayPal button" />
<img alt="" border="0" src="https://www.paypal.com/en_IL/i/scr/pixel.gif" width="1" height="1" />
</form>
