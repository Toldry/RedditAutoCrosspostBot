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


def get_db_connection():
    db_url = os.environ.get('DATABASE_URL', '')
    debug = env_bool('DEBUG', False)
    sslmode = os.environ.get('DB_SSLMODE')
    if not sslmode:
        if '@db:' in db_url or '@localhost:' in db_url or '@127.0.0.1:' in db_url or debug:
            sslmode = 'disable'
        else:
            sslmode = 'prefer'

    max_retries = 10
    for attempt in range(1, max_retries + 1):
        try:
            connection = psycopg2.connect(dsn=db_url, sslmode=sslmode)
            return connection
        except psycopg2.OperationalError as e:
            if attempt == max_retries:
                logging.error(f'Failed to connect to database after {max_retries} attempts: {e}')
                raise
            logging.warning(f'Database not ready yet (attempt {attempt}/{max_retries}). Retrying in 2s...')
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
