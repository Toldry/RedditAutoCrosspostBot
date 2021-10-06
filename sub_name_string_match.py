import logging
import os

import requests

from bs4 import BeautifulSoup as Soup
from thefuzz import process
import pandas as pd

SUBREDDITS_FILENAME = 'subreddits.csv'
FILTERED_SUBREDDITS_FILENAME = 'filtered_subreddits.csv'
MINIMUM_SUBS = 1000
MINIMUM_MATCH_SCORE = 70
EXTRACT_LIMIT = 6

ready = False

def get_latest_csv_url():
    frontpagemetrics_url = 'https://frontpagemetrics.com'
    list_all_subreddits_url = f'{frontpagemetrics_url}/list-all-subreddits'
    html = requests.get(list_all_subreddits_url).content
    soup = Soup(html, features="html.parser")
    anchor = soup.select('td a[href$=csv]')[0]
    href = anchor['href']
    csv_url = f'{frontpagemetrics_url}{href}'
    return csv_url

def download_csv():
    if os.path.exists(SUBREDDITS_FILENAME):
        return

    csv_url = get_latest_csv_url()
    #Why encoding='latin1'? see here: https://stackoverflow.com/a/51763708/2792280
    df = pd.read_csv(csv_url, encoding='latin1')  
    df.to_csv(SUBREDDITS_FILENAME)
    
def filter_data():
    df = pd.read_csv(SUBREDDITS_FILENAME)
    df = df.rename(columns={'real_name': 'subreddit_name'})
    df = df.sort_values(by='subs', ascending=False)
    df = df[df['subs'] >= MINIMUM_SUBS]
    df = df.loc[:, 'subreddit_name']
    df.to_csv(FILTERED_SUBREDDITS_FILENAME)


def get_matches(name, return_empty_if_not_prepared = True):
    global ready
    if not ready:
        if return_empty_if_not_prepared:
            return []
        else:
            raise 'Subreddit name list not ready yet.'

    df = pd.read_csv(FILTERED_SUBREDDITS_FILENAME)
    subreddit_names = df['subreddit_name']
    extracted = process.extract(name, subreddit_names, limit=EXTRACT_LIMIT, )
    results = [name for name,score,key in extracted if score >= MINIMUM_MATCH_SCORE]
    return results



def prepare_subreddits_name_list():
    logging.info(f'Downloading list of subreddits.')
    download_csv()
    filter_data()
    logging.info(f'Done preparing list of subreddits.')
    global ready
    ready = True