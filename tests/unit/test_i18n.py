"""Unit tests for racb.i18n translation and localization module."""

import pytest
from racb import i18n
from racb.core.reddit import (
    AUTO_CROSSPOST_BOT_NAME,
    SUB_DOESNT_EXIST_BOT_NAME,
    SAME_SUBREDDIT_BOT_NAME,
    SAME_POST_BOT_NAME,
)


@pytest.mark.unit
def test_translations_dict_structure():
    assert 'POST_SUFFIX_TEXT' in i18n.translations
    assert 'REPLY_TO_CROSSPOST' in i18n.translations
    assert 'en' in i18n.translations['POST_SUFFIX_TEXT']
    assert 'es' in i18n.translations['POST_SUFFIX_TEXT']
    assert 'de' in i18n.translations['POST_SUFFIX_TEXT']
    assert 'fr' in i18n.translations['POST_SUFFIX_TEXT']
    assert 'he' in i18n.translations['POST_SUFFIX_TEXT']
    assert 'totallynotrobots' in i18n.translations['POST_SUFFIX_TEXT']


@pytest.mark.unit
def test_subreddit_language_map():
    assert i18n.subreddit_language_map['ich_iel'] == 'de'
    assert i18n.subreddit_language_map['yo_elvr'] == 'es'
    assert i18n.subreddit_language_map['ani_bm'] == 'he'
    assert i18n.subreddit_language_map['moi_dlvv'] == 'fr'
    assert i18n.subreddit_language_map['totallynotrobots'] == 'totallynotrobots'
    assert i18n.subreddit_language_map.get('unknown_sub_name_123', 'en') == 'en'


@pytest.mark.unit
def test_get_suffix_for_all_bots():
    for bot in [AUTO_CROSSPOST_BOT_NAME, SUB_DOESNT_EXIST_BOT_NAME, SAME_SUBREDDIT_BOT_NAME, SAME_POST_BOT_NAME]:
        suffix = i18n.get_suffix('pics', bot_name=bot)
        assert bot in suffix
        assert '🤖' in suffix or '🧑' in suffix


@pytest.mark.unit
def test_get_translated_string_fallback():
    text = i18n.get_translated_string('REPLY_TO_CROSSPOST', 'some_generic_subreddit')
    assert 'I crossposted this' in text


@pytest.mark.unit
def test_get_translated_string_totallynotrobots():
    text = i18n.get_translated_string('THATS_WHERE_WE_ARE', 'totallynotrobots')
    assert 'YES, THAT\'S WHERE WE ARE' in text or 'YES' in text
