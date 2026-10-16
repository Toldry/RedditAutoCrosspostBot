# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Per-Bot Switches Replace `LISTEN_ONLY`:** each bot account can now be turned off on its own with `AUTO_CROSSPOST_BOT_ENABLED`, `SUB_DOESNT_EXIST_BOT_ENABLED`, `SAME_SUBREDDIT_BOT_ENABLED` and `SAME_POST_BOT_ENABLED` (all `True` by default). A disabled bot never comments, posts or deletes, and its inbox is left unread; turning off `AutoCrosspostBot` also stops crossposting and the negative-score cleanup. With all four off, the bot only reads r/all and stores matching comments. `LISTEN_ONLY` is removed and ignored: an old `.env` with `LISTEN_ONLY=True` must switch to `AUTO_CROSSPOST_BOT_ENABLED=False`, or the bot starts crossposting.
- **Leaner `--process-one`:** it now authenticates only `AutoCrosspostBot`, the one account Phase 3 uses, and skips everything else at startup, so it no longer needs working credentials for the other 3 bots.
- **Multithreaded Bot:** the bot ran on a single thread, so while Phase 2, Phase 3, the inbox pass, the cleanup or a Phase 1 duplicate search ran, nobody read the r/all comment stream, and since PRAW's stream only reads the newest 100 comments when it catches up, comments posted meanwhile were lost. Now the main thread only reads the stream, every scheduled job runs on a thread of its own, and Phase 1 runs on a pool of worker threads (`PHASE1_WORKERS`, default 4) that get matching comments through a queue. Each thread has its own PRAW instances and database connection, since neither PRAW nor a shared connection is safe across threads. Phase 2 and Phase 3 still never run at the same time. Log lines now name their thread, and the heartbeat reports matches dropped because the Phase 1 queue was full.
- **Graceful Shutdown:** `docker stop` (SIGTERM) and Ctrl-C now stop every thread cleanly: Phase 2 and Phase 3 stop between entries instead of being killed mid-entry, and back-off waits end at once. The bot service gets `stop_grace_period: 60s` in `docker-compose.yml`, so Docker waits for it.
- **`schedule` Dependency Removed:** the job threads replace the `schedule` library.

### Removed
- **`--only-phase2` Flag:** it was added for a Heroku process type that no longer exists, and nothing uses it any more. Phase 2 still runs every 20 minutes.

## [2.14.3] - 2026-10-16

### Fixed
- **Gallery Posts in the Image Duplicate Scan:** gallery posts were not treated as image posts, so the scan skipped them, never compared with them, and left them out of the "I also looked through all N image posts" count under a crosspost. A gallery now counts as an image post: up to its first 10 pictures are fingerprinted, and it matches when any of its pictures matches. A gallery source post (or a crosspost of a gallery) is now scanned too.
- **Crossposts in the Image Duplicate Scan:** a crosspost's own `url` is often just the original post's permalink, with no preview, so crossposts of images, galleries and videos were skipped and left out of the count. The scan now reads a crosspost's media from the original post (`crosspost_parent_list`), which also lets a crosspost of the very same image count as a duplicate.
- **Unreadable Previews:** when a post's preview image could not be downloaded or read, the post was silently left out of the scan and of the count. The full image is now tried instead, and a scan that still could not read some posts logs a `WARNING` with how many.

## [2.14.2] - 2026-10-16

### Changed
- **Dead-Source Log Detail:** when a source post is skipped as `SOURCE_DELETED_OR_REMOVED`, the log now names the field Reddit reported (`removed_by_category` or `[deleted]`/`[removed]` self text) and its value, so a post that looks live in a browser can be checked.

### Fixed
- **Banned From the Target Subreddit:** when `AutoCrosspostBot` was banned from the target subreddit, Phase 3 still ran the full duplicate scan, and the crosspost then failed with `SUBREDDIT_NOTALLOWED_BANNED`, logged as an unexpected `ERROR` with a traceback. Phases 2 and 3 now check the subreddit's `user_is_banned` before the duplicate scan and drop the row (`BANNED_FROM_TARGET_SUBREDDIT`), and a crosspost rejected with `SUBREDDIT_NOTALLOWED_BANNED` is logged as a graceful rejection.

## [2.14.1] - 2026-10-16

### Changed
- **Single-Entry Runs in the README:** the README now documents `--process-one`, with commands for a local run, inside the Docker stack, and from your own computer over SSH to the VM.

### Fixed
- **Crosspost Reply Day Count:** the reply under a crosspost said the source comment was written, and that the bot waited, the configured `PHASE3_WAITING_PERIOD` (e.g. "90 days") before crossposting, even when less time had passed, as with `--process-one`, which skips the wait. It now gives the number of days since the comment was actually written, in every language.

## [2.14.0] - 2026-10-16

### Added
- **Reply Logging:** every comment a bot posts is now logged after it is posted, with the new comment's full URL: `/u/<bot> replied to <user> suggestion (r/<source> -> r/<target>): <reply URL>` for replies about a subreddit suggestion, and `/u/<bot> replied to <user> feedback (r/<subreddit>): <reply URL>` for answers to "good bot"/"bad bot". Before, the explanatory reply under each crosspost and the replies to "good bot"/"bad bot" feedback were not logged at all, and the Phase 1 auxiliary replies were only announced before posting, without the reply's own URL.

### Fixed
- **Banned Subreddits:** a suggestion of a banned subreddit (such as r/jar) got a `sub_doesnt_exist_bot` reply saying the subreddit does not exist and inviting the reader to create it, because Reddit answers 404 for banned subreddits too. A 404 whose reason is `banned` is now `SUBREDDIT_IS_BANNED` and gets no reply.
- **Inbox Comment Links:** comments from an inbox listing have no `permalink`, only a `context` link, so feedback log lines showed `?` instead of a URL, and the "Not thanking" line (a second "good bot" under the same bot comment) and the `DRY_RUN` reply line raised `AttributeError`, which aborted that inbox pass and left the comment unread. They now use the `context` link.

## [2.13.0] - 2026-10-16

### Added
- **"Other Discussions" Check:** the duplicate check now also asks Reddit for the source post's "other discussions" (`submission.duplicates()`, one call) and treats any of them in the target subreddit, such as an earlier crosspost of the same post, as a duplicate. This runs in all three phases.
- **Title Search:** Phases 2 and 3 search the target for a post with the same title (`title:"..."`, compared ignoring case and punctuation), which catches reposts of videos and text posts that keep the title. Titles shorter than 4 words are not searched.
- **Top Posts Before Crossposting:** Phase 3's full scan now also compares an image or video with the target's 100 top posts of all time and 100 top posts of the past year. The crosspost reply still counts only the posts made since the source comment (`RepostScan.top_posts_checked`).
- **Dead Source Posts Are Skipped:** Phases 2 and 3 drop a row whose source post was deleted or removed (`SOURCE_DELETED_OR_REMOVED`), locked (`SOURCE_LOCKED`), archived (`SOURCE_ARCHIVED`) or cannot be read (`SOURCE_UNAVAILABLE`).

### Fixed
- **Case-Insensitive Blacklist:** `SUB_BLACKLIST` entries with capital letters (`ReviewsByRetards`) never matched, because only the subreddit names were lowercased. It is now compared case-insensitively through `constants.is_crosspost_blocked`, and Phases 2 and 3 apply it too, so rows scraped before a subreddit was blacklisted are dropped (`CROSSPOST_BLOCKED`).

## [2.12.1] - 2026-10-15

### Fixed
- **Cloud Logging Queries:** the queries in `docs/logs.md` found nothing when typed into the Logs Explorer's search bar, which turns unquoted text into a full-text search. They now use `log_id("gcplogs-docker-driver")` in the query editor, with quoted values.

## [2.12.0] - 2026-10-15

### Added
- **Cloud Logging:** `docker-compose.gcp.yml` sends the bot's logs to Google Cloud Logging, so they can be read and searched in the Cloud Console without SSH. Off until `COMPOSE_FILE=docker-compose.yml:docker-compose.gcp.yml` is added to the VM's `.env`; see `docs/logs.md`.

### Changed
- **Container Log Rotation:** the bot and database containers keep at most 3 Docker log files of 10 MB each. Docker's default never rotated them, so they grew until the VM's disk filled.

## [2.11.1] - 2026-10-15

### Fixed
- **Live Duplicate-Detector Test:** `test_live_duplicate_detector_query` failed since 2.10.0 for the resized image. Its target post was the mostly transparent test asset, while the variations dropped the alpha channel and so showed the colours hidden under the transparency. That only matched while both sides were compared as full images; now that Reddit's preview images are compared, it cannot. The test now posts an opaque copy of the asset as the target.
- **Failed Image Downloads Were Cached:** a preview or image that could not be downloaded (a timeout, or a preview Reddit had not made yet) was remembered as unreadable until it fell out of the fingerprint cache. Only successful fingerprints are cached now, so a later check downloads it again.

## [2.11.0] - 2026-10-15

### Added
- **Video Repost Scan Before Crossposting:** video posts (Reddit-hosted, YouTube and Streamable) are now compared with the target subreddit's video posts since the source comment was written, the same way images are, by perceptual hashing of Reddit's preview frame. The crosspost reply gets a matching sentence in every language ("I also looked through all N video posts made in r/<target> in the last D days and checked for duplicates of this video. None were found."). A trimmed or re-edited copy usually has a different preview frame and is missed.

- **Wider Quick Duplicate Check:** the check made when a comment is first seen (the one `u/same_post_bot` answers with, also re-run by Phase 2) now compares an image or video with the target subreddit's 100 newest posts instead of 50, plus its 100 top posts of all time, where famous reposts usually come from. Phase 3's full scan is unchanged.

### Changed
- **Shorter Cut-Short Scan Sentence:** when Reddit's listing ends before the scan reaches the source comment's date, the crosspost reply no longer adds "Reddit doesn't list older posts, so I couldn't check further back." (in any language); it still says how many of the newest posts were checked and how far back they go. The singular wording for a cut-short scan that found exactly one image or video post ("the newest image post ... It doesn't.") is gone too; that rare case now reads "the 1 newest image posts". A scan that found no image (or video) posts at all adds no sentence; `REPLY_TO_CROSSPOST_NO_IMAGE_POSTS` is gone.
- **Duplicate Detector Renames:** `scan_subreddit_for_image` is now `scan_subreddit_for_duplicates`, `ImageRepostScan` is now `RepostScan` (with a `media` field), and `phase2.run_filters(full_image_scan=...)` is now `full_media_scan`.

## [2.10.0] - 2026-10-15

### Changed
- **Crosspost Reply Notes:** the crosspost reply now explains how reposts were ruled out ("To prevent reposts, I looked through r/<target> for posts with the same URL."), again says the bot waited (the Phase 3 waiting period) before crossposting, and asks readers who think the crosspost is a mistake to reply "Bad bot" (in their language) with an explanation. Only footnote 1 ("Assuming /u/<user> is human") remains.
- **Full Image Repost Scan Before Crossposting:** for an image post, Phase 3 now compares the image with every image post the target subreddit got since the source comment was written, instead of only the 50 newest, as far back as Reddit's ~1000-post listing reaches. The reply says how many image posts were checked and over what period, links to `duplicate_detector.py`, and says so when Reddit's listing ended first. Images are compared through Reddit's small previews, 8 downloads at a time, to keep the scan cheap on the free VM. `duplicate_detector.get_reposts_in_sub` is replaced by `scan_subreddit_for_image`, which returns an `ImageRepostScan`, and the module is reorganized and documented for outside readers.
- **"Bad Bot" In Every Language:** the inbox recognizes "bad bot" / "good bot" in every supported language (`BAD_BOT_PHRASE` / `GOOD_BOT_PHRASE` in each catalog, e.g. "Schlechter Bot", "Плохой бот", "Bad human" on r/totallynotrobots), not just English. A "bad bot" followed by an explanation, as the crosspost reply asks for, is not answered (the bot would otherwise ask why); the negative-feedback report now finds such replies through the inbox.
- **Reply Phrasing Tests:** `tests/unit/test_crosspost_reply_phrasings.py` renders the crosspost reply for every combination of locale, author (present or deleted), waiting-period plural form and image-scan outcome (700 in all) and checks placeholders, footnotes, plural forms, links, markdown and language consistency.

## [2.9.1] - 2026-10-15

### Fixed
- **"Human-Made" Footnote Restored:** the crosspost reply again calls the source comment **human-made**^1, with the footnote "1 - Assuming /u/<user> is human". English lost it in 2021 when the sentence was reworded; German, Spanish, French and Russian now have it too (Hebrew and r/totallynotrobots always kept it).

## [2.9.0] - 2026-10-15

### Added
- **Database Web Viewer:** an optional `pgweb` service (Docker Compose profile `viewer`) shows the database in a browser. It is read-only, asks for the password set in `PGWEB_AUTH_PASS` (and refuses to start without one), and listens on the VM's `127.0.0.1:8081` only, so it is reached through an SSH tunnel over IAP. Automatic deploys neither start nor stop it. See `docs/db-viewer.md`.

### Changed
- **Compiled Catalogs Are No Longer Committed:** the gettext `.mo` files are git-ignored and built from the `.po` sources instead: the Dockerfile compiles them with Babel in a throwaway environment (Babel moves to a new `i18n` dependency group and stays out of the image), and `tests/conftest.py` compiles them before every test run. Editing a `.po` file can no longer leave a stale `.mo` behind, which is what turned master red after the "github -> GitHub" footer change.
- **Missing Catalogs Fail Loudly:** a locale without a compiled `.mo` used to fall back silently to an empty catalog, so the bot would have posted raw message keys such as `POST_SUFFIX_TEXT`. Loading it now raises `CatalogNotCompiledError`, and the bot checks every catalog at startup and exits if one is missing.

### Fixed
- **Comment Footer Link:** the footer of every bot comment now reads "GitHub" instead of "github".

## [2.8.0] - 2026-10-15

### Fixed
- **Irrelevant Subreddit Suggestions:** `u/sub_doesnt_exist_bot` suggested unrelated subreddits (e.g. r/nocontext for r/wtfdidijustread) because Reddit's `/subreddits/search` matches on a subreddit's topic, not its name. `sub_search.get_matches()` now keeps only results whose name is at least 60% similar to the requested one (`difflib`), ranked most similar first, and looks at 25 results instead of 10. If none is similar enough, the reply falls back to the "maybe a typo" text.
- **Unavailable Suggestions Dropped Silently:** a suggested subreddit that could not be fetched (private, banned, quarantined, ...) used to vanish from the list. It is now listed with its state in bold, as Reddit names it and untranslated, e.g. `* r/name (**private**)` or `(**banned**)`; restricted and archived subreddits are marked too. The "subscribers" label is now translated in every locale.

### Added
- **Known Subreddits Table:** every subreddit the `r/all` stream sees a comment in is recorded in a new `known_subreddits` table (buffered in memory, written in one batch every 5 minutes). "Did you mean" suggestions now also come from this table, matched with PostgreSQL's `pg_trgm` trigram similarity (GIN-indexed), so they no longer depend only on Reddit's search. The extension is created automatically; if it cannot be, suggestions fall back to Reddit's search alone.

## [2.7.0] - 2026-10-15

### Added
- **Russian:** A new `ru` locale with every string translated, including Russian's three plural forms for day counts (1 день, 3 дня, 5 дней). It covers 38 subreddits, among them r/ru, r/Pikabu, r/KafkaFPS, r/SPb and r/rubooks; most were found by sampling the languages of their newest comments.
- **Translation completeness test:** A unit test fails when any locale leaves a string, or one of its plural forms, untranslated.

### Fixed
- **Untranslated replies:** Spanish and French subreddits got their "subreddit doesn't exist", "that's where we are" and "same content" replies entirely in English, because those strings had no Spanish or French translation. Every locale now translates every string, including the "bad bot" question and the new "good bot" thank-you.

## [2.6.0] - 2026-10-15

### Changed
- The negative feedback report shows only each thread's new feedback at first; the post and the full conversation open with a "Show full conversation" button.

### Fixed
- The negative feedback report no longer counts comments whose body is `[unavailable]` (or `[deleted]` / `[removed]` in any letter case) as written feedback.

## [2.5.0] - 2026-10-15

### Added
- **Negative Feedback Report:** `uv run python scripts/negative_feedback_report.py` writes an HTML report of the "bad bot" exchanges in which someone explained what was wrong: a reply below the bots' `RESPOND_TO_NEGATIVE_SENTIMENT` question, or a "bad bot" comment, that says more than a bare "bad bot". Each exchange is shown with its post as a Reddit-style comment tree, with the new feedback highlighted. A report covers only feedback that no earlier report in `reports/negative_feedback/` covered, and links to those earlier reports.
- **Replies to Feedback:** The bots answer feedback again. A "bad bot" style reply gets the question asking why the post or comment was inappropriate, and the first "good bot" under a bot comment gets "Thank you (◕ᴗ◕✿)", translated into every locale (later ones under the same comment are not thanked). Both use the usual footer and respect `DRY_RUN`.

### Fixed
- **Inbox Feedback Crash:** Every "good bot" or "bad bot" reply raised `AttributeError` since 2.4.2, because the inbox handler called `full_url` on the PRAW instance instead of the `racb.core.reddit` module, so feedback was never logged or marked read. A reply that Reddit rejects (locked or archived thread) is now marked read instead of being retried every 10 minutes.

## [2.4.11] - 2026-10-15

### Changed
- **Quick Test Cleanup:** The integration tests now record every post and comment they create, and the `integration-tests` CI job ends with the new `scripts/delete_test_artifacts.py --strict`, which deletes only those recorded items that are still on Reddit. It replaces the `cleanup-test-history` job, whose sweep of all 4 bots' history took minutes. It runs even after failed or cancelled tests, never touches another run's posts, and blocks the deploy if a recorded item could not be deleted. `scripts/cleanup_test_history.py` stays for manual use.

## [2.4.10] - 2026-10-15

### Changed
- **Newer Pushes Cancel Stale Test Runs:** A push to `master` that reaches the `integration-tests` CI job now cancels the previous run's `cleanup-test-history` sweep, or its still-running integration tests, instead of waiting for them. The newer run's own sweep removes the leftovers. A sweep no longer runs after cancelled integration tests.

## [2.4.9] - 2026-10-15

### Fixed
- **Half-Applied Deploys:** `scripts/redeploy.sh` now checks that it can write to every directory of the checkout before resetting it, and stops with the `chown` command to run if not. A directory owned by another user (for example after a `sudo git pull`) used to make `git reset --hard` fail partway, leaving the checkout half-updated.

## [2.4.8] - 2026-10-15

### Changed
- **Faster Deploys:** The slow sweep of leftover test posts (`scripts/cleanup_test_history.py --strict`) moved out of the `integration-tests` CI job into its own `cleanup-test-history` job, which runs in parallel with `deploy` instead of before it. A failed sweep now turns that job red but no longer blocks the deploy. It still runs after failed or cancelled integration tests, and never while another run's tests are posting.

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
