"""Accesses a database to store scraped comments"""

import os
from datetime import datetime
import time

import psycopg2
import psycopg2.extras

import environment

def add_comment(comment):
    with conn.cursor() as cur:
        permalink = comment.permalink
        cur.callproc('insert_scraped_comment', (permalink,))
        conn.commit()


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

conn = psycopg2.connect(
    dsn=os.environ['DATABASE_URL'], 
    sslmode='require' if not environment.DEBUG else None)
instantiate_database()