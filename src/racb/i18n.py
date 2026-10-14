"""Translates strings according to the target subreddit

Translations are gettext catalogs in ``locales/<locale>/LC_MESSAGES/messages.po``, compiled to
``messages.mo`` with ``uv run pybabel compile -d src/racb/locales``. Message ids are symbolic keys
(e.g. ``REPLY_TO_CROSSPOST``); the ``en`` catalog holds the English source text, and every other
locale falls back to it for strings it leaves untranslated. Which subreddit gets which locale is
configured in ``locales/subreddits.toml``.

A subreddit-specific variant of a language uses the gettext ``@modifier`` locale syntax, as in
``en@totallynotrobots``.
"""

import functools
import gettext
import tomllib
from pathlib import Path

DEFAULT_LANGUAGE = 'en'
LOCALES_DIR = Path(__file__).parent / 'locales'
DOMAIN = 'messages'
SUFFIX_KEY = 'POST_SUFFIX_TEXT'


def load_subreddit_language_map(path=LOCALES_DIR / 'subreddits.toml'):
    """Returns {lowercased subreddit name: locale} from a TOML file of {locale: [subreddits]}."""
    with open(path, 'rb') as toml_file:
        subreddits_by_locale = tomllib.load(toml_file)

    language_map = {}
    for locale, subreddits in subreddits_by_locale.items():
        for subreddit in subreddits:
            subreddit = subreddit.lower()
            if subreddit in language_map:
                raise ValueError(f'r/{subreddit} is mapped to both {language_map[subreddit]} and {locale}')
            language_map[subreddit] = locale
    return language_map


# Subreddit to language code mapping
subreddit_language_map = load_subreddit_language_map()


@functools.cache
def get_catalog(locale):
    """Loads one compiled catalog, without fallbacks, so a miss can be told apart from a hit."""
    mo_path = LOCALES_DIR / locale / 'LC_MESSAGES' / f'{DOMAIN}.mo'
    if not mo_path.is_file():
        return gettext.NullTranslations()
    with open(mo_path, 'rb') as mo_file:
        return gettext.GNUTranslations(mo_file)


def available_locales():
    return sorted(path.parent.parent.name for path in LOCALES_DIR.glob(f'*/LC_MESSAGES/{DOMAIN}.mo'))


def get_language(subreddit):
    return subreddit_language_map.get(subreddit.lower(), DEFAULT_LANGUAGE)


def lookup(string_key, subreddit, count=None):
    """Returns (language, text) for the subreddit's locale, falling back to English.

    With ``count``, picks the plural form for that number (the plural id is ``<key>_PLURAL``).
    """
    return lookup_in_languages(string_key, [get_language(subreddit), DEFAULT_LANGUAGE], count)


def lookup_in_languages(string_key, languages, count=None):
    plural_key = f'{string_key}_PLURAL'
    for language in dict.fromkeys(languages):
        catalog = get_catalog(language)
        if count is None:
            text = catalog.gettext(string_key)
            untranslated = text == string_key
        else:
            text = catalog.ngettext(string_key, plural_key, count)
            untranslated = text in (string_key, plural_key)
        if not untranslated:
            return language, text
    raise KeyError(f'No text for message {string_key!r} in {languages}')


def get_translated_string(string_key, subreddit, add_suffix=True, bot_name=None):
    language, translated_string = lookup(string_key, subreddit)
    if add_suffix:
        # The suffix uses the same language as the body, so a reply never mixes languages
        _, suffix_text = lookup_in_languages(SUFFIX_KEY, [language, DEFAULT_LANGUAGE])
        translated_string += format_suffix(suffix_text, bot_name)
    return translated_string


def format_suffix(suffix_text, bot_name):
    if bot_name is not None:
        suffix_text = suffix_text.format(bot_name=bot_name)
    return suffix_text


def get_suffix(subreddit, bot_name):
    _, suffix_text = lookup(SUFFIX_KEY, subreddit)
    return format_suffix(suffix_text, bot_name)


def format_duration_in_days(seconds, subreddit):
    """Formats a duration as a whole number of days in the subreddit's language."""
    days = round(seconds / 86400)
    _, text = lookup('DURATION_IN_DAYS', subreddit, count=days)
    return text.format(days=days)
