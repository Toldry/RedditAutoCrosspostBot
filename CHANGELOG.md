# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
