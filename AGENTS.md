# AGENTS.md

Welcome to the **RedditAutoCrosspostBot** repository. This document is a comprehensive guide for AI agents and human contributors working on, debugging, extending, or maintaining this codebase.

---

## 1. Project Overview & Multi-Bot Architecture

This repository hosts a suite of 4 automated Reddit bots powered by [PRAW (Python Reddit API Wrapper)](https://praw.readthedocs.io/), backed by a PostgreSQL database and containerized with Docker / Docker Compose for deployment on free cloud VPS instances (e.g. Google Cloud Always Free or Oracle Cloud Always Free) or local hardware.

### Primary Bot: `AutoCrosspostBot`
- **Goal:** Monitors Reddit comments on `r/all` for community crosspost recommendations matching `r/SomeSubreddit` or `/r/SomeSubreddit`.
- **Workflow:** When a user suggests another subreddit in a comment, the bot tracks the comment. If the comment gains sufficient community support (score threshold) after a maturation waiting period (e.g. 3 months), the bot crossposts the parent submission to the target subreddit with an explanatory comment in the target language.

### Companion Auxiliary Bots (Phase 1 Immediate Triggers)
All 4 bots share credentials configured in [`reddit_instantiator.py`](./reddit_instantiator.py) and operate during Phase 1:
1. **`sub_doesnt_exist_bot`** (`u/sub_doesnt_exist_bot`):
   - Trigger: Comment links to a non-existent subreddit (HTTP 404 / redirect).
   - Behavior: Performs live community search via `reddit.subreddits.search()` to suggest close existing subreddits, or suggests creating the subreddit if the name length is 3–24 characters.
2. **`same_subreddit_bot`** (`u/same_subreddit_bot`):
   - Trigger: Comment links to the exact same subreddit where it was posted.
   - Behavior: Replies humorously (*"Yes, that's where we are."*).
3. **`same_post_bot`** (`u/same_post_bot`):
   - Trigger: Comment links to a target subreddit that already contains a post with the exact same URL/content.
   - Behavior: Detects existing posts via Reddit search and [RepostSleuth API](https://api.repostsleuth.com), replying with a link to the prior post.

---

## 2. Architecture & Lifecycle Phases

The core processing pipeline follows a 3-phase staggered lifecycle orchestrated in [`reddit_auto_crosspost_bot.py`](./reddit_auto_crosspost_bot.py):

```mermaid
flowchart TD
    A["Reddit r/all Comment Stream"] --> B["Phase 1: Ingestion & Fast Filters\n(phase1_handler.py)"]
    B -->|Self Subreddit| C["u/same_subreddit_bot reply"]
    B -->|Duplicate Found| D["u/same_post_bot reply"]
    B -->|Nonexistent Sub| E["u/sub_doesnt_exist_bot reply"]
    B -->|Valid Recommendation| F[("PostgreSQL DB\nscraped_comments")]
    
    F --> G["Phase 2: Batch Filter Check\n(phase2_handler.py, every 20m)"]
    G -->|Batch Fetch via reddit.info()| H["Validate Availability & Score"]
    H -->|Score < Threshold or Invalid| I["DELETE from DB"]
    H -->|Passes Filters| J["Set phase2_checked = true"]
    
    J --> K["Phase 3: Final Verification & Crosspost\n(phase3_handler.py, every 6m)"]
    K -->|Re-verify Score & Availability| L["Crosspost to Target Subreddit"]
    L --> M["Reply to Crosspost (Localized Explanation)"]
    L --> N["DELETE from DB"]
    
    O["Scheduled Task (every 7m)\nunwanted_submission_remover.py"] --> P["Purge Bot Submissions with Score < 0"]
```

### Phase Details
- **Phase 1 ([`phase1_handler.py`](./phase1_handler.py)):**
  - Continuous streaming from `reddit.subreddit('all').stream.comments(skip_existing=True, pause_after=-1)`.
  - Filters: Discards moderator comments, non-top-level comments (`comment.parent_id != comment.link_id`), submissions with meta-phrases (`sub`, `subreddit`), and blacklisted subreddits ([`consts.py`](./consts.py)).
  - Qualified suggestions are stored via stored procedure `insert_scraped_comment(permalink)`.
- **Phase 2 ([`phase2_handler.py`](./phase2_handler.py)):**
  - Runs periodically via `schedule` (or manually via `python reddit_auto_crosspost_bot.py --only-phase2`).
  - Fetches unchecked rows older than `PHASE2_WAITING_PERIOD` using `get_unchecked_comments_older_than(interval)`.
  - Executes **batch fetching via `reddit.info(fullnames=chunk)`** in chunks of 100 to minimize API requests by 99%:
    - Comment availability (not deleted / removed).
    - Score >= `COMMENT_SCORE_THRESHOLD`.
    - Regex pattern still holds (user didn't edit comment).
    - Target subreddit availability and duplicate check.
  - Prunes failing entries from DB to conserve PostgreSQL table storage.
- **Phase 3 ([`phase3_handler.py`](./phase3_handler.py)):**
  - Runs periodically (every 6 minutes) if `LISTEN_ONLY` is false.
  - Queries records older than `PHASE3_WAITING_PERIOD` using `get_comments_older_than(interval)`.
  - Re-runs validation checks.
  - Formats submission title (e.g. uppercase for `r/totallynotrobots`).
  - Executes `submission.crosspost()` and posts an explanatory reply using localized text templates from [`my_i18n.py`](./my_i18n.py).
  - Handles Reddit API crosspost exceptions gracefully via modern `e.items` inspection (e.g., `NO_CROSSPOSTS`, `SUBREDDIT_NOTALLOWED`, `NO_IMAGES`, `SUBMIT_VALIDATION_*`).
  - Deletes the record from PostgreSQL.
- **Maintenance & Feedback:**
  - [`unwanted_submission_remover.py`](./unwanted_submission_remover.py): Periodically fetches the latest 40 submissions made by `AutoCrosspostBot` and deletes any with score < 0.
  - [`inbox_handler.py`](./inbox_handler.py): Monitors inbox stream for feedback (`good bot` / `bad bot`).

---

## 3. Directory & File Reference

| File | Purpose |
| :--- | :--- |
| [`reddit_auto_crosspost_bot.py`](./reddit_auto_crosspost_bot.py) | Application entry point, logging setup, stream loop, exception recovery, and `schedule` runner. |
| [`reddit_instantiator.py`](./reddit_instantiator.py) | Singleton factory for PRAW instances with native `ratelimit_seconds=300` and updated user-agent formats. |
| [`phase1_handler.py`](./phase1_handler.py) | Phase 1 comment matching, blacklisting, auxiliary bot dispatching, DB insertion. |
| [`phase2_handler.py`](./phase2_handler.py) | Phase 2 batch comment filtering via `reddit.info()` and DB cleanup. |
| [`phase3_handler.py`](./phase3_handler.py) | Phase 3 final validation, crosspost execution, comment reply, modern API exception handling. |
| [`racb_db.py`](./racb_db.py) | PostgreSQL connector (`psycopg2-binary`) with connection retry logic and stored procedure invocation. |
| [`instantiate_db.sql`](./instantiate_db.sql) | DDL schema, indexes, view `v_scraped_comments`, and PL/pgSQL stored procedures. Executed on startup. |
| [`repost_detector.py`](./repost_detector.py) | RepostSleuth API wrapper (`https://api.repostsleuth.com/image`) for reverse image & duplicate search. |
| [`sub_name_string_match.py`](./sub_name_string_match.py) | Live Reddit community search using `reddit.subreddits.search()` for typo suggestions. |
| [`my_i18n.py`](./my_i18n.py) | Custom localization mapping (`en`, `es`, `de`, `fr`, `he`, `totallynotrobots`) mapped by subreddit names. |
| [`unwanted_submission_remover.py`](./unwanted_submission_remover.py) | Auto-cleanup job deleting downvoted posts made by the bot. |
| [`inbox_handler.py`](./inbox_handler.py) | Evaluates feedback sent to the bot's inbox. |
| [`consts.py`](./consts.py) | Hardcoded blacklist of subreddits to ignore (`SUB_BLACKLIST`). |
| [`Dockerfile`](./Dockerfile) | Lightweight Python 3.11 container image definition. |
| [`docker-compose.yml`](./docker-compose.yml) | Multi-container stack definition (PostgreSQL 15 + Python bot worker). |
| [`cloud-init.yaml`](./cloud-init.yaml) | Cloud-init configuration for provisioning cloud VMs with swap and Docker. |
| [`startup-script.sh`](./startup-script.sh) | GCP startup script for automated VM bootstrapping. |
| [`_version.py`](./_version.py) | Application version definition (`__version__`). |
| [`.bumpversion.toml`](./.bumpversion.toml) | Configuration for `bump-my-version` automation. |
| [`CHANGELOG.md`](./CHANGELOG.md) | Release history following Keep a Changelog. |
| [`.env.example`](./.env.example) | Environment variable template. |
| [`requirements.txt`](./requirements.txt) | Modernized runtime Python dependencies (`praw>=7.7.1`, `psycopg2-binary`, etc.). |
| [`requirements-dev.txt`](./requirements-dev.txt) | Development & release dependencies (`bump-my-version`). |

---

## 4. Environment Variables

Store these in `.env` for local development or production:

| Variable | Type | Description |
| :--- | :--- | :--- |
| `DATABASE_URL` | String | PostgreSQL connection URI (`postgresql://user:pass@host:5432/db`). |
| `DEBUG` | Boolean (`True`/`False`) | Debug mode toggle. Sets logging to `DEBUG`, disables catch-all exception swallow. |
| `LISTEN_ONLY` | Boolean (`True`/`False`) | When `True`, disables Phase 3 crossposting while retaining stream listening and Phase 2 filtering. |
| `COMMENT_SCORE_THRESHOLD` | Integer | Minimum upvote score a comment must have to survive Phase 2 and be crossposted in Phase 3. |
| `PHASE2_WAITING_PERIOD` | Time string | Time duration before Phase 2 checks a comment (parsed via `pytimeparse`, e.g. `2 hours` or `1 day`). |
| `PHASE3_WAITING_PERIOD` | Time string | Time duration before Phase 3 executes the crosspost (e.g. `3 months` or `90 days`). |
| `PASSWORD` | String | Account password for `AutoCrosspostBot`. |
| `APP_CLIENT_SECRET` | String | App client secret for `AutoCrosspostBot`. |
| `PASSWORD__SUB_DOESNT_EXIST` | String | Account password for `sub_doesnt_exist_bot`. |
| `APP_CLIENT_SECRET__SUB_DOESNT_EXIST` | String | App client secret for `sub_doesnt_exist_bot`. |
| `PASSWORD__SAME_SUBREDDIT` | String | Account password for `same_subreddit_bot`. |
| `APP_CLIENT_SECRET__SAME_SUBREDDIT` | String | App client secret for `same_subreddit_bot`. |
| `PASSWORD__SAME_POST` | String | Account password for `same_post_bot`. |
| `APP_CLIENT_SECRET__SAME_POST` | String | App client secret for `same_post_bot`. |

---

## 5. Development & Operational Guidelines

### Setup & Execution via Docker Compose (Recommended)
1. **Configure Environment:**
   ```bash
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

### Setup & Local Execution (Without Docker)
1. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt # For local development and release management
   ```
2. **Database Setup:**
   Ensure a local PostgreSQL instance is running and set `DATABASE_URL` in `.env`.
   When [`racb_db.py`](./racb_db.py) initializes, it automatically runs [`instantiate_db.sql`](./instantiate_db.sql) to ensure tables, views, and functions exist.
3. **Run the Bot:**
   ```bash
   python reddit_auto_crosspost_bot.py
   ```
4. **Run Only Phase 2 (Manual Filter Pass):**
   ```bash
   python reddit_auto_crosspost_bot.py --only-phase2
   ```

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
When run, `bump-my-version` updates [`_version.py`](./_version.py), updates [`CHANGELOG.md`](./CHANGELOG.md), generates a git commit, and tags the release with `vX.Y.Z`. Push commits and tags to GitHub with `git push --follow-tags`.

### Critical Implementation Details & Gotchas
- **PRAW Native Rate Limiting:**
  [`reddit_instantiator.py`](./reddit_instantiator.py) configures `ratelimit_seconds=300` on all `praw.Reddit` instances. PRAW handles Reddit rate limit sleeps and retries natively.
- **PostgreSQL Stored Procedures:**
  Database interactions are routed through PL/pgSQL functions (`insert_scraped_comment`, `get_comments_older_than`, `get_unchecked_comments_older_than`, `delete_scraped_comment`, `set_comment_checked`). Any schema or query modifications must be updated in [`instantiate_db.sql`](./instantiate_db.sql) and reflected in [`racb_db.py`](./racb_db.py).
- **Batch Comment Processing in Phase 2:**
  To adhere to Reddit API rate limits (100 QPM), Phase 2 fetches up to 100 comments per request using `reddit.info(fullnames=...)`. Do not revert to individual per-comment HTTP requests.

---

## 6. Coding & Contribution Rules for Agents

1. **Preserve Comments & Docstrings:** Do not remove existing comments, docstrings, or developer notes during edits.
2. **Follow Existing Style:** Use clean, standard Python style matching the codebase (PEP 8 conventions, explicit error handling with `prawcore.exceptions` and `praw.exceptions.RedditAPIException`).
3. **Graceful Reddit Exception Handling:** Any newly introduced Reddit API calls must handle potential exceptions (`Forbidden`, `ServerError`, `RedditAPIException`, sub bans, locked threads, deleted comments) without crashing the main loop.
4. **Localization Awareness:** When adding or modifying bot comments, update translations in [`my_i18n.py`](./my_i18n.py) for all supported languages.
