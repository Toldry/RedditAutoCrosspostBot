-- This script runs on every new database connection, so every statement in it MUST be idempotent.
-- Never ADD and then DROP a column here: PostgreSQL never reuses a dropped column's attribute number,
-- and a table can only ever have 1600 of them (dropped ones included).

CREATE TABLE IF NOT EXISTS scraped_comments (
    id SERIAL PRIMARY KEY,
    permalink VARCHAR (600) NOT NULL,
    scraped_time TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_time_limits_scraped_time
ON scraped_comments (scraped_time);

-- Rename column score_checked to phase2_checked if it has not already been renamed
DO $$
BEGIN
  IF EXISTS(SELECT *
    FROM information_schema.columns
    WHERE table_name='scraped_comments' and column_name='score_checked')
  THEN
    IF EXISTS(SELECT *
        FROM information_schema.columns
        WHERE table_name='scraped_comments' and column_name='phase2_checked')
    THEN
      ALTER TABLE scraped_comments DROP COLUMN score_checked;
    ELSE
      ALTER TABLE "scraped_comments" RENAME COLUMN "score_checked" TO "phase2_checked";
    END IF;
  END IF;
END $$;

ALTER TABLE scraped_comments
ADD COLUMN IF NOT EXISTS phase2_checked BOOLEAN DEFAULT FALSE NOT NULL;

-- The target subreddit as written when the comment was scraped. Phases 2 and 3 refuse to crosspost
-- if the comment was later edited to point elsewhere. NULL for rows scraped before this column existed.
ALTER TABLE scraped_comments
ADD COLUMN IF NOT EXISTS target_subreddit VARCHAR (32);

DROP VIEW IF EXISTS scraped_comments_expanded;
DROP VIEW IF EXISTS v_scraped_comments;
CREATE OR REPLACE VIEW v_scraped_comments AS
    SELECT sc.*, (LOCALTIMESTAMP - sc.scraped_time) as time_since_scraped
    FROM scraped_comments AS sc;


-- The age predicates below compare the bare column against a constant (instead of computing
-- the age of every row) so that idx_time_limits_scraped_time can be used.
DROP FUNCTION IF EXISTS get_comments_older_than(INTERVAL);
CREATE OR REPLACE FUNCTION get_comments_older_than(t INTERVAL)
RETURNS
    TABLE(id INTEGER, permalink VARCHAR, scraped_time TIMESTAMP, target_subreddit VARCHAR)
AS
$$
BEGIN RETURN QUERY
    SELECT sc.id, sc.permalink, sc.scraped_time, sc.target_subreddit
    FROM scraped_comments as sc
    WHERE sc.scraped_time < LOCALTIMESTAMP - t;
END;
$$
LANGUAGE plpgsql;

DROP FUNCTION IF EXISTS get_unchecked_comments_older_than(INTERVAL);
CREATE OR REPLACE FUNCTION get_unchecked_comments_older_than(t INTERVAL)
RETURNS
    TABLE(id INTEGER, permalink VARCHAR, scraped_time TIMESTAMP, target_subreddit VARCHAR)
AS
$$
BEGIN RETURN QUERY
    SELECT sc.id, sc.permalink, sc.scraped_time, sc.target_subreddit
    FROM scraped_comments as sc
    WHERE sc.scraped_time < LOCALTIMESTAMP - t AND sc.phase2_checked = 'false';
END;
$$
LANGUAGE plpgsql;

DROP FUNCTION IF EXISTS get_oldest_scraped_comment();
CREATE OR REPLACE FUNCTION get_oldest_scraped_comment()
RETURNS
    TABLE(id INTEGER, permalink VARCHAR, scraped_time TIMESTAMP, target_subreddit VARCHAR)
AS
$$
BEGIN RETURN QUERY
    SELECT sc.id, sc.permalink, sc.scraped_time, sc.target_subreddit
    FROM scraped_comments as sc
    ORDER BY sc.scraped_time, sc.id
    LIMIT 1;
END;
$$
LANGUAGE plpgsql;

DROP FUNCTION IF EXISTS insert_scraped_comment(VARCHAR);
DROP FUNCTION IF EXISTS insert_scraped_comment(VARCHAR, VARCHAR);
CREATE OR REPLACE FUNCTION insert_scraped_comment(new_permalink VARCHAR, new_target_subreddit VARCHAR)
RETURNS
    VOID
AS
$$
BEGIN
    INSERT INTO scraped_comments(permalink, scraped_time, target_subreddit)
    VALUES(new_permalink, LOCALTIMESTAMP, new_target_subreddit);
END;
$$
LANGUAGE plpgsql;

DROP FUNCTION IF EXISTS delete_scraped_comment(INTEGER);
CREATE OR REPLACE FUNCTION delete_scraped_comment(id_to_delete INTEGER)
RETURNS
    VOID
AS
$$
BEGIN
    DELETE FROM scraped_comments
    WHERE scraped_comments.id=id_to_delete;
END;
$$
LANGUAGE plpgsql;

DROP FUNCTION IF EXISTS set_comment_checked(INTEGER);
CREATE OR REPLACE FUNCTION set_comment_checked(p_id INTEGER)
RETURNS
    VOID
AS
$$
BEGIN
    UPDATE scraped_comments
    SET phase2_checked = 'true'
    WHERE id=p_id;
END;
$$
LANGUAGE plpgsql;


-- Subreddits the bot has seen comments in, so "did you mean" suggestions do not rely only on Reddit's
-- search (which matches on topic, not name). Filled in batches from the r/all comment stream.
CREATE TABLE IF NOT EXISTS known_subreddits (
    name_lower VARCHAR (32) PRIMARY KEY,
    display_name VARCHAR (32) NOT NULL,
    last_seen TIMESTAMP NOT NULL
);

-- pg_trgm is a trusted extension (PostgreSQL 13+), so the database owner can create it. If it cannot be
-- created, everything else still works and the suggestions fall back to Reddit's search alone.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'pg_trgm is unavailable (%); known_subreddits similarity search is disabled', SQLERRM;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
        CREATE INDEX IF NOT EXISTS idx_known_subreddits_name_trgm
        ON known_subreddits USING GIN (name_lower gin_trgm_ops);
    END IF;
END $$;

DROP FUNCTION IF EXISTS record_known_subreddits(TEXT[]);
CREATE OR REPLACE FUNCTION record_known_subreddits(names TEXT[])
RETURNS
    VOID
AS
$$
BEGIN
    INSERT INTO known_subreddits(name_lower, display_name, last_seen)
    SELECT DISTINCT ON (lower(n)) lower(n), n, LOCALTIMESTAMP
    FROM unnest(names) AS n
    ORDER BY lower(n)
    ON CONFLICT (name_lower) DO UPDATE
    SET display_name = EXCLUDED.display_name, last_seen = EXCLUDED.last_seen;
END;
$$
LANGUAGE plpgsql;

-- Candidates for a misspelled subreddit name, most similar first. `%` keeps names whose trigram
-- similarity is at least pg_trgm.similarity_threshold (default 0.3) and can use the GIN index.
DROP FUNCTION IF EXISTS find_similar_subreddits(VARCHAR, INTEGER);
CREATE OR REPLACE FUNCTION find_similar_subreddits(query VARCHAR, max_results INTEGER)
RETURNS
    TABLE(display_name VARCHAR, similarity REAL)
AS
$$
BEGIN RETURN QUERY
    SELECT ks.display_name, similarity(ks.name_lower, lower(query))
    FROM known_subreddits AS ks
    WHERE ks.name_lower % lower(query)
    ORDER BY 2 DESC, ks.name_lower
    LIMIT max_results;
END;
$$
LANGUAGE plpgsql;

-- Where the comment reader (racb/comment_reader.py) stopped, so a restart resumes there instead of
-- skipping what was posted meanwhile. Every comment ID below next_comment_id (a base-36 comment ID as a
-- number) has been read, and so have the blocks of IDs starting at each of done_blocks.
CREATE TABLE IF NOT EXISTS stream_cursors (
    name VARCHAR (32) PRIMARY KEY,
    next_comment_id BIGINT NOT NULL,
    done_blocks BIGINT[] NOT NULL DEFAULT '{}',
    updated_at TIMESTAMP NOT NULL
);

DROP FUNCTION IF EXISTS save_stream_cursor(VARCHAR, BIGINT, BIGINT[]);
CREATE OR REPLACE FUNCTION save_stream_cursor(cursor_name VARCHAR, new_next_comment_id BIGINT, new_done_blocks BIGINT[])
RETURNS
    VOID
AS
$$
BEGIN
    INSERT INTO stream_cursors(name, next_comment_id, done_blocks, updated_at)
    VALUES (cursor_name, new_next_comment_id, new_done_blocks, LOCALTIMESTAMP)
    ON CONFLICT (name) DO UPDATE
    SET next_comment_id = EXCLUDED.next_comment_id,
        done_blocks = EXCLUDED.done_blocks,
        updated_at = EXCLUDED.updated_at;
END;
$$
LANGUAGE plpgsql;

DROP FUNCTION IF EXISTS get_stream_cursor(VARCHAR);
CREATE OR REPLACE FUNCTION get_stream_cursor(cursor_name VARCHAR)
RETURNS
    TABLE (next_comment_id BIGINT, done_blocks BIGINT[], updated_at TIMESTAMP)
AS
$$
BEGIN
    RETURN QUERY
    SELECT sc.next_comment_id, sc.done_blocks, sc.updated_at
    FROM stream_cursors AS sc
    WHERE sc.name = cursor_name;
END;
$$
LANGUAGE plpgsql;
