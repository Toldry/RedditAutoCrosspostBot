"""Global constants and configuration values for RACB."""

# Subs that contain a high likelihood of having submissions that break reddit's rules go here
# also in general subs that give me the heebie-jeebies 🤢
SUB_BLACKLIST = [
    'all',  # Added to prevent interaction with r/all

    # Test subreddits ignored in production
    'racb_test_1',
    'racb_test_2',

    'suddenlysexoffender',
    'cringetopia',
    'ReviewsByRetards',
]
