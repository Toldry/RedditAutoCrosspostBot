"""Accesses a database to store scraped comments"""

import logging
import os
from distutils import util

import psycopg2
import psycopg2.extras

def add_comment(comment):
    try:
        with conn.cursor() as cur:
            permalink = comment.permalink
            cur.callproc('insert_scraped_comment', (permalink,))
            conn.commit()
    except (psycopg2.errors.InsufficientPrivilege, psycopg2.errors.InFailedSqlTransaction,):
        logging.error('Cannot insert row, probably because heroku blocked access to the table.')
        conn.rollback()
        debug = bool(util.strtobool(os.environ.get('DEBUG')))
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
    with conn.cursor() as cur, open('instantiate_db.sql') as f:
        sql=f.read()
        cur.execute(sql)
        conn.commit()

debug = bool(util.strtobool(os.environ.get('DEBUG')))
conn = psycopg2.connect(
    dsn=os.environ['DATABASE_URL'], 
    sslmode='require' if not debug else None)
instantiate_database()