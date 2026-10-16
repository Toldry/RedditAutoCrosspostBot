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
On the GCP VM you can also send the logs to Google Cloud Logging and search them in the browser; see
[`docs/logs.md`](./docs/logs.md).

### 4. Stop Services
```bash
docker compose down
```

### 5. Browse the Database (Optional)
A password-protected, read-only web viewer ([pgweb](https://github.com/sosedoff/pgweb)) can run next to the bot.
It is never exposed to the internet; you open it through an SSH tunnel. See
[`docs/db-viewer.md`](./docs/db-viewer.md).

### 6. Process a Single Entry (Production Testing)
`--process-one` runs Phase 3 (the final checks, the crosspost and its reply) on the oldest row in the
`scraped_comments` table only, and exits. Use it to test the bot in production one crosspost at a time:

* The row is processed even if it is not due yet, but it still has to pass every Phase 2 filter.
* The row is deleted afterwards whether or not it was crossposted, so repeated runs walk through the table
  oldest first. With `DRY_RUN=True` nothing is posted or deleted; the bot only logs what it would do.
* Exit code 1 means the table is empty (or a bot account failed to log in).

Inside the running Docker stack, from the checkout directory (on the VM, `/opt/RedditAutoCrosspostBot`):
```bash
docker compose exec bot python -m racb --process-one                 # crosspost the oldest row
docker compose exec -e DRY_RUN=True bot python -m racb --process-one # only log the crosspost
```

`exec` runs inside the bot's container, so if the bot restarts (it crashed, or a deploy replaced it), Docker kills
the run mid-way without a message and the exit code is 137. Check `docker compose logs --tail 80 bot` for why
the bot stopped. `docker compose run --rm bot python -m racb --process-one` runs it in a container of its own
instead, which a bot restart does not touch.

From your own computer, through SSH to the VM (same command in bash and PowerShell; use your VM name and
zone, see [`docs/logs.md`](./docs/logs.md)). `-T` is needed because the SSH command runs without a terminal:
```bash
gcloud compute ssh reddit-crosspost-bot --zone us-central1-a --command "cd /opt/RedditAutoCrosspostBot && docker compose exec -T -e DRY_RUN=True bot python -m racb --process-one"
```
Drop `-e DRY_RUN=True` to crosspost for real.

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

To test one entry at a time, see [Process a Single Entry](#6-process-a-single-entry-production-testing). Without Docker:
```bash
uv run python -m racb --process-one                 # crosspost the oldest row and exit
DRY_RUN=True uv run python -m racb --process-one    # same checks, but only log the crosspost
```
In PowerShell, set the variable first: `$env:DRY_RUN='True'; uv run python -m racb --process-one`
(it stays set for the rest of the session; `Remove-Item Env:DRY_RUN` clears it).

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
> `r/racb_test_1` and `r/racb_test_2`. They delete what they create and record it; whatever a killed run left
> behind is removed with `uv run python scripts/delete_test_artifacts.py`. To sweep the bots' whole history
> for test posts instead, run `uv run python scripts/cleanup_test_history.py --dry-run` (preview) and then `--limit 0`.

CI runs the unit tests on Python 3.11 for every pull request and every push to `master`
(see [`.github/workflows/tests.yml`](./.github/workflows/tests.yml)). When automatic deployment is on, pushes
to `master` also run the live integration tests, delete the posts they created, and deploy only if both pass
([`docs/auto-deploy.md`](./docs/auto-deploy.md)).

### Versioning & Releases
Releases adhere to [Semantic Versioning](https://semver.org/) and are automatic: after the tests pass on
`master`, the `release` job in [`.github/workflows/tests.yml`](./.github/workflows/tests.yml) bumps the
version with `bump-my-version`, compiles the changelog fragments in [`changelog.d/`](./changelog.d/) into a
dated section of [`CHANGELOG.md`](./CHANGELOG.md) with towncrier, and pushes a `vX.Y.Z` tag. A `feat` commit
makes a minor release, a breaking one (`feat!:` or a `BREAKING CHANGE:` footer) a major release, and anything
else a patch release. Pull requests only add a fragment; they never edit CHANGELOG.md or change the version.
See [`CONTRIBUTING.md`](./CONTRIBUTING.md).

---

## Cloud Hosting Setup (Google Cloud Always Free)

You can run this bot 100% free on Google Cloud's Always Free `e2-micro` tier:
1. **Provision the VM using declarative flags:**
   ```bash
   gcloud compute instances create reddit-crosspost-bot --flags-file=deploy/instance-flags.yaml
   ```
2. The VM will automatically execute [`deploy/cloud-init.yaml`](./deploy/cloud-init.yaml) on first boot, provisioning 2GB swap space, Docker, and Docker Compose.
3. See [`AGENTS.md`](./AGENTS.md) for architecture details.
4. Optionally, have every push to `master` that passes the unit and integration tests redeploy the VM automatically: see
   [`docs/auto-deploy.md`](./docs/auto-deploy.md) for the one-time setup.

---

## License & Support
If you like the bot, you're welcome to donate to help maintain and improve it:

[![paypal](https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=FBQP2PLKZJ988)
