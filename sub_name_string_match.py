import requests

from bs4 import BeautifulSoup as Soup
import thefuzz
import pandas as pd

subreddits_filename = 'subreddits.csv'
filtered_subreddits_filename = 'filtered_subreddits.csv'
minimum_subs = 1000

def get_latest_csv_url():
    frontpagemetrics_url = 'https://frontpagemetrics.com'
    list_all_subreddits_url = f'{frontpagemetrics_url}/list-all-subreddits'
    html = requests.get(list_all_subreddits_url).content
    soup = Soup(html)
    anchor = soup.select('td a[href$=csv]')[0]
    href = anchor['href']
    csv_url = f'{frontpagemetrics_url}{href}'
    return csv_url

def download_csv():
    csv_url = get_latest_csv_url()
    response = requests.get(csv_url)
    with open(subreddits_filename, 'w') as handle:
        handle.write(response.content)
    
def filter_data():
    df = pd.read_csv(subreddits_filename)
    df.rename(columns={'real_name': 'subreddit_name', 'subs' : 'subscribers'})
    df = df.sort_values(by='subscribers', ascending=False)
    df = df[df['subscribers'] >= minimum_subs]
    df = df[:, ['subreddit_name']]
    df.to_csv(filtered_subreddits_filename)

def get_matches(name):
    df = pd.read_csv(filtered_subreddits_filename)
    subreddit_names = df['subreddit_name']
    extracted = thefuzz.process.extract(name, subreddit_names, limit=6, )
    minimum_match_score = 70
    results = [name for name,score,key in extracted if score >= minimum_match_score]
    return results