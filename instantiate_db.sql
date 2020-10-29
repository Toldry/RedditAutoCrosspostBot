CREATE TABLE IF NOT EXISTS scraped_comments (
    id SERIAL PRIMARY KEY,
    permalink VARCHAR (600) NOT NULL,
    scraped_time TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_time_limits_scraped_time
ON scraped_comments (scraped_time);


CREATE OR REPLACE FUNCTION get_comments_older_than(t INTERVAL) 
RETURNS 
    TABLE(id INTEGER, permalink VARCHAR, scraped_time TIMESTAMP) 
AS
$$ 
BEGIN RETURN QUERY
    SELECT scraped_comments.id,scraped_comments.permalink,scraped_comments.scraped_time
    FROM scraped_comments
    WHERE CURRENT_TIMESTAMP  - scraped_comments.scraped_time > t;
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