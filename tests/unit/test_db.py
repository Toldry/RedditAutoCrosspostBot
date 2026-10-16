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
        db.add_comment(comment, 'target_sub')
        mock_cursor.callproc.assert_called_with('insert_scraped_comment', (comment.permalink, 'target_sub'))
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

        # 6. get_oldest_comment
        mock_cursor.fetchone.return_value = {'id': 42, 'permalink': '/r/test/'}
        assert db.get_oldest_comment()['id'] == 42
        mock_cursor.callproc.assert_called_with('get_oldest_scraped_comment')


@pytest.mark.unit
def test_each_thread_gets_its_own_connection():
    import threading
    with patch('racb.core.db.get_db_connection', side_effect=lambda: MagicMock(closed=False)), \
         patch('racb.core.db.instantiate_database') as mock_instantiate:
        db.reset_connection()
        main_thread_connection = db.get_connection()
        assert db.get_connection() is main_thread_connection
        other_thread_connections = []
        thread = threading.Thread(target=lambda: other_thread_connections.append(db.get_connection()))
        thread.start()
        thread.join()
        assert other_thread_connections[0] is not main_thread_connection
        assert mock_instantiate.call_count == 2
        db.reset_connection()
        assert db.get_connection() is not main_thread_connection
        db.reset_connection()
