"""Unit tests for scripts/delete_test_artifacts.py."""

import json
from unittest.mock import MagicMock, patch
import pytest

from scripts import delete_test_artifacts
from scripts.delete_test_artifacts import (
    delete_recorded_artifacts,
    main,
    manifest_path,
    read_manifest,
    record_artifact,
)


def _item(fullname, author='AutoCrosspostBot'):
    item = MagicMock()
    item.fullname = fullname
    item.author = author
    return item


def _reddit_returning(items):
    bot_reddit = MagicMock()
    bot_reddit.info.return_value = iter(items)
    return bot_reddit


@pytest.mark.unit
def test_manifest_path_uses_environment_variable(monkeypatch, tmp_path):
    custom_path = str(tmp_path / 'custom.jsonl')
    monkeypatch.setenv('RACB_TEST_ARTIFACTS_FILE', custom_path)
    assert manifest_path() == custom_path

    monkeypatch.delenv('RACB_TEST_ARTIFACTS_FILE')
    assert manifest_path().endswith('racb-test-artifacts.jsonl')


@pytest.mark.unit
def test_record_and_read_manifest(tmp_path):
    path = str(tmp_path / 'manifest.jsonl')
    record_artifact('AutoCrosspostBot', 't3_post', path)
    record_artifact('same_post_bot', 't1_reply', path)
    record_artifact('AutoCrosspostBot', 't1_comment', path)
    record_artifact('AutoCrosspostBot', 't3_post', path)

    assert read_manifest(path) == {
        'AutoCrosspostBot': ['t3_post', 't1_comment'],
        'same_post_bot': ['t1_reply'],
    }


@pytest.mark.unit
def test_read_manifest_skips_malformed_lines(tmp_path):
    path = tmp_path / 'manifest.jsonl'
    path.write_text(json.dumps({'bot': 'AutoCrosspostBot', 'fullname': 't3_a'}) + '\n{"bot": "Auto')
    assert read_manifest(str(path)) == {'AutoCrosspostBot': ['t3_a']}


@pytest.mark.unit
def test_read_manifest_missing_file(tmp_path):
    assert read_manifest(str(tmp_path / 'missing.jsonl')) == {}


@pytest.mark.unit
def test_delete_recorded_artifacts_deletes_only_live_items(tmp_path):
    path = str(tmp_path / 'manifest.jsonl')
    record_artifact('AutoCrosspostBot', 't3_live', path)
    record_artifact('AutoCrosspostBot', 't3_deleted', path)
    live_item = _item('t3_live')
    deleted_item = _item('t3_deleted', author=None)
    bot_reddit = _reddit_returning([live_item, deleted_item])

    with patch.object(delete_test_artifacts.reddit, 'get_reddit_instance', return_value=bot_reddit) as get_instance:
        errors = delete_recorded_artifacts(path)

    assert errors == []
    get_instance.assert_called_once_with('AutoCrosspostBot')
    bot_reddit.info.assert_called_once_with(fullnames=['t3_live', 't3_deleted'])
    live_item.delete.assert_called_once()
    deleted_item.delete.assert_not_called()
    # A clean run removes the manifest, so the next test run starts empty
    assert read_manifest(path) == {}


@pytest.mark.unit
def test_delete_recorded_artifacts_keeps_manifest_on_failure(tmp_path):
    path = str(tmp_path / 'manifest.jsonl')
    record_artifact('AutoCrosspostBot', 't3_stuck', path)
    stuck_item = _item('t3_stuck')
    stuck_item.delete.side_effect = Exception('403 Forbidden')

    with patch.object(delete_test_artifacts.reddit, 'get_reddit_instance', return_value=_reddit_returning([stuck_item])):
        errors = delete_recorded_artifacts(path)

    assert len(errors) == 1
    assert 't3_stuck' in errors[0]
    assert read_manifest(path) == {'AutoCrosspostBot': ['t3_stuck']}


@pytest.mark.unit
def test_delete_recorded_artifacts_records_lookup_and_unknown_bot_errors(tmp_path):
    path = str(tmp_path / 'manifest.jsonl')
    record_artifact('AutoCrosspostBot', 't3_a', path)
    record_artifact('someone_else', 't3_b', path)
    failing_reddit = MagicMock()
    failing_reddit.info.side_effect = Exception('503 Service Unavailable')

    def get_instance(bot_name):
        if bot_name == 'someone_else':
            raise KeyError(bot_name)
        return failing_reddit

    with patch.object(delete_test_artifacts.reddit, 'get_reddit_instance', side_effect=get_instance):
        errors = delete_recorded_artifacts(path)

    assert len(errors) == 2
    assert any('could not look up' in error for error in errors)
    assert any('unknown bot' in error for error in errors)


@pytest.mark.unit
def test_delete_recorded_artifacts_dry_run(tmp_path):
    path = str(tmp_path / 'manifest.jsonl')
    record_artifact('AutoCrosspostBot', 't3_live', path)
    live_item = _item('t3_live')

    with patch.object(delete_test_artifacts.reddit, 'get_reddit_instance', return_value=_reddit_returning([live_item])):
        errors = delete_recorded_artifacts(path, dry_run=True)

    assert errors == []
    live_item.delete.assert_not_called()
    assert read_manifest(path) == {'AutoCrosspostBot': ['t3_live']}


@pytest.mark.unit
def test_delete_recorded_artifacts_without_manifest(tmp_path):
    with patch.object(delete_test_artifacts.reddit, 'get_reddit_instance') as get_instance:
        assert delete_recorded_artifacts(str(tmp_path / 'missing.jsonl')) == []
    get_instance.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize('strict, errors, expected_exit', [
    (True, ['could not delete t3_a'], 1),
    (False, ['could not delete t3_a'], None),
    (True, [], None),
])
def test_main_exit_codes(monkeypatch, strict, errors, expected_exit):
    argv = ['delete_test_artifacts.py'] + (['--strict'] if strict else [])
    monkeypatch.setattr('sys.argv', argv)
    with patch.object(delete_test_artifacts, 'delete_recorded_artifacts', return_value=errors):
        if expected_exit is None:
            main()
        else:
            with pytest.raises(SystemExit) as exit_info:
                main()
            assert exit_info.value.code == expected_exit
