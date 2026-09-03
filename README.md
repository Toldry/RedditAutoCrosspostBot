# RedditAutoCrosspostBot

This repository hosts a suite of 4 automated Reddit bots:
- [u/AutoCrosspostBot](https://www.reddit.com/user/AutoCrosspostBot)
- [u/sub_doesnt_exist_bot](https://www.reddit.com/user/sub_doesnt_exist_bot)
- [u/same_subreddit_bot](https://www.reddit.com/user/same_subreddit_bot)
- [u/same_post_bot](https://www.reddit.com/user/same_post_bot)

---

## What Does AutoCrosspostBot Do?

The primary bot is `AutoCrosspostBot`.\
It monitors Reddit comments across `r/all` and looks for comments that suggest a community matching the pattern:

`/r/SomeOtherSubreddit` or `r/SomeOtherSubreddit`

When a user suggests another subreddit (e.g. `r/gatesopencomeonin` in a `r/wholesomememes` thread):
1. **Phase 1:** The bot saves the suggestion to a PostgreSQL database.
2. **Phase 2 (Aged Filter Check):** After a waiting period (e.g. 2 hours), the bot batch-verifies via `reddit.info()` that the comment is still available and meets the score threshold.
3. **Phase 3 (Final Crosspost):** After a maturation period (e.g. 3 months), the bot crossposts the original submission to the target subreddit with an explanatory localized reply.

![AutoCrosspostBot Example](https://user-images.githubusercontent.com/7353619/202482938-dcf929f4-df8f-4394-8fc4-c07a3f17e943.png)

---

## Auxiliary Bots (Phase 1 Immediate Triggers)

* **`u/sub_doesnt_exist_bot`**: Replies to comments that link to non-existent subreddits, searching Reddit's live community index for close matches or suggesting community creation.
* **`u/same_subreddit_bot`**: Replies to comments that link to the exact same subreddit where the comment was posted (*"Yes, that's where we are."*).
* **`u/same_post_bot`**: Replies to comments linking to a target subreddit that already has an existing post with the exact same content.

---

## Quickstart & Deployment with Docker Compose (Recommended)

The easiest way to run the bot and PostgreSQL 24/7 (e.g. on a free Google Cloud `e2-micro` or Oracle Cloud VM) is using **Docker Compose**:

### 1. Clone & Configure
```bash
git clone https://github.com/Toldry/RedditAutoCrosspostBot.git
cd RedditAutoCrosspostBot

cp .env.example .env
# Edit .env with your Reddit account credentials and API secrets
```

### 2. Start the Stack (Background Daemon)
```bash
docker compose up -d --build
```

### 3. View Live Logs
```bash
docker compose logs -f bot
```

### 4. Stop Services
```bash
docker compose down
```

---

## Local Development (Without Docker)

### Requirements
* Python 3.10+
* PostgreSQL 14+

```bash
# 1. Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt # Optional: for release management (bump-my-version)

# 2. Configure .env with your local DATABASE_URL
cp .env.example .env

# 3. Run the bot
python reddit_auto_crosspost_bot.py
```

### Versioning & Releases
Releases adhere to [Semantic Versioning](https://semver.org/) and are managed with `bump-my-version`:
```bash
bump-my-version bump patch   # or minor / major
```

---

## Cloud Hosting Setup (Google Cloud Always Free)

You can run this bot 100% free on Google Cloud's Always Free `e2-micro` tier:
* Use [`cloud-init.yaml`](./cloud-init.yaml) or [`startup-script.sh`](./startup-script.sh) to automatically provision the instance with 2GB swap space, Docker, and Docker Compose.
* See [`AGENTS.md`](./AGENTS.md) for architecture details.

---

## License & Support
If you like the bot, you're welcome to donate to help maintain and improve it:

[![paypal](https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=FBQP2PLKZJ988)
