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
cd /opt
git clone https://github.com/Toldry/RedditAutoCrosspostBot.git
cd RedditAutoCrosspostBot

cp .env.example .env
# Edit .env with your Reddit account credentials and API secrets
```
> [!TIP]
> You can create and manage your Reddit bot credentials (client IDs and client secrets) via the classic developer portal at **[old.reddit.com/prefs/apps](https://old.reddit.com/prefs/apps)**.

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
* [uv](https://docs.astral.sh/uv/getting-started/installation/), which manages the virtual environment, the dependencies and the Python version (3.11, from `.python-version`; uv downloads it if it is missing)
* PostgreSQL 14+

```bash
# 1. Create .venv and install the locked dependencies, including the dev tools (pytest, bump-my-version)
uv sync

# 2. Configure .env with your local DATABASE_URL
cp .env.example .env

# 3. Run the bot module inside the virtual environment
uv run python -m racb
```

To test in production one entry at a time, `uv run python -m racb --process-one 42` runs the final checks and
the crosspost for the database row with id 42 (or, with no id, the oldest row that is due) and exits. Add
`DRY_RUN=True` to only log what it would do. See section 5 of [`AGENTS.md`](./AGENTS.md) for details.

`uv run` uses `.venv` without activating it. To use plain `python`, `pytest` etc. directly, activate it first:
```bash
source .venv/bin/activate      # Linux/macOS
.venv\Scripts\Activate.ps1     # Windows PowerShell
```

### Managing Dependencies
Dependencies are declared in [`pyproject.toml`](./pyproject.toml), and the exact version of every package,
including indirect ones, is pinned in [`uv.lock`](./uv.lock). Commit both files together.
```bash
uv add some-package                   # add a runtime dependency
uv add --dev some-package             # add a development-only dependency
uv lock --upgrade-package praw        # upgrade one package within the bounds in pyproject.toml
uv lock --upgrade                     # upgrade everything
uv sync                               # apply the lock to .venv
```
Dependabot version updates are paused (`open-pull-requests-limit: 0` in [`.github/dependabot.yml`](./.github/dependabot.yml)); remove those lines to get monthly update pull requests again.

### Running the Tests
The unit tests are fully mocked: they need no Reddit credentials, network access or database.

```bash
uv sync          # once, or after the lock changes
uv run pytest    # pytest.ini skips the live integration tests
```

Useful variations:
```bash
uv run pytest tests/unit/test_phase1.py   # one file
uv run pytest -k duplicate                # tests whose name matches "duplicate"
uv run pytest -x -vv                      # stop at the first failure, verbose output
```

> [!WARNING]
> `uv run pytest -m integration` runs the **live** integration tests. They log in as all 4 real bot accounts
> (credentials from `.env`) and post real submissions and comments in the private test subreddits
> `r/racb_test_1` and `r/racb_test_2`. They delete what they create; leftovers can be removed with
> `uv run python scripts/cleanup_test_history.py --dry-run` (preview) and then `--limit 0`.

CI runs the unit tests on Python 3.11 for every pull request and every push to `master`
(see [`.github/workflows/tests.yml`](./.github/workflows/tests.yml)).

### Versioning & Releases
Releases adhere to [Semantic Versioning](https://semver.org/) and are managed with `bump-my-version`:
```bash
uv run bump-my-version bump patch   # or minor / major
```

---

## Cloud Hosting Setup (Google Cloud Always Free)

You can run this bot 100% free on Google Cloud's Always Free `e2-micro` tier:
1. **Provision the VM using declarative flags:**
   ```bash
   gcloud compute instances create reddit-crosspost-bot --flags-file=deploy/instance-flags.yaml
   ```
2. The VM will automatically execute [`deploy/cloud-init.yaml`](./deploy/cloud-init.yaml) on first boot, provisioning 2GB swap space, Docker, and Docker Compose.
3. See [`AGENTS.md`](./AGENTS.md) for architecture details.

---

## License & Support
If you like the bot, you're welcome to donate to help maintain and improve it:

[![paypal](https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=FBQP2PLKZJ988)
