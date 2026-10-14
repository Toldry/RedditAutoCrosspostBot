"""Unit tests for racb.i18n translation and localization module."""

import string

import pytest
from babel.messages.mofile import read_mo
from babel.messages.pofile import read_po

from racb import i18n
from racb.core.reddit import (
    AUTO_CROSSPOST_BOT_NAME,
    SUB_DOESNT_EXIST_BOT_NAME,
    SAME_SUBREDDIT_BOT_NAME,
    SAME_POST_BOT_NAME,
)

EXPECTED_LOCALES = ['de', 'en', 'en@totallynotrobots', 'es', 'fr', 'he']
PO_PATHS = sorted(i18n.LOCALES_DIR.glob(f'*/LC_MESSAGES/{i18n.DOMAIN}.po'))


def read_catalog(po_path):
    with open(po_path, 'rb') as po_file:
        return read_po(po_file)


def locale_of(po_path):
    return po_path.parent.parent.name


def message_strings(message):
    """Returns the non-empty translations of a message (one per plural form for plurals)."""
    strings = message.string if isinstance(message.string, (list, tuple)) else (message.string,)
    return [text for text in strings if text]


def message_key(message):
    return message.id[0] if isinstance(message.id, (list, tuple)) else message.id


@pytest.mark.unit
def test_every_locale_has_a_compiled_catalog():
    assert i18n.available_locales() == EXPECTED_LOCALES
    assert sorted(locale_of(path) for path in PO_PATHS) == EXPECTED_LOCALES


def as_hashable(value):
    return tuple(value) if isinstance(value, (list, tuple)) else value


def translated_messages(catalog):
    """Returns {id: string} for the messages a compiled catalog contains."""
    return {
        as_hashable(message.id): as_hashable(message.string)
        for message in catalog
        if message.id and not message.fuzzy and message_strings(message)
    }


@pytest.mark.unit
@pytest.mark.parametrize('po_path', PO_PATHS, ids=locale_of)
def test_compiled_catalog_is_up_to_date(po_path):
    mo_path = po_path.with_suffix('.mo')
    with open(mo_path, 'rb') as mo_file:
        compiled = read_mo(mo_file)
    assert translated_messages(compiled) == translated_messages(read_catalog(po_path)), (
        f'{mo_path} is stale; run `uv run pybabel compile -d src/racb/locales`'
    )
    assert compiled.plural_forms == read_catalog(po_path).plural_forms


@pytest.mark.unit
def test_english_catalog_is_complete_and_defines_every_key():
    english = read_catalog(i18n.LOCALES_DIR / 'en' / 'LC_MESSAGES' / 'messages.po')
    english_keys = {message_key(message) for message in english if message.id}
    assert 'POST_SUFFIX_TEXT' in english_keys
    assert 'REPLY_TO_CROSSPOST' in english_keys
    for message in english:
        if message.id:
            assert message_strings(message), f'en is missing {message.id}'

    for po_path in PO_PATHS:
        keys = {message_key(message) for message in read_catalog(po_path) if message.id}
        assert keys <= english_keys, f'{locale_of(po_path)} has keys that en lacks: {keys - english_keys}'


@pytest.mark.unit
def test_every_locale_translates_the_suffix():
    for locale in EXPECTED_LOCALES:
        assert i18n.get_catalog(locale).gettext('POST_SUFFIX_TEXT') != 'POST_SUFFIX_TEXT', locale


@pytest.mark.unit
def test_subreddit_language_map():
    assert i18n.subreddit_language_map['ich_iel'] == 'de'
    assert i18n.subreddit_language_map['yo_elvr'] == 'es'
    assert i18n.subreddit_language_map['ani_bm'] == 'he'
    assert i18n.subreddit_language_map['moi_dlvv'] == 'fr'
    assert i18n.subreddit_language_map['totallynotrobots'] == 'en@totallynotrobots'
    assert i18n.subreddit_language_map.get('unknown_sub_name_123', 'en') == 'en'
    assert i18n.get_language('ICH_IEL') == 'de'


@pytest.mark.unit
def test_subreddit_language_map_only_uses_existing_locales():
    assert set(i18n.subreddit_language_map.values()) <= set(EXPECTED_LOCALES)


@pytest.mark.unit
def test_subreddit_language_map_rejects_a_subreddit_in_two_locales(tmp_path):
    toml_path = tmp_path / 'subreddits.toml'
    toml_path.write_text('de = ["Example"]\nfr = ["example"]\n', encoding='utf-8')
    with pytest.raises(ValueError, match='r/example'):
        i18n.load_subreddit_language_map(toml_path)


@pytest.mark.unit
def test_get_suffix_for_all_bots():
    for bot in [AUTO_CROSSPOST_BOT_NAME, SUB_DOESNT_EXIST_BOT_NAME, SAME_SUBREDDIT_BOT_NAME, SAME_POST_BOT_NAME]:
        suffix = i18n.get_suffix('pics', bot_name=bot)
        assert f'(https://www.botrank.net/bots/{bot})' in suffix
        assert '🤖' in suffix or '🧑' in suffix


@pytest.mark.unit
def test_get_translated_string_fallback():
    text = i18n.get_translated_string('REPLY_TO_CROSSPOST', 'some_generic_subreddit')
    assert 'I crossposted this' in text


@pytest.mark.unit
def test_get_translated_string_totallynotrobots():
    text = i18n.get_translated_string('THATS_WHERE_WE_ARE', 'totallynotrobots')
    assert 'YES, THAT\'S WHERE WE ARE' in text
    assert 'WRITTEN BY A HUMAN' in text


@pytest.mark.unit
def test_untranslated_string_falls_back_to_english_with_an_english_suffix():
    # THATS_WHERE_WE_ARE has no Spanish translation
    text = i18n.get_translated_string('THATS_WHERE_WE_ARE', 'yo_elvr', bot_name='BotX')
    assert text.startswith("Yes, that's where we are.")
    assert '𝙱𝙴𝙴𝙿 𝙱𝙾𝙾𝙿' in text  # the English footer
    assert 'escrito por un bot' not in text


@pytest.mark.unit
def test_translated_string_gets_a_suffix_in_the_same_language():
    text = i18n.get_translated_string('THATS_WHERE_WE_ARE', 'ich_iel', bot_name='BotX')
    assert text.startswith('Ja, da sind wir.')
    assert 'von einem Bot geschrieben' in text


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
    for po_path in PO_PATHS:
        for message in read_catalog(po_path):
            if message.id:
                for template in message_strings(message):
                    yield message_key(message), locale_of(po_path), template


def placeholders(template):
    return {field for _, field, _, _ in string.Formatter().parse(template) if field}


@pytest.mark.unit
def test_all_templates_format_without_missing_placeholders():
    for key, language, template in iterate_templates():
        template.format(**ALL_TEMPLATE_KWARGS)  # raises KeyError/IndexError on a bad placeholder


@pytest.mark.unit
def test_translations_only_use_placeholders_the_english_text_provides():
    english = {}
    for key, language, template in iterate_templates():
        if language == 'en':
            english.setdefault(key, set()).update(placeholders(template))
    for key, language, template in iterate_templates():
        extra = placeholders(template) - english[key]
        assert not extra, f'{key}/{language} uses placeholders that English does not: {extra}'


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


@pytest.mark.unit
def test_format_duration_in_days_uses_plural_forms():
    assert i18n.format_duration_in_days(86400, 'pics') == '1 day'
    assert i18n.format_duration_in_days(86400, 'ich_iel') == '1 Tag'
    assert i18n.format_duration_in_days(86400, 'moi_dlvv') == '1 jour'
    assert i18n.format_duration_in_days(86400, 'totallynotrobots') == '1 DAY'
    # Hebrew has a dual form for two
    assert i18n.format_duration_in_days(86400, 'ani_bm') == 'יום אחד'
    assert i18n.format_duration_in_days(2 * 86400, 'ani_bm') == 'יומיים'
