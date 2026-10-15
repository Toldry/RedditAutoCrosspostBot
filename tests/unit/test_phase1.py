"""Unit tests for racb.phases.phase1 module."""

from unittest.mock import patch, MagicMock
import prawcore
import pytest
from racb import i18n
from racb.phases import phase1


@pytest.mark.unit
def test_check_pattern(make_mock_comment):
    # Valid patterns
    assert phase1.check_pattern(make_mock_comment(body='r/funny')) == 'funny'
    assert phase1.check_pattern(make_mock_comment(body='/r/funny')) == 'funny'
    assert phase1.check_pattern(make_mock_comment(body='r/ProgrammerHumor')) == 'ProgrammerHumor'
    assert phase1.check_pattern(make_mock_comment(body='r/sub_with_underscores_1')) == 'sub_with_underscores_1'

    # Invalid patterns
    assert phase1.check_pattern(make_mock_comment(body='check out r/funny')) is None
    assert phase1.check_pattern(make_mock_comment(body='r/')) is None
    assert phase1.check_pattern(make_mock_comment(body='r/a')) is None  # < 2 chars
    assert phase1.check_pattern(make_mock_comment(body='r/this_subreddit_name_is_way_too_long_for_reddit')) is None
    assert phase1.check_pattern(make_mock_comment(body='just some text')) is None


@pytest.mark.unit
def test_is_mod_post(make_mock_comment):
    mod_comment = make_mock_comment(distinguished='moderator')
    regular_comment = make_mock_comment(distinguished=None)
    assert phase1.is_mod_post(mod_comment) is True
    assert phase1.is_mod_post(regular_comment) is False


@pytest.mark.unit
def test_is_top_level_comment(make_mock_comment):
    top_level = make_mock_comment(parent_id='t3_sub123', link_id='t3_sub123')
    nested = make_mock_comment(parent_id='t1_c999', link_id='t3_sub123')
    assert phase1.is_top_level_comment(top_level) is True
    assert phase1.is_top_level_comment(nested) is False


@pytest.mark.unit
def test_title_contains_prohibited_phrases(make_mock_comment, make_mock_submission):
    bad_title_sub = make_mock_submission(title='What is your favorite sub to browse?')
    good_title_sub = make_mock_submission(title='A very interesting cat photo')

    assert phase1.title_contains_prohibited_phrases(make_mock_comment(submission=bad_title_sub)) is True
    assert phase1.title_contains_prohibited_phrases(make_mock_comment(submission=good_title_sub)) is False


@pytest.mark.unit
def test_is_subreddit_name_length_valid():
    assert phase1.is_subreddit_name_length_valid('abc') is True
    assert phase1.is_subreddit_name_length_valid('a' * 24) is True
    assert phase1.is_subreddit_name_length_valid('ab') is False
    assert phase1.is_subreddit_name_length_valid('a' * 25) is False


@pytest.mark.unit
def test_handle_incoming_comment_routes_same_sub(make_mock_comment):
    comment = make_mock_comment(body='r/test_source', subreddit='test_source')
    with patch('racb.phases.phase1.reply_to_source_equals_target_comment') as mock_reply:
        phase1.handle_incoming_comment(comment)
        mock_reply.assert_called_once_with(comment)


@pytest.mark.unit
def test_handle_incoming_comment_routes_duplicate_post(make_mock_comment):
    comment = make_mock_comment(body='r/target_sub', subreddit='test_source')
    mock_duplicate_res = MagicMock()
    mock_duplicate_res.posts_found = True
    mock_duplicate_res.posts = [MagicMock(permalink='/r/target_sub/comments/dup1/')]

    with patch('racb.phases.phase1.get_posts_with_same_content', return_value=mock_duplicate_res):
        with patch('racb.phases.phase1.reply_to_same_content_post_comment') as mock_reply:
            phase1.handle_incoming_comment(comment)
            mock_reply.assert_called_once()


@pytest.mark.unit
def test_handle_incoming_comment_routes_nonexistent_sub(make_mock_comment):
    comment = make_mock_comment(body='r/nonexistent_sub_xyz', subreddit='test_source')
    mock_res = MagicMock()
    mock_res.posts_found = False
    mock_res.unable_to_search = True
    mock_res.unable_to_search_reason = 'SUBREDDIT_DOES_NOT_EXIST'

    with patch('racb.phases.phase1.get_posts_with_same_content', return_value=mock_res):
        with patch('racb.phases.phase1.reply_to_nonexistent_target_subreddit_comment') as mock_reply:
            phase1.handle_incoming_comment(comment)
            mock_reply.assert_called_once_with(comment, 'nonexistent_sub_xyz')


@pytest.mark.unit
def test_handle_incoming_comment_adds_qualified_recommendation(make_mock_comment):
    comment = make_mock_comment(body='r/target_sub', subreddit='test_source')
    mock_res = MagicMock()
    mock_res.posts_found = False
    mock_res.unable_to_search = False

    with patch('racb.phases.phase1.get_posts_with_same_content', return_value=mock_res):
        with patch('racb.core.db.add_comment') as mock_db_add:
            phase1.handle_incoming_comment(comment)
            mock_db_add.assert_called_once_with(comment, 'target_sub')


def make_redirect(path):
    response = MagicMock()
    response.headers = {'location': f'https://www.reddit.com{path}'}
    return prawcore.exceptions.Redirect(response)


@pytest.mark.unit
def test_search_redirect_to_community_search_means_subreddit_does_not_exist(make_mock_comment):
    comment = make_mock_comment(body='r/doesnotexist')
    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value.search.side_effect = make_redirect('/subreddits/search.json')

    with patch('racb.core.reddit.get_reddit_instance', return_value=mock_reddit):
        result = phase1.get_posts_with_same_content(comment, 'doesnotexist')

    assert result.unable_to_search is True
    assert result.unable_to_search_reason == 'SUBREDDIT_DOES_NOT_EXIST'


@pytest.mark.unit
def test_other_search_redirects_are_not_treated_as_nonexistent(make_mock_comment):
    comment = make_mock_comment(body='r/somesub')
    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value.search.side_effect = make_redirect('/r/somesub/submit')

    with patch('racb.core.reddit.get_reddit_instance', return_value=mock_reddit):
        result = phase1.get_posts_with_same_content(comment, 'somesub')

    assert result.unable_to_search is False


@pytest.mark.unit
def test_reply_as_bot_dry_run_does_not_reply(make_mock_comment):
    comment = make_mock_comment()
    with patch.dict('os.environ', {'DRY_RUN': 'true'}):
        with patch('racb.phases.phase1.get_comment_with_different_praw_instance') as mock_get_comment:
            assert phase1.reply_as_bot(comment, 'text', 'same_post_bot') is None
            mock_get_comment.assert_not_called()


@pytest.mark.unit
def test_nonexistent_subreddit_reply_suffix_uses_source_language(make_mock_comment):
    comment = make_mock_comment(body='r/nonexistent_sub_xyz', subreddit='ich_iel')
    with patch('racb.phases.phase1.sub_search.get_matches', return_value=[]):
        with patch('racb.phases.phase1.reply_as_bot') as mock_reply:
            phase1.reply_to_nonexistent_target_subreddit_comment(comment, 'nonexistent_sub_xyz')
    reply_text = mock_reply.call_args.args[1]
    assert 'Dieser Kommentar wurde von einem Bot geschrieben' in reply_text
    assert 'THIS COMMENT WAS WRITTEN BY A BOT' not in reply_text


def nonexistent_subreddit_reply(make_mock_comment, subreddit, matches=()):
    comment = make_mock_comment(body='r/bilze', subreddit=subreddit)
    with patch('racb.phases.phase1.sub_search.get_matches', return_value=list(matches)):
        with patch('racb.phases.phase1.reply_as_bot') as mock_reply:
            phase1.reply_to_nonexistent_target_subreddit_comment(comment, 'bilze')
    return mock_reply.call_args.args[1]


@pytest.mark.unit
def test_german_nonexistent_subreddit_reply_is_all_german(make_mock_comment):
    # Regression: the German reply used to contain the English "Maybe there's a typo?"
    reply_text = nonexistent_subreddit_reply(make_mock_comment, 'ich_iel')
    assert reply_text.startswith('Den Subreddit r/bilze gibt es nicht. Vielleicht ist da ein Tippfehler?')
    assert 'typo' not in reply_text


@pytest.mark.unit
def test_partly_translated_nonexistent_subreddit_reply_falls_back_to_english_as_a_whole(make_mock_comment):
    real_lookup = i18n.lookup_in_languages

    def lookup_without_german_typo(string_key, languages, count=None):
        if string_key == 'MAYBE_TYPO':
            languages = [language for language in languages if language != 'de']
            if not languages:
                raise KeyError(string_key)
        return real_lookup(string_key, languages, count)

    with patch('racb.i18n.lookup_in_languages', side_effect=lookup_without_german_typo):
        reply_text = nonexistent_subreddit_reply(make_mock_comment, 'ich_iel')
    assert reply_text.startswith("The subreddit r/bilze does not exist. Maybe there's a typo?")
    assert 'gibt es nicht' not in reply_text
    assert 'von einem Bot geschrieben' not in reply_text


@pytest.mark.unit
@pytest.mark.parametrize('subreddit', ['pics', 'ich_iel', 'yo_elvr', 'moi_dlvv', 'ani_bm', 'totallynotrobots'])
@pytest.mark.parametrize('has_matches', [False, True])
def test_nonexistent_subreddit_reply_uses_one_language(make_mock_comment, subreddit, has_matches):
    matches = []
    if has_matches:
        matches = [MagicMock()]
    with patch('racb.phases.phase1.get_subreddit_suggestion_list_line', return_value='* r/bilder'):
        reply_text = nonexistent_subreddit_reply(make_mock_comment, subreddit, matches)
    english_pieces = ['does not exist', "Maybe there's a typo?", 'Did you mean?', 'creating a new subreddit']
    language = i18n.language_with_all(
        ['NONEXISTENT_SUBREDDIT', 'MAYBE_TYPO', 'ALTERNATE_SUBS_SUGGESTION',
         'PROMPT_NONEXISTENT_SUBREDDIT_CREATION', i18n.SUFFIX_KEY],
        subreddit,
    )
    if language == 'en':
        assert reply_text.startswith('The subreddit r/bilze does not exist.')
    else:
        assert not any(piece in reply_text for piece in english_pieces), reply_text
