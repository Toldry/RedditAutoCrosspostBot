"""Accesses a database to store scraped comments"""

import os
from datetime import datetime
import time

import psycopg2
import psycopg2.extras

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


def remove_comment(comment_entry):
    with conn.cursor() as cur:
        cur.callproc('delete_scraped_comment', (comment_entry['id'],))
        conn.commit()



def instantiate_database():
    with conn.cursor() as cur, open('instantiate_db.sql') as f:
        sql=f.read()
        cur.execute(sql)
        conn.commit()

DATABASE_URL = os.environ['DATABASE_URL']
conn = psycopg2.connect(dsn=os.environ['DATABASE_URL'], sslmode='require')
instantiate_database()


def __migrate_data(): #TODO: delete this
    from tinydb import TinyDB, Query

    db = TinyDB('db.json', sort_keys=True, indent=4, separators=(',', ': '))

    comments = db.table('comments', cache_size=0)

    Comment = Query()
    with conn.cursor() as cur:
        l = [(c['permalink'], datetime.fromtimestamp(c['scarped_time'])) for c in comments.all()]
        cur.executemany('''
        INSERT INTO scraped_comments(permalink, scraped_time)
        VALUES(%s,  %s );
        ''', l)
        conn.commit()

    print('done')
    exit(0)
__migrate_data()