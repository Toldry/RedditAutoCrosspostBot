"""Unit tests for racb.phases.phase1 module."""

from unittest.mock import patch, MagicMock, PropertyMock
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
@pytest.mark.parametrize('subreddit', ['pics', 'ich_iel', 'yo_elvr', 'moi_dlvv', 'ani_bm', 'pikabu', 'totallynotrobots'])
@pytest.mark.parametrize('has_matches', [False, True])
def test_nonexistent_subreddit_reply_uses_one_language(make_mock_comment, subreddit, has_matches):
    matches = []
    if has_matches:
        matches = ['bilder', 'bildr']
    descriptions = {
        'bilder': {'name': 'bilder', 'state': None, 'subscribers': 1234, 'over18': False},
        'bildr': {'name': 'bildr', 'state': 'private', 'subscribers': None, 'over18': False},
    }
    with patch('racb.phases.phase1.describe_subreddit', side_effect=descriptions.get):
        reply_text = nonexistent_subreddit_reply(make_mock_comment, subreddit, matches)
    english_pieces = ['does not exist', "Maybe there's a typo?", 'Did you mean?', 'creating a new subreddit',
                      'subscribers:']
    language = i18n.language_with_all(
        ['NONEXISTENT_SUBREDDIT', 'MAYBE_TYPO', 'ALTERNATE_SUBS_SUGGESTION',
         'PROMPT_NONEXISTENT_SUBREDDIT_CREATION', 'SUGGESTION_SUBSCRIBERS', i18n.SUFFIX_KEY],
        subreddit,
    )
    if has_matches:
        # The state is Reddit's own word, never translated
        assert '* r/bilder (' in reply_text and '* r/bildr (**private**)' in reply_text
    if language == 'en':
        assert reply_text.startswith('The subreddit r/bilze does not exist.')
    else:
        assert not any(piece in reply_text for piece in english_pieces), reply_text


def make_prawcore_error(error_class, status_code, reason):
    response = MagicMock(status_code=status_code)
    response.json.return_value = {'reason': reason} if reason else {}
    return error_class(response)


def describe_with_fetch_error(error):
    mock_reddit = MagicMock()
    type(mock_reddit.subreddit.return_value).subscribers = PropertyMock(side_effect=error)
    with patch('racb.phases.phase1.reddit.get_reddit_instance', return_value=mock_reddit):
        return phase1.describe_subreddit('somesub')


@pytest.mark.unit
@pytest.mark.parametrize('error, expected_state', [
    (make_prawcore_error(prawcore.exceptions.Forbidden, 403, 'private'), 'private'),
    (make_prawcore_error(prawcore.exceptions.Forbidden, 403, 'quarantined'), 'quarantined'),
    (make_prawcore_error(prawcore.exceptions.NotFound, 404, 'banned'), 'banned'),
    (make_prawcore_error(prawcore.exceptions.NotFound, 404, None), 'unavailable'),
    (make_prawcore_error(prawcore.exceptions.Forbidden, 403, 'gold_only'), 'gold_only'),
    (make_prawcore_error(prawcore.exceptions.Forbidden, 403, None), 'private'),
])
def test_describe_subreddit_keeps_unavailable_subreddits_with_their_state(error, expected_state):
    info = describe_with_fetch_error(error)
    assert info == {'name': 'somesub', 'state': expected_state, 'subscribers': None, 'over18': False}


@pytest.mark.unit
def test_describe_subreddit_lists_network_errors_without_a_state():
    info = describe_with_fetch_error(prawcore.exceptions.ServerError(MagicMock()))
    assert info['state'] is None and info['subscribers'] is None


@pytest.mark.unit
@pytest.mark.parametrize('subreddit_type, quarantine, expected_state', [
    ('public', False, None),
    ('restricted', False, 'restricted'),
    ('archived', False, 'archived'),
    ('public', True, 'quarantined'),
])
def test_describe_subreddit_reads_the_type_of_a_fetched_subreddit(subreddit_type, quarantine, expected_state):
    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value = MagicMock(
        subscribers=50, over18=True, subreddit_type=subreddit_type, quarantine=quarantine
    )
    with patch('racb.phases.phase1.reddit.get_reddit_instance', return_value=mock_reddit):
        info = phase1.describe_subreddit('somesub')
    assert info == {'name': 'somesub', 'state': expected_state, 'subscribers': 50, 'over18': True}


@pytest.mark.unit
@pytest.mark.parametrize('suggestion, expected_line', [
    ({'name': 'pics', 'state': None, 'subscribers': 1234567, 'over18': False},
     '* r/pics (subscribers: 1,234,567)'),
    ({'name': 'nsfwsub', 'state': None, 'subscribers': 10, 'over18': True},
     '* r/nsfwsub (**NSFW**, subscribers: 10)'),
    ({'name': 'smallsub', 'state': 'restricted', 'subscribers': 50, 'over18': False},
     '* r/smallsub (**restricted**, subscribers: 50)'),
    ({'name': 'secret', 'state': 'private', 'subscribers': None, 'over18': False}, '* r/secret (**private**)'),
    ({'name': 'gone', 'state': 'banned', 'subscribers': None, 'over18': False}, '* r/gone (**banned**)'),
    ({'name': 'unknown', 'state': None, 'subscribers': None, 'over18': False}, '* r/unknown'),
])
def test_subreddit_suggestion_list_line(suggestion, expected_line):
    assert phase1.get_subreddit_suggestion_list_line(suggestion, 'en') == expected_line


@pytest.mark.unit
def test_german_suggestion_line_translates_subscribers_but_not_the_state():
    suggestion = {'name': 'geheim', 'state': 'restricted', 'subscribers': 7, 'over18': False}
    assert phase1.get_subreddit_suggestion_list_line(suggestion, 'de') == '* r/geheim (**restricted**, Abonnenten: 7)'


@pytest.mark.unit
def test_quick_duplicate_check_reads_newest_and_all_time_top_posts(make_mock_comment):
    comment = make_mock_comment(body='r/target_sub')
    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value.search.return_value = []

    with patch('racb.core.reddit.get_reddit_instance', return_value=mock_reddit), \
            patch('racb.core.duplicate_detector.scan_subreddit_for_duplicates', return_value=None) as mock_scan:
        phase1.get_posts_with_same_content(comment, 'target_sub')

    assert mock_scan.call_args.kwargs['limit'] == 100
    assert mock_scan.call_args.kwargs['top_limit'] == 100


@pytest.mark.unit
def test_handle_incoming_comment_ignores_blacklisted_target(make_mock_comment):
    comment = make_mock_comment(body='r/reviewsbyretards', subreddit='test_source')
    with patch('racb.phases.phase1.get_posts_with_same_content') as mock_search, \
            patch('racb.core.db.add_comment') as mock_db_add:
        assert phase1.handle_incoming_comment(comment) is None
    mock_search.assert_not_called()
    mock_db_add.assert_not_called()


def search_for_same_content(comment, title_results=(), **kwargs):
    """Runs get_posts_with_same_content with an empty URL search; the title search returns `title_results`."""
    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value.search.side_effect = lambda query, **_: (
        list(title_results) if query.startswith('title:') else []
    )
    with patch('racb.core.reddit.get_reddit_instance', return_value=mock_reddit), \
            patch('racb.core.duplicate_detector.scan_subreddit_for_duplicates', return_value=None) as mock_scan:
        result = phase1.get_posts_with_same_content(comment, 'target_sub', **kwargs)
    return result, mock_reddit, mock_scan


def make_post_in(subreddit, post_id='other', title='Another title'):
    post = MagicMock(id=post_id, title=title)
    post.subreddit.display_name = subreddit
    return post


@pytest.mark.unit
def test_other_discussion_in_the_target_counts_as_same_content(make_mock_comment, make_mock_submission):
    crosspost = make_post_in('Target_Sub')
    submission = make_mock_submission(duplicates=[make_post_in('elsewhere', 'x'), crosspost])
    comment = make_mock_comment(body='r/target_sub', submission=submission)
    result, _, mock_scan = search_for_same_content(comment)
    assert result.posts_found is True
    assert result.posts == [crosspost]
    mock_scan.assert_not_called()


@pytest.mark.unit
def test_other_discussions_elsewhere_do_not_count(make_mock_comment, make_mock_submission):
    submission = make_mock_submission(duplicates=[make_post_in('elsewhere')])
    comment = make_mock_comment(body='r/target_sub', submission=submission)
    result, _, mock_scan = search_for_same_content(comment)
    assert result.posts_found is False
    mock_scan.assert_called_once()


@pytest.mark.unit
def test_failed_other_discussions_call_does_not_stop_the_check(make_mock_comment, make_mock_submission):
    submission = make_mock_submission()
    submission.duplicates.side_effect = prawcore.exceptions.ServerError(MagicMock(status_code=500))
    comment = make_mock_comment(body='r/target_sub', submission=submission)
    result, _, mock_scan = search_for_same_content(comment)
    assert result.posts_found is False
    mock_scan.assert_called_once()


TITLE = 'Doom on a pregnancy tester'


@pytest.mark.unit
def test_title_search_finds_a_post_with_the_same_title(make_mock_comment, make_mock_submission):
    comment = make_mock_comment(body='r/target_sub', submission=make_mock_submission(title=TITLE))
    repost = make_post_in('target_sub', 'repost', title='doom on a pregnancy tester!')
    unrelated = make_post_in('target_sub', 'unrelated', title='Doom on a pregnancy tester, part 2')
    result, mock_reddit, _ = search_for_same_content(comment, title_results=[unrelated, repost], search_titles=True)
    assert result.posts == [repost]
    queries = [call.kwargs['query'] for call in mock_reddit.subreddit.return_value.search.call_args_list]
    assert f'title:"{TITLE}"' in queries


@pytest.mark.unit
def test_title_search_is_off_by_default(make_mock_comment, make_mock_submission):
    comment = make_mock_comment(body='r/target_sub', submission=make_mock_submission(title=TITLE))
    repost = make_post_in('target_sub', 'repost', title=TITLE)
    result, _, _ = search_for_same_content(comment, title_results=[repost])
    assert result.posts_found is False


@pytest.mark.unit
def test_short_titles_are_not_searched(make_mock_comment, make_mock_submission):
    comment = make_mock_comment(body='r/target_sub', submission=make_mock_submission(title='My cat'))
    repost = make_post_in('target_sub', 'repost', title='My cat')
    result, mock_reddit, _ = search_for_same_content(comment, title_results=[repost], search_titles=True)
    assert result.posts_found is False
    queries = [call.kwargs['query'] for call in mock_reddit.subreddit.return_value.search.call_args_list]
    assert not any(query.startswith('title:') for query in queries)


@pytest.mark.unit
@pytest.mark.parametrize('reason, expected', [
    ('banned', 'SUBREDDIT_IS_BANNED'),
    (None, 'SUBREDDIT_DOES_NOT_EXIST'),
])
def test_search_404_tells_a_banned_subreddit_from_a_nonexistent_one(make_mock_comment, reason, expected):
    comment = make_mock_comment()
    mock_reddit = MagicMock()
    error = make_prawcore_error(prawcore.exceptions.NotFound, 404, reason)
    mock_reddit.subreddit.return_value.search.side_effect = error
    with patch('racb.core.reddit.get_reddit_instance', return_value=mock_reddit):
        result = phase1.get_posts_with_same_content(comment, 'jar')
    assert result.unable_to_search is True
    assert result.unable_to_search_reason == expected


@pytest.mark.unit
def test_banned_target_gets_no_sub_doesnt_exist_reply(make_mock_comment):
    comment = make_mock_comment(body='r/jar')
    result = MagicMock(posts_found=False, unable_to_search=True, unable_to_search_reason='SUBREDDIT_IS_BANNED')
    with patch('racb.phases.phase1.get_posts_with_same_content', return_value=result), \
            patch('racb.phases.phase1.reply_to_nonexistent_target_subreddit_comment') as mock_reply, \
            patch('racb.phases.phase1.db.add_comment'):
        phase1.handle_incoming_comment(comment)
    mock_reply.assert_not_called()
