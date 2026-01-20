"""
Unit tests for core/registry_db.py

Tests SQLite-based session registry with SQLAlchemy ORM.
"""

import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add core directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))

from registry_db import RegistryDatabase, SessionRecord, DMSubscription, AskUserQuestion, Base


class TestRegistryDatabaseInit:
    """Tests for RegistryDatabase initialization."""

    def test_init_creates_database(self, temp_db_path):
        """Database file is created on initialization."""
        assert not os.path.exists(temp_db_path)
        db = RegistryDatabase(temp_db_path)
        assert os.path.exists(temp_db_path)

    def test_init_creates_tables(self, temp_db_path):
        """Sessions table is created on initialization."""
        db = RegistryDatabase(temp_db_path)
        # Query should not raise
        sessions = db.list_sessions()
        assert isinstance(sessions, list)

    def test_init_enables_wal_mode(self, temp_db_path):
        """WAL mode is enabled for concurrency."""
        db = RegistryDatabase(temp_db_path)
        with db.engine.connect() as conn:
            from sqlalchemy import text
            result = conn.execute(text("PRAGMA journal_mode"))
            mode = result.fetchone()[0]
            assert mode.lower() == 'wal'


class TestCreateSession:
    """Tests for create_session()"""

    def test_create_session_basic(self, temp_registry_db, sample_session_data):
        """Creates a new session record."""
        result = temp_registry_db.create_session(sample_session_data)
        assert result['session_id'] == sample_session_data['session_id']
        assert result['project'] == sample_session_data['project']
        assert result['status'] == 'active'

    def test_create_session_with_all_fields(self, temp_registry_db):
        """Creates session with all optional fields."""
        data = {
            'session_id': 'full1234',
            'project': 'full-project',
            'project_dir': '/path/to/project',
            'terminal': 'terminal-1',
            'socket_path': '/tmp/full.sock',
            'thread_ts': '1234567890.123456',
            'channel': 'C123456',
            'permissions_channel': 'C789012',
            'slack_user_id': 'U111111',
        }
        result = temp_registry_db.create_session(data)
        assert result['session_id'] == 'full1234'
        assert result['project_dir'] == '/path/to/project'
        assert result['permissions_channel'] == 'C789012'

    def test_create_session_sets_timestamps(self, temp_registry_db, sample_session_data):
        """created_at and last_activity are set automatically."""
        before = datetime.now()
        result = temp_registry_db.create_session(sample_session_data)
        after = datetime.now()

        created = datetime.fromisoformat(result['created_at'])
        assert before <= created <= after

        activity = datetime.fromisoformat(result['last_activity'])
        assert before <= activity <= after


class TestGetSession:
    """Tests for get_session()"""

    def test_get_session_exists(self, temp_registry_db, sample_session_data):
        """Retrieves existing session by ID."""
        temp_registry_db.create_session(sample_session_data)
        result = temp_registry_db.get_session(sample_session_data['session_id'])
        assert result is not None
        assert result['session_id'] == sample_session_data['session_id']

    def test_get_session_not_found(self, temp_registry_db):
        """Returns None for non-existent session."""
        result = temp_registry_db.get_session('nonexistent')
        assert result is None


class TestUpdateSession:
    """Tests for update_session()"""

    def test_update_session_status(self, temp_registry_db, sample_session_data):
        """Updates session status field."""
        temp_registry_db.create_session(sample_session_data)
        result = temp_registry_db.update_session(
            sample_session_data['session_id'],
            {'status': 'idle'}
        )
        assert result is True

        updated = temp_registry_db.get_session(sample_session_data['session_id'])
        assert updated['status'] == 'idle'

    def test_update_session_slack_metadata(self, temp_registry_db, sample_session_data):
        """Updates Slack-related fields."""
        temp_registry_db.create_session(sample_session_data)
        result = temp_registry_db.update_session(
            sample_session_data['session_id'],
            {
                'slack_thread_ts': 'new.thread.ts',
                'slack_channel': 'C999999',
                'todo_message_ts': 'todo.ts.123'
            }
        )
        assert result is True

        updated = temp_registry_db.get_session(sample_session_data['session_id'])
        assert updated['thread_ts'] == 'new.thread.ts'
        assert updated['channel'] == 'C999999'
        assert updated['todo_message_ts'] == 'todo.ts.123'

    def test_update_session_not_found(self, temp_registry_db):
        """Returns False for non-existent session."""
        result = temp_registry_db.update_session('nonexistent', {'status': 'idle'})
        assert result is False

    def test_update_session_updates_last_activity(self, temp_registry_db, sample_session_data):
        """last_activity is updated on any update."""
        temp_registry_db.create_session(sample_session_data)

        # Wait briefly to ensure timestamp changes
        time.sleep(0.01)

        temp_registry_db.update_session(
            sample_session_data['session_id'],
            {'status': 'idle'}
        )

        updated = temp_registry_db.get_session(sample_session_data['session_id'])
        created = datetime.fromisoformat(updated['created_at'])
        activity = datetime.fromisoformat(updated['last_activity'])
        assert activity >= created


class TestDeleteSession:
    """Tests for delete_session()"""

    def test_delete_session_exists(self, temp_registry_db, sample_session_data):
        """Deletes existing session."""
        temp_registry_db.create_session(sample_session_data)
        result = temp_registry_db.delete_session(sample_session_data['session_id'])
        assert result is True

        # Verify deleted
        session = temp_registry_db.get_session(sample_session_data['session_id'])
        assert session is None

    def test_delete_session_not_found(self, temp_registry_db):
        """Returns False for non-existent session."""
        result = temp_registry_db.delete_session('nonexistent')
        assert result is False


class TestListSessions:
    """Tests for list_sessions()"""

    def test_list_sessions_all(self, temp_registry_db, sample_session_data):
        """Lists all sessions."""
        temp_registry_db.create_session(sample_session_data)

        data2 = sample_session_data.copy()
        data2['session_id'] = 'test5678'
        temp_registry_db.create_session(data2)

        sessions = temp_registry_db.list_sessions()
        assert len(sessions) == 2

    def test_list_sessions_by_status(self, temp_registry_db, sample_session_data):
        """Filters sessions by status."""
        temp_registry_db.create_session(sample_session_data)

        data2 = sample_session_data.copy()
        data2['session_id'] = 'idle5678'
        temp_registry_db.create_session(data2)
        temp_registry_db.update_session('idle5678', {'status': 'idle'})

        active = temp_registry_db.list_sessions(status='active')
        assert len(active) == 1
        assert active[0]['session_id'] == sample_session_data['session_id']

        idle = temp_registry_db.list_sessions(status='idle')
        assert len(idle) == 1
        assert idle[0]['session_id'] == 'idle5678'

    def test_list_sessions_empty(self, temp_registry_db):
        """Returns empty list when no sessions."""
        sessions = temp_registry_db.list_sessions()
        assert sessions == []


class TestGetByThread:
    """Tests for get_by_thread()"""

    def test_get_by_thread_exists(self, temp_registry_db, sample_session_data):
        """Finds session by thread_ts."""
        temp_registry_db.create_session(sample_session_data)
        result = temp_registry_db.get_by_thread(sample_session_data['thread_ts'])
        assert result is not None
        assert result['session_id'] == sample_session_data['session_id']

    def test_get_by_thread_not_found(self, temp_registry_db):
        """Returns None when thread not found."""
        result = temp_registry_db.get_by_thread('nonexistent.thread')
        assert result is None


class TestGetByProjectDir:
    """Tests for get_by_project_dir()"""

    def test_get_by_project_dir_exists(self, temp_registry_db, sample_session_data):
        """Finds session by project directory."""
        temp_registry_db.create_session(sample_session_data)
        result = temp_registry_db.get_by_project_dir(sample_session_data['project_dir'])
        assert result is not None
        assert result['session_id'] == sample_session_data['session_id']

    def test_get_by_project_dir_filters_status(self, temp_registry_db, sample_session_data):
        """Respects status filter."""
        temp_registry_db.create_session(sample_session_data)
        temp_registry_db.update_session(sample_session_data['session_id'], {'status': 'ended'})

        # Active filter should not find it
        result = temp_registry_db.get_by_project_dir(
            sample_session_data['project_dir'],
            status='active'
        )
        assert result is None

        # Ended filter should find it
        result = temp_registry_db.get_by_project_dir(
            sample_session_data['project_dir'],
            status='ended'
        )
        assert result is not None

    def test_get_by_project_dir_returns_most_recent(self, temp_registry_db, sample_session_data):
        """Returns most recently created session for project."""
        temp_registry_db.create_session(sample_session_data)

        time.sleep(0.01)

        data2 = sample_session_data.copy()
        data2['session_id'] = 'newer123'
        temp_registry_db.create_session(data2)

        result = temp_registry_db.get_by_project_dir(sample_session_data['project_dir'])
        assert result['session_id'] == 'newer123'


class TestCleanupOldSessions:
    """Tests for cleanup_old_sessions()"""

    def test_cleanup_old_sessions(self, temp_registry_db, sample_session_data):
        """Deletes sessions older than specified hours."""
        temp_registry_db.create_session(sample_session_data)

        # Manually set last_activity to 25 hours ago
        with temp_registry_db.session_scope() as session:
            record = session.query(SessionRecord).filter_by(
                session_id=sample_session_data['session_id']
            ).first()
            record.last_activity = datetime.now() - timedelta(hours=25)

        count = temp_registry_db.cleanup_old_sessions(older_than_hours=24)
        assert count == 1

        # Verify deleted
        result = temp_registry_db.get_session(sample_session_data['session_id'])
        assert result is None

    def test_cleanup_preserves_recent_sessions(self, temp_registry_db, sample_session_data):
        """Preserves sessions within age threshold."""
        temp_registry_db.create_session(sample_session_data)

        count = temp_registry_db.cleanup_old_sessions(older_than_hours=24)
        assert count == 0

        # Verify still exists
        result = temp_registry_db.get_session(sample_session_data['session_id'])
        assert result is not None


class TestConcurrency:
    """Tests for concurrent database access."""

    def test_concurrent_reads(self, temp_registry_db, sample_session_data):
        """WAL mode allows concurrent reads."""
        temp_registry_db.create_session(sample_session_data)

        results = []
        errors = []

        def read_session():
            try:
                result = temp_registry_db.get_session(sample_session_data['session_id'])
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=read_session) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 10
        assert all(r['session_id'] == sample_session_data['session_id'] for r in results)


class TestSessionScope:
    """Tests for session_scope() context manager."""

    def test_session_scope_commits_on_success(self, temp_registry_db, sample_session_data):
        """Transaction is committed when no error."""
        with temp_registry_db.session_scope() as session:
            record = SessionRecord(
                session_id='scope123',
                project='scope-project',
                terminal='scope-terminal',
                socket_path='/tmp/scope.sock',
                status='active'
            )
            session.add(record)

        # Verify committed
        result = temp_registry_db.get_session('scope123')
        assert result is not None

    def test_session_scope_rollback_on_error(self, temp_registry_db, sample_session_data):
        """Transaction is rolled back on error."""
        try:
            with temp_registry_db.session_scope() as session:
                record = SessionRecord(
                    session_id='rollback1',
                    project='rollback-project',
                    terminal='rollback-terminal',
                    socket_path='/tmp/rollback.sock',
                    status='active'
                )
                session.add(record)
                session.flush()
                raise ValueError("Simulated error")
        except ValueError:
            pass

        # Verify rolled back
        result = temp_registry_db.get_session('rollback1')
        assert result is None


class TestSessionRecordToDict:
    """Tests for SessionRecord.to_dict()"""

    def test_to_dict_includes_all_fields(self, temp_registry_db, sample_session_data):
        """to_dict() includes all expected fields."""
        temp_registry_db.create_session(sample_session_data)
        result = temp_registry_db.get_session(sample_session_data['session_id'])

        expected_fields = [
            'session_id', 'project', 'project_dir', 'terminal', 'socket_path',
            'thread_ts', 'channel', 'permissions_channel', 'slack_user_id',
            'reply_to_ts', 'todo_message_ts', 'buffer_file_path',
            'status', 'created_at', 'last_activity'
        ]
        for field in expected_fields:
            assert field in result, f"Missing field: {field}"


class TestSchemaMigration:
    """Tests for database schema migrations."""

    def test_migration_adds_project_dir(self, temp_db_path):
        """project_dir column is added if missing."""
        # Create database with current schema
        db = RegistryDatabase(temp_db_path)

        # Verify column exists
        with db.engine.connect() as conn:
            from sqlalchemy import text
            result = conn.execute(text("PRAGMA table_info(sessions)"))
            columns = [row[1] for row in result.fetchall()]
            assert 'project_dir' in columns

    def test_migration_adds_buffer_file_path(self, temp_db_path):
        """buffer_file_path column is added if missing."""
        db = RegistryDatabase(temp_db_path)

        with db.engine.connect() as conn:
            from sqlalchemy import text
            result = conn.execute(text("PRAGMA table_info(sessions)"))
            columns = [row[1] for row in result.fetchall()]
            assert 'buffer_file_path' in columns


class TestDMSubscriptions:
    """Tests for DM subscription CRUD methods."""

    def test_create_dm_subscription(self, temp_registry_db, sample_session_data):
        """Create subscription returns dict with user_id, session_id, dm_channel_id, created_at."""
        # Create a session first
        temp_registry_db.create_session(sample_session_data)

        # Create subscription
        result = temp_registry_db.create_dm_subscription(
            user_id='U123456',
            session_id=sample_session_data['session_id'],
            dm_channel_id='D123456'
        )

        assert result['user_id'] == 'U123456'
        assert result['session_id'] == sample_session_data['session_id']
        assert result['dm_channel_id'] == 'D123456'
        assert result['created_at'] is not None
        assert result['id'] is not None

    def test_create_dm_subscription_duplicate_replaces(self, temp_registry_db, sample_session_data):
        """Second subscription for same user replaces first (one subscription per user)."""
        temp_registry_db.create_session(sample_session_data)

        # Create another session
        session2_data = sample_session_data.copy()
        session2_data['session_id'] = 'sess5678'
        temp_registry_db.create_session(session2_data)

        # Create first subscription
        first = temp_registry_db.create_dm_subscription(
            user_id='U123456',
            session_id=sample_session_data['session_id'],
            dm_channel_id='D123456'
        )

        # Create second subscription (replaces first)
        second = temp_registry_db.create_dm_subscription(
            user_id='U123456',
            session_id='sess5678',
            dm_channel_id='D123456'
        )

        # User should only have one subscription
        sub = temp_registry_db.get_dm_subscription_for_user('U123456')
        assert sub['session_id'] == 'sess5678'

        # No subscription for first session
        subs_for_first = temp_registry_db.get_dm_subscriptions_for_session(sample_session_data['session_id'])
        assert len(subs_for_first) == 0

    def test_get_dm_subscriptions_for_session(self, temp_registry_db, sample_session_data):
        """Returns list of all subscribers for a session."""
        temp_registry_db.create_session(sample_session_data)

        # Create multiple subscriptions for same session
        temp_registry_db.create_dm_subscription(
            user_id='U111111',
            session_id=sample_session_data['session_id'],
            dm_channel_id='D111111'
        )
        temp_registry_db.create_dm_subscription(
            user_id='U222222',
            session_id=sample_session_data['session_id'],
            dm_channel_id='D222222'
        )

        subs = temp_registry_db.get_dm_subscriptions_for_session(sample_session_data['session_id'])
        assert len(subs) == 2
        user_ids = {s['user_id'] for s in subs}
        assert user_ids == {'U111111', 'U222222'}

    def test_get_dm_subscription_for_user(self, temp_registry_db, sample_session_data):
        """Returns user's current subscription or None."""
        temp_registry_db.create_session(sample_session_data)

        # No subscription initially
        assert temp_registry_db.get_dm_subscription_for_user('U123456') is None

        # Create subscription
        temp_registry_db.create_dm_subscription(
            user_id='U123456',
            session_id=sample_session_data['session_id'],
            dm_channel_id='D123456'
        )

        # Now should have subscription
        sub = temp_registry_db.get_dm_subscription_for_user('U123456')
        assert sub is not None
        assert sub['user_id'] == 'U123456'

    def test_delete_dm_subscription(self, temp_registry_db, sample_session_data):
        """Removes subscription, returns True."""
        temp_registry_db.create_session(sample_session_data)
        temp_registry_db.create_dm_subscription(
            user_id='U123456',
            session_id=sample_session_data['session_id'],
            dm_channel_id='D123456'
        )

        result = temp_registry_db.delete_dm_subscription('U123456')
        assert result is True

        # Subscription should be gone
        sub = temp_registry_db.get_dm_subscription_for_user('U123456')
        assert sub is None

    def test_delete_dm_subscription_not_found(self, temp_registry_db):
        """Returns False when no subscription exists."""
        result = temp_registry_db.delete_dm_subscription('U999999')
        assert result is False

    def test_cleanup_dm_subscriptions_for_session(self, temp_registry_db, sample_session_data):
        """Removes all subscriptions for a session, returns count."""
        temp_registry_db.create_session(sample_session_data)

        # Create multiple subscriptions
        temp_registry_db.create_dm_subscription(
            user_id='U111111',
            session_id=sample_session_data['session_id'],
            dm_channel_id='D111111'
        )
        temp_registry_db.create_dm_subscription(
            user_id='U222222',
            session_id=sample_session_data['session_id'],
            dm_channel_id='D222222'
        )

        count = temp_registry_db.cleanup_dm_subscriptions_for_session(sample_session_data['session_id'])
        assert count == 2

        # All subscriptions should be gone
        subs = temp_registry_db.get_dm_subscriptions_for_session(sample_session_data['session_id'])
        assert len(subs) == 0


class TestAskUserQuestionCreate:
    """Tests for create_askuser_question()"""

    def test_create_askuser_question_basic(self, temp_registry_db, sample_session_data):
        """Creates a new AskUserQuestion record."""
        temp_registry_db.create_session(sample_session_data)

        result = temp_registry_db.create_askuser_question(
            session_id=sample_session_data['session_id'],
            request_id='req-123',
            question_data='{"questions": []}',
        )

        assert result['session_id'] == sample_session_data['session_id']
        assert result['request_id'] == 'req-123'
        assert result['status'] == 'pending'
        assert result['question_data'] == '{"questions": []}'
        assert result['id'] is not None
        assert result['created_at'] is not None
        assert result['answer_data'] is None
        assert result['answered_at'] is None

    def test_create_askuser_question_with_slack_info(self, temp_registry_db, sample_session_data):
        """Creates AskUserQuestion with Slack channel and message ts."""
        temp_registry_db.create_session(sample_session_data)

        result = temp_registry_db.create_askuser_question(
            session_id=sample_session_data['session_id'],
            request_id='req-456',
            question_data='{"questions": [{"question": "Test?"}]}',
            slack_channel='C123456',
            slack_message_ts='1234567890.123456'
        )

        assert result['slack_channel'] == 'C123456'
        assert result['slack_message_ts'] == '1234567890.123456'

    def test_create_askuser_question_unique_request_id(self, temp_registry_db, sample_session_data):
        """request_id must be unique."""
        temp_registry_db.create_session(sample_session_data)

        temp_registry_db.create_askuser_question(
            session_id=sample_session_data['session_id'],
            request_id='unique-req',
            question_data='{}',
        )

        # Duplicate should raise
        with pytest.raises(Exception):  # IntegrityError
            temp_registry_db.create_askuser_question(
                session_id=sample_session_data['session_id'],
                request_id='unique-req',
                question_data='{}',
            )


class TestAskUserQuestionGet:
    """Tests for get_askuser_question() and related getters."""

    def test_get_askuser_question_exists(self, temp_registry_db, sample_session_data):
        """Retrieves existing question by request_id."""
        temp_registry_db.create_session(sample_session_data)
        temp_registry_db.create_askuser_question(
            session_id=sample_session_data['session_id'],
            request_id='get-req-123',
            question_data='{"q": 1}',
        )

        result = temp_registry_db.get_askuser_question('get-req-123')
        assert result is not None
        assert result['request_id'] == 'get-req-123'

    def test_get_askuser_question_not_found(self, temp_registry_db):
        """Returns None for non-existent request_id."""
        result = temp_registry_db.get_askuser_question('nonexistent')
        assert result is None

    def test_get_askuser_question_by_message(self, temp_registry_db, sample_session_data):
        """Finds question by Slack channel and message ts."""
        temp_registry_db.create_session(sample_session_data)
        temp_registry_db.create_askuser_question(
            session_id=sample_session_data['session_id'],
            request_id='msg-req-123',
            question_data='{}',
            slack_channel='C999999',
            slack_message_ts='9999999999.999999'
        )

        result = temp_registry_db.get_askuser_question_by_message(
            slack_channel='C999999',
            slack_message_ts='9999999999.999999'
        )
        assert result is not None
        assert result['request_id'] == 'msg-req-123'

    def test_get_askuser_question_by_message_not_found(self, temp_registry_db):
        """Returns None when message not found."""
        result = temp_registry_db.get_askuser_question_by_message(
            slack_channel='CNOTFOUND',
            slack_message_ts='0000000000.000000'
        )
        assert result is None

    def test_get_pending_askuser_questions(self, temp_registry_db, sample_session_data):
        """Returns only pending questions for session, ordered by created_at."""
        temp_registry_db.create_session(sample_session_data)

        # Create 3 questions
        temp_registry_db.create_askuser_question(
            session_id=sample_session_data['session_id'],
            request_id='pending-1',
            question_data='{}',
        )
        time.sleep(0.01)
        temp_registry_db.create_askuser_question(
            session_id=sample_session_data['session_id'],
            request_id='pending-2',
            question_data='{}',
        )
        time.sleep(0.01)
        temp_registry_db.create_askuser_question(
            session_id=sample_session_data['session_id'],
            request_id='answered-1',
            question_data='{}',
        )

        # Answer one
        temp_registry_db.answer_askuser_question('answered-1', '{"answer": "test"}')

        pending = temp_registry_db.get_pending_askuser_questions(sample_session_data['session_id'])
        assert len(pending) == 2
        assert pending[0]['request_id'] == 'pending-1'  # Ordered by created_at
        assert pending[1]['request_id'] == 'pending-2'


class TestAskUserQuestionAnswer:
    """Tests for answer_askuser_question()"""

    def test_answer_askuser_question_success(self, temp_registry_db, sample_session_data):
        """Updates question with answer and marks as answered."""
        temp_registry_db.create_session(sample_session_data)
        temp_registry_db.create_askuser_question(
            session_id=sample_session_data['session_id'],
            request_id='answer-req-123',
            question_data='{}',
        )

        result = temp_registry_db.answer_askuser_question(
            'answer-req-123',
            '{"question_0": "Option A"}'
        )
        assert result is True

        # Verify updated
        question = temp_registry_db.get_askuser_question('answer-req-123')
        assert question['status'] == 'answered'
        assert question['answer_data'] == '{"question_0": "Option A"}'
        assert question['answered_at'] is not None

    def test_answer_askuser_question_not_found(self, temp_registry_db):
        """Returns False for non-existent request_id."""
        result = temp_registry_db.answer_askuser_question('nonexistent', '{}')
        assert result is False


class TestAskUserQuestionExpire:
    """Tests for expire_askuser_question()"""

    def test_expire_askuser_question_success(self, temp_registry_db, sample_session_data):
        """Marks question as expired."""
        temp_registry_db.create_session(sample_session_data)
        temp_registry_db.create_askuser_question(
            session_id=sample_session_data['session_id'],
            request_id='expire-req-123',
            question_data='{}',
        )

        result = temp_registry_db.expire_askuser_question('expire-req-123')
        assert result is True

        question = temp_registry_db.get_askuser_question('expire-req-123')
        assert question['status'] == 'expired'

    def test_expire_askuser_question_not_found(self, temp_registry_db):
        """Returns False for non-existent request_id."""
        result = temp_registry_db.expire_askuser_question('nonexistent')
        assert result is False


class TestAskUserQuestionDelete:
    """Tests for delete_askuser_question()"""

    def test_delete_askuser_question_success(self, temp_registry_db, sample_session_data):
        """Deletes question record."""
        temp_registry_db.create_session(sample_session_data)
        temp_registry_db.create_askuser_question(
            session_id=sample_session_data['session_id'],
            request_id='delete-req-123',
            question_data='{}',
        )

        result = temp_registry_db.delete_askuser_question('delete-req-123')
        assert result is True

        # Verify deleted
        question = temp_registry_db.get_askuser_question('delete-req-123')
        assert question is None

    def test_delete_askuser_question_not_found(self, temp_registry_db):
        """Returns False for non-existent request_id."""
        result = temp_registry_db.delete_askuser_question('nonexistent')
        assert result is False


class TestAskUserQuestionCleanup:
    """Tests for cleanup methods."""

    def test_cleanup_old_askuser_questions(self, temp_registry_db, sample_session_data):
        """Deletes old answered/expired questions."""
        from registry_db import AskUserQuestion

        temp_registry_db.create_session(sample_session_data)

        # Create old answered question
        temp_registry_db.create_askuser_question(
            session_id=sample_session_data['session_id'],
            request_id='old-answered',
            question_data='{}',
        )
        temp_registry_db.answer_askuser_question('old-answered', '{}')

        # Create old expired question
        temp_registry_db.create_askuser_question(
            session_id=sample_session_data['session_id'],
            request_id='old-expired',
            question_data='{}',
        )
        temp_registry_db.expire_askuser_question('old-expired')

        # Create old pending question (should NOT be deleted)
        temp_registry_db.create_askuser_question(
            session_id=sample_session_data['session_id'],
            request_id='old-pending',
            question_data='{}',
        )

        # Manually set created_at to 25 hours ago for all
        with temp_registry_db.session_scope() as session:
            questions = session.query(AskUserQuestion).all()
            for q in questions:
                q.created_at = datetime.now() - timedelta(hours=25)

        count = temp_registry_db.cleanup_old_askuser_questions(older_than_hours=24)
        assert count == 2  # Only answered and expired

        # Pending should still exist
        pending = temp_registry_db.get_askuser_question('old-pending')
        assert pending is not None

    def test_cleanup_askuser_questions_for_session(self, temp_registry_db, sample_session_data):
        """Deletes all questions for a session."""
        temp_registry_db.create_session(sample_session_data)

        # Create multiple questions
        for i in range(3):
            temp_registry_db.create_askuser_question(
                session_id=sample_session_data['session_id'],
                request_id=f'session-cleanup-{i}',
                question_data='{}',
            )

        count = temp_registry_db.cleanup_askuser_questions_for_session(
            sample_session_data['session_id']
        )
        assert count == 3

        # All should be gone
        pending = temp_registry_db.get_pending_askuser_questions(sample_session_data['session_id'])
        assert len(pending) == 0


class TestAskUserQuestionMigration:
    """Tests for askuser_questions table migration."""

    def test_migration_creates_askuser_questions_table(self, temp_db_path):
        """askuser_questions table is created if missing."""
        from sqlalchemy import text

        db = RegistryDatabase(temp_db_path)

        with db.engine.connect() as conn:
            result = conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='askuser_questions'"
            ))
            assert result.fetchone() is not None

    def test_migration_creates_askuser_indexes(self, temp_db_path):
        """Indexes are created for askuser_questions."""
        from sqlalchemy import text

        db = RegistryDatabase(temp_db_path)

        with db.engine.connect() as conn:
            result = conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_askuser_%'"
            ))
            indexes = [row[0] for row in result.fetchall()]
            assert 'idx_askuser_session' in indexes
            assert 'idx_askuser_status' in indexes
