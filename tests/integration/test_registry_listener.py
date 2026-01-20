"""
Integration tests for Registry <-> Listener

Tests the integration between SessionRegistry and SlackListener,
verifying message routing through the registry lookup system.
"""

import json
import os
import socket
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add core directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))


class TestListenerQueriesRegistry:
    """Test listener looks up session from registry."""

    def test_listener_queries_registry_by_thread(self, temp_registry_db, sample_session_data):
        """Listener looks up session by thread_ts."""
        temp_registry_db.create_session(sample_session_data)

        # Query by thread_ts
        session = temp_registry_db.get_by_thread(sample_session_data['thread_ts'])

        assert session is not None
        assert session['session_id'] == sample_session_data['session_id']
        assert session['socket_path'] == sample_session_data['socket_path']

    def test_listener_queries_registry_by_channel(self, temp_registry_db, sample_session_data_custom_channel):
        """Listener looks up session by channel in custom channel mode."""
        temp_registry_db.create_session(sample_session_data_custom_channel)

        # Query active sessions for channel
        sessions = temp_registry_db.list_sessions(status='active')
        channel_sessions = [s for s in sessions if s['channel'] == sample_session_data_custom_channel['channel']]

        assert len(channel_sessions) == 1
        assert channel_sessions[0]['session_id'] == sample_session_data_custom_channel['session_id']


class TestListenerRoutesToSocket:
    """Test message reaches wrapper socket."""

    def test_listener_routes_to_correct_socket(self, temp_registry_db, sample_session_data, tmp_path):
        """Message is routed to correct session socket."""
        # Create socket
        socket_path = tmp_path / "route_test.sock"
        sample_session_data['socket_path'] = str(socket_path)
        temp_registry_db.create_session(sample_session_data)

        # Create a simple echo server
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(socket_path))
        server.listen(1)
        server.setblocking(False)

        try:
            # Query registry for socket path
            session = temp_registry_db.get_by_thread(sample_session_data['thread_ts'])
            assert session['socket_path'] == str(socket_path)

            # Verify socket exists and is connectable
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(str(socket_path))
            client.send(b"test message")
            client.close()

        finally:
            server.close()

    def test_listener_handles_socket_not_found(self, temp_registry_db, sample_session_data):
        """Listener handles missing socket gracefully."""
        sample_session_data['socket_path'] = '/nonexistent/socket.sock'
        temp_registry_db.create_session(sample_session_data)

        session = temp_registry_db.get_by_thread(sample_session_data['thread_ts'])

        # Socket path in registry but file doesn't exist
        assert session['socket_path'] == '/nonexistent/socket.sock'
        assert not os.path.exists(session['socket_path'])


class TestListenerHandlesMissingSession:
    """Test graceful handling of unknown threads."""

    def test_listener_handles_unknown_thread(self, temp_registry_db):
        """Returns None for unknown thread_ts."""
        session = temp_registry_db.get_by_thread('unknown.thread.ts')
        assert session is None

    def test_listener_handles_ended_session(self, temp_registry_db, sample_session_data):
        """Returns None for ended sessions when filtering by active."""
        temp_registry_db.create_session(sample_session_data)
        temp_registry_db.update_session(sample_session_data['session_id'], {'status': 'ended'})

        # Active filter should not find it
        sessions = temp_registry_db.list_sessions(status='active')
        assert len(sessions) == 0

    def test_listener_handles_stale_session(self, temp_registry_db, sample_session_data, tmp_path):
        """Detects sessions with missing socket files as stale."""
        sample_session_data['socket_path'] = str(tmp_path / "stale.sock")
        temp_registry_db.create_session(sample_session_data)

        session = temp_registry_db.get_by_thread(sample_session_data['thread_ts'])

        # Session exists but socket doesn't
        assert session is not None
        assert not os.path.exists(session['socket_path'])


class TestMultiSessionRouting:
    """Test correct session receives message when multiple exist."""

    def test_multi_session_different_threads(self, temp_registry_db, sample_session_data, tmp_path):
        """Multiple sessions with different threads route correctly."""
        # Create first session
        session1 = sample_session_data.copy()
        session1['session_id'] = 'session1'
        session1['thread_ts'] = '111.111'
        session1['socket_path'] = str(tmp_path / "session1.sock")
        temp_registry_db.create_session(session1)

        # Create second session
        session2 = sample_session_data.copy()
        session2['session_id'] = 'session2'
        session2['thread_ts'] = '222.222'
        session2['socket_path'] = str(tmp_path / "session2.sock")
        temp_registry_db.create_session(session2)

        # Query for each thread
        found1 = temp_registry_db.get_by_thread('111.111')
        found2 = temp_registry_db.get_by_thread('222.222')

        assert found1['session_id'] == 'session1'
        assert found2['session_id'] == 'session2'
        assert found1['socket_path'] != found2['socket_path']

    def test_multi_session_same_channel_different_threads(self, temp_registry_db, sample_session_data, tmp_path):
        """Multiple sessions in same channel with different threads."""
        channel = 'C_SHARED_CHANNEL'

        # Create sessions in same channel
        for i in range(3):
            session = sample_session_data.copy()
            session['session_id'] = f'shared{i}'
            session['thread_ts'] = f'{i}{i}{i}.{i}{i}{i}'
            session['channel'] = channel
            session['socket_path'] = str(tmp_path / f"shared{i}.sock")
            temp_registry_db.create_session(session)

        # Each thread routes to correct session
        for i in range(3):
            found = temp_registry_db.get_by_thread(f'{i}{i}{i}.{i}{i}{i}')
            assert found['session_id'] == f'shared{i}'

    def test_multi_session_no_cross_contamination(self, temp_registry_db, sample_session_data, tmp_path):
        """Messages don't leak between sessions."""
        # Create two sessions
        session1 = sample_session_data.copy()
        session1['session_id'] = 'isolated1'
        session1['thread_ts'] = '1000.1000'
        session1['channel'] = 'C_CHANNEL_A'
        temp_registry_db.create_session(session1)

        session2 = sample_session_data.copy()
        session2['session_id'] = 'isolated2'
        session2['thread_ts'] = '2000.2000'
        session2['channel'] = 'C_CHANNEL_B'
        temp_registry_db.create_session(session2)

        # Query for session 1's thread
        found = temp_registry_db.get_by_thread('1000.1000')
        assert found['session_id'] == 'isolated1'
        assert found['channel'] == 'C_CHANNEL_A'

        # Query for session 2's thread
        found = temp_registry_db.get_by_thread('2000.2000')
        assert found['session_id'] == 'isolated2'
        assert found['channel'] == 'C_CHANNEL_B'
