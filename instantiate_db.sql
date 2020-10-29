CREATE TABLE IF NOT EXISTS scraped_comments (
    id SERIAL PRIMARY KEY,
    permalink VARCHAR (600) NOT NULL,
    scraped_time TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_time_limits_scraped_time
ON scraped_comments (scraped_time);


DROP VIEW IF EXISTS scraped_comments_expanded;
CREATE OR REPLACE VIEW scraped_comments_expanded AS
    SELECT sc.*, (CURRENT_TIMESTAMP - sc.scraped_time) as time_interval_since_scraped
    FROM scraped_comments AS sc;

CREATE OR REPLACE FUNCTION get_comments_older_than(t INTERVAL) 
RETURNS 
    TABLE(id INTEGER, permalink VARCHAR, scraped_time TIMESTAMP) 
AS
$$ 
BEGIN RETURN QUERY
    SELECT sc.id,sc.permalink,sc.scraped_time
    FROM scraped_comments_expanded as sc
    WHERE sc.time_interval_since_scraped > t;
END;
$$ 
LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION get_unchecked_comments_older_than(t INTERVAL) 
RETURNS 
    TABLE(id INTEGER, permalink VARCHAR, scraped_time TIMESTAMP) 
AS
$$ 
BEGIN RETURN QUERY
    SELECT sc.id,sc.permalink,sc.scraped_time
    FROM scraped_comments_expanded as sc
    WHERE sc.time_interval_since_scraped > t AND sc.score_checked = 'false';
END;
$$ 
LANGUAGE plpgsql;



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

ALTER TABLE scraped_comments
ADD COLUMN IF NOT EXISTS score_checked BOOLEAN DEFAULT FALSE NOT NULL;

CREATE OR REPLACE FUNCTION set_comment_checked(p_id INTEGER) 
RETURNS 
    VOID
AS
$$ 
BEGIN
    UPDATE scraped_comments
    SET score_checked = 'true'
    WHERE id=p_id;
END;
$$ 
LANGUAGE plpgsql;