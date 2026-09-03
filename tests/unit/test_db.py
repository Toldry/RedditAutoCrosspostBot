"""Unit tests for racb.core.db module."""

from unittest.mock import patch, MagicMock
import pytest
from racb.core import db


@pytest.mark.unit
def test_env_bool():
    with patch.dict('os.environ', {'TEST_BOOL_TRUE': 'true', 'TEST_BOOL_1': '1', 'TEST_BOOL_FALSE': 'false', 'TEST_BOOL_0': '0'}):
        assert db.env_bool('TEST_BOOL_TRUE') is True
        assert db.env_bool('TEST_BOOL_1') is True
        assert db.env_bool('TEST_BOOL_FALSE') is False
        assert db.env_bool('TEST_BOOL_0') is False
        assert db.env_bool('NON_EXISTENT_VAR', default=True) is True
        assert db.env_bool('NON_EXISTENT_VAR', default=False) is False


@pytest.mark.unit
def test_mask_db_url():
    url_with_pass = 'postgresql://myuser:secret123@localhost:5432/mydb'
    masked = db._mask_db_url(url_with_pass)
    assert 'secret123' not in masked
    assert '***' in masked
    assert 'myuser' in masked

    assert db._mask_db_url('') == '<empty>'
    assert db._mask_db_url(None) == '<empty>'


@pytest.mark.unit
def test_db_operations(make_mock_comment):
    comment = make_mock_comment(permalink='/r/test/comments/123/abc/')
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [{'id': 1, 'permalink': '/r/test/'}]

    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch('racb.core.db.get_connection', return_value=mock_conn):
        # 1. add_comment
        db.add_comment(comment)
        mock_cursor.callproc.assert_called_with('insert_scraped_comment', (comment.permalink,))
        mock_conn.commit.assert_called()

        # 2. get_comments_older_than
        comments = db.get_comments_older_than(3600)
        mock_cursor.callproc.assert_called_with('get_comments_older_than', ('3600 seconds',))
        assert len(comments) == 1

        # 3. get_unchecked_comments_older_than
        unchecked = db.get_unchecked_comments_older_than(7200)
        mock_cursor.callproc.assert_called_with('get_unchecked_comments_older_than', ('7200 seconds',))
        assert len(unchecked) == 1

        # 4. delete_comment
        db.delete_comment({'id': 42})
        mock_cursor.callproc.assert_called_with('delete_scraped_comment', (42,))

        # 5. set_comment_checked
        db.set_comment_checked({'id': 42})
        mock_cursor.callproc.assert_called_with('set_comment_checked', (42,))
