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

DROP FUNCTION IF EXISTS get_scraped_comment_by_id(INTEGER);
CREATE OR REPLACE FUNCTION get_scraped_comment_by_id(p_id INTEGER)
RETURNS
    TABLE(id INTEGER, permalink VARCHAR, scraped_time TIMESTAMP, target_subreddit VARCHAR)
AS
$$
BEGIN RETURN QUERY
    SELECT sc.id, sc.permalink, sc.scraped_time, sc.target_subreddit
    FROM scraped_comments as sc
    WHERE sc.id = p_id;
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
