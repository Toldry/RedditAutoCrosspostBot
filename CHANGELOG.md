# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.4.7] - 2026-10-15

### Fixed
- **Auto-Deploy Setup on a Fresh Project:** `scripts/setup_auto_deploy.sh` failed with "Service account ... does not exist" when granting roles right after creating the `github-deployer` service account, because IAM takes a short while to see a new account. It now retries those grants for up to 2 minutes.

## [2.4.6] - 2026-10-15

### Added
- **Automatic Deployment:** A new `deploy` CI job rebuilds and restarts the bot on the Google Cloud VM after every push to `master` that passes the unit tests, the live integration tests and their cleanup, at the commit the `release` job ended on. The live tests run in a new `integration-tests` job with the bot credentials stored as repository secrets, followed by `scripts/cleanup_test_history.py --strict`, which removes leftover test posts and fails if any could not be deleted. If `master` moved on during a run, that run deploys nothing and the newer push's run deploys instead. It signs in to Google Cloud with Workload Identity Federation (no stored key), reaches the VM over an IAP tunnel, and runs the new `scripts/redeploy.sh` through an SSH key that can run nothing else. The script checks that the bot is still running 30 seconds later and fails the job otherwise. It stays off until the `AUTO_DEPLOY` repository variable is `true`; the one-time setup (`scripts/setup_auto_deploy.sh`, `scripts/authorize_deploy_key.sh`) is described in `docs/auto-deploy.md`.
- **Strict Test Cleanup:** `scripts/cleanup_test_history.py --strict` exits with code 1 when a bot could not be checked or a test item could not be deleted, and any such problems are now listed at the end of every run.

## [2.4.5] - 2026-10-15

### Fixed
- **Mixed-language "subreddit doesn't exist" replies:** German subreddits got "Den Subreddit r/… gibt es nicht. Maybe there's a typo?" because `MAYBE_TYPO` and `ALTERNATE_SUBS_SUGGESTION` had no German translation and each sentence fell back to English on its own. Both are now translated, the German creation prompt says "ihn" (der Subreddit) instead of "es", and the reply now picks one language for all of its sentences and its footer, switching entirely to English when any piece is untranslated.

## [2.4.4] - 2026-10-15

### Fixed
- **Deleting Posts and Comments:** Every delete failed with `404 HTTP response`, because Reddit no longer routes PRAW's delete URL (`POST /api/del/`, with a trailing slash) and answers it with a plain-text "404 page not found". `racb.core.reddit` now points PRAW at `/api/del`, which works. This fixes `scripts/cleanup_test_history.py`, the scheduled removal of negative-score crossposts, and the integration tests' own cleanup.

## [2.4.3] - 2026-10-14

### Fixed
- **Test History Cleanup 404s:** When Reddit answers 404 to deleting an item, `scripts/cleanup_test_history.py` now says why instead of only logging the error: it re-fetches the item (an item that turns out to be deleted already now counts as deleted), checks whether the bot can still open the item's subreddit (banned, or private without approved-user access), and checks once per bot whether logged-out readers can see the account (a 404 there means shadowbanned or suspended). The live integration tests now log failed teardown deletes instead of silently ignoring them, which is how test posts piled up unnoticed.

## [2.4.2] - 2026-10-14

### Changed
- **Clickable Log Links:** Log lines that mention a comment or submission now print its full URL (`https://reddit.com/r/.../comments/...`) instead of the bare permalink path, so it can be opened straight from the log. This also fixes the doubled slash in the "Qualified recommendation match found" line.

## [2.4.1] - 2026-10-14

### Changed
- **Shorter Crosspost Reply:** The reply on each crosspost no longer says how long the bot waited before crossposting, no longer asks readers to downvote the post if it was a mistake, and drops the "This value was chosen arbitrarily" footnote, in every language. Negative-score crossposts are still removed.

## [2.4.0] - 2026-10-14

### Added
- **Single-Entry Runs:** `python -m racb --process-one` runs Phase 3 on the oldest row in `scraped_comments` only and exits, for testing the bot in production one crosspost at a time. The row is processed even if it is not due yet. Combine with `DRY_RUN=True` to only log. Adds the `get_oldest_scraped_comment` database function.
- **gettext Translations:** Bot replies are now translated with standard gettext catalogs (`src/racb/locales/<locale>/LC_MESSAGES/messages.po`, compiled to committed `.mo` files with Babel) instead of a hardcoded dictionary in `i18n.py`. The r/totallynotrobots style is now the `en@totallynotrobots` locale, an English variant that falls back to English. The subreddit-to-language map moved to `src/racb/locales/subreddits.toml`. All existing replies are unchanged, apart from the plural fix below.

### Changed
- **Automatic Releases:** Nobody bumps the version by hand anymore. After the tests pass on `master`, a new `release` CI job picks the bump from the Conventional Commits since the last tag (`feat` minor, `!` or `BREAKING CHANGE:` major, anything else patch; see `scripts/release_level.py`), then runs `bump-my-version`, which moves these `[Unreleased]` entries into a dated section, commits and pushes a `vX.Y.Z` tag. It skips the release when `[Unreleased]` is empty.
- **Dependabot Paused:** `.github/dependabot.yml` now sets `open-pull-requests-limit: 0`, so Dependabot no longer opens version-update pull requests. Dependencies are upgraded manually with `uv lock --upgrade-package`. Remove those lines to resume.
- **Bot Footer:** Removed the "feel welcome to respond 'Bad bot'/'Good bot'" line in every language, and the Rank link now points to `https://www.botrank.net/bots/<bot name>` instead of botranks.com.

### Fixed
- **Plural Day Counts:** Durations use plural forms, so a 1-day waiting period reads "1 day" instead of "1 days" (and "יום אחד" / "יומיים" for 1 and 2 days in Hebrew).
- **Merged Subreddit Names:** Missing commas in the old language map had glued German subreddit names together (`GermanAustriaberlin`, `zurichEasy_German`, `kreiswichsFragReddit`, `borussiadortmundwien`), so none of them matched. r/Austria, r/berlin, r/zurich, r/kreiswichs, r/FragReddit, r/borussiadortmund and r/wien now get German replies. r/German and r/Easy_German stay English, since they are for learners.

## [2.3.0] - 2026-10-15

### Fixed
- **Database Schema Time Bomb:** `instantiate_db.sql` re-added and dropped the legacy `score_checked` column on every database connection. PostgreSQL never reuses dropped column slots, so the table would have stopped accepting the script after ~1595 connections and bricked the bot. The migration is now idempotent; verified against PostgreSQL 16 with fresh and legacy databases.
- **Bait-and-Switch Crossposts:** The target subreddit is now stored at scrape time (new `target_subreddit` column). Phases 2 and 3 reject comments that were edited to point at a different subreddit (`TARGET_SUBREDDIT_CHANGED`). Rows scraped before this release skip the check.
- **Stream Disruption on Per-Comment Errors:** Errors while handling a single comment (e.g. `403 Forbidden` because an auxiliary bot is banned from a subreddit) now skip only that comment. Previously they caused a 30-second sleep and a stream reset that dropped comments, or crashed the process.
- **Exception Matching:** Main-loop error handling now uses `isinstance`, and handles `TooManyRequests` (60-second back-off) and database connection errors (reconnect) instead of crashing.
- **Failing Scheduled Jobs:** Jobs are wrapped so an exception is logged and the job is rescheduled normally. Previously `schedule` left a failing job overdue, so it re-ran on every loop iteration.
- **Waiting Period Parsing:** `PHASE3_WAITING_PERIOD=3 months` only worked through a silent 90-day fallback, because `pytimeparse` does not support months; `6 months` also became 90 days. Months and years are now supported, unparseable values log a warning, and `PHASE2_WAITING_PERIOD=0 seconds` is no longer turned into 2 hours.
- **`.env` Load Order:** `.env` is now loaded before logging is configured, so `DEBUG` and `LOG_DIR` from `.env` apply outside Docker.
- **Nonexistent Subreddit Detection:** A search redirect to `/subreddits/search` is now recognized as a nonexistent subreddit. It was previously treated as "exists, no duplicates", and the code meant to handle it was unreachable.
- **Same-Template Meme False Positives:** Duplicate image matching now also requires every cell of a 4×4 region-hash grid to match, so different memes made from the same template no longer count as duplicates. Rotated reposts are no longer matched either.
- **Crosspost Reply Text:** Removed the false claim that RepostSleuth was checked (that integration was removed in 2.2.1). The waiting period is now shown as a localized number of days instead of the raw English config value.
- **Translations:** Fixed Markdown links broken by `] (` (es/de/fr), an unclosed superscript in the Spanish footer, Hebrew footnote numbering and typos, and German machine-translation calques (*Pfosten*, *gekreuzt*, *Das Unter*). Added the missing "waited N days" sentence to es/de/fr.
- **Mixed-Language Replies:** `sub_doesnt_exist_bot` built its footer in the (nonexistent) target subreddit's language instead of the source subreddit's.
- **Stale Transaction Timestamps:** Database connections now use autocommit, so read calls no longer leave the session idle in a transaction and inserts are stamped with the actual insert time.
- **Index Use:** Age queries compare `scraped_time` against a constant, so the `scraped_time` index is used.
- **AGENTS.md Diagram:** The lifecycle Mermaid diagram failed to render on GitHub, because an unquoted edge label contained parentheses (`reddit.info()`).

### Added
- **`DRY_RUN` Mode:** Logs auxiliary replies, crossposts and cleanup deletions instead of performing them. Phase 3 keeps the rows it would have crossposted.
- **Inbox Feedback for All Bots:** A scheduled job reads the unread inboxes of all 4 bots every 10 minutes and logs good/bad bot feedback. It replaces the continuous `AutoCrosspostBot`-only inbox stream, which polled without pause and shared the request budget with the `r/all` stream.
- **`racb.config` Module:** Single place for environment parsing (booleans, durations, flags); it replaces three inconsistent boolean parsers.
- **CI & Dependabot:** GitHub Actions workflow running the unit tests on Python 3.11, and monthly Dependabot updates. `.github/` is now allowed by `.gitignore`.
- **README Test & Dependency Instructions:** How to set up the environment with uv, run the unit tests, manage dependencies, and what the live integration tests do.
- **Unit Tests:** 33 new tests covering the fixes above (94 unit tests in total).

### Changed
- **Dependency Management with uv:** `requirements.txt` and `requirements-dev.txt` are replaced by `pyproject.toml` and a cross-platform `uv.lock`. The lock pins every package, including transitive ones such as numpy and scipy that were previously unpinned, at the tested versions (PRAW 8.0.3 / prawcore 4.0.0, which a fresh install had already resolved). `racb` is now an installable package, so `PYTHONPATH=src` is no longer needed. The Docker image installs from the lock into `/opt/venv` without dev dependencies, CI uses `uv sync --locked`, and Dependabot updates the lock. A new `.dockerignore` keeps local virtualenvs and caches out of the image (`.env` is still included).
- **Python Version:** `requires-python = ">=3.11"`, recorded in `.python-version`. The README previously claimed 3.10+, which was never tested.
- **Default Test Selection:** A bare `pytest` no longer runs the live Reddit integration tests; use `pytest -m integration`.
- **Integration Test Cleanup:** Integration tests delete what they create again by default; set `RACB_KEEP_TEST_ARTIFACTS=true` to keep it. The live image test no longer expects rotated images to match.
- **`DEBUG` Mode:** At startup it now runs only Phase 2 immediately, instead of every job including real Phase 3 crossposts and cleanup deletions. It no longer disables TLS for remote databases.
- **`--help`:** No longer requires authenticating all 4 Reddit accounts.
- **Image Downloads:** Capped at 20 MB, and sent with an honest `RACB/<version>` User-Agent instead of a spoofed browser string.
- **Release Rule:** Version bumps and tags now happen on `master` after merge, not on feature branches.
- **AGENTS.md Audit:** Corrected inaccurate descriptions (Phase 1 decision order, Phase 2 batching savings, inbox/cleanup behavior, rate-limit semantics) and documented previously missing topics: all environment variables with code defaults and parsing caveats, `DEBUG`/`LISTEN_ONLY` side effects, database schema, scheduler and exception-handling semantics, test suite usage (unit vs. live integration), cleanup script, logging, release mechanics, repository/deployment gotchas, and a verified known-defects list.

## [2.2.4] - 2026-09-05

### Added
- **Bot Startup Authentication Verification:** Added `authenticate_all_bots()` in `racb.core.reddit` and integrated it into `main()` in `racb.main` to verify active Reddit API authentication and account identity for all 4 bot accounts (`AutoCrosspostBot`, `sub_doesnt_exist_bot`, `same_subreddit_bot`, `same_post_bot`) at startup, immediately halting execution with an explicit error message if any credentials fail.
- **Bot Authentication Unit & Integration Tests:** Added automated unit tests in `test_reddit_core.py` and `test_main.py` covering successful startup authentication, credential errors, missing accounts, and identity mismatches, as well as live API integration tests in `test_reddit_integration.py`.

## [2.2.3] - 2026-09-04

### Added
- **Test Artifact Cleanup Script:** Added `scripts/cleanup_test_history.py` to query the submission and comment histories of all four bot accounts (`AutoCrosspostBot`, `sub_doesnt_exist_bot`, `same_subreddit_bot`, `same_post_bot`) and safely purge any lingering test posts and comments.

### Changed
- **Integration Test Comment Cleanup & Bypass Flag:** Updated `tests/integration/test_reddit_integration.py` to track and delete all created comments and bot replies upon test completion alongside submissions, and added `DISABLE_CLEANUP` flag to enable preserving Reddit posts and comments for debugging.


## [2.2.2] - 2026-09-03

### Changed
- **Exposed Database Port to Localhost:** Bound PostgreSQL port 5432 to `127.0.0.1` in `docker-compose.yml` to support secure SSH tunneling for external GUI clients (pgAdmin 4, DBeaver) without public internet exposure.

## [2.2.1] - 2026-09-03

### Added
- **Self-Hosted Perceptual Image Hashing:** Replaced deprecated RepostSleuth API with self-hosted perceptual image hashing (`imagehash` / `dhash`) in `duplicate_detector.py` to identify duplicate image submissions across target subreddits.
- **Image Variation Testing:** Added repository test asset `tests/assets/test_sample.png` and an automated variation generator testing 4 distinct visual modifications (resize, watermark pixels, brightness adjustment, rotation) matching within `DEFAULT_HASH_THRESHOLD = 5`.
- **3-Tier Candidate Retrieval:** Added fallback chain in `duplicate_detector.py` (`target_sub.new()`, `target_sub.hot()`, and user submission query for private test subreddits).
- **Dependencies:** Added `imagehash>=4.3.0` and `Pillow>=10.0.0` to `requirements.txt`.


## [2.2.0] - 2026-09-03

### Added
- **Comprehensive Unit Test Suite:** 46 automated unit tests under `tests/unit/` testing constants, i18n, reddit core, duplicate detector, sub search, db operations, phases (1, 2, 3), cleanup, inbox, and main loop.
- **Live Reddit API Integration Tests:** 7 end-to-end integration tests under `tests/integration/` verifying bot authentication, permissions, auxiliary bot triggers (`same_subreddit_bot`, `sub_doesnt_exist_bot`, `same_post_bot`), graceful private crosspost handling, and cleanup query.
- **Test Discovery & Configuration:** Added `pytest.ini` with test markers (`unit`, `integration`) and updated `requirements-dev.txt` with `pytest` and `pytest-mock`.

### Changed
- **Test Subreddits in Blacklist:** Added `racb_test_1` and `racb_test_2` to `SUB_BLACKLIST` in `constants.py` to ensure the production bot ignores testing activities on `r/all`.
- **Lazy Database Initialization:** Refactored `racb.core.db` to connect on demand rather than at module import time, facilitating clean unit testing without a live database.
- **Phase 3 Exception Handling:** Added `PRIVATE_SUBREDDIT_CROSSPOST` to `familiar_error_types` in `phase3.py` for graceful crosspost rejection.
- **Phase 1 Reply Returns:** Updated Phase 1 reply helper functions to return created comment reply objects.
- **Case-Insensitive Sub Search Deduplication:** Enhanced `sub_search.get_matches` to deduplicate community search results case-insensitively.

## [2.1.2] - 2026-09-03

### Changed
- **Docker Container & Volume Names:** Renamed container name from `racb_bot` to `racb` and logs volume from `racb_bot_logs` to `racb_logs` to eliminate redundant naming.

## [2.1.1] - 2026-09-03

### Added
- **Module-Level Loggers:** Replaced root logging with dedicated module loggers (`racb.phases.phase1`, `racb.core.db`, etc.) across all components.
- **Stream Heartbeat Metrics:** Added 10-minute heartbeat reporting scanned comment throughput, saved recommendations, and auxiliary bot metrics.
- **Persistent Log Storage:** Standardized log output to `logs/app.log` with 5 x 5MB rotation, mounting to Docker volume `racb_logs`.
- **Aligned ISO Log Formatting:** Standardized timestamps and aligned log levels (`YYYY-MM-DD HH:MM:SS [INFO    ] module: msg`).

## [2.1.0] - 2026-09-03

### Added
- **Modern `src/racb` Package Structure:** Restructured codebase into standard Python package layout with `phases/`, `core/`, `sql/`, and `__main__.py` entrypoint (`python -m racb`).
- **Declarative GCP Provisioning:** Added `deploy/instance-flags.yaml` for 1-command VM creation via `gcloud --flags-file`.
- **Shared Multi-User Directory Permissions:** Configured `/opt/RedditAutoCrosspostBot` for seamless group collaboration between local SSH and GCP Web SSH users.

### Changed
- Consolidated cloud deployment configuration into universal `deploy/cloud-init.yaml`.
- Updated documentation across `README.md` and `AGENTS.md`.

### Removed
- Deleted redundant `deploy/startup-script.sh` in favor of declarative `deploy/cloud-init.yaml`.
- Removed defunct `Procfile` and root-level scripts.

## [2.0.0] - 2026-09-03

### Added
- **GCP VM Deployment & Containerization:** Full multi-container Docker Compose stack (`Dockerfile`, `docker-compose.yml`, `cloud-init.yaml`, `startup-script.sh`).
- **PostgreSQL Database Backend:** Switched to persistent PostgreSQL with stored procedures (`instantiate_db.sql`, `racb_db.py`) for efficient batch processing.
- **Batch Processing in Phase 2:** `phase2_handler.py` now fetches up to 100 comments at a time via `reddit.info()`, cutting Reddit API requests by 99%.
- **Native Rate Limit Handling:** Integrated native PRAW rate limiting with `ratelimit_seconds=300`.
- **Duplicate & Typo Detection:** RepostSleuth reverse search integration in `repost_detector.py` and live subreddit search in `sub_name_string_match.py`.
- **Semantic Versioning & Tooling:** Automated version bumping via `bump-my-version`, `_version.py`, `.bumpversion.toml`, and `requirements-dev.txt`.

### Changed
- Migrated from legacy Heroku hosting to Google Cloud Platform e2-micro Always Free VM.
- Upgraded Python dependencies including `praw>=7.7.1`, `psycopg2-binary`, and modern typing.
- Centralized application version dynamically in `reddit_instantiator.py` User-Agent strings.

### Removed
- Deprecated legacy Heroku `Procfile` and static CSV subreddit caches.

---

## [1.0.0] - 2023-10-23

### Added
- Initial implementation of `AutoCrosspostBot` suite on Heroku.
- Phase 1 stream monitoring, Phase 2 filtering, Phase 3 crossposting.
- Auxiliary bots: `sub_doesnt_exist_bot`, `same_subreddit_bot`, `same_post_bot`.
- Localization support in `my_i18n.py`.
