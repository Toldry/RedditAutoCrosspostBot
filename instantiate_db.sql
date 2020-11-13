CREATE TABLE IF NOT EXISTS scraped_comments (
    id SERIAL PRIMARY KEY,
    permalink VARCHAR (600) NOT NULL,
    scraped_time TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_time_limits_scraped_time
ON scraped_comments (scraped_time);

ALTER TABLE scraped_comments
ADD COLUMN IF NOT EXISTS score_checked BOOLEAN DEFAULT FALSE NOT NULL;

-- ALTER TABLE scraped_comments 
-- RENAME COLUMN IF EXISTS score_checked TO phase2_checked;

-- Apparently "RENAME COLUMN IF EXISTS" is not supported

-- Rename column score_checked to phase2_checked if it has not already been renamed
DO $$
BEGIN
  IF EXISTS(SELECT *
    FROM information_schema.columns
    WHERE table_name='scraped_comments' and column_name='score_checked') 
    AND EXISTS(
        SELECT *
        FROM information_schema.columns
        WHERE table_name='scraped_comments' and column_name='phase2_checked') 
    )
  THEN
    ALTER TABLE scraped_comments DROP COLUMN score_checked;
  ELSE
    ALTER TABLE "scraped_comments" RENAME COLUMN "score_checked" TO "phase2_checked";
  END IF;
END $$;


DROP VIEW IF EXISTS scraped_comments_expanded;
CREATE OR REPLACE VIEW scraped_comments_expanded AS
    SELECT sc.*, (CURRENT_TIMESTAMP - sc.scraped_time) as time_interval_since_scraped
    FROM scraped_comments AS sc;

DROP VIEW IF EXISTS scraped_comments_expanded;
DROP VIEW IF EXISTS v_scraped_comments;
CREATE OR REPLACE VIEW v_scraped_comments AS
    SELECT sc.*, (CURRENT_TIMESTAMP - sc.scraped_time) as time_since_scraped
    FROM scraped_comments AS sc;


CREATE OR REPLACE FUNCTION get_comments_older_than(t INTERVAL) 
RETURNS 
    TABLE(id INTEGER, permalink VARCHAR, scraped_time TIMESTAMP) 
AS
$$ 
BEGIN RETURN QUERY
    SELECT sc.id,sc.permalink,sc.scraped_time
    FROM v_scraped_comments as sc
    WHERE sc.time_since_scraped > t;
END;
$$ 
LANGUAGE plpgsql;

DROP FUNCTION IF EXISTS get_unchecked_comments_older_than(INTERVAL);
CREATE OR REPLACE FUNCTION get_unchecked_comments_older_than(t INTERVAL) 
RETURNS 
    TABLE(id INTEGER, permalink VARCHAR, scraped_time TIMESTAMP) 
AS
$$ 
BEGIN RETURN QUERY
    SELECT sc.id,sc.permalink,sc.scraped_time
    FROM v_scraped_comments as sc
    WHERE sc.time_since_scraped > t AND sc.phase2_checked = 'false';
END;
$$ 
LANGUAGE plpgsql;

DROP FUNCTION IF EXISTS insert_scraped_comment(VARCHAR);
CREATE OR REPLACE FUNCTION insert_scraped_comment(new_permalink VARCHAR) 
RETURNS 
    VOID
AS
$$ 
BEGIN
    INSERT INTO scraped_comments(permalink, scraped_time)
    VALUES(new_permalink,  CURRENT_TIMESTAMP );
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