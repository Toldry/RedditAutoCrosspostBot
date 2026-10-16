# AGENTS.md

Welcome to the **RedditAutoCrosspostBot** repository. This document is a comprehensive guide for AI agents and human contributors working on, debugging, extending, or maintaining this codebase.

> [!IMPORTANT]
> Section 9 lists **known limitations and open defects**. Read it before you assume a feature works the
> way its name suggests. If you fix one, remove it from that list.

---

## 1. Project Overview & Multi-Bot Architecture

This repository hosts a suite of 4 automated Reddit bots powered by [PRAW (Python Reddit API Wrapper)](https://praw.readthedocs.io/), backed by a PostgreSQL database and containerized with Docker / Docker Compose for deployment on free cloud VPS instances (e.g. Google Cloud Always Free or Oracle Cloud Always Free) or local hardware.

All 4 bots run in **one Python process, on several threads**. New comments are read by ID on a few
reader threads (`comment_reader.py`, section 7), with the main thread tracking the newest comment ID.
Phase 1 runs on a pool of worker threads, and every scheduled job (Phase 2, Phase 3, inbox, cleanup, ...)
on a thread of its own, so a slow job never pauses reading.

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
   - Behavior: Suggests existing subreddits with similar names (`sub_search.get_matches()`), and suggests creating the subreddit if the name length is 3–24 characters.
     Candidates come from the `known_subreddits` table (filled from the `r/all` stream, matched with
     `pg_trgm`) and from `reddit.subreddits.search()`. That endpoint matches on topic (title and description),
     not name, so every candidate must have a `difflib` name similarity of at least `MIN_NAME_SIMILARITY` (0.6);
     the best 4 are kept, most similar first. Each suggestion is fetched once (`phase1.describe_subreddit`):
     a private, quarantined, banned, restricted, archived or otherwise unavailable one is **still listed**,
     with its state in bold, as Reddit names it (the 403/404 `reason`, or `subreddit_type`) and untranslated
     (e.g. `* r/name (**private**)`).
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
    T -->|No| BL{"Source or target in\nSUB_BLACKLIST?\n(case-insensitive)"}
    BL -->|Yes| X4["ignore"]
    BL -->|No| SS{"target == source\n(case-insensitive)?"}
    SS -->|Yes| A1["same_subreddit_bot reply"]
    SS -->|No| DUP["get_posts_with_same_content()\n1 URL search + 1 other-discussions call\n+ image/video-hash scan of\n100 newest + 100 all-time top posts"]
    DUP -->|posts_found| A2["same_post_bot reply"]
    DUP -->|SUBREDDIT_DOES_NOT_EXIST| A3["sub_doesnt_exist_bot reply"]
    DUP -->|otherwise| TL{"Top-level comment?"}
    TL -->|No| X5["ignore"]
    TL -->|Yes| DB[("insert_scraped_comment(permalink, target)")]
```

Note: a private target (`SUBREDDIT_IS_PRIVATE`) is **still inserted** in Phase 1. Phase 2 deletes it later.
A banned target (a 404 whose body says `"reason": "banned"`, `SUBREDDIT_IS_BANNED`) gets no
`sub_doesnt_exist_bot` reply and is handled the same way.

### Phase Details
- **Phase 1 ([`src/racb/phases/phase1.py`](./src/racb/phases/phase1.py)):**
  - Continuous reading of every new comment by ID (`comment_reader.CommentReader`, the default,
    `STREAM_SOURCE=ids`): blocks of 100 consecutive comment IDs fetched with `reddit.info()` on
    `STREAM_READERS` threads, keeping only what r/all would show (`is_shown_on_r_all`: not NSFW, quarantined,
    private or on a user profile). Subreddits that opted out of r/all cannot be told apart, so their comments
    are read too. `STREAM_SOURCE=listing` instead reads
    `reddit.subreddit('all').stream.comments(skip_existing=True, pause_after=-1)` one request at a time, which
    sees only a fraction of r/all (section 9).
  - Filters: Discards moderator comments, submissions with meta-phrases (`sub`, `subreddit`), and blacklisted subreddits ([`src/racb/constants.py`](./src/racb/constants.py), compared case-insensitively by `is_crosspost_blocked`). Non-top-level comments (`comment.parent_id != comment.link_id`) are discarded only **after** auxiliary-bot dispatch (see the warning in section 1).
  - Qualified suggestions are stored via stored procedure `insert_scraped_comment(permalink, target_subreddit)`.
  - Phases 2 and 3 re-parse the target from the live comment body and reject the row
    (`TARGET_SUBREDDIT_CHANGED`) if it no longer equals the stored target, case-insensitively. This blocks
    bait-and-switch edits. Rows scraped before v2.3 have `target_subreddit = NULL` and skip this check.
  - The reader threads (or the stream thread) only run the regex (`check_pattern`, no I/O) and queue the comment ID of a match. A
    Phase 1 worker thread fetches the comment again through its own PRAW instance (one request) and does all
    of this work, including the duplicate search and image downloads (section 7).
  - Auxiliary replies go through `phase1.reply_as_bot()`, which only logs when `DRY_RUN` is enabled.
  - Any error while handling one comment is caught in `main.handle_comment_safely()` and skips only that
    comment (section 7).
- **Phase 2 ([`src/racb/phases/phase2.py`](./src/racb/phases/phase2.py)):**
  - Runs every 20 minutes on its own thread.
  - Fetches unchecked rows older than `PHASE2_WAITING_PERIOD` using `get_unchecked_comments_older_than(interval)`.
  - Executes **batch fetching via `reddit.info(fullnames=chunk)`** in chunks of 100 to minimize API requests by 99%:
    - Comment availability (not deleted / removed).
    - Score >= `COMMENT_SCORE_THRESHOLD`.
    - Regex pattern still holds (user didn't edit comment), and still names the stored target subreddit.
    - Neither the source nor the target is in `SUB_BLACKLIST` (`CROSSPOST_BLOCKED`), so subreddits blacklisted
      after a row was scraped are honored.
    - `AutoCrosspostBot` is not banned from the target (`user_is_banned`, one request,
      `BANNED_FROM_TARGET_SUBREDDIT`), checked before the duplicate scan. Phase 3 also treats a crosspost
      rejected with `SUBREDDIT_NOTALLOWED_BANNED` as an expected outcome, for a ban that came after the check.
    - The source post is not deleted or removed (`removed_by_category`, `[deleted]` / `[removed]` self text),
      locked or archived (`get_source_submission_problem`).
    - Target subreddit availability and duplicate check. Besides the URL search and picture scan, a post in the
      target that Reddit lists among the source's "other discussions" (`submission.duplicates()`), or one with
      the same title (`title:"..."` search, titles of 4+ words, compared ignoring case and punctuation), counts
      as a duplicate.
  - *Accuracy note:* only the **comment fetch** is batched. The duplicate check
    (`phase1.get_posts_with_same_content`) still costs a URL search, an "other discussions" call and a title
    search per row, plus a lazy submission
    fetch and, for image and video submissions, a scan of the target's 100 newest and 100 all-time top posts
    (`QUICK_SCAN_NEWEST_LIMIT`, `QUICK_SCAN_TOP_LIMIT`; fingerprints are cached, so the rarely changing top posts
    are mostly downloaded once). The real saving is therefore well
    under 99%.
  - If the batch `info()` call throws, it falls back to fetching each comment individually (`run_filters(ce, comment=None)`).
  - Prunes failing entries from DB to conserve PostgreSQL table storage.
- **Phase 3 ([`src/racb/phases/phase3.py`](./src/racb/phases/phase3.py)):**
  - Runs periodically (every 6 minutes) if `AUTO_CROSSPOST_BOT_ENABLED` is true.
  - Queries records older than `PHASE3_WAITING_PERIOD` using `get_comments_older_than(interval)`. This
    function does **not** filter on `phase2_checked`, so Phase 3 depends on Phase 2 having pruned rows first.
  - Re-runs validation checks (`phase2.run_filters(..., full_media_scan=True)`, which fetches each comment
    individually). For an image or video post, the full scan compares it with **every post of the same kind
    the target got since the source comment was written**, not just the 100 newest, plus the target's 100 top posts of all time and of the past year (the reply
    counts only the posts by date, so `RepostScan.top_posts_checked` is subtracted): `duplicate_detector.scan_subreddit_for_duplicates`
    walks the target's "new" listing back to the comment's `created_utc`, or until Reddit's listing ends
    (about 1000 posts, `REDDIT_LISTING_LIMIT`). It compares Reddit's small preview images (`PREVIEW_MIN_WIDTH`,
    320 px), 8 downloads at a time, so even a full scan stays cheap. It runs on the `phase3` thread, not the stream's.
    A video (`duplicate_detector.media_kind`: `is_video`, or a `v.redd.it`, YouTube or Streamable link) is
    compared through its preview frame only, so a trimmed or re-edited copy is usually missed, and a video
    without a preview is not scanned.
    The resulting `RepostScan` (posts checked, whether the scan reached the comment's date) is passed to
    the reply.
  - Formats submission title (e.g. uppercase for `r/totallynotrobots`).
  - Executes `submission.crosspost()` and posts an explanatory reply using localized text templates loaded by [`src/racb/i18n.py`](./src/racb/i18n.py) from the gettext catalogs in [`src/racb/locales/`](./src/racb/locales/).
    The reply (`REPLY_TO_CROSSPOST`) says the whole target was searched for the same URL and for crossposts and,
    for image and video posts, adds one sentence from `phase3.describe_media_scan`: how many image (or video)
    posts were compared and over what period (`REPLY_TO_CROSSPOST_IMAGE_CHECK` / `..._VIDEO_CHECK`), that the
    newest ones from before the comment (the Phase 1 check) and the top posts were compared too, the same for a scan cut short
    by Reddit's listing limit (`..._IMAGE_CHECK_PARTIAL` / `..._VIDEO_CHECK_PARTIAL`). A scan that
    found no posts to compare adds no sentence. That sentence links to
    `duplicate_detector.py` on GitHub (`DUPLICATE_DETECTOR_SOURCE_URL`), so keep that file readable.
  - Handles Reddit API crosspost exceptions gracefully via modern `e.items` inspection (e.g., `NO_CROSSPOSTS`, `SUBREDDIT_NOTALLOWED`, `NO_IMAGES`, `SUBMIT_VALIDATION_*`).
  - A rate limit or network error (`reddit.TRANSIENT_NETWORK_ERRORS`) while handling an entry keeps its row,
    logs a warning and stops that batch (`handle_comment_or_defer`), so the entry is retried next run.
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
    (there is no continuous inbox stream any more). Feedback is logged at INFO, answered by the bot that
    received it, and marked read. The phrases are recognized in **every** supported language, in any
    subreddit: each catalog's `BAD_BOT_PHRASE` / `GOOD_BOT_PHRASE` (e.g. "Schlechter Bot", "Плохой бот", and
    "Bad human" for r/totallynotrobots), plus English `bad bot`, `delete this`, `no`, `good bot`. A bare
    negative phrase gets `RESPOND_TO_NEGATIVE_SENTIMENT`, which asks why; every such reply gets it. A "bad bot"
    phrase followed by more words ("Bad bot, this sub bans memes", which the crosspost reply asks for) gets no
    answer: it is only logged and marked read, and `scripts/negative_feedback_report.py` finds it through the
    inbox. Positive feedback (`good bot`)
    gets `RESPOND_TO_POSITIVE_SENTIMENT` ("Thank you (◕ᴗ◕✿)"), but only the oldest `good bot` reply under a
    given bot comment is thanked (`is_first_positive_reply`, which compares only the replies Reddit returns
    without "load more"). Both replies carry the usual footer, respect `DRY_RUN`, and a reply Reddit rejects
    (locked or archived thread) is logged and marked read.

---

## 3. Directory & File Reference

| File | Purpose |
| :--- | :--- |
| [`src/racb/main.py`](./src/racb/main.py) | Application entry point, logging setup, `StreamMetrics` (the heartbeat), `run_stream` (starts the comment reader, or the listing stream loop with `STREAM_SOURCE=listing`), exception recovery, the job list (`build_jobs`), and graceful shutdown on SIGTERM/SIGINT. |
| [`src/racb/comment_reader.py`](./src/racb/comment_reader.py) | `CommentReader`: reads every new comment by blocks of 100 consecutive IDs on `STREAM_READERS` threads spread over the 4 accounts, and saves its position (`stream_cursors`) so restarts resume. Its module docstring explains the method. |
| [`src/racb/workers.py`](./src/racb/workers.py) | Background threads: `PeriodicJob` (one thread per scheduled job), `Phase1Workers` (the Phase 1 thread pool fed by the stream), and `run_job_safely`. |
| [`src/racb/__main__.py`](./src/racb/__main__.py) | Module executable entrypoint for `python -m racb`. |
| [`src/racb/__init__.py`](./src/racb/__init__.py) | Package marker; re-exports `__version__`. |
| [`src/racb/config.py`](./src/racb/config.py) | **All** environment parsing: `load_environment()`, `env_bool()`, `is_debug()` / `is_dry_run()` / `is_bot_enabled()`, `parse_duration()` (adds months and years on top of `pytimeparse`), waiting periods, score threshold. Use it instead of reading `os.environ` directly. |
| [`src/racb/core/reddit.py`](./src/racb/core/reddit.py) | Per-thread factory for PRAW instances (one per account per thread, created lazily) with native `ratelimit_seconds=300` and updated user-agent formats. Also `authenticate_all_bots()` / `authenticate_bots(names)` (startup check), `TRANSIENT_NETWORK_ERRORS` and the bot-name constants. |
| [`src/racb/core/db.py`](./src/racb/core/db.py) | PostgreSQL connector (`psycopg2-binary`) with one connection per thread, connection retry logic and stored procedure invocation. |
| [`src/racb/core/shutdown.py`](./src/racb/core/shutdown.py) | The process-wide stop signal: `request_stop()`, `stop_requested()`, `stop_requested_within(seconds)` (an interruptible sleep that returns True on shutdown), and `fail()`, which records a thread's crash and stops the process. |
| [`src/racb/core/sub_search.py`](./src/racb/core/sub_search.py) | Typo suggestions: merges candidates from `known_subreddits` and `reddit.subreddits.search()`, filtered and ranked by name similarity. |
| [`src/racb/core/known_subreddits.py`](./src/racb/core/known_subreddits.py) | Buffers subreddit names seen in the comment stream (no I/O per comment) and writes them to `known_subreddits` in one batch every 5 minutes; also the trigram lookup. |
| [`src/racb/core/duplicate_detector.py`](./src/racb/core/duplicate_detector.py) | Self-hosted perceptual image hashing (`imagehash` / `dhash`) for visual duplicate detection in target subreddits: `scan_subreddit_for_duplicates` compares an image or video post (videos by preview frame) with the subreddit's posts of the same kind back to a given time and returns a `RepostScan`. Linked from every crosspost reply about an image or video, so it is written to be read by outsiders; its module docstring explains the method. |
| [`src/racb/phases/phase1.py`](./src/racb/phases/phase1.py) | Phase 1 comment matching, blacklisting, auxiliary bot dispatching, DB insertion. |
| [`src/racb/phases/phase2.py`](./src/racb/phases/phase2.py) | Phase 2 batch comment filtering via `reddit.info()` and DB cleanup. |
| [`src/racb/phases/phase3.py`](./src/racb/phases/phase3.py) | Phase 3 final validation, crosspost execution, comment reply, modern API exception handling. |
| [`src/racb/phases/inbox.py`](./src/racb/phases/inbox.py) | Evaluates feedback sent to the bot's inbox. |
| [`src/racb/phases/cleanup.py`](./src/racb/phases/cleanup.py) | Auto-cleanup job deleting downvoted posts made by the bot. |
| [`src/racb/sql/instantiate_db.sql`](./src/racb/sql/instantiate_db.sql) | DDL schema, indexes, view `v_scraped_comments`, and PL/pgSQL stored procedures. Executed on **every new DB connection**, so every statement must be idempotent (see section 6). |
| [`src/racb/constants.py`](./src/racb/constants.py) | Hardcoded blacklist of subreddits to ignore as source and target (`SUB_BLACKLIST`), and `is_crosspost_blocked()`, which checks it case-insensitively. |
| [`src/racb/i18n.py`](./src/racb/i18n.py) | Loads the gettext catalogs, picks the locale for a subreddit, and falls back to English for untranslated strings (body and footer always share one language; a reply built from several messages, like the nonexistent-subreddit reply, switches entirely to English if any piece is untranslated). |
| [`src/racb/locales/`](./src/racb/locales/) | gettext catalogs: `<locale>/LC_MESSAGES/messages.po` (edited) and `messages.mo` (compiled, git-ignored: the Dockerfile and `tests/conftest.py` build them). Locales: `en` (source text), `es`, `de`, `fr`, `he`, `ru`, and `en@totallynotrobots`, an English variant using the gettext `@modifier` syntax. |
| [`src/racb/locales/subreddits.toml`](./src/racb/locales/subreddits.toml) | Which locale each subreddit gets (`locale = [subreddits]`, case-insensitive). Unlisted subreddits get English. |
| [`src/racb/version.py`](./src/racb/version.py) | Application version definition (`__version__`). |
| [`scripts/release_level.py`](./scripts/release_level.py) | Used by the CI `release` job: prints `major`, `minor`, `patch` or `none` from the Conventional Commits since the last `v*` tag and whether `changelog.d/` holds fragments. |
| [`scripts/cleanup_test_history.py`](./scripts/cleanup_test_history.py) | Deletes integration-test posts and comments from all 4 bot accounts. Supports `--dry-run`, `--limit N` (`0` means all) and `--strict` (exit code 1 if a bot could not be checked or a test item could not be deleted). Slow; for manual use. |
| [`scripts/delete_test_artifacts.py`](./scripts/delete_test_artifacts.py) | Deletes only the posts and comments the integration tests recorded in their manifest (`$RACB_TEST_ARTIFACTS_FILE`, default `racb-test-artifacts.jsonl` in the temp directory), as the bot that wrote each one, then removes the manifest. Supports `--dry-run`, `--manifest PATH` and `--strict`. The last step of CI's `integration-tests` job. |
| [`scripts/verify_comment_ids.py`](./scripts/verify_comment_ids.py) | Read-only live check of what `comment_reader.py` assumes: comment IDs follow creation time, 100-ID blocks come back mostly full with the fields the r/all filter reads, how many IDs Reddit hands out per second, and what each request costs (with a rough egress estimate). Needs only `racb.core.reddit`; its docstring shows how to pipe it into the bot container on the VM. |
| [`scripts/redeploy.sh`](./scripts/redeploy.sh) | Runs **on the VM**: resets the checkout to a commit of `origin/master` (argument, `SSH_ORIGINAL_COMMAND`, or the latest), runs `docker compose up --detach --build`, and fails if the bot is not running 30 s later. The CI `deploy` job's SSH key is pinned to it. Refuses to run on a dirty or diverged checkout. |
| [`scripts/authorize_deploy_key.sh`](./scripts/authorize_deploy_key.sh) | Runs **on the VM** once: adds the CI deploy key to `~/.ssh/authorized_keys` as a forced command for `redeploy.sh` and prints the host key. |
| [`scripts/setup_auto_deploy.sh`](./scripts/setup_auto_deploy.sh) | One-time setup of automatic deployment, run in Cloud Shell: service account, Workload Identity Federation, IAP firewall rule, deploy key, GitHub variables. See [`docs/auto-deploy.md`](./docs/auto-deploy.md). |
| [`docs/auto-deploy.md`](./docs/auto-deploy.md) | How the CI `deploy` job works, its security model, setup, rollback and troubleshooting. |
| [`docs/db-viewer.md`](./docs/db-viewer.md) | How to start the optional read-only `pgweb` database viewer on the VM and open it through an SSH tunnel. |
| [`docs/logs.md`](./docs/logs.md) | How to read the bot's logs over SSH or in Google Cloud Logging, and how to turn Cloud Logging on. |
| [`scripts/negative_feedback_report.py`](./scripts/negative_feedback_report.py) | Writes an HTML report to `reports/negative_feedback/` (gitignored) of the "bad bot" exchanges with written feedback: a reply below a bot's `RESPOND_TO_NEGATIVE_SENTIMENT` question (or the "bad bot" comment itself) that says more than a bare sentiment phrase (bodies `[deleted]`, `[removed]` and `[unavailable]` never count). Each qualifying exchange shows only its new feedback, with the post and the Reddit-style comment tree collapsed behind a "Show full conversation" button. Scans all 4 bots by default (`--bot` picks some). Each report embeds the feedback ids it shows, so the next run only reports exchanges with new feedback, and links to the earlier reports. Reddit listings stop at about 1000 items, so very old comments may be unreachable. |
| [`tests/conftest.py`](./tests/conftest.py) | `MockComment` / `MockSubmission` / `MockSubreddit` / `MockRedditor` plus the `make_mock_comment` and `make_mock_submission` factory fixtures. |
| [`tests/unit/`](./tests/unit/) | 1700+ mocked unit tests (`@pytest.mark.unit`), one file per module. `test_crosspost_reply_phrasings.py` renders the crosspost reply for all 1330 combinations of locale, author, comment age and media-scan outcome (none, or each image and video scan) and checks each; its `render_crosspost_reply` is handy for reading them. |
| [`tests/integration/test_reddit_integration.py`](./tests/integration/test_reddit_integration.py) | 10 **live** Reddit tests (`@pytest.mark.integration`) that submit real posts and comments. |
| [`tests/assets/test_sample.png`](./tests/assets/test_sample.png) | Base image for perceptual-hash variation tests. |
| [`pytest.ini`](./pytest.ini) | Test discovery: `testpaths = tests`, `pythonpath = src .`, markers `unit` and `integration`, and `addopts = -m "not integration"`. |
| [`.github/workflows/tests.yml`](./.github/workflows/tests.yml) | CI: `uv sync --locked` then `uv run pytest` on Python 3.11 (from `.python-version`), for pushes to `master`/`main` and for pull requests. Fails if `uv.lock` is out of date. On `master`, a `release` job then bumps the version and tags it (section 5), and, when the `AUTO_DEPLOY` variable is `true`, an `integration-tests` job runs the live tests with the bot credentials from repository secrets, then deletes what they created with `delete_test_artifacts.py --strict` (even after failed or cancelled tests), after which a `deploy` job redeploys the VM at the released commit ([`docs/auto-deploy.md`](./docs/auto-deploy.md)). |
| [`.github/dependabot.yml`](./.github/dependabot.yml) | Dependabot config for `pyproject.toml` / `uv.lock` (`uv` ecosystem) and GitHub Actions. Version updates are **paused** with `open-pull-requests-limit: 0`; delete those lines to resume monthly PRs. |
| [`examples/example_reddit_comment.py`](./examples/example_reddit_comment.py) | Not executable code: a commented-out attribute dump of a PRAW `Comment` and `Submission`, kept for reference. |
| [`deploy/cloud-init.yaml`](./deploy/cloud-init.yaml) | Universal cloud-init configuration for provisioning cloud VMs (GCP, Oracle, AWS) with swap and Docker. It does **not** clone or start the bot. |
| [`deploy/instance-flags.yaml`](./deploy/instance-flags.yaml) | Declarative Google Cloud CLI flags file for provisioning the Always Free e2-micro instance. |
| [`Dockerfile`](./Dockerfile) | Lightweight Python 3.11 container image definition. |
| [`docker-compose.yml`](./docker-compose.yml) | Multi-container stack definition (PostgreSQL 15 + Python bot worker, plus the optional `pgweb` viewer in the `viewer` profile, bound to `127.0.0.1:8081`). Containers keep at most 3 × 10 MB of Docker logs. |
| [`docker-compose.gcp.yml`](./docker-compose.gcp.yml) | Override for the GCP VM only, enabled by `COMPOSE_FILE` in its `.env`: sends the bot's output to Cloud Logging with Docker's `gcplogs` driver. See [`docs/logs.md`](./docs/logs.md). |
| [`.bumpversion.toml`](./.bumpversion.toml) | Configuration for `bump-my-version`, which the CI `release` job runs. |
| [`.gitignore`](./.gitignore) | Ignores **all dotfiles** (`.*`) except an explicit allowlist. See section 8. |
| [`CHANGELOG.md`](./CHANGELOG.md) | Release history following Keep a Changelog. Written only by the CI `release` job; PRs never edit it (section 5). |
| [`changelog.d/`](./changelog.d/) | Changelog fragments, one file per change, waiting for the next release. Naming and format in its [`README.md`](./changelog.d/README.md); checked by `tests/unit/test_changelog_fragments.py`. |
| [`CONTRIBUTING.md`](./CONTRIBUTING.md) | Short guide for human contributors: setup, tests, commit titles, changelog fragments. |
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
| `AUTO_CROSSPOST_BOT_ENABLED` | Boolean (`True`/`False`) | `True` | When `False`, `AutoCrosspostBot` never writes: Phase 3 (crossposting) and the negative-score cleanup are not scheduled, its inbox is not read, and `--process-one` refuses to run (exit code 2). Its account is still used to read Reddit. |
| `SUB_DOESNT_EXIST_BOT_ENABLED` | Boolean (`True`/`False`) | `True` | When `False`, `sub_doesnt_exist_bot` never replies (its suggestions are not even looked up) and its inbox is not read. |
| `SAME_SUBREDDIT_BOT_ENABLED` | Boolean (`True`/`False`) | `True` | When `False`, `same_subreddit_bot` never replies and its inbox is not read. |
| `SAME_POST_BOT_ENABLED` | Boolean (`True`/`False`) | `True` | When `False`, `same_post_bot` never replies and its inbox is not read. |
| `DRY_RUN` | Boolean (`True`/`False`) | `False` | When `True`, nothing is written to Reddit: auxiliary replies, crossposts and cleanup deletions are only logged with a `[DRY RUN]` prefix. The database is still written to, but Phase 3 keeps the rows it would have crossposted. |
| `COMMENT_SCORE_THRESHOLD` | Integer | `1` (`.env.example`: `30`) | Minimum upvote score a comment must have to survive Phase 2 and be crossposted in Phase 3. |
| `PHASE2_WAITING_PERIOD` | Time string | `2 hours` | Time duration before Phase 2 checks a comment (parsed via `pytimeparse`, e.g. `2 hours` or `1 day`). |
| `PHASE3_WAITING_PERIOD` | Time string | `90 days` | Time duration before Phase 3 executes the crosspost (e.g. `3 months` or `90 days`). The public crosspost reply shows it as a localized whole number of days. |
| `PHASE1_WORKERS` | Integer | `4` | Number of Phase 1 worker threads (at least 1). Each makes its own Reddit requests, all sharing the accounts' rate limits (section 7). |
| `STREAM_SOURCE` | `ids` / `listing` | `ids` | How new comments are read: `ids` reads every comment by ID range (`comment_reader.py`), `listing` reads PRAW's r/all comment stream one request at a time (the pre-3.1 way, kept as a fallback). An unknown value logs a WARNING and uses `ids`. |
| `STREAM_READERS` | Integer | `4` | With `STREAM_SOURCE=ids`: threads fetching ID blocks (at least 1), taking turns between the 4 accounts. Raising it only helps if the heartbeat's `behind:` keeps growing; requests per minute are set by how fast Reddit gets new comments, not by this. |
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
- Runs Phase 2 once at startup (on its thread, while the stream starts). Cleanup and Phase 3, which write to
  Reddit, keep their normal schedule.
- Errors in comment handling, scheduled jobs and the stream loop are re-raised. On a worker or job thread,
  that stops every thread (`shutdown.fail`) and `start_bot()` re-raises it, which crashes the process.
- `add_comment` and `exec_crosspost` re-raise instead of logging.

### Per-bot switches vs. `DRY_RUN`
- `<BOT>_ENABLED=False` turns one account's writes off silently. Phase 1 still decides what it would have
  done (a comment that would have got an auxiliary reply is still **not** stored), it just does not reply.
  With all four off, only the stream, Phase 1's filtering, the database inserts, Phase 2 and the
  known-subreddits job run. All 4 accounts still need valid credentials, since startup authenticates them all (except `--process-one`).
- `DRY_RUN=True` keeps every bot running but logs each write instead of making it.

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
   For a read-only view in the browser, start `pgweb` (`docker compose --profile viewer up -d pgweb`, needs
   `PGWEB_AUTH_PASS` in `.env`) and tunnel to port 8081; see [`docs/db-viewer.md`](./docs/db-viewer.md).

### Setup & Local Execution (Without Docker)
1. **Install Dependencies** (needs [uv](https://docs.astral.sh/uv/getting-started/installation/)):
   ```bash
   uv sync   # creates .venv with Python 3.11 and installs uv.lock, dev group included
   ```
   Prefix commands with `uv run` (e.g. `uv run pytest`), or activate `.venv` first.
2. **Database Setup:**
   Ensure a local PostgreSQL instance is running and set `DATABASE_URL` in `.env`.
   When [`src/racb/core/db.py`](./src/racb/core/db.py) initializes, it automatically runs [`src/racb/sql/instantiate_db.sql`](./src/racb/sql/instantiate_db.sql) to ensure tables, views, and functions exist.
   The connection is **lazy** and **per thread**: each Phase 1 worker and each job opens its own on first use,
   not at startup, so up to `PHASE1_WORKERS` + 6 connections are open (Postgres allows 100 by default).
   The schema script runs on every new connection, one connection at a time (`db._schema_lock`).
3. **Run the Bot:**
   ```bash
   uv run python -m racb
   ```
4. **Process a Single Entry (Production Testing):**
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
   `--process-one` starts nothing else (no stream, jobs or worker threads) and authenticates only
   `AutoCrosspostBot`, the one account Phase 3 uses, so the other 3 need no valid credentials for it. It exits
   with code 2 when `AUTO_CROSSPOST_BOT_ENABLED` is false. Every other invocation except `--help` first runs
   `authenticate_all_bots()`. That needs network access and valid credentials for
   **all 4** accounts, and exits with code 1 otherwise.

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
  debugging (this sets `DISABLE_CLEANUP` in the test file). Every created post and comment must also be
  wrapped in `track()`, which records it in the manifest that `scripts/delete_test_artifacts.py` reads, so
  items a killed run left behind can be deleted without searching for them:
  ```bash
  python scripts/delete_test_artifacts.py --dry-run   # preview what this machine's runs recorded
  python scripts/delete_test_artifacts.py             # delete it
  ```
  To sweep the bots' whole recent history for test litter instead (slow):
  ```bash
  python scripts/cleanup_test_history.py --dry-run   # preview
  python scripts/cleanup_test_history.py --limit 0   # delete all matching items
  ```
  The script treats an item as test litter if its subreddit starts with `racb_test_` or `racb_fake_`, or if
  its title or body contains `[test`, `racb_test_` or `racb_fake_`.
- CI runs them on every push to `master` while auto-deploy is on (`integration-tests` job) and only deploys
  when they pass. The job's last step, `scripts/delete_test_artifacts.py --strict`, runs even after failed or
  cancelled tests and also gates the deploy. Pull requests never run them.
- The integration tests call the reply and crosspost helpers **directly**. They do not exercise detection
  (`get_posts_with_same_content`, `handle_incoming_comment`) against live Reddit, apart from the image-hash tests. Those also scan a gallery
  post, and read real public crossposts from r/all (the test subreddits are private, so the tests cannot
  make crossposts of their own).
- Unit tests mock PRAW objects with `tests/conftest.py`. Patch at the import site actually used, e.g.
  `racb.phases.phase1.get_posts_with_same_content` or `racb.core.db.add_comment`.

### Logging
- Format: `YYYY-MM-DD HH:MM:SS [LEVEL   ] [thread] module.name: message`, sent to stderr and to
  `$LOG_DIR/app.log` (rotating, 5 x 5 MB). `thread` is `MainThread` (head polls, or the listing stream),
  `reader-<n>` (comment ID blocks), `phase1-<n>`, or the job's name (`phase2`, `phase3`, `inbox`, `cleanup`,
  `heartbeat`, `known-subreddits`).
- The `prawcore` and `urllib3.connectionpool` loggers are **disabled entirely**, which also hides prawcore's rate-limit messages.
- `[DRY RUN]` lines show writes that `DRY_RUN` suppressed. `Skipping comment ... after Reddit error` lines
  show per-comment failures (e.g. a bot banned from a subreddit) that no longer interrupt the stream.
- Every comment a bot posts is logged after it is posted: `/u/<bot> replied to <user> suggestion (r/<source> -> r/<target>): <reply URL>`
  (Phase 1 auxiliary replies and the crosspost reply) or `/u/<bot> replied to <user> feedback (r/<subreddit>): <reply URL>` (inbox).
  A Phase 1 `Replying via ...` line without a matching `replied to` line means the reply failed.
- A heartbeat line (`[Heartbeat] Last ~10m: scanned N comments ...`) is logged every 10 minutes by `StreamMetrics`. It is the quickest health and throughput signal.
  It says `N match(es) dropped, Phase 1 queue full` when the Phase 1 workers fell behind. Besides that,
  only two lines are logged per backlog: when the queue fills and when it has room again.
  Its second half says how comments were read, for example
  `reads: 412 requests, 87.3 comments/request, 0.95 s/request | head: 50 polls, 1.70 s/poll | behind: 9 s, 340 IDs`:
  - `reads`: ID blocks (or listing batches) fetched, and how many comments each returned (all of them, before
    the r/all filter). With `STREAM_SOURCE=listing`, about 100 comments/request means the listing returned
    full pages, so the bot was falling behind r/all.
  - `behind`: age of the newest comment read, and (`ids` only) how many IDs wait to be read. Growing values
    mean the readers do not keep up; `N ID block(s) failed` and `N IDs skipped` count comments given up on.

### Release & Semantic Versioning Workflow
We follow [Semantic Versioning (SemVer)](https://semver.org/) and [Keep a Changelog](https://keepachangelog.com/). **Releases are fully automatic; nobody bumps the version by hand.**

After every push to `master`, once the unit tests pass, the `release` job in
[`.github/workflows/tests.yml`](./.github/workflows/tests.yml):
1. runs [`scripts/release_level.py`](./scripts/release_level.py), which prints `none` if
   [`changelog.d/`](./changelog.d/) holds no fragments, otherwise the bump implied by the commits since the last `v*` tag
   (merge commits and earlier release commits are ignored):
   - `major`: a breaking Conventional Commit, `type!: ...` / `type(scope)!: ...` or a `BREAKING CHANGE:` footer
   - `minor`: a `feat` commit
   - `patch`: anything else, including commits that are not Conventional Commits
2. runs `towncrier build --version <new version>` (the version from `bump-my-version show`), which writes a
   `## [X.Y.Z] - YYYY-MM-DD` section into [`CHANGELOG.md`](./CHANGELOG.md) and `git rm`s the fragments;
3. runs `uv run bump-my-version bump --allow-dirty <level>`, which updates
   [`src/racb/version.py`](./src/racb/version.py), makes a `chore(release): bump version X -> Y` commit as
   `github-actions[bot]` (including towncrier's staged changes), and an annotated `vX.Y.Z` tag;
4. pushes both to `master` (`git push --follow-tags`).

Mechanics worth knowing:
- Write Conventional Commit subjects (`feat:`, `fix:`, `docs:`, ...) on feature branches; they decide
  the bump. With squash merges, the PR title becomes the commit, so it must follow the convention too.
- The job checks out the latest `master` and runs in a `release` concurrency group, so releases queue.
  If someone merges while a release is running, its push is rejected and the job fails; the run for that
  later merge then releases everything. Pushes made with `GITHUB_TOKEN` do not trigger workflows, so the
  release commit cannot start another release (and its tests do not re-run).
- It needs `master` to accept pushes from GitHub Actions. If branch protection is ever enabled, allow
  `github-actions[bot]` to bypass it, or the job fails.
- To preview locally: `uv run towncrier build --draft --version X.Y.Z` (the next CHANGELOG section) and
  `uv run bump-my-version bump patch --dry-run --verbose` (clean working tree required).
- towncrier inserts each release right below the `<!-- towncrier release notes start -->` line in
  CHANGELOG.md, so that line must stay. Its settings (directory, heading format, the six Keep a Changelog
  types) are under `[tool.towncrier]` in `pyproject.toml`.
- PRs edit `changelog.d/`, never CHANGELOG.md, and each fragment is a new file, so merging one PR cannot
  conflict with another's changelog entry. For the same reason, do not put counts that every PR changes
  (like the exact number of unit tests) in shared docs.
- The version also appears in every PRAW User-Agent (`linux:<bot>:v<version> (by /u/orqa)`) and in the
  image downloader's User-Agent (`RACB/<version> (+github URL)`).

### Critical Implementation Details & Gotchas
- **PRAW Native Rate Limiting:**
  [`src/racb/core/reddit.py`](./src/racb/core/reddit.py) configures `ratelimit_seconds=300` on all `praw.Reddit` instances. PRAW handles Reddit rate limit sleeps and retries natively.
  Precisely: request-rate limiting (the per-minute OAuth quota) is handled by prawcore. `ratelimit_seconds`
  only controls how long PRAW will **sleep** on a `RATELIMIT` API error ("you are doing that too much")
  before retrying. Up to 300 s of that sleep happens on the thread that made the call (a Phase 1 worker or a
  job), so only that thread stalls.
- **PRAW delete endpoint patch:**
  [`src/racb/core/reddit.py`](./src/racb/core/reddit.py) sets `praw.endpoints.API_PATH['del'] = 'api/del'` at import.
  On 2026-10-15 Reddit answered PRAW's default `POST /api/del/` (trailing slash) with a plain-text
  `404 page not found` for every item, while `POST /api/del` worked. By 2026-10-16 both paths worked again
  (tested live on the bot's profile and in r/racb_test_1), so it was probably temporary on Reddit's side.
  The patch is harmless and kept as a safeguard. If deletes 404 again, compare both paths with
  `/api/del/` vs `/api/del` before assuming anything else is wrong.
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
- **Lazy PRAW fields:** reading a field a listed post lacks (`crosspost_parent_list`, `preview`, ...) makes PRAW
  fetch the whole post, one request each. Read media fields through `duplicate_detector.post_attribute`, which
  only looks at what the listing returned.
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
| `known_subreddits` | `name_lower VARCHAR(32) PK`, `display_name VARCHAR(32) NOT NULL`, `last_seen TIMESTAMP NOT NULL`. GIN trigram index `idx_known_subreddits_name_trgm` on `name_lower`. Every subreddit (except `u_` profiles) the stream has seen a comment in; rows are never deleted, so a later-banned subreddit stays (its suggestion line then says so). |
| `record_known_subreddits(text[])` | Upserts a batch of names, refreshing `display_name` and `last_seen`. |
| `find_similar_subreddits(varchar, int)` | `(display_name, similarity)` for names matching `name_lower % lower(query)` (trigram similarity ≥ `pg_trgm.similarity_threshold`, default 0.3), most similar first. |
| `stream_cursors` | `name VARCHAR(32) PK`, `next_comment_id BIGINT NOT NULL`, `done_blocks BIGINT[] NOT NULL`, `updated_at TIMESTAMP NOT NULL`. Where `comment_reader` stopped (row `comments`): every comment ID (base 36 as a number) below `next_comment_id` was read, and so were the 100-ID blocks starting at each of `done_blocks`. |
| `save_stream_cursor(varchar, bigint, bigint[])` / `get_stream_cursor(varchar)` | Upsert / read one cursor; `get` returns no row if none is saved. |

Behavior to be aware of:
- The script runs `CREATE EXTENSION IF NOT EXISTS pg_trgm` inside a `DO` block that only warns on failure.
  `pg_trgm` is a trusted extension (PostgreSQL 13+), so the database owner can create it without being a
  superuser. Without it, the trigram index is skipped and `find_similar_subreddits` fails at call time;
  `get_matches()` logs that and uses Reddit's search alone.
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

## 7. Threads, Scheduling & Error Handling

```mermaid
flowchart LR
    M["MainThread\npolls the newest comment ID"] -->|"blocks of\n100 IDs"| BQ[["block queue"]]
    M -.->|"every 30 s\nand at shutdown"| C[("stream_cursors")]
    BQ --> S["reader-1 ... reader-N\n(STREAM_READERS,\n4 accounts in turn)"]
    S -->|"comment ID of a\nregex match"| Q[["Phase 1 queue\n(1000 IDs)"]]
    Q --> W["phase1-1 ... phase1-N\n(PHASE1_WORKERS)"]
    S -->|"subreddit name\n(no I/O)"| K["known_subreddits buffer"]
    J1["phase2"] -.-|"one lock"| J2["phase3"]
    J3["inbox"]
    J4["cleanup"]
    J5["heartbeat"]
    J6["known-subreddits"] --> K
```

| Job (thread name) | Interval | Function |
| :--- | :--- | :--- |
| Cleanup of negative-score submissions (`cleanup`, only if `AUTO_CROSSPOST_BOT_ENABLED`) | 7 min | `cleanup.delete_unwanted_submissions` |
| Heartbeat log (`heartbeat`) | 10 min | `metrics.log_heartbeat` |
| Phase 2 (`phase2`) | 20 min | `phase2.filter_comments_from_db` |
| Phase 3 (`phase3`, only if `AUTO_CROSSPOST_BOT_ENABLED`) | 6 min | `phase3.process_comment_entries` |
| Inbox feedback of the enabled bots (`inbox`, only if any is enabled) | 10 min | `inbox.process_unread_inboxes` |
| Write buffered known subreddits (`known-subreddits`) | 5 min | `known_subreddits.flush` |

Each job is a `workers.PeriodicJob` thread. It first runs one interval after startup, and each next run
starts one interval after the previous one **ended**, so a job never overlaps itself. Phase 2 and Phase 3
share a lock and never run at the same time, because both take rows from `scraped_comments` and Phase 3
re-runs Phase 2's filters. Every run is wrapped in `workers.run_job_safely()`, which logs a failing job's
exception instead of ending its thread, and calls `db.reset_connection()` on DB connection errors.

**Reading comments (`STREAM_SOURCE=ids`, `comment_reader.CommentReader`).** Reddit numbers comments in
base 36, in order across the whole site. The main thread asks the r/all listing for its newest comment
(one request, then a 10 s pause, which only delays reading, never skips anything) and queues every complete block of 100 IDs up to it. Each `reader-<n>`
thread takes a block, fetches it with `reddit.info(fullnames=...)` (one request, as its account) and passes
each comment r/all would show to `main.dispatch_comment`, several threads at once. So requests per minute
follow Reddit's comment rate (about one per 100 new comments), whatever `STREAM_READERS` is. A block that
fails with a network error is retried after the usual back-off, up to 3 tries, then given up on and counted.

The reader saves its position every 30 s and at shutdown: the lowest ID not read yet (the watermark), and
the blocks already done above it, so a restart neither skips nor re-reads them. A restart more than
`MAX_BACKLOG_IDS` (200,000, about half an hour at a guessed 100 comments a second) behind starts at the newest comment instead, and so does
a running reader that falls that far behind (the skipped IDs are logged and counted).

**Reading comments (`STREAM_SOURCE=listing`).** `main_loop()` reads one batch from PRAW's comment stream. The
stream uses `pause_after=-1`. In PRAW this makes the generator yield `None` after **every** request and
**never sleep**, so the loop polls continuously, throttled only by prawcore's rate limiter. After a stream
error, the new stream continues after the newest comment seen (`continue_after_id`); after a restart it
starts with `skip_existing=True`.

Either way, for each comment the reading thread only counts it, buffers its subreddit name and runs the
regex. A match goes into the Phase 1 queue as a comment ID; when the queue is full, the match is dropped and
counted in the heartbeat, so reading never waits for the workers.

Rules for thread safety:

- **PRAW is not thread safe**, so `reddit.get_reddit_instance()` returns a separate instance per thread and
  account. PRAW objects stay bound to the instance that fetched them: hand **IDs** to another thread, never
  PRAW objects (a worker would make requests through the stream thread's session and rate limiter).
- Each instance has its own prawcore rate limiter, but Reddit counts requests per account and reports the
  shared remaining quota in every response. Each limiter paces itself on those headers, so together they
  stay close to the quota and all wait for the reset once it is used up. They can still overshoot by a few
  requests at once, which ends in a `TooManyRequests` that the thread backs off from (60 s).
- **Each thread has its own database connection** (`db.get_connection()`, thread-local).
- Module-level state written by more than one thread needs a lock (`StreamMetrics`, `known_subreddits`,
  `Phase1Workers.submit`, `CommentReader`'s block bookkeeping).

**Shutdown:** SIGTERM (`docker stop`) and SIGINT set the stop signal in `racb.core.shutdown`. The main
thread, the reader threads, the Phase 1 workers and the job threads check it, every back-off waits with `shutdown.stop_requested_within()`
instead of `time.sleep()`, and Phase 2 and Phase 3 stop between entries, so an entry is never left
crossposted but not deleted. `start_bot()` waits up to `SHUTDOWN_TIMEOUT_SECONDS` (50 s) for all threads;
`docker-compose.yml` gives the bot `stop_grace_period: 60s`. Matches still waiting in the Phase 1 queue are
dropped and logged. If any worker or job thread crashes (only possible with `DEBUG`, or a bug in the thread
loop itself), `shutdown.fail()` stops every thread and `start_bot()` re-raises the error, so the process
exits and Docker restarts it.

Errors are handled in three layers:

```mermaid
flowchart TD
    C["phase1.handle_incoming_comment(comment)\non a Phase 1 worker"] -->|raises| S{"handle_comment_safely()"}
    S -->|"network error\n(ServerError, RequestException,\nTooManyRequests, requests errors)"| B["skip comment, the worker\nwaits 60 s (rate limit) or 30 s"]
    S -->|"DB connection error"| R["reset DB connection,\nskip comment"]
    S -->|"other Reddit / PRAW error\n(e.g. 403 bot banned)"| K["log INFO, skip comment"]
    S -->|"anything else"| L["log with traceback, skip comment"]
    F["head poll or listing fetch\non the main thread"] -->|raises| H{"handle_exception()"}
    H -->|"TooManyRequests"| W60["sleep 60 s"]
    H -->|"network error or Forbidden"| W30["sleep 30 s"]
    H -->|"DB connection error"| WDB["reset DB, sleep 30 s"]
    H -->|"graceful RedditAPIException"| N["no sleep"]
    W60 & W30 & WDB & N --> RS["poll again (ids), or re-create\nthe stream after the newest\ncomment seen (listing)"]
    H -->|"anything else"| X["crash; Docker restarts"]
```

- Matching uses `isinstance`, so subclasses are covered.
- With `DEBUG=True`, every layer re-raises.
- A failing head poll only stops new blocks from being queued; the readers finish the ones queued and the
  next poll catches up, so nothing is lost. Reader threads back off from their own errors
  (`workers.back_off_after_network_error`) and retry the block.

Rule for new code (reinforcing rule 3 in section 10): catch per-item errors **inside** the per-comment or
per-row handler. Only network-level failures should reach the worker's back-off or `handle_exception()`.
A back-off on the main thread only pauses finding new comments; the readers, workers and jobs keep running.

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
- **Automatic deploys** ([`docs/auto-deploy.md`](./docs/auto-deploy.md)) build on the VM, never in CI, because
  the image contains `.env`. They `git reset --hard` the VM checkout, so `scripts/redeploy.sh` refuses to run
  when tracked files were edited on the VM. Keep `redeploy.sh`'s body inside `main()`: the reset rewrites the
  running script.
- **Always Free limits on the e2-micro** (https://cloud.google.com/free/docs/free-cloud-features#compute):
  instance hours, 30 GB-months of standard disk, and 1 GB a month of outbound data transfer. CPU use is not
  among them, so it costs nothing; an e2-micro that needs more than its share is only slowed down. Egress is
  the limit to watch: downloads from Reddit are inbound, but every request and its TCP acknowledgements go out.
  Check the VM's network egress in Cloud Monitoring after a change that adds requests
  (`scripts/verify_comment_ids.py` prints a rough estimate for the comment reader).
- Python versions: `requires-python = ">=3.11"`. The Docker image, CI and `.python-version` use 3.11, and the
  unit tests also pass on 3.13. 3.10 is not supported.
- Mermaid diagrams: GitHub's renderer rejects unquoted labels that contain parentheses, e.g.
  `-->|reddit.info()|`. Quote them: `-->|"reddit.info()"|`.

---

## 9. Known Limitations & Open Questions

| Topic | Status |
| :--- | :--- |
| Duplicate crossposts | Before every crosspost, Phase 3 re-runs `get_posts_with_same_content()`. That is a Reddit search for the submission URL in the target (subject to search-index lag of minutes) plus Reddit's "other discussions" of the source post, a search for the same title, and a scan of every image (or video) post the target got since the source comment was written (a live listing, so it catches a crosspost made seconds earlier, but only for **image (including gallery) and video** posts, galleries by their first 10 pictures, crossposts through their original post, videos only by preview frame, and in busy subreddits only as far back as Reddit's ~1000-post listing reaches). The remaining gap is link and text posts whose title was changed and that are not linked as "other discussions", when two rows for the same submission and target are handled in one Phase 3 run, or after a crash between `crosspost()` and the row delete. The owner considers this acceptable, and Reddit may also reject duplicates server-side (unverified). An `ALREADY_SUB`-style rejection is not in `familiar_error_types`, so it would be logged as unexpected. If the reply fails after a successful crosspost, the crosspost is reported as failed. |
| Negative-score cleanup | Deletes `AutoCrosspostBot` submissions with `score < 0`. The owner reports that submission scores can go negative; this was not independently confirmed. |
| Phase 1 backlog | Phase 1 (duplicate search, up to 200 preview downloads per match, subreddit suggestions) runs on `PHASE1_WORKERS` threads. If matches arrive faster than they can handle for long enough to fill the 1000-ID queue, new matches are dropped; the heartbeat counts them. Not observed; matches are a small fraction of `r/all`. |
| Comment coverage | `STREAM_SOURCE=listing` reads at most 100 comments per request, one request at a time. On 2026-10-15/16 that was about 850 comments a minute by day and up to 2,500 at night, when Reddit answered faster, so it missed most of r/all. The `ids` reader was written for that; it relies on Reddit numbering comments in order site-wide, which `test_live_comment_reader_reads_recent_comments_by_id` checks before every deploy, and its real coverage is still to be confirmed from the heartbeat. |
| Re-reads after a crash | The comment cursor is saved every 30 s and at shutdown. After a crash or `kill -9`, up to 30 s of comments are read again, so an auxiliary bot could reply twice to a comment from those seconds. |
| Opted-out subreddits | `/api/info` does not say whether a subreddit opted out of r/all, so the `ids` reader also reads comments in subreddits that did (NSFW, quarantined, private and profile comments are dropped). |
| Rotated reposts | Not detected as duplicates (see the image-matching note in section 5). |
| Edited video reposts | Videos are compared by their preview frame only. A trimmed, re-cut or re-encoded copy usually has a different preview frame and is missed. |
| Pre-v2.3 rows | Have `target_subreddit = NULL`, so the bait-and-switch check is skipped for them until they age out (at most one Phase 3 waiting period after upgrading). |
| Already-consumed column slots | Deployments that ran the pre-v2.3 schema script keep the attribute numbers it used up (section 6). |

---

## 10. Coding & Contribution Rules for Agents

1. **Preserve Comments & Docstrings:** Do not remove existing comments, docstrings, or developer notes during edits.
2. **Follow Existing Style:** Use clean, standard Python style matching the codebase (PEP 8 conventions, explicit error handling with `prawcore.exceptions` and `praw.exceptions.RedditAPIException`).
3. **Graceful Reddit Exception Handling:** Any newly introduced Reddit API calls must handle potential exceptions (`Forbidden`, `ServerError`, `RedditAPIException`, sub bans, locked threads, deleted comments) without crashing the main loop.
4. **Localization Awareness:** When adding or modifying bot comments, update the `.po` catalog of every locale under [`src/racb/locales/`](./src/racb/locales/) (message ids are symbolic keys; `en` holds the source text, and an empty `msgstr` falls back to English, but a unit test requires every locale to translate every key). Do not commit `.mo` files: they are git-ignored, the Dockerfile compiles them into the image, and `tests/conftest.py` recompiles them before every test run. To run the bot locally outside Docker, run `uv run pybabel compile -d src/racb/locales` first; the bot refuses to start if a catalog is not compiled. Counts use plural forms (`ngettext`, plural id `<KEY>_PLURAL`). To add a locale, create its `.po` (e.g. `uv run pybabel init -i src/racb/locales/en/LC_MESSAGES/messages.po -d src/racb/locales -l <locale>`) and list its subreddits in [`subreddits.toml`](./src/racb/locales/subreddits.toml).
5. **Mandatory Changelog Fragments, No Manual Version Bumps:** Every code, configuration, or infrastructure change MUST add a changelog fragment, a new file `changelog.d/+<slug>.<type>.md` (`<type>` is `added`, `changed`, `deprecated`, `removed`, `fixed` or `security`), in the same branch; see [`changelog.d/README.md`](./changelog.d/README.md) for the format. **Never edit [`CHANGELOG.md`](./CHANGELOG.md) directly**: the CI `release` job compiles the fragments into it, and direct edits are what made every merge conflict with every other open PR. A change with nothing worth telling users or deployers (a test or docs fix) needs none, but then its merge makes no release. **Never bump the version or create a tag yourself**, on a feature branch or on `master`: the CI `release` job does it after merge (section 5). Parallel branches would otherwise claim the same version number. Use Conventional Commit subjects, since they choose the bump (`feat` minor, `!` major, anything else patch).
6. **Never run live integration tests casually:** plain `pytest` runs only unit tests. `pytest -m integration` posts publicly as the real bot accounts.
7. **Keep section 9 current:** when you fix a known defect, delete its row; when you find a new one, add it.
8. **Read configuration through `src/racb/config.py`:** do not add new `os.environ` reads or boolean/duration parsers elsewhere.
9. **Every new Reddit write must respect `DRY_RUN`** (see `phase1.reply_as_bot` for the pattern).
