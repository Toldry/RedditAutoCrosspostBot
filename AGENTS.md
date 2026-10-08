# AGENTS.md

Welcome to the **RedditAutoCrosspostBot** repository. This document is a comprehensive guide for AI agents and human contributors working on, debugging, extending, or maintaining this codebase.

> [!IMPORTANT]
> Section 9 lists **known defects** that were verified against the code, as of v2.2.4. Read it before you
> assume a feature works the way its name or docstring suggests. If you fix one, remove it from that list.

---

## 1. Project Overview & Multi-Bot Architecture

This repository hosts a suite of 4 automated Reddit bots powered by [PRAW (Python Reddit API Wrapper)](https://praw.readthedocs.io/), backed by a PostgreSQL database and containerized with Docker / Docker Compose for deployment on free cloud VPS instances (e.g. Google Cloud Always Free or Oracle Cloud Always Free) or local hardware.

All 4 bots run in **one Python process, on a single thread**. A single loop alternates between the
`r/all` comment stream, the inbox stream and the `schedule` jobs, so any slow call blocks everything else
(see section 7).

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
    G -->|Batch Fetch via reddit.info()| H["Validate Availability & Score"]
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
    TL -->|Yes| DB[("insert_scraped_comment(permalink)")]
```

Note: a private target (`SUBREDDIT_IS_PRIVATE`) is **still inserted** in Phase 1. Phase 2 deletes it later.

### Phase Details
- **Phase 1 ([`src/racb/phases/phase1.py`](./src/racb/phases/phase1.py)):**
  - Continuous streaming from `reddit.subreddit('all').stream.comments(skip_existing=True, pause_after=-1)`.
  - Filters: Discards moderator comments, submissions with meta-phrases (`sub`, `subreddit`), and blacklisted subreddits ([`src/racb/constants.py`](./src/racb/constants.py)). Non-top-level comments (`comment.parent_id != comment.link_id`) are discarded only **after** auxiliary-bot dispatch (see the warning in section 1).
  - Qualified suggestions are stored via stored procedure `insert_scraped_comment(permalink)`.
  - Only the **permalink** is stored. The target subreddit is **re-parsed from the live comment body** in Phases 2 and 3.
  - All of this work, including the duplicate search and image downloads, runs synchronously on the stream loop.
- **Phase 2 ([`src/racb/phases/phase2.py`](./src/racb/phases/phase2.py)):**
  - Runs periodically via `schedule` (or manually via `python -m racb --only-phase2`).
  - Fetches unchecked rows older than `PHASE2_WAITING_PERIOD` using `get_unchecked_comments_older_than(interval)`.
  - Executes **batch fetching via `reddit.info(fullnames=chunk)`** in chunks of 100 to minimize API requests by 99%:
    - Comment availability (not deleted / removed).
    - Score >= `COMMENT_SCORE_THRESHOLD`.
    - Regex pattern still holds (user didn't edit comment).
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
  - Executes `submission.crosspost()` and posts an explanatory reply using localized text templates from [`src/racb/i18n.py`](./src/racb/i18n.py).
  - Handles Reddit API crosspost exceptions gracefully via modern `e.items` inspection (e.g., `NO_CROSSPOSTS`, `SUBREDDIT_NOTALLOWED`, `NO_IMAGES`, `SUBMIT_VALIDATION_*`).
  - Deletes the record from PostgreSQL. This happens **after** the crosspost, and Phase 3 keeps no
    "already crossposted" state. A crash between the crosspost and the delete re-crossposts on the next run.
- **Maintenance & Feedback:**
  - [`src/racb/phases/cleanup.py`](./src/racb/phases/cleanup.py): Periodically fetches the latest 40 submissions made by `AutoCrosspostBot` and deletes any with score < 0.
    Only submissions are covered. Comments made by any of the 4 bots are never cleaned up.
  - [`src/racb/phases/inbox.py`](./src/racb/phases/inbox.py): Monitors inbox stream for feedback (`good bot` / `bad bot`).
    Both `respond_to_positive_sentiment` and `respond_to_negative_sentiment` are currently **no-ops**
    (bodies commented out). Matching messages are only marked read. Only `AutoCrosspostBot`'s inbox is
    streamed; the 3 auxiliary bots' inboxes are never read.

---

## 3. Directory & File Reference

| File | Purpose |
| :--- | :--- |
| [`src/racb/main.py`](./src/racb/main.py) | Application entry point, logging setup, stream loop, exception recovery, and `schedule` runner. |
| [`src/racb/__main__.py`](./src/racb/__main__.py) | Module executable entrypoint for `python -m racb`. |
| [`src/racb/__init__.py`](./src/racb/__init__.py) | Package marker; re-exports `__version__`. |
| [`src/racb/core/reddit.py`](./src/racb/core/reddit.py) | Singleton factory for PRAW instances with native `ratelimit_seconds=300` and updated user-agent formats. Also `authenticate_all_bots()` (startup check) and the bot-name constants. |
| [`src/racb/core/db.py`](./src/racb/core/db.py) | PostgreSQL connector (`psycopg2-binary`) with connection retry logic and stored procedure invocation. |
| [`src/racb/core/sub_search.py`](./src/racb/core/sub_search.py) | Live Reddit community search using `reddit.subreddits.search()` for typo suggestions. |
| [`src/racb/core/duplicate_detector.py`](./src/racb/core/duplicate_detector.py) | Self-hosted perceptual image hashing (`imagehash` / `dhash`) for visual duplicate detection in target subreddits. |
| [`src/racb/phases/phase1.py`](./src/racb/phases/phase1.py) | Phase 1 comment matching, blacklisting, auxiliary bot dispatching, DB insertion. |
| [`src/racb/phases/phase2.py`](./src/racb/phases/phase2.py) | Phase 2 batch comment filtering via `reddit.info()` and DB cleanup. |
| [`src/racb/phases/phase3.py`](./src/racb/phases/phase3.py) | Phase 3 final validation, crosspost execution, comment reply, modern API exception handling. |
| [`src/racb/phases/inbox.py`](./src/racb/phases/inbox.py) | Evaluates feedback sent to the bot's inbox. |
| [`src/racb/phases/cleanup.py`](./src/racb/phases/cleanup.py) | Auto-cleanup job deleting downvoted posts made by the bot. |
| [`src/racb/sql/instantiate_db.sql`](./src/racb/sql/instantiate_db.sql) | DDL schema, indexes, view `v_scraped_comments`, and PL/pgSQL stored procedures. Executed on **every new DB connection**, not only at startup (see section 6). |
| [`src/racb/constants.py`](./src/racb/constants.py) | Hardcoded blacklist of subreddits to ignore (`SUB_BLACKLIST`). |
| [`src/racb/i18n.py`](./src/racb/i18n.py) | Custom localization mapping (`en`, `es`, `de`, `fr`, `he`, `totallynotrobots`) mapped by subreddit names. |
| [`src/racb/version.py`](./src/racb/version.py) | Application version definition (`__version__`). |
| [`scripts/cleanup_test_history.py`](./scripts/cleanup_test_history.py) | Deletes integration-test posts and comments from all 4 bot accounts. Supports `--dry-run` and `--limit N` (`0` means all). |
| [`tests/conftest.py`](./tests/conftest.py) | `MockComment` / `MockSubmission` / `MockSubreddit` / `MockRedditor` plus the `make_mock_comment` and `make_mock_submission` factory fixtures. |
| [`tests/unit/`](./tests/unit/) | 61 mocked unit tests (`@pytest.mark.unit`), one file per module. |
| [`tests/integration/test_reddit_integration.py`](./tests/integration/test_reddit_integration.py) | 10 **live** Reddit tests (`@pytest.mark.integration`) that submit real posts and comments. |
| [`tests/assets/test_sample.png`](./tests/assets/test_sample.png) | Base image for perceptual-hash variation tests. |
| [`pytest.ini`](./pytest.ini) | Test discovery: `testpaths = tests`, `pythonpath = src .`, markers `unit` and `integration`. |
| [`examples/example_reddit_comment.py`](./examples/example_reddit_comment.py) | Not executable code: a commented-out attribute dump of a PRAW `Comment` and `Submission`, kept for reference. |
| [`deploy/cloud-init.yaml`](./deploy/cloud-init.yaml) | Universal cloud-init configuration for provisioning cloud VMs (GCP, Oracle, AWS) with swap and Docker. It does **not** clone or start the bot. |
| [`deploy/instance-flags.yaml`](./deploy/instance-flags.yaml) | Declarative Google Cloud CLI flags file for provisioning the Always Free e2-micro instance. |
| [`Dockerfile`](./Dockerfile) | Lightweight Python 3.11 container image definition. |
| [`docker-compose.yml`](./docker-compose.yml) | Multi-container stack definition (PostgreSQL 15 + Python bot worker). |
| [`.bumpversion.toml`](./.bumpversion.toml) | Configuration for `bump-my-version` automation. |
| [`.gitignore`](./.gitignore) | Ignores **all dotfiles** (`.*`) except an explicit allowlist. See section 8. |
| [`CHANGELOG.md`](./CHANGELOG.md) | Release history following Keep a Changelog. |
| [`.env.example`](./.env.example) | Environment variable template. |
| [`requirements.txt`](./requirements.txt) | Modernized runtime Python dependencies (`praw>=7.7.1`, `psycopg2-binary`, etc.). |
| [`requirements-dev.txt`](./requirements-dev.txt) | Development & release dependencies (`bump-my-version`, `pytest`, `pytest-mock`). |

There is **no** `pyproject.toml` / `setup.py`: the package is not installable and relies on
`PYTHONPATH=src` (the Dockerfile sets `PYTHONPATH=/app/src`). There is also **no CI**: no `.github/`
directory exists, and nothing runs the tests automatically.

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
| `DB_SSLMODE` | String | auto | libpq `sslmode`. If unset: `disable` when the URL contains `@db:`, `@localhost:`, `@127.0.0.1:` **or `DEBUG` is true**, otherwise `prefer`. Compose sets `disable`. |
| `DEBUG` | Boolean (`True`/`False`) | `False` | Debug mode toggle. Sets logging to `DEBUG`, disables catch-all exception swallow. See side effects below. |
| `LISTEN_ONLY` | Boolean (`True`/`False`) | `False` | When `True`, disables Phase 3 crossposting while retaining stream listening and Phase 2 filtering. |
| `COMMENT_SCORE_THRESHOLD` | Integer | `1` (`.env.example`: `30`) | Minimum upvote score a comment must have to survive Phase 2 and be crossposted in Phase 3. |
| `PHASE2_WAITING_PERIOD` | Time string | `2 hours` | Time duration before Phase 2 checks a comment (parsed via `pytimeparse`, e.g. `2 hours` or `1 day`). |
| `PHASE3_WAITING_PERIOD` | Time string | `3 months` | Time duration before Phase 3 executes the crosspost (e.g. `3 months` or `90 days`). The raw string is also inserted verbatim into the public crosspost reply. |
| `LOG_DIR` | Path | `logs` | Directory for `app.log` (relative to the CWD; `/app/logs` in Docker, mounted on volume `racb_logs`). |
| `PASSWORD` | String | | Account password for `AutoCrosspostBot`. |
| `APP_CLIENT_SECRET` | String | | App client secret for `AutoCrosspostBot`. |
| `PASSWORD__SUB_DOESNT_EXIST` | String | | Account password for `sub_doesnt_exist_bot`. |
| `APP_CLIENT_SECRET__SUB_DOESNT_EXIST` | String | | App client secret for `sub_doesnt_exist_bot`. |
| `PASSWORD__SAME_SUBREDDIT` | String | | Account password for `same_subreddit_bot`. |
| `APP_CLIENT_SECRET__SAME_SUBREDDIT` | String | | App client secret for `same_subreddit_bot`. |
| `PASSWORD__SAME_POST` | String | | Account password for `same_post_bot`. |
| `APP_CLIENT_SECRET__SAME_POST` | String | | App client secret for `same_post_bot`. |

### Parsing caveats
- **Booleans** are parsed by three different helpers. `main.env_bool` and `db.env_bool` accept
  `true/1/yes/y/t` (case-insensitive). `phase3.exec_crosspost` accepts only `true/1`.
- **Time strings:** `pytimeparse` does **not** understand months. `timeparse('3 months')` returns `None`.
  Phase 3 then silently falls back to 90 days, so the shipped default works only by accident: `6 months`
  would also become 90 days. Use days or weeks (`180 days`, `26 weeks`).
- **`PHASE2_WAITING_PERIOD=0 seconds`** silently becomes 2 hours, because Phase 2 uses
  `timeparse(...) or 7200` and `0` is falsy. Phase 3 checks `is None` correctly.
- **`.env` loading order:** `dotenv.load_dotenv()` runs lazily, inside the first `get_reddit_instance()`
  call. `configure_logging()` runs **before** that, so outside Docker `DEBUG` and `LOG_DIR` from `.env`
  do not affect logging. Export them in the shell instead. Docker uses `env_file`, so it is unaffected.

### `DEBUG=True` side effects (do not enable in production)
- Calls `schedule.run_all()` at startup, which runs cleanup, Phase 2 **and Phase 3 (real crossposts)** immediately.
- Any exception in the main loop crashes the process instead of being retried.
- `add_comment` and `exec_crosspost` re-raise instead of logging.
- Disables TLS to the database when `DB_SSLMODE` is unset, even for remote hosts.

### `LISTEN_ONLY=True` is **not** a dry run
It only skips scheduling Phase 3. The 3 auxiliary bots **still post public replies** from Phase 1, and the
cleanup job **still deletes** submissions. There is no flag that makes the bot fully read-only.

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
1. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt # For local development and release management
   ```
2. **Database Setup:**
   Ensure a local PostgreSQL instance is running and set `DATABASE_URL` in `.env`.
   When [`src/racb/core/db.py`](./src/racb/core/db.py) initializes, it automatically runs [`src/racb/sql/instantiate_db.sql`](./src/racb/sql/instantiate_db.sql) to ensure tables, views, and functions exist.
   The connection is **lazy**: it opens on first use (a Phase 1 match or a scheduled phase), not at startup.
3. **Run the Bot:**
   ```bash
   # Add src directory to PYTHONPATH if running locally without editable install
   export PYTHONPATH=src # On Linux/macOS
   $env:PYTHONPATH="src" # On Windows PowerShell

   python -m racb
   ```
4. **Run Only Phase 2 (Manual Filter Pass):**
   ```bash
   python -m racb --only-phase2
   ```
   Every invocation, including `--only-phase2` and `--help`, first runs `authenticate_all_bots()`. That
   needs network access and valid credentials for **all 4** accounts, and exits with code 1 otherwise.

### Testing
```bash
# Unit tests only (fast, fully mocked, no network or DB). Use this by default.
pytest -m unit

# Live integration tests: post REAL content to Reddit as the bot accounts.
pytest -m integration
```

> [!CAUTION]
> A bare `pytest` collects **all 71 tests**, including the 10 live integration tests, because `pytest.ini`
> does not deselect them. Always pass `-m unit` unless you intend to post to Reddit.

- The integration tests need valid credentials for all 4 bots. They use the private test subreddits
  `r/racb_test_1` (source) and `r/racb_test_2` (target), both in `SUB_BLACKLIST` so production ignores them.
- `DISABLE_CLEANUP = True` is currently committed at the top of the integration test file, so test posts
  and comments are **left on Reddit**. Remove them with:
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
- A heartbeat line (`[Heartbeat] Last ~10m: scanned N comments ...`) is logged every 10 minutes by `StreamMetrics`. It is the quickest health and throughput signal.

### Release & Semantic Versioning Workflow
We follow [Semantic Versioning (SemVer)](https://semver.org/) and [Keep a Changelog](https://keepachangelog.com/). Version bumps are automated via [`bump-my-version`](https://github.com/callowayproject/bump-my-version).

- **Bump Patch (`v2.0.0` -> `v2.0.1`):**
  ```bash
  bump-my-version bump patch
  ```
- **Bump Minor (`v2.0.0` -> `v2.1.0`):**
  ```bash
  bump-my-version bump minor
  ```
- **Bump Major (`v2.0.0` -> `v3.0.0`):**
  ```bash
  bump-my-version bump major
  ```
- **Dry-run verification before releasing:**
  ```bash
  bump-my-version bump patch --dry-run --verbose
  ```
When run, `bump-my-version` updates [`src/racb/version.py`](./src/racb/version.py), updates [`CHANGELOG.md`](./CHANGELOG.md), generates a git commit, and tags the release with `vX.Y.Z`. Push commits and tags to GitHub with `git push --follow-tags`.

Mechanics worth knowing:
- `allow_dirty = false`: **commit your change first** (including the `## [Unreleased]` CHANGELOG entry).
  Then run the bump, which makes its own `chore(release): bump version X -> Y` commit and an annotated tag.
- The CHANGELOG edit is a plain string replacement: `## [Unreleased]` becomes `## [Unreleased]` followed
  by `## [X.Y.Z] - YYYY-MM-DD`. Whatever sat under Unreleased moves into the new version, so the
  `## [Unreleased]` heading must exist exactly once.
- The version also appears in every PRAW User-Agent (`linux:<bot>:v<version> (by /u/orqa)`). The image
  downloader in `duplicate_detector.py` hardcodes its own `RACB/2.2` string, which bumps do not update.

### Critical Implementation Details & Gotchas
- **PRAW Native Rate Limiting:**
  [`src/racb/core/reddit.py`](./src/racb/core/reddit.py) configures `ratelimit_seconds=300` on all `praw.Reddit` instances. PRAW handles Reddit rate limit sleeps and retries natively.
  Precisely: request-rate limiting (the per-minute OAuth quota) is handled by prawcore. `ratelimit_seconds`
  only controls how long PRAW will **sleep** on a `RATELIMIT` API error ("you are doing that too much")
  before retrying. Up to 300 s of that sleep happens on the single main thread, so the stream stalls meanwhile.
- **PostgreSQL Stored Procedures:**
  Database interactions are routed through PL/pgSQL functions (`insert_scraped_comment`, `get_comments_older_than`, `get_unchecked_comments_older_than`, `delete_scraped_comment`, `set_comment_checked`). Any schema or query modifications must be updated in [`src/racb/sql/instantiate_db.sql`](./src/racb/sql/instantiate_db.sql) and reflected in [`src/racb/core/db.py`](./src/racb/core/db.py).
- **Batch Comment Processing in Phase 2:**
  To adhere to Reddit API rate limits (100 QPM), Phase 2 fetches up to 100 comments per request using `reddit.info(fullnames=...)`. Do not revert to individual per-comment HTTP requests.
- **Dependencies are unpinned** (`>=` only, no lockfile). A fresh install currently resolves **PRAW 8.x
  and prawcore 4.x**, two major versions newer than the `praw>=7.7.1` floor suggests. Every Docker rebuild
  can pull a new major version.

---

## 6. Database Schema

Defined in [`src/racb/sql/instantiate_db.sql`](./src/racb/sql/instantiate_db.sql):

| Object | Definition |
| :--- | :--- |
| `scraped_comments` | `id SERIAL PK`, `permalink VARCHAR(600) NOT NULL`, `scraped_time TIMESTAMP` (no time zone), `phase2_checked BOOLEAN NOT NULL DEFAULT FALSE`. Index on `scraped_time`. **No unique constraint** on `permalink`, and the target subreddit is not stored. |
| `v_scraped_comments` | `scraped_comments.*` plus `time_since_scraped = CURRENT_TIMESTAMP - scraped_time`. |
| `insert_scraped_comment(varchar)` | Inserts with `scraped_time = CURRENT_TIMESTAMP`. |
| `get_comments_older_than(interval)` | Rows where `time_since_scraped > t`, regardless of `phase2_checked` (Phase 3). |
| `get_unchecked_comments_older_than(interval)` | The same with `phase2_checked = false` (Phase 2). |
| `set_comment_checked(int)` / `delete_scraped_comment(int)` | Mark checked / delete by `id`. |

Behavior to be aware of:
- The script runs on **every new connection** (`db.get_connection()` reconnects whenever the cached
  connection is closed). Each run re-adds the legacy column `score_checked` and then drops it again,
  which permanently consumes one PostgreSQL attribute number. See defect D1 in section 9.
- The age predicate (`CURRENT_TIMESTAMP - scraped_time > t`) cannot use the `scraped_time` index.
- psycopg2 runs in its default non-autocommit mode, and the read helpers never commit. The single shared
  connection therefore stays *idle in transaction* after a read until the next write commits. Because
  `CURRENT_TIMESTAMP` is the **transaction start** time, an insert made inside such a lingering transaction
  is stamped with the time of the earlier read, not the time of the insert.

---

## 7. Main Loop, Scheduling & Error Handling

| Job | Interval | Function |
| :--- | :--- | :--- |
| Cleanup of negative-score submissions | 7 min | `cleanup.delete_unwanted_submissions` |
| Heartbeat log | 10 min | `metrics.log_heartbeat` |
| Phase 2 | 20 min | `phase2.filter_comments_from_db` |
| Phase 3 (skipped if `LISTEN_ONLY`) | 6 min | `phase3.process_comment_entries` |

`main_loop()` drains one batch from the comment stream, then one from the inbox stream, then calls
`schedule.run_pending()`. Both streams use `pause_after=-1`. In PRAW this makes the generator yield `None`
after **every** request and **never sleep**, so the loop polls continuously. Only prawcore's rate limiter
throttles it, and the inbox polling consumes part of the same request budget as the `r/all` stream.

Every exception from Phase 1, the inbox or a scheduled job propagates out of `main_loop()` into `handle_exception()`:
- Retried after a 30 s sleep, followed by **re-creating both streams** with `skip_existing=True` (comments
  posted meanwhile are never seen): `ServerError`, `Forbidden`, `requests.ConnectTimeout`, and prawcore
  `RequestException` wrapping a urllib3 `MaxRetryError`/`ReadTimeoutError`. Types are matched with
  `type(e) in (...)`, i.e. **exact type, not subclasses**.
- Logged and the streams re-created without a sleep: `RedditAPIException` containing `DELETED_COMMENT`, `THREAD_LOCKED`, `SOMETHING_IS_BROKEN` or `RATELIMIT`.
- Anything else, e.g. `TooManyRequests`, `NotFound` or `psycopg2.OperationalError`, **crashes the process**.
  Docker's `restart: unless-stopped` then restarts it, which re-authenticates all 4 bots and opens a new DB
  connection (see section 6).
- With `schedule`, a job that raises is **not rescheduled**: `Job.run()` sets `next_run` only after the job
  succeeds. A job that keeps failing is therefore retried on every loop iteration.

Rule for new code (reinforcing rule 3 in section 10): catch per-item errors **inside** the per-comment or
per-row handler. Letting them reach `handle_exception()` costs a stream reset, and possibly a 30 s stall or a crash.

---

## 8. Repository & Deployment Gotchas

- **`.gitignore` ignores every dotfile** (`.*`) except `.env.example`, `.gitignore` and `.bumpversion.toml`.
  Any new dotfile is silently ignored until you add a `!` exception: `.github/` (CI, Dependabot),
  `.dockerignore`, `.pre-commit-config.yaml`, `.claude/`. Check with `git check-ignore -v <path>`.
- **There is no `.dockerignore`.** `COPY . .` therefore bakes `.env` (all 4 account passwords and
  secrets), `.git/` and `tests/` into the image. Never push this image to a registry as-is.
- The container runs as root and has no `HEALTHCHECK` for the bot service (only `db` has one).
- `deploy/cloud-init.yaml` adds **every** account under `/home` to the `docker` group (root-equivalent)
  and makes `/opt` group-writable. It installs Docker only; cloning and starting the bot is manual.
- Python versions: the Docker image uses 3.11, README says "3.10+", and the unit tests also pass on 3.13.

---

## 9. Known Defects (verified against v2.2.4)

| ID | Severity | Defect |
| :--- | :--- | :--- |
| D1 | **Critical** | `instantiate_db.sql` adds `score_checked` and then drops it on every new DB connection. Dropped columns keep their attribute number forever. Reproduced on PostgreSQL 16: run 1595 fails with `tables can have at most 1600 columns`. After that the bot cannot reach its DB until the table is rebuilt. `VACUUM FULL` does not reclaim the numbers. |
| D2 | High | Cleanup deletes submissions with `score < 0`. Reddit floors submission scores at 0, so this most likely never fires. The public crosspost reply still promises *"I'll remove posts with negative scores."* |
| D3 | High | Bait-and-switch: the target is re-parsed from the **current** comment body in Phases 2 and 3. Someone can earn upvotes with `r/aww`, edit the comment to `r/anything_else`, and the bot crossposts there carrying the old score. |
| D4 | High | `Forbidden` (typically the bot being banned from the subreddit it replied in) is treated as a network error. It costs a 30 s sleep and a stream reset with comments lost. |
| D5 | High | The English, Hebrew and totallynotrobots crosspost replies claim *"I checked on repostsleuth.com"*. RepostSleuth integration was removed in 2.2.1. |
| D6 | Medium | `PHASE3_WAITING_PERIOD=3 months` cannot be parsed and falls back to 90 days. `PHASE2_WAITING_PERIOD=0 seconds` becomes 2 hours (section 4). |
| D7 | Medium | `phase1.get_posts_with_same_content` catches `prawcore.exceptions.Redirect` and treats it as "exists, no duplicates". The later string check for `'Redirect to /subreddits/search'` is dead code. If Reddit answers a search on a nonexistent subreddit with a redirect rather than a 404, `sub_doesnt_exist_bot` never fires and the row is stored. |
| D8 | Medium | Phase 3 is not idempotent, and duplicate rows for the same submission and target are possible (no unique key). If the reply fails after a successful crosspost, the result is reported as a failed crosspost. |
| D9 | Medium | The `sub_doesnt_exist_bot` reply body uses the source subreddit's language, but `get_suffix()` uses the **target** (nonexistent) subreddit's language, producing mixed-language replies in localized subreddits. |
| D10 | Medium | The inbox stream is polled continuously, but both handlers are no-ops. The footer invites "Good bot/Bad bot" replies that nothing reads. |
| D11 | Low | Translation bugs: `es` / `de` / `fr` crosspost replies contain `] (` (a space breaks the Markdown link); the `es` footer has an unclosed `^(`; the `he` footnotes are numbered 1, 2, 2; several typos (`אלול`, `לדכתך`, `hoochgewählten`); and the raw English `PHASE3_WAITING_PERIOD` string is inserted into non-English text. |
| D12 | Low | `DISABLE_CLEANUP = True` is committed in the integration tests, so every integration run leaves posts on Reddit. |

---

## 10. Coding & Contribution Rules for Agents

1. **Preserve Comments & Docstrings:** Do not remove existing comments, docstrings, or developer notes during edits.
2. **Follow Existing Style:** Use clean, standard Python style matching the codebase (PEP 8 conventions, explicit error handling with `prawcore.exceptions` and `praw.exceptions.RedditAPIException`).
3. **Graceful Reddit Exception Handling:** Any newly introduced Reddit API calls must handle potential exceptions (`Forbidden`, `ServerError`, `RedditAPIException`, sub bans, locked threads, deleted comments) without crashing the main loop.
4. **Localization Awareness:** When adding or modifying bot comments, update translations in [`src/racb/i18n.py`](./src/racb/i18n.py) for all supported languages.
5. **Mandatory Version Bumping & Changelog Updates:** Every code, configuration, or infrastructure change intended for commit/deployment MUST include updating [`CHANGELOG.md`](./CHANGELOG.md) under `## [Unreleased]` and running `bump-my-version bump patch` (or `minor`/`major`). Never push, release, or deploy without completing the version bump and creating the release tag.
6. **Never run live integration tests casually:** use `pytest -m unit`. Integration tests post publicly as the real bot accounts.
7. **Keep section 9 current:** when you fix a known defect, delete its row; when you find a new one, add it.
