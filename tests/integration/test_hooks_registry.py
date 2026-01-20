"""
Integration tests for Hooks <-> Registry

Tests the integration between hook scripts and the session registry,
verifying hooks can query and update session metadata.
"""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add core directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))


class TestHookQueriesSession:
    """Test hook finds Slack metadata by session_id."""

    def test_hook_queries_session_by_id(self, temp_registry_db, sample_session_data):
        """Hook finds session metadata by session_id."""
        temp_registry_db.create_session(sample_session_data)

        session = temp_registry_db.get_session(sample_session_data['session_id'])

        assert session is not None
        assert session['channel'] == sample_session_data['channel']
        assert session['thread_ts'] == sample_session_data['thread_ts']

    def test_hook_queries_session_by_project_dir(self, temp_registry_db):
        """Hook can find session by project_dir."""
        session_data = {
            'session_id': 'hook1234',
            'project': 'test-project',
            'project_dir': '/path/to/project',
            'terminal': 'term',
            'socket_path': '/tmp/hook.sock',
            'thread_ts': '111.222',
            'channel': 'C111',
            'slack_user_id': 'U111'
        }
        temp_registry_db.create_session(session_data)

        session = temp_registry_db.get_by_project_dir('/path/to/project')

        assert session is not None
        assert session['session_id'] == 'hook1234'

    def test_hook_gets_latest_session_for_project(self, temp_registry_db):
        """Hook gets most recent session for project_dir."""
        project_dir = '/path/to/shared/project'

        # Create older session
        temp_registry_db.create_session({
            'session_id': 'older001',
            'project': 'shared',
            'project_dir': project_dir,
            'terminal': 'term1',
            'socket_path': '/tmp/older.sock',
            'thread_ts': '100.100',
            'channel': 'C100',
            'slack_user_id': 'U100'
        })

        time.sleep(0.01)

        # Create newer session
        temp_registry_db.create_session({
            'session_id': 'newer002',
            'project': 'shared',
            'project_dir': project_dir,
            'terminal': 'term2',
            'socket_path': '/tmp/newer.sock',
            'thread_ts': '200.200',
            'channel': 'C200',
            'slack_user_id': 'U200'
        })

        session = temp_registry_db.get_by_project_dir(project_dir)

        assert session['session_id'] == 'newer002'


class TestHookUpdatesMessageTs:
    """Test todo message_ts stored in registry."""

    def test_hook_stores_todo_message_ts(self, temp_registry_db, sample_session_data):
        """Hook stores Slack message timestamp for todo updates."""
        temp_registry_db.create_session(sample_session_data)

        temp_registry_db.update_session(
            sample_session_data['session_id'],
            {'todo_message_ts': '333.444'}
        )

        session = temp_registry_db.get_session(sample_session_data['session_id'])
        assert session['todo_message_ts'] == '333.444'

    def test_hook_stores_reply_to_ts(self, temp_registry_db, sample_session_data):
        """Hook stores message timestamp for threading responses."""
        temp_registry_db.create_session(sample_session_data)

        temp_registry_db.update_session(
            sample_session_data['session_id'],
            {'reply_to_ts': '555.666'}
        )

        session = temp_registry_db.get_session(sample_session_data['session_id'])
        assert session['reply_to_ts'] == '555.666'


class TestHookSelfHealing:
    """Test hook recovers missing data by looking up wrapper session."""

    def test_hook_self_heals_from_wrapper_session(self, temp_registry_db):
        """Hook copies Slack metadata from wrapper session."""
        # Create wrapper session with Slack metadata
        temp_registry_db.create_session({
            'session_id': 'wrapper01',
            'project': 'test',
            'terminal': 'term',
            'socket_path': '/tmp/wrapper.sock',
            'thread_ts': '777.888',
            'channel': 'C777',
            'slack_user_id': 'U777'
        })

        # Create UUID session without Slack metadata
        temp_registry_db.create_session({
            'session_id': '12345678-1234-5678-1234-567812345678',
            'project': 'test',
            'terminal': 'term',
            'socket_path': '/tmp/uuid.sock',
            'thread_ts': None,
            'channel': None,
            'slack_user_id': None
        })

        # Simulate hook self-healing
        wrapper_data = temp_registry_db.get_session('wrapper01')
        if wrapper_data:
            temp_registry_db.update_session(
                '12345678-1234-5678-1234-567812345678',
                {
                    'slack_thread_ts': wrapper_data['thread_ts'],
                    'slack_channel': wrapper_data['channel']
                }
            )

        healed = temp_registry_db.get_session('12345678-1234-5678-1234-567812345678')
        assert healed['thread_ts'] == '777.888'
        assert healed['channel'] == 'C777'

    def test_hook_self_heals_via_project_dir(self, temp_registry_db):
        """Hook finds wrapper session via project_dir."""
        project_dir = '/path/to/project'

        temp_registry_db.create_session({
            'session_id': 'wrapper99',
            'project': 'myproject',
            'project_dir': project_dir,
            'terminal': 'term',
            'socket_path': '/tmp/wrapper.sock',
            'thread_ts': '999.999',
            'channel': 'C999',
            'slack_user_id': 'U999'
        })

        session = temp_registry_db.get_by_project_dir(project_dir)

        assert session is not None
        assert session['thread_ts'] == '999.999'

    def test_hook_handles_missing_wrapper_gracefully(self, temp_registry_db):
        """Hook handles case where wrapper doesn't exist."""
        temp_registry_db.create_session({
            'session_id': 'orphan-uuid-1234-5678-1234-567812345678',
            'project': 'orphan',
            'terminal': 'term',
            'socket_path': '/tmp/orphan.sock',
            'thread_ts': None,
            'channel': None,
            'slack_user_id': None
        })

        # Try to find wrapper
        wrapper_session = temp_registry_db.get_session('orphan-u')
        assert wrapper_session is None

        # Orphan session still exists
        orphan = temp_registry_db.get_session('orphan-uuid-1234-5678-1234-567812345678')
        assert orphan is not None


class TestHookBufferFileLookup:
    """Test hook finds terminal output buffer via registry."""

    def test_hook_stores_buffer_file_path(self, temp_registry_db, sample_session_data):
        """Hook can store buffer file path."""
        temp_registry_db.create_session(sample_session_data)

        temp_registry_db.update_session(
            sample_session_data['session_id'],
            {'buffer_file_path': '/tmp/claude_output_test.txt'}
        )

        session = temp_registry_db.get_session(sample_session_data['session_id'])
        assert session['buffer_file_path'] == '/tmp/claude_output_test.txt'

    def test_hook_finds_buffer_via_project_dir(self, temp_registry_db):
        """Hook finds buffer file by project_dir lookup."""
        temp_registry_db.create_session({
            'session_id': 'buffer01',
            'project': 'buffer-proj',
            'project_dir': '/path/to/buffer/project',
            'terminal': 'term',
            'socket_path': '/tmp/buffer.sock',
            'buffer_file_path': '/tmp/output_buffer.txt',
            'thread_ts': '444.555',
            'channel': 'C444',
            'slack_user_id': 'U444'
        })

        session = temp_registry_db.get_by_project_dir('/path/to/buffer/project')

        assert session is not None
        assert session['buffer_file_path'] == '/tmp/output_buffer.txt'
