from thefuzz import process
import pandas as pd

# this file is created by processing and filtering the files provided by https://frontpagemetrics.com/list-all-subreddits
FILTERED_SUBREDDITS_FILENAME = 'subreddit_names.csv' 
MINIMUM_SUBS = 5000
MINIMUM_MATCH_SCORE = 70
EXTRACT_LIMIT = 6


def get_matches(name):
    df = pd.read_csv(FILTERED_SUBREDDITS_FILENAME)
    subreddit_names = df['subreddit_name']
    extracted = process.extract(name, subreddit_names, limit=EXTRACT_LIMIT, )
    results = [name for name,score,key in extracted if score >= MINIMUM_MATCH_SCORE]
    return results