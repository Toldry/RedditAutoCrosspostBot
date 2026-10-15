# AGENTS.md

Welcome to the **RedditAutoCrosspostBot** repository. This document is a comprehensive guide for AI agents and human contributors working on, debugging, extending, or maintaining this codebase.

> [!IMPORTANT]
> Section 9 lists **known limitations and open defects**. Read it before you assume a feature works the
> way its name suggests. If you fix one, remove it from that list.

---

## 1. Project Overview & Multi-Bot Architecture

This repository hosts a suite of 4 automated Reddit bots powered by [PRAW (Python Reddit API Wrapper)](https://praw.readthedocs.io/), backed by a PostgreSQL database and containerized with Docker / Docker Compose for deployment on free cloud VPS instances (e.g. Google Cloud Always Free or Oracle Cloud Always Free) or local hardware.

All 4 bots run in **one Python process, on a single thread**. A single loop alternates between the
`r/all` comment stream and the `schedule` jobs (inbox processing is one of them), so any slow call blocks
everything else (see section 7).

### Primary Bot: `AutoCrosspostBot`
- **Goal:** Monitors Reddit comments on `r/all` for community crosspost recommendations matching `r/SomeSubreddit` or `/r/SomeSubreddit`.
- **Workflow:** When a user suggests another subreddit in a comment, the bot tracks the comment. If the comment gains sufficient community support (score threshold) after a maturation waiting period (e.g. 3 months), the bot crossposts the parent submission to the target subreddit with an explanatory comment in the target language.
- **Matching rule:** The **entire** comment body must be the subreddit reference. The regex is
  `^(/)?r/(?P<sub_name>[a-zA-Z0-9_]{2,24})$` (no `MULTILINE`). `check out r/funny`, ` r/funny` (leading
  space) and `r/funny.` do **not** match. A single trailing newline does match, because `$` matches just before it.

### Companion Auxiliary Bots (Phase 1 Immediate Triggers)
Each of the 4 bots has its **own** Reddit account and "script"-type OAuth app. Client IDs are hardcoded in
`bot_details` in [`src/racb/core/reddit.py`](./src/racb/core/reddit.py). Passwords and client secrets come
from environment variables (section 4). Auxiliary replies are sent by re-fetching the comment through the
auxiliary bot's PRAW instance (`get_comment_with_different_praw_instance`). All auxiliary bots act during Phase 1:
1. **`sub_doesnt_exist_bot`** (`u/sub_doesnt_exist_bot`):
   - Trigger: Comment links to a non-existent subreddit (HTTP 404 / redirect).
   - Behavior: Performs live community search via `reddit.subreddits.search()` to suggest close existing subreddits, or suggests creating the subreddit if the name length is 3–24 characters.
2. **`same_subreddit_bot`** (`u/same_subreddit_bot`):
   - Trigger: Comment links to the exact same subreddit where it was posted.
   - Behavior: Replies humorously (*"Yes, that's where we are."*).
3. **`same_post_bot`** (`u/same_post_bot`):
   - Trigger: Comment links to a target subreddit that already contains a post with the exact same URL/content or visually matching image.
   - Behavior: Detects existing posts via Reddit search and self-hosted perceptual image hashing (`imagehash`), replying with a link to the prior post.

> [!WARNING]
> Auxiliary bots reply to matching comments at **any depth**. The top-level-only check
> (`comment.parent_id == comment.link_id`) runs **after** the auxiliary dispatch and gates only the DB insert.

---

## 2. Architecture & Lifecycle Phases

The core processing pipeline follows a 3-phase staggered lifecycle orchestrated in [`src/racb/main.py`](./src/racb/main.py):

```mermaid
flowchart TD
    A["Reddit r/all Comment Stream"] --> B["Phase 1: Ingestion & Fast Filters\n(src/racb/phases/phase1.py)"]
    B -->|Self Subreddit| C["u/same_subreddit_bot reply"]
    B -->|Duplicate Found| D["u/same_post_bot reply"]
    B -->|Nonexistent Sub| E["u/sub_doesnt_exist_bot reply"]
    B -->|Valid Recommendation| F[("PostgreSQL DB\nscraped_comments")]
    
    F --> G["Phase 2: Batch Filter Check\n(src/racb/phases/phase2.py, every 20m)"]
    G -->|"Batch Fetch via reddit.info()"| H["Validate Availability & Score"]
    H -->|Score < Threshold or Invalid| I["DELETE from DB"]
    H -->|Passes Filters| J["Set phase2_checked = true"]
    
    J --> K["Phase 3: Final Verification & Crosspost\n(src/racb/phases/phase3.py, every 6m)"]
    K -->|Re-verify Score & Availability| L["Crosspost to Target Subreddit"]
    L --> M["Reply to Crosspost (Localized Explanation)"]
    L --> N["DELETE from DB"]
    
    O["Scheduled Task (every 7m)\nsrc/racb/phases/cleanup.py"] --> P["Purge Bot Submissions with Score < 0"]
```

### Phase 1 Decision Order (exact)
`phase1.handle_incoming_comment()` evaluates in this order and stops at the first terminal branch:

```mermaid
flowchart TD
    S["Incoming comment"] --> R{"Body matches\nsubreddit regex?"}
    R -->|No| X1["ignore"]
    R -->|Yes| M{"distinguished ==\n'moderator'?"}
    M -->|Yes| X2["ignore"]
    M -->|No| T{"Submission title contains\nsub/subs/subreddit/subreddits?"}
    T -->|Yes| X3["ignore"]
    T -->|No| BL{"Source or target in\nSUB_BLACKLIST?"}
    BL -->|Yes| X4["ignore"]
    BL -->|No| SS{"target == source\n(case-insensitive)?"}
    SS -->|Yes| A1["same_subreddit_bot reply"]
    SS -->|No| DUP["get_posts_with_same_content()\n1 search call + up to 50-post\nimage-hash scan"]
    DUP -->|posts_found| A2["same_post_bot reply"]
    DUP -->|SUBREDDIT_DOES_NOT_EXIST| A3["sub_doesnt_exist_bot reply"]
    DUP -->|otherwise| TL{"Top-level comment?"}
    TL -->|No| X5["ignore"]
    TL -->|Yes| DB[("insert_scraped_comment(permalink, target)")]
```

Note: a private target (`SUBREDDIT_IS_PRIVATE`) is **still inserted** in Phase 1. Phase 2 deletes it later.

### Phase Details
- **Phase 1 ([`src/racb/phases/phase1.py`](./src/racb/phases/phase1.py)):**
  - Continuous streaming from `reddit.subreddit('all').stream.comments(skip_existing=True, pause_after=-1)`.
  - Filters: Discards moderator comments, submissions with meta-phrases (`sub`, `subreddit`), and blacklisted subreddits ([`src/racb/constants.py`](./src/racb/constants.py)). Non-top-level comments (`comment.parent_id != comment.link_id`) are discarded only **after** auxiliary-bot dispatch (see the warning in section 1).
  - Qualified suggestions are stored via stored procedure `insert_scraped_comment(permalink, target_subreddit)`.
  - Phases 2 and 3 re-parse the target from the live comment body and reject the row
    (`TARGET_SUBREDDIT_CHANGED`) if it no longer equals the stored target, case-insensitively. This blocks
    bait-and-switch edits. Rows scraped before v2.3 have `target_subreddit = NULL` and skip this check.
  - All of this work, including the duplicate search and image downloads, runs synchronously on the stream loop.
  - Auxiliary replies go through `phase1.reply_as_bot()`, which only logs when `DRY_RUN` is enabled.
  - Any error while handling one comment is caught in `main.handle_comment_safely()` and skips only that
    comment (section 7).
- **Phase 2 ([`src/racb/phases/phase2.py`](./src/racb/phases/phase2.py)):**
  - Runs periodically via `schedule` (or manually via `python -m racb --only-phase2`).
  - Fetches unchecked rows older than `PHASE2_WAITING_PERIOD` using `get_unchecked_comments_older_than(interval)`.
  - Executes **batch fetching via `reddit.info(fullnames=chunk)`** in chunks of 100 to minimize API requests by 99%:
    - Comment availability (not deleted / removed).
    - Score >= `COMMENT_SCORE_THRESHOLD`.
    - Regex pattern still holds (user didn't edit comment), and still names the stored target subreddit.
    - Target subreddit availability and duplicate check.
  - *Accuracy note:* only the **comment fetch** is batched. The duplicate check
    (`phase1.get_posts_with_same_content`) still costs one search request per row, plus a lazy submission
    fetch and a scan of up to 50 target posts for image submissions. The real saving is therefore well
    under 99%.
  - If the batch `info()` call throws, it falls back to fetching each comment individually (`run_filters(ce, comment=None)`).
  - Prunes failing entries from DB to conserve PostgreSQL table storage.
- **Phase 3 ([`src/racb/phases/phase3.py`](./src/racb/phases/phase3.py)):**
  - Runs periodically (every 6 minutes) if `LISTEN_ONLY` is false.
  - Queries records older than `PHASE3_WAITING_PERIOD` using `get_comments_older_than(interval)`. This
    function does **not** filter on `phase2_checked`, so Phase 3 depends on Phase 2 having pruned rows first.
  - Re-runs validation checks (`phase2.run_filters`, which fetches each comment individually).
  - Formats submission title (e.g. uppercase for `r/totallynotrobots`).
  - Executes `submission.crosspost()` and posts an explanatory reply using localized text templates loaded by [`src/racb/i18n.py`](./src/racb/i18n.py) from the gettext catalogs in [`src/racb/locales/`](./src/racb/locales/).
  - Handles Reddit API crosspost exceptions gracefully via modern `e.items` inspection (e.g., `NO_CROSSPOSTS`, `SUBREDDIT_NOTALLOWED`, `NO_IMAGES`, `SUBMIT_VALIDATION_*`).
  - Deletes the record from PostgreSQL. This happens **after** the crosspost, and Phase 3 keeps no
    "already crossposted" state (see section 9).
  - With `DRY_RUN`, it logs what it would crosspost and keeps the row.
  - `python -m racb --process-one` runs this on the oldest row only and exits (section 5).
- **Maintenance & Feedback:**
  - [`src/racb/phases/cleanup.py`](./src/racb/phases/cleanup.py): Periodically fetches the latest 40 submissions made by `AutoCrosspostBot` and deletes any with score < 0.
    Only submissions are covered. Comments made by any of the 4 bots are never cleaned up. A failed
    delete is logged and skipped; `DRY_RUN` only logs.
  - [`src/racb/phases/inbox.py`](./src/racb/phases/inbox.py): Evaluates feedback (`good bot` / `bad bot`).
    `process_unread_inboxes()` runs every 10 minutes and reads the unread inbox of **all 4** bots
    (there is no continuous inbox stream any more). Feedback is logged at INFO and marked read.
    `respond_to_positive_sentiment` and `respond_to_negative_sentiment` are still deliberate **no-ops**
    (bodies commented out by the author), so the bots never reply to feedback.

---

## 3. Directory & File Reference

| File | Purpose |
| :--- | :--- |
| [`src/racb/main.py`](./src/racb/main.py) | Application entry point, logging setup, stream loop, exception recovery, and `schedule` runner. |
| [`src/racb/__main__.py`](./src/racb/__main__.py) | Module executable entrypoint for `python -m racb`. |
| [`src/racb/__init__.py`](./src/racb/__init__.py) | Package marker; re-exports `__version__`. |
| [`src/racb/config.py`](./src/racb/config.py) | **All** environment parsing: `load_environment()`, `env_bool()`, `is_debug()` / `is_dry_run()` / `is_listen_only()`, `parse_duration()` (adds months and years on top of `pytimeparse`), waiting periods, score threshold. Use it instead of reading `os.environ` directly. |
| [`src/racb/core/reddit.py`](./src/racb/core/reddit.py) | Singleton factory for PRAW instances with native `ratelimit_seconds=300` and updated user-agent formats. Also `authenticate_all_bots()` (startup check) and the bot-name constants. |
| [`src/racb/core/db.py`](./src/racb/core/db.py) | PostgreSQL connector (`psycopg2-binary`) with connection retry logic and stored procedure invocation. |
| [`src/racb/core/sub_search.py`](./src/racb/core/sub_search.py) | Live Reddit community search using `reddit.subreddits.search()` for typo suggestions. |
| [`src/racb/core/duplicate_detector.py`](./src/racb/core/duplicate_detector.py) | Self-hosted perceptual image hashing (`imagehash` / `dhash`) for visual duplicate detection in target subreddits. |
| [`src/racb/phases/phase1.py`](./src/racb/phases/phase1.py) | Phase 1 comment matching, blacklisting, auxiliary bot dispatching, DB insertion. |
| [`src/racb/phases/phase2.py`](./src/racb/phases/phase2.py) | Phase 2 batch comment filtering via `reddit.info()` and DB cleanup. |
| [`src/racb/phases/phase3.py`](./src/racb/phases/phase3.py) | Phase 3 final validation, crosspost execution, comment reply, modern API exception handling. |
| [`src/racb/phases/inbox.py`](./src/racb/phases/inbox.py) | Evaluates feedback sent to the bot's inbox. |
| [`src/racb/phases/cleanup.py`](./src/racb/phases/cleanup.py) | Auto-cleanup job deleting downvoted posts made by the bot. |
| [`src/racb/sql/instantiate_db.sql`](./src/racb/sql/instantiate_db.sql) | DDL schema, indexes, view `v_scraped_comments`, and PL/pgSQL stored procedures. Executed on **every new DB connection**, so every statement must be idempotent (see section 6). |
| [`src/racb/constants.py`](./src/racb/constants.py) | Hardcoded blacklist of subreddits to ignore (`SUB_BLACKLIST`). |
| [`src/racb/i18n.py`](./src/racb/i18n.py) | Loads the gettext catalogs, picks the locale for a subreddit, and falls back to English for untranslated strings (body and footer always share one language; a reply built from several messages, like the nonexistent-subreddit reply, switches entirely to English if any piece is untranslated). |
| [`src/racb/locales/`](./src/racb/locales/) | gettext catalogs: `<locale>/LC_MESSAGES/messages.po` (edited) and `messages.mo` (compiled, committed). Locales: `en` (source text), `es`, `de`, `fr`, `he`, and `en@totallynotrobots`, an English variant using the gettext `@modifier` syntax. |
| [`src/racb/locales/subreddits.toml`](./src/racb/locales/subreddits.toml) | Which locale each subreddit gets (`locale = [subreddits]`, case-insensitive). Unlisted subreddits get English. |
| [`src/racb/version.py`](./src/racb/version.py) | Application version definition (`__version__`). |
| [`scripts/release_level.py`](./scripts/release_level.py) | Used by the CI `release` job: prints `major`, `minor`, `patch` or `none` from the Conventional Commits since the last `v*` tag and whether CHANGELOG `[Unreleased]` has entries. |
| [`scripts/cleanup_test_history.py`](./scripts/cleanup_test_history.py) | Deletes integration-test posts and comments from all 4 bot accounts. Supports `--dry-run` and `--limit N` (`0` means all). |
| [`tests/conftest.py`](./tests/conftest.py) | `MockComment` / `MockSubmission` / `MockSubreddit` / `MockRedditor` plus the `make_mock_comment` and `make_mock_submission` factory fixtures. |
| [`tests/unit/`](./tests/unit/) | 124 mocked unit tests (`@pytest.mark.unit`), one file per module. |
| [`tests/integration/test_reddit_integration.py`](./tests/integration/test_reddit_integration.py) | 10 **live** Reddit tests (`@pytest.mark.integration`) that submit real posts and comments. |
| [`tests/assets/test_sample.png`](./tests/assets/test_sample.png) | Base image for perceptual-hash variation tests. |
| [`pytest.ini`](./pytest.ini) | Test discovery: `testpaths = tests`, `pythonpath = src .`, markers `unit` and `integration`, and `addopts = -m "not integration"`. |
| [`.github/workflows/tests.yml`](./.github/workflows/tests.yml) | CI: `uv sync --locked` then `uv run pytest` on Python 3.11 (from `.python-version`), for pushes to `master`/`main` and for pull requests. Fails if `uv.lock` is out of date. On `master`, a `release` job then bumps the version and tags it (section 5). |
| [`.github/dependabot.yml`](./.github/dependabot.yml) | Dependabot config for `pyproject.toml` / `uv.lock` (`uv` ecosystem) and GitHub Actions. Version updates are **paused** with `open-pull-requests-limit: 0`; delete those lines to resume monthly PRs. |
| [`examples/example_reddit_comment.py`](./examples/example_reddit_comment.py) | Not executable code: a commented-out attribute dump of a PRAW `Comment` and `Submission`, kept for reference. |
| [`deploy/cloud-init.yaml`](./deploy/cloud-init.yaml) | Universal cloud-init configuration for provisioning cloud VMs (GCP, Oracle, AWS) with swap and Docker. It does **not** clone or start the bot. |
| [`deploy/instance-flags.yaml`](./deploy/instance-flags.yaml) | Declarative Google Cloud CLI flags file for provisioning the Always Free e2-micro instance. |
| [`Dockerfile`](./Dockerfile) | Lightweight Python 3.11 container image definition. |
| [`docker-compose.yml`](./docker-compose.yml) | Multi-container stack definition (PostgreSQL 15 + Python bot worker). |
| [`.bumpversion.toml`](./.bumpversion.toml) | Configuration for `bump-my-version`, which the CI `release` job runs. |
| [`.gitignore`](./.gitignore) | Ignores **all dotfiles** (`.*`) except an explicit allowlist. See section 8. |
| [`CHANGELOG.md`](./CHANGELOG.md) | Release history following Keep a Changelog. |
| [`.env.example`](./.env.example) | Environment variable template. |
| [`pyproject.toml`](./pyproject.toml) | Project metadata for uv and hatchling: `requires-python = ">=3.11"`, runtime dependencies with lower bounds (PRAW and prawcore also capped below their next major), and the `dev` dependency group (`bump-my-version`, `pytest`, `pytest-mock`). The version is read dynamically from `src/racb/version.py`. |
| [`uv.lock`](./uv.lock) | Cross-platform lockfile: the exact version and hashes of **every** package, including transitive ones (numpy, scipy, ...). Generated by `uv lock`; never edit it by hand. |
| [`.python-version`](./.python-version) | `3.11`. Tells uv which interpreter to use for `.venv` (and CI). Keep it in sync with the Dockerfile base image. |
| [`.dockerignore`](./.dockerignore) | Keeps local virtualenvs and caches out of the image. It deliberately does **not** exclude `.env` (section 8). |

`uv sync` installs `racb` itself into `.venv` as an **editable** package (from `src/`), so `python -m racb`
works from any directory without `PYTHONPATH`. The Docker image does the same, with its venv at
`/opt/venv` (outside `/app`, so `COPY . .` cannot clobber it) and no dev dependencies.

---

## 4. Environment Variables

Store these in `.env` for local development or production:

| Variable | Type | Code default | Description |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` | String | *(built from `POSTGRES_*`)* | PostgreSQL connection URI (`postgresql://user:pass@host:5432/db`). Docker Compose injects one pointing at the `db` service unless you override it. |
| `POSTGRES_DB` | String | `racb` | Used by the Compose `db` service, and by `db.py` when `DATABASE_URL` is empty. |
| `POSTGRES_USER` | String | `racb_user` | Same as above. |
| `POSTGRES_PASSWORD` | String | *(none; required if no `DATABASE_URL`)* | Same as above. Compose falls back to `racb_secure_password`; change it. |
| `POSTGRES_HOST` | String | `localhost` | Used only when building the URL from parts. |
| `POSTGRES_PORT` | String | `5432` | Used only when building the URL from parts. |
| `DB_SSLMODE` | String | auto | libpq `sslmode`. If unset: `disable` when the URL contains `@db:`, `@localhost:` or `@127.0.0.1:`, otherwise `prefer`. Compose sets `disable`. |
| `DEBUG` | Boolean (`True`/`False`) | `False` | Debug mode toggle. Sets logging to `DEBUG`, disables catch-all exception swallow. See side effects below. |
| `LISTEN_ONLY` | Boolean (`True`/`False`) | `False` | When `True`, disables Phase 3 crossposting while retaining stream listening and Phase 2 filtering. |
| `DRY_RUN` | Boolean (`True`/`False`) | `False` | When `True`, nothing is written to Reddit: auxiliary replies, crossposts and cleanup deletions are only logged with a `[DRY RUN]` prefix. The database is still written to, but Phase 3 keeps the rows it would have crossposted. |
| `COMMENT_SCORE_THRESHOLD` | Integer | `1` (`.env.example`: `30`) | Minimum upvote score a comment must have to survive Phase 2 and be crossposted in Phase 3. |
| `PHASE2_WAITING_PERIOD` | Time string | `2 hours` | Time duration before Phase 2 checks a comment (parsed via `pytimeparse`, e.g. `2 hours` or `1 day`). |
| `PHASE3_WAITING_PERIOD` | Time string | `90 days` | Time duration before Phase 3 executes the crosspost (e.g. `3 months` or `90 days`). The public crosspost reply shows it as a localized whole number of days. |
| `LOG_DIR` | Path | `logs` | Directory for `app.log` (relative to the CWD; `/app/logs` in Docker, mounted on volume `racb_logs`). |
| `PASSWORD` | String | | Account password for `AutoCrosspostBot`. |
| `APP_CLIENT_SECRET` | String | | App client secret for `AutoCrosspostBot`. |
| `PASSWORD__SUB_DOESNT_EXIST` | String | | Account password for `sub_doesnt_exist_bot`. |
| `APP_CLIENT_SECRET__SUB_DOESNT_EXIST` | String | | App client secret for `sub_doesnt_exist_bot`. |
| `PASSWORD__SAME_SUBREDDIT` | String | | Account password for `same_subreddit_bot`. |
| `APP_CLIENT_SECRET__SAME_SUBREDDIT` | String | | App client secret for `same_subreddit_bot`. |
| `PASSWORD__SAME_POST` | String | | Account password for `same_post_bot`. |
| `APP_CLIENT_SECRET__SAME_POST` | String | | App client secret for `same_post_bot`. |

### Parsing rules (`src/racb/config.py`)
- **Booleans:** `true/1/yes/y/t`, case-insensitive, via `config.env_bool()`. Anything else is false.
- **Durations:** `config.parse_duration()` accepts everything `pytimeparse` does (`2 hours`, `90 days`,
  `1w 2d`) plus `N month(s)` (30 days each) and `N year(s)` (365 days each). An unparseable value logs a
  WARNING and falls back to the default. `0 seconds` is respected as zero.
- **`.env` loading:** `main()` calls `config.load_environment()` first, before logging is configured, so
  `DEBUG` and `LOG_DIR` from `.env` apply outside Docker too. Existing environment variables always win
  over `.env`.

### `DEBUG=True` side effects (do not enable in production)
- Runs Phase 2 once at startup. Cleanup and Phase 3, which write to Reddit, keep their normal schedule.
- Errors in comment handling, scheduled jobs and the stream loop are re-raised, which crashes the process.
- `add_comment` and `exec_crosspost` re-raise instead of logging.

### `LISTEN_ONLY=True` is **not** a dry run
It only skips scheduling Phase 3. The 3 auxiliary bots **still post public replies** from Phase 1, and the
cleanup job **still deletes** submissions. Use `DRY_RUN=True` for a fully read-only run.

---

## 5. Development & Operational Guidelines

### Setup & Execution via Docker Compose (Recommended)
1. **Clone & Configure Environment:**
   ```bash
   cd /opt
   git clone https://github.com/Toldry/RedditAutoCrosspostBot.git
   cd RedditAutoCrosspostBot
   cp .env.example .env
   # Edit .env with your Reddit passwords and API secrets
   ```
2. **Start Database & Bot:**
   ```bash
   docker compose up -d --build
   ```
3. **View Live Logs:**
   ```bash
   docker compose logs -f bot
   ```
4. **Stop Services:**
   ```bash
   docker compose down
   ```
5. **Inspect the database** (Postgres is published on `127.0.0.1:5432` only; tunnel over SSH from elsewhere):
   ```bash
   docker compose exec db psql -U racb_user -d racb -c "SELECT * FROM v_scraped_comments ORDER BY scraped_time DESC LIMIT 20;"
   ```

### Setup & Local Execution (Without Docker)
1. **Install Dependencies** (needs [uv](https://docs.astral.sh/uv/getting-started/installation/)):
   ```bash
   uv sync   # creates .venv with Python 3.11 and installs uv.lock, dev group included
   ```
   Prefix commands with `uv run` (e.g. `uv run pytest`), or activate `.venv` first.
2. **Database Setup:**
   Ensure a local PostgreSQL instance is running and set `DATABASE_URL` in `.env`.
   When [`src/racb/core/db.py`](./src/racb/core/db.py) initializes, it automatically runs [`src/racb/sql/instantiate_db.sql`](./src/racb/sql/instantiate_db.sql) to ensure tables, views, and functions exist.
   The connection is **lazy**: it opens on first use (a Phase 1 match or a scheduled phase), not at startup.
3. **Run the Bot:**
   ```bash
   uv run python -m racb
   ```
4. **Run Only Phase 2 (Manual Filter Pass):**
   ```bash
   python -m racb --only-phase2
   ```
5. **Process a Single Entry (Production Testing):**
   ```bash
   uv run python -m racb --process-one                    # the oldest row in scraped_comments
   DRY_RUN=True uv run python -m racb --process-one       # same checks, but only logs the crosspost
   ```
   Runs Phase 3 (`phase3.process_oldest_entry`) on the row with the earliest `scraped_time` and exits,
   without the stream or the schedule. The row is processed **even if it is younger than
   `PHASE3_WAITING_PERIOD`**, but it still has to pass every Phase 2 filter (score threshold included). As in
   the scheduled run, the row is deleted afterwards whether or not it was crossposted, unless `DRY_RUN` is on,
   so repeated runs walk through the table oldest first. Exit code 1 means the table is empty.
   Under Docker Compose, inside the running bot container: `docker compose exec bot python -m racb --process-one`
   (add `-e DRY_RUN=True` after `exec` for a dry run).
   Every invocation except `--help`, including `--only-phase2` and `--process-one`, first runs `authenticate_all_bots()`. That
   needs network access and valid credentials for **all 4** accounts, and exits with code 1 otherwise.

### Testing
```bash
# Unit tests only (fast, fully mocked, no network or DB). pytest.ini makes this the default.
uv run pytest

# Live integration tests: post REAL content to Reddit as the bot accounts.
uv run pytest -m integration
```
(Without `uv run`, activate `.venv` first.)

- The integration tests need valid credentials for all 4 bots. They use the private test subreddits
  `r/racb_test_1` (source) and `r/racb_test_2` (target), both in `SUB_BLACKLIST` so production ignores them.
- They delete what they create. Set `RACB_KEEP_TEST_ARTIFACTS=true` to keep posts and comments for
  debugging (this sets `DISABLE_CLEANUP` in the test file). Remove leftovers with:
  ```bash
  python scripts/cleanup_test_history.py --dry-run   # preview
  python scripts/cleanup_test_history.py --limit 0   # delete all matching items
  ```
  The script treats an item as test litter if its subreddit starts with `racb_test_` or `racb_fake_`, or if
  its title or body contains `[test`, `racb_test_` or `racb_fake_`.
- The integration tests call the reply and crosspost helpers **directly**. They do not exercise detection
  (`get_posts_with_same_content`, `handle_incoming_comment`) against live Reddit, apart from the image-hash test.
- Unit tests mock PRAW objects with `tests/conftest.py`. Patch at the import site actually used, e.g.
  `racb.phases.phase1.get_posts_with_same_content` or `racb.core.db.add_comment`.

### Logging
- Format: `YYYY-MM-DD HH:MM:SS [LEVEL   ] module.name: message`, sent to stderr and to
  `$LOG_DIR/app.log` (rotating, 5 x 5 MB).
- The `prawcore`, `urllib3.connectionpool` and `schedule` loggers are **disabled entirely**, which also hides prawcore's rate-limit messages.
- `[DRY RUN]` lines show writes that `DRY_RUN` suppressed. `Skipping comment ... after Reddit error` lines
  show per-comment failures (e.g. a bot banned from a subreddit) that no longer interrupt the stream.
- A heartbeat line (`[Heartbeat] Last ~10m: scanned N comments ...`) is logged every 10 minutes by `StreamMetrics`. It is the quickest health and throughput signal.

### Release & Semantic Versioning Workflow
We follow [Semantic Versioning (SemVer)](https://semver.org/) and [Keep a Changelog](https://keepachangelog.com/). **Releases are fully automatic; nobody bumps the version by hand.**

After every push to `master`, once the unit tests pass, the `release` job in
[`.github/workflows/tests.yml`](./.github/workflows/tests.yml):
1. runs [`scripts/release_level.py`](./scripts/release_level.py), which prints `none` if CHANGELOG
   `## [Unreleased]` is empty, otherwise the bump implied by the commits since the last `v*` tag
   (merge commits and earlier release commits are ignored):
   - `major`: a breaking Conventional Commit, `type!: ...` / `type(scope)!: ...` or a `BREAKING CHANGE:` footer
   - `minor`: a `feat` commit
   - `patch`: anything else, including commits that are not Conventional Commits
2. runs `uv run bump-my-version bump <level>`, which updates [`src/racb/version.py`](./src/racb/version.py)
   and [`CHANGELOG.md`](./CHANGELOG.md), makes a `chore(release): bump version X -> Y` commit as
   `github-actions[bot]`, and an annotated `vX.Y.Z` tag;
3. pushes both to `master` (`git push --follow-tags`).

Mechanics worth knowing:
- Write Conventional Commit subjects (`feat:`, `fix:`, `docs:`, ...) on feature branches; they decide
  the bump. With squash merges, the PR title becomes the commit, so it must follow the convention too.
- The job checks out the latest `master` and runs in a `release` concurrency group, so releases queue.
  If someone merges while a release is running, its push is rejected and the job fails; the run for that
  later merge then releases everything. Pushes made with `GITHUB_TOKEN` do not trigger workflows, so the
  release commit cannot start another release (and its tests do not re-run).
- It needs `master` to accept pushes from GitHub Actions. If branch protection is ever enabled, allow
  `github-actions[bot]` to bypass it, or the job fails.
- To preview locally: `uv run bump-my-version bump patch --dry-run --verbose` (clean working tree required).
- The CHANGELOG edit is a plain string replacement: `## [Unreleased]` becomes `## [Unreleased]` followed
  by `## [X.Y.Z] - YYYY-MM-DD`. Whatever sat under Unreleased moves into the new version, so the
  `## [Unreleased]` heading must exist exactly once.
- The version also appears in every PRAW User-Agent (`linux:<bot>:v<version> (by /u/orqa)`) and in the
  image downloader's User-Agent (`RACB/<version> (+github URL)`).

### Critical Implementation Details & Gotchas
- **PRAW Native Rate Limiting:**
  [`src/racb/core/reddit.py`](./src/racb/core/reddit.py) configures `ratelimit_seconds=300` on all `praw.Reddit` instances. PRAW handles Reddit rate limit sleeps and retries natively.
  Precisely: request-rate limiting (the per-minute OAuth quota) is handled by prawcore. `ratelimit_seconds`
  only controls how long PRAW will **sleep** on a `RATELIMIT` API error ("you are doing that too much")
  before retrying. Up to 300 s of that sleep happens on the single main thread, so the stream stalls meanwhile.
- **PostgreSQL Stored Procedures:**
  Database interactions are routed through PL/pgSQL functions (`insert_scraped_comment`, `get_comments_older_than`, `get_unchecked_comments_older_than`, `get_oldest_scraped_comment`, `delete_scraped_comment`, `set_comment_checked`). Any schema or query modifications must be updated in [`src/racb/sql/instantiate_db.sql`](./src/racb/sql/instantiate_db.sql) and reflected in [`src/racb/core/db.py`](./src/racb/core/db.py).
- **Batch Comment Processing in Phase 2:**
  To adhere to Reddit API rate limits (100 QPM), Phase 2 fetches up to 100 comments per request using `reddit.info(fullnames=...)`. Do not revert to individual per-comment HTTP requests.
- **Dependencies are locked** in `uv.lock`, including transitive ones, so a Docker rebuild cannot pull an
  untested version. The Dockerfile and CI use `uv sync --locked`, which fails instead of silently
  re-resolving. Change dependencies only with `uv add` / `uv remove` / `uv lock --upgrade-package <name>`,
  commit `pyproject.toml` and `uv.lock` together, and run the unit tests. Dependabot version updates are
  deliberately **paused** (`open-pull-requests-limit: 0` in `.github/dependabot.yml`); upgrades are manual.
- **Do not reintroduce `requirements.txt`.** If a tool needs one, generate it with
  `uv export --no-dev --no-hashes -o requirements.txt` rather than maintaining it by hand.
- **Image duplicate matching** (`duplicate_detector.fingerprints_match`) needs both a whole-image dhash
  distance ≤ `DEFAULT_HASH_THRESHOLD` (5) **and** every cell of a 4×4 grid within
  `DEFAULT_REGION_HASH_THRESHOLD` (10). The grid check stops different memes made from the same template
  from matching, but it also rejects rotated reposts. Downloads larger than `MAX_IMAGE_BYTES` (20 MB) are skipped.

---

## 6. Database Schema

Defined in [`src/racb/sql/instantiate_db.sql`](./src/racb/sql/instantiate_db.sql):

| Object | Definition |
| :--- | :--- |
| `scraped_comments` | `id SERIAL PK`, `permalink VARCHAR(600) NOT NULL`, `scraped_time TIMESTAMP` (no time zone), `phase2_checked BOOLEAN NOT NULL DEFAULT FALSE`, `target_subreddit VARCHAR(32)` (NULL for pre-v2.3 rows). Index on `scraped_time`. **No unique constraint** on `permalink`. |
| `v_scraped_comments` | `scraped_comments.*` plus `time_since_scraped = LOCALTIMESTAMP - scraped_time`. For humans; the functions query the table directly. |
| `insert_scraped_comment(varchar, varchar)` | `(permalink, target_subreddit)`; inserts with `scraped_time = LOCALTIMESTAMP`. |
| `get_comments_older_than(interval)` | `(id, permalink, scraped_time, target_subreddit)` where `scraped_time < LOCALTIMESTAMP - t`, regardless of `phase2_checked` (Phase 3). |
| `get_unchecked_comments_older_than(interval)` | The same with `phase2_checked = false` (Phase 2). |
| `get_oldest_scraped_comment()` | The same columns for the row with the earliest `scraped_time`, regardless of age (`--process-one`). |
| `set_comment_checked(int)` / `delete_scraped_comment(int)` | Mark checked / delete by `id`. |

Behavior to be aware of:
- The script runs on **every new connection** (`db.get_connection()` reconnects whenever the cached
  connection is closed or `db.reset_connection()` was called), so it must stay idempotent. In particular,
  **never ADD then DROP a column**: PostgreSQL never reuses a dropped column's attribute number, and a table
  may only ever have 1600. Before v2.3 the script did exactly that on every run; it would have failed for
  good after about 1595 connections.
- Existing deployments keep the attribute numbers already used. To check the headroom:
  ```sql
  SELECT max(attnum) FROM pg_attribute WHERE attrelid = 'scraped_comments'::regclass;
  ```
  If it is ever close to 1600, rebuild the table (`CREATE TABLE ... AS SELECT`, recreate the index, swap names).
- To change a function's signature or return type, `DROP FUNCTION IF EXISTS` the old signature first.
  `CREATE OR REPLACE` cannot change either.
- The age predicates compare the bare `scraped_time` column with a constant, so the index is used.
- Connections use **autocommit**, so every stored-procedure call is its own transaction. No session sits
  *idle in transaction*, and `LOCALTIMESTAMP` is the time of the call.

---

## 7. Main Loop, Scheduling & Error Handling

| Job | Interval | Function |
| :--- | :--- | :--- |
| Cleanup of negative-score submissions | 7 min | `cleanup.delete_unwanted_submissions` |
| Heartbeat log | 10 min | `metrics.log_heartbeat` |
| Phase 2 | 20 min | `phase2.filter_comments_from_db` |
| Phase 3 (skipped if `LISTEN_ONLY`) | 6 min | `phase3.process_comment_entries` |
| Inbox feedback (all 4 bots) | 10 min | `inbox.process_unread_inboxes` |

All jobs except the heartbeat are wrapped in `main.run_job_safely()`. It logs a failing job's exception
instead of propagating it. This matters because `schedule` computes `next_run` only after a job returns
normally, so a raising job would re-run on every loop iteration. It also calls `db.reset_connection()` on
DB connection errors.

`main_loop()` drains one batch from the comment stream, then calls `schedule.run_pending()`. The stream uses
`pause_after=-1`. In PRAW this makes the generator yield `None` after **every** request and **never sleep**,
so the loop polls continuously, throttled only by prawcore's rate limiter.

Errors are handled in three layers:

```mermaid
flowchart TD
    C["phase1.handle_incoming_comment(comment)"] -->|raises| S{"handle_comment_safely()"}
    S -->|"network error\n(ServerError, RequestException,\nTooManyRequests, requests errors)"| H
    S -->|"DB connection error"| R["reset DB connection,\nskip comment"]
    S -->|"other Reddit / PRAW error\n(e.g. 403 bot banned)"| K["log INFO, skip comment"]
    S -->|"anything else"| L["log with traceback, skip comment"]
    F["stream fetch"] -->|raises| H{"handle_exception()"}
    H -->|"TooManyRequests"| W60["sleep 60 s"]
    H -->|"network error or Forbidden"| W30["sleep 30 s"]
    H -->|"DB connection error"| WDB["reset DB, sleep 30 s"]
    H -->|"graceful RedditAPIException"| N["no sleep"]
    W60 & W30 & WDB & N --> RS["re-create stream\n(skip_existing=True)"]
    H -->|"anything else"| X["crash; Docker restarts"]
```

- Matching uses `isinstance`, so subclasses are covered.
- With `DEBUG=True`, every layer re-raises.
- A stream reset uses `skip_existing=True`, so comments posted during the outage are never seen.

Rule for new code (reinforcing rule 3 in section 10): catch per-item errors **inside** the per-comment or
per-row handler. Only network-level failures should reach `handle_exception()`.

---

## 8. Repository & Deployment Gotchas

- **`.gitignore` ignores every dotfile** (`.*`) except `.env.example`, `.gitignore`, `.bumpversion.toml`,
  `.python-version`, `.dockerignore` and `.github/`. Any other new dotfile (`.pre-commit-config.yaml`, `.claude/`, ...)
  is silently ignored until you add a `!` exception. Check with `git check-ignore -v <path>`.
- **`.dockerignore` excludes only virtualenvs and caches.** `COPY . .` still bakes `.env`, `.git/` and `tests/` into the image. Baking
  `.env` in is the owner's choice. At runtime, Compose's `env_file: .env` already supplies the variables,
  and `load_dotenv()` never overrides existing ones, so the baked copy is only a fallback. Never push
  this image to a registry, because it contains all 4 account passwords and secrets.
- The container runs as root and has no `HEALTHCHECK` for the bot service (only `db` has one).
- `deploy/cloud-init.yaml` adds **every** account under `/home` to the `docker` group (root-equivalent)
  and makes `/opt` group-writable. This is deliberate, for the GCP web-SSH workflow on a single-admin VM;
  do not reuse it on a shared machine. It installs Docker only; cloning and starting the bot is manual.
- Python versions: `requires-python = ">=3.11"`. The Docker image, CI and `.python-version` use 3.11, and the
  unit tests also pass on 3.13. 3.10 is not supported.
- Mermaid diagrams: GitHub's renderer rejects unquoted labels that contain parentheses, e.g.
  `-->|reddit.info()|`. Quote them: `-->|"reddit.info()"|`.

---

## 9. Known Limitations & Open Questions

| Topic | Status |
| :--- | :--- |
| Duplicate crossposts | Before every crosspost, Phase 3 re-runs `get_posts_with_same_content()`. That is a Reddit search for the submission URL in the target (subject to search-index lag of minutes) plus a scan of the target's 50 newest posts (a live listing, so it catches a crosspost made seconds earlier, but only for **image** posts). The remaining gap is non-image posts, when two rows for the same submission and target are handled in one Phase 3 run, or after a crash between `crosspost()` and the row delete. The owner considers this acceptable, and Reddit may also reject duplicates server-side (unverified). An `ALREADY_SUB`-style rejection is not in `familiar_error_types`, so it would be logged as unexpected. If the reply fails after a successful crosspost, the crosspost is reported as failed. |
| Negative-score cleanup | Deletes `AutoCrosspostBot` submissions with `score < 0`. The owner reports that submission scores can go negative; this was not independently confirmed. |
| Single-threaded Phase 1 | The duplicate search, up to 50 image downloads (6 s timeout each) and the subreddit suggestions all run on the stream loop. A slow call delays reading `r/all`. The heartbeat's "scanned" count is the throughput signal. |
| Rotated reposts | Not detected as duplicates (see the image-matching note in section 5). |
| Pre-v2.3 rows | Have `target_subreddit = NULL`, so the bait-and-switch check is skipped for them until they age out (at most one Phase 3 waiting period after upgrading). |
| Already-consumed column slots | Deployments that ran the pre-v2.3 schema script keep the attribute numbers it used up (section 6). |
| Inbox feedback replies | Intentionally disabled by the author; feedback is only logged. |

---

## 10. Coding & Contribution Rules for Agents

1. **Preserve Comments & Docstrings:** Do not remove existing comments, docstrings, or developer notes during edits.
2. **Follow Existing Style:** Use clean, standard Python style matching the codebase (PEP 8 conventions, explicit error handling with `prawcore.exceptions` and `praw.exceptions.RedditAPIException`).
3. **Graceful Reddit Exception Handling:** Any newly introduced Reddit API calls must handle potential exceptions (`Forbidden`, `ServerError`, `RedditAPIException`, sub bans, locked threads, deleted comments) without crashing the main loop.
4. **Localization Awareness:** When adding or modifying bot comments, update the `.po` catalog of every locale under [`src/racb/locales/`](./src/racb/locales/) (message ids are symbolic keys; `en` holds the source text, and an empty `msgstr` falls back to English). Then run `uv run pybabel compile -d src/racb/locales` and commit the regenerated `.mo` files; a unit test fails if they are stale. Counts use plural forms (`ngettext`, plural id `<KEY>_PLURAL`). To add a locale, create its `.po` (e.g. `uv run pybabel init -i src/racb/locales/en/LC_MESSAGES/messages.po -d src/racb/locales -l <locale>`) and list its subreddits in [`subreddits.toml`](./src/racb/locales/subreddits.toml).
5. **Mandatory Changelog Updates, No Manual Version Bumps:** Every code, configuration, or infrastructure change MUST add an entry to [`CHANGELOG.md`](./CHANGELOG.md) under `## [Unreleased]` in the same branch. **Never bump the version or create a tag yourself**, on a feature branch or on `master`: the CI `release` job does it after merge (section 5). Parallel branches would otherwise claim the same version number. Use Conventional Commit subjects, since they choose the bump (`feat` minor, `!` major, anything else patch).
6. **Never run live integration tests casually:** plain `pytest` runs only unit tests. `pytest -m integration` posts publicly as the real bot accounts.
7. **Keep section 9 current:** when you fix a known defect, delete its row; when you find a new one, add it.
8. **Read configuration through `src/racb/config.py`:** do not add new `os.environ` reads or boolean/duration parsers elsewhere.
9. **Every new Reddit write must respect `DRY_RUN`** (see `phase1.reply_as_bot` for the pattern).
