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


ALL_TEMPLATE_KWARGS = {
    'bot_name': 'AutoCrosspostBot',
    'source_subreddit': 'src',
    'target_subreddit': 'dst',
    'source_comment_permalink': 'https://reddit.com/r/src/comments/1/t/c/',
    'source_comment_score': 42,
    'source_submission_id': 'abc123',
    'timedelta_string': '90 days',
    'source_comment_author_name': '/u/someone',
    'same_content_post_url': '/r/dst/comments/2/t/',
    'alternate_subreddits_string': '* r/x',
    'days': 90,
}


def iterate_templates():
    for key, by_language in i18n.translations.items():
        for language, template in by_language.items():
            if template is not None:
                yield key, language, template


@pytest.mark.unit
def test_all_templates_format_without_missing_placeholders():
    for key, language, template in iterate_templates():
        template.format(**ALL_TEMPLATE_KWARGS)  # raises KeyError/IndexError on a bad placeholder


@pytest.mark.unit
def test_no_markdown_link_is_broken_by_a_space():
    for key, language, template in iterate_templates():
        assert '] (' not in template, f'{key}/{language} has "] (" which breaks the Markdown link'


@pytest.mark.unit
def test_every_superscript_group_is_closed_on_its_line():
    for key, language, template in iterate_templates():
        for line in template.splitlines():
            if '^(' in line:
                assert line.rstrip().endswith(')'), f'{key}/{language} has an unclosed ^( group: {line!r}'


@pytest.mark.unit
def test_no_template_claims_a_repostsleuth_check():
    for key, language, template in iterate_templates():
        assert 'repostsleuth' not in template.lower(), f'{key}/{language} still mentions RepostSleuth'


@pytest.mark.unit
def test_format_duration_in_days_is_localized():
    assert i18n.format_duration_in_days(90 * 86400, 'pics') == '90 days'
    assert i18n.format_duration_in_days(90 * 86400, 'ich_iel') == '90 Tage'
    assert i18n.format_duration_in_days(90 * 86400, 'ani_bm') == '90 ימים'
