# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Module-Level Loggers:** Replaced root logging with dedicated module loggers (`racb.phases.phase1`, `racb.core.db`, etc.) across all components.
- **Stream Heartbeat Metrics:** Added 10-minute heartbeat reporting scanned comment throughput, saved recommendations, and auxiliary bot metrics.
- **Persistent Log Storage:** Standardized log output to `logs/app.log` with 5 x 5MB rotation, mounting to Docker volume `racb_bot_logs`.
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
