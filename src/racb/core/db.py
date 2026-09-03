"""Accesses a PostgreSQL database to store and retrieve scraped comments"""

import logging
import os
import time
from pathlib import Path

import psycopg2
import psycopg2.extras

SQL_FILE_PATH = Path(__file__).resolve().parent.parent / 'sql' / 'instantiate_db.sql'


def env_bool(key, default=False):
    val = os.environ.get(key)
    if val is None:
        return default
    return str(val).strip().lower() in ('true', '1', 'yes', 'y', 't')


def _mask_db_url(url_str):
    """Returns database URL with password masked for safe logging."""
    if not url_str:
        return '<empty>'
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url_str)
        if parsed.password:
            masked_netloc = parsed.netloc.replace(f':{parsed.password}@', ':***@')
            return parsed._replace(netloc=masked_netloc).geturl()
        return url_str
    except Exception:
        return '<unparseable db_url>'


def get_db_connection():
    db_url = os.environ.get('DATABASE_URL', '').strip()
    if not db_url:
        err_msg = 'DATABASE_URL environment variable is not set or empty.'
        logging.error(err_msg)
        raise ValueError(err_msg)

    debug = env_bool('DEBUG', False)
    sslmode = os.environ.get('DB_SSLMODE')
    if not sslmode:
        if '@db:' in db_url or '@localhost:' in db_url or '@127.0.0.1:' in db_url or debug:
            sslmode = 'disable'
        else:
            sslmode = 'prefer'

    masked_url = _mask_db_url(db_url)
    logging.info(f'Connecting to database: {masked_url} (sslmode={sslmode})')

    # Errors that cannot be resolved by retrying (fail fast immediately)
    fatal_error_keywords = (
        'password authentication failed',
        'does not exist',
        'no password supplied',
        'invalid connection option',
        'invalid dsn',
    )

    max_retries = 10
    for attempt in range(1, max_retries + 1):
        try:
            connection = psycopg2.connect(dsn=db_url, sslmode=sslmode)
            logging.info('Database connection established successfully.')
            return connection
        except psycopg2.OperationalError as e:
            err_str = str(e).strip()

            # Check if this is a fatal auth or config error
            if any(kw in err_str.lower() for kw in fatal_error_keywords):
                logging.error(
                    f'Fatal database authentication or configuration error for {masked_url}:\n{err_str}\n'
                    f'Check your POSTGRES_USER, POSTGRES_PASSWORD, and DATABASE_URL in .env.'
                )
                raise

            if attempt == max_retries:
                logging.error(f'Failed to connect to database after {max_retries} attempts ({masked_url}): {err_str}')
                raise
            logging.warning(f'Database not ready yet (attempt {attempt}/{max_retries}): {err_str}. Retrying in 2s...')
            time.sleep(2)


conn = get_db_connection()


def add_comment(comment):
    try:
        with conn.cursor() as cur:
            permalink = comment.permalink
            cur.callproc('insert_scraped_comment', (permalink,))
            conn.commit()
    except (psycopg2.errors.InsufficientPrivilege, psycopg2.errors.InFailedSqlTransaction):
        logging.error('Cannot insert row, database transaction failed or permission denied.')
        conn.rollback()
        debug = env_bool('DEBUG', False)
        if debug:
            raise


def get_comments_older_than(num_seconds):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        interval = f'{num_seconds} seconds'
        cur.callproc('get_comments_older_than', (interval,))
        comment_entries = cur.fetchall()
        return comment_entries


def get_unchecked_comments_older_than(num_seconds):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        interval = f'{num_seconds} seconds'
        cur.callproc('get_unchecked_comments_older_than', (interval,))
        comment_entries = cur.fetchall()
        return comment_entries


def delete_comment(comment_entry):
    with conn.cursor() as cur:
        cur.callproc('delete_scraped_comment', (comment_entry['id'],))
        conn.commit()


def set_comment_checked(comment_entry):
    with conn.cursor() as cur:
        cur.callproc('set_comment_checked', (comment_entry['id'],))
        conn.commit()


def instantiate_database():
    with conn.cursor() as cur, open(SQL_FILE_PATH, 'r', encoding='utf-8') as f:
        sql = f.read()
        cur.execute(sql)
        conn.commit()


instantiate_database()
