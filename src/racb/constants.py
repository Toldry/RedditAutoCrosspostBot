"""Global constants and configuration values for RACB."""

# Subs that contain a high likelihood of having submissions that break reddit's rules go here
# also in general subs that give me the heebie-jeebies 🤢
# Neither crossposted from nor to, and the auxiliary bots ignore comments in them. Compared case-insensitively.
SUB_BLACKLIST = [
    'all',  # Added to prevent interaction with r/all

    # Test subreddits ignored in production
    'racb_test_1',
    'racb_test_2',

    'suddenlysexoffender',
    'cringetopia',
    'ReviewsByRetards',
]

_SUB_BLACKLIST_LOWER = {name.lower() for name in SUB_BLACKLIST}


def is_crosspost_blocked(source_subreddit, target_subreddit):
    """True if a post from `source_subreddit` must never be crossposted to `target_subreddit`."""
    return source_subreddit.lower() in _SUB_BLACKLIST_LOWER or target_subreddit.lower() in _SUB_BLACKLIST_LOWER
