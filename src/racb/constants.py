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

# Never crossposted to. Each one drew repeated complaints in the negative feedback report: its posts must fit
# a narrow premise the bot cannot check (staged texts, "not" a Disney vacation, maps without New Zealand), or
# commenters name it as a joke about the post rather than as a place it belongs. Compared case-insensitively.
TARGET_SUB_BLACKLIST = [
    'MapsWithoutNZ',
    'mapswithoutnewzealand',
    'donthelpjustfilm',
    'notdisneyvacation',
    'killthecameraman',
    'confusingperspective',
    'rpghorrorstories',
    'DiWHY',
    'itrunsdoom',
    'GoodFakeTexts',
    'Badfaketexts',
    'pointlesslygendered',
    'LeopardsAteMyFace',
    'engrish',
    'wyomingdoesntexist',
]

# Subreddit pairs that are opposites by design, so no post fits both. Neither is crossposted to the other.
# Compared case-insensitively.
OPPOSITE_SUB_PAIRS = [
    ('disneyvacation', 'notdisneyvacation'),
    ('bertstrips', 'WholesomeBertstrips'),
]

_SUB_BLACKLIST_LOWER = {name.lower() for name in SUB_BLACKLIST}
_TARGET_SUB_BLACKLIST_LOWER = {name.lower() for name in TARGET_SUB_BLACKLIST}
_OPPOSITE_SUB_PAIRS_LOWER = {frozenset((a.lower(), b.lower())) for a, b in OPPOSITE_SUB_PAIRS}


def is_crosspost_blocked(source_subreddit, target_subreddit):
    """True if a post from `source_subreddit` must never be crossposted to `target_subreddit`."""
    source = source_subreddit.lower()
    target = target_subreddit.lower()
    return (
        source in _SUB_BLACKLIST_LOWER
        or target in _SUB_BLACKLIST_LOWER
        or target in _TARGET_SUB_BLACKLIST_LOWER
        or frozenset((source, target)) in _OPPOSITE_SUB_PAIRS_LOWER
    )
