import logging

from thefuzz import process
import pandas as pd

# this file is created by processing and filtering the files provided by https://frontpagemetrics.com/list-all-subreddits 
FILTERED_SUBREDDITS_FILENAME = 'subreddit_names.csv' 

MINIMUM_SUBS = 5000
MINIMUM_MATCH_SCORE = 70
EXTRACT_LIMIT = 12
MAX_STRING_LEN_RATIO_DELTA = 0.4
RESULTS_LIMIT = 4


def get_matches(subreddit_name):
    df = pd.read_csv(FILTERED_SUBREDDITS_FILENAME) 
    subreddit_names = df['subreddit_name']
    extracted = process.extract(subreddit_name, subreddit_names, limit=EXTRACT_LIMIT, )
    n = len(subreddit_name)

    results = [
        name for name,score,key 
        in extracted
        if score >= MINIMUM_MATCH_SCORE
        and (abs( len(name) - n))/n <= MAX_STRING_LEN_RATIO_DELTA
    ]

    results = results[:RESULTS_LIMIT]
    return results