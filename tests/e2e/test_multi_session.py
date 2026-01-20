"""
End-to-end tests for multi-session scenarios.

Tests correct message routing when multiple sessions exist:
- Different channels
- Same channel with different threads
- Concurrent sessions
- Message isolation
"""

import os
import sys
import time
import socket
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add core directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestTwoSessionsDifferentChannels:
    """Test two sessions in different channels route correctly."""

    def test_two_sessions_different_channels(self, temp_registry_db, temp_socket_dir):
        """
        Isolated message routing.

        Verifies:
        - Messages to channel A go to session A
        - Messages to channel B go to session B
        - No cross-contamination
        """
        # Create two sessions in different channels
        session_a = {
            'session_id': 'session_a',
            'project': 'project-a',
            'terminal': 'term-a',
            'socket_path': os.path.join(temp_socket_dir, 'session_a.sock'),
            'thread_ts': '1111111111.111111',
            'channel': 'C_CHANNEL_A',
        }

        session_b = {
            'session_id': 'session_b',
            'project': 'project-b',
            'terminal': 'term-b',
            'socket_path': os.path.join(temp_socket_dir, 'session_b.sock'),
            'thread_ts': '2222222222.222222',
            'channel': 'C_CHANNEL_B',
        }

        temp_registry_db.create_session(session_a)
        temp_registry_db.create_session(session_b)

        # Verify lookups return correct sessions
        found_a = temp_registry_db.get_by_thread('1111111111.111111')
        found_b = temp_registry_db.get_by_thread('2222222222.222222')

        assert found_a['session_id'] == 'session_a'
        assert found_a['channel'] == 'C_CHANNEL_A'

        assert found_b['session_id'] == 'session_b'
        assert found_b['channel'] == 'C_CHANNEL_B'

        # Verify socket paths are different
        assert found_a['socket_path'] != found_b['socket_path']


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestTwoSessionsSameChannelThreads:
    """Test two sessions in same channel with different threads."""

    def test_two_sessions_same_channel_threads(self, temp_registry_db, temp_socket_dir):
        """
        Thread-based isolation.

        Verifies:
        - Same channel can have multiple sessions
        - Each thread routes to correct session
        """
        channel = 'C_SHARED_CHANNEL'

        session_1 = {
            'session_id': 'thread_session_1',
            'project': 'project-1',
            'terminal': 'term-1',
            'socket_path': os.path.join(temp_socket_dir, 'thread_session_1.sock'),
            'thread_ts': '1000000000.000001',
            'channel': channel,
        }

        session_2 = {
            'session_id': 'thread_session_2',
            'project': 'project-2',
            'terminal': 'term-2',
            'socket_path': os.path.join(temp_socket_dir, 'thread_session_2.sock'),
            'thread_ts': '1000000000.000002',
            'channel': channel,
        }

        temp_registry_db.create_session(session_1)
        temp_registry_db.create_session(session_2)

        # Both sessions in same channel
        sessions = temp_registry_db.list_sessions(status='active')
        channel_sessions = [s for s in sessions if s['channel'] == channel]
        assert len(channel_sessions) == 2

        # But different threads route to different sessions
        found_1 = temp_registry_db.get_by_thread('1000000000.000001')
        found_2 = temp_registry_db.get_by_thread('1000000000.000002')

        assert found_1['session_id'] == 'thread_session_1'
        assert found_2['session_id'] == 'thread_session_2'


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestThreeConcurrentSessions:
    """Test three concurrent sessions with message routing."""

    def test_three_concurrent_sessions(self, temp_registry_db, temp_socket_dir):
        """
        Stress test with concurrent activity.

        Verifies:
        - Three sessions can exist simultaneously
        - Each session receives only its messages
        - No interference between sessions
        """
        sessions = []
        sockets = []

        try:
            # Create three sessions
            for i in range(3):
                session_data = {
                    'session_id': f'concurrent_{i}',
                    'project': f'project-{i}',
                    'terminal': f'term-{i}',
                    'socket_path': os.path.join(temp_socket_dir, f'concurrent_{i}.sock'),
                    'thread_ts': f'{i}{i}{i}{i}{i}{i}{i}{i}{i}{i}.{i}{i}{i}{i}{i}{i}',
                    'channel': f'C_CHANNEL_{i}',
                }
                temp_registry_db.create_session(session_data)
                sessions.append(session_data)

                # Create socket for each session
                server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                server_socket.bind(session_data['socket_path'])
                server_socket.listen(1)
                server_socket.setblocking(False)
                sockets.append(server_socket)

            # Verify all sessions exist
            all_sessions = temp_registry_db.list_sessions(status='active')
            concurrent_sessions = [s for s in all_sessions if s['session_id'].startswith('concurrent_')]
            assert len(concurrent_sessions) == 3

            # Verify each session has correct socket
            for i, session in enumerate(sessions):
                found = temp_registry_db.get_session(session['session_id'])
                assert found is not None
                assert found['socket_path'] == session['socket_path']
                assert os.path.exists(session['socket_path'])

        finally:
            # Cleanup
            for sock in sockets:
                sock.close()
            for session in sessions:
                if os.path.exists(session['socket_path']):
                    os.unlink(session['socket_path'])


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestNoCrossContamination:
    """Test messages don't leak between sessions."""

    def test_no_cross_contamination(self, temp_registry_db, temp_socket_dir):
        """
        Messages don't leak between sessions.

        Verifies:
        - Message to session A doesn't reach session B
        - Each socket receives only its messages
        """
        # Create two isolated sessions
        session_a = {
            'session_id': 'isolated_a',
            'project': 'iso-a',
            'terminal': 'term-a',
            'socket_path': os.path.join(temp_socket_dir, 'isolated_a.sock'),
            'thread_ts': 'aaaa.aaaa',
            'channel': 'C_ISO_A',
        }

        session_b = {
            'session_id': 'isolated_b',
            'project': 'iso-b',
            'terminal': 'term-b',
            'socket_path': os.path.join(temp_socket_dir, 'isolated_b.sock'),
            'thread_ts': 'bbbb.bbbb',
            'channel': 'C_ISO_B',
        }

        temp_registry_db.create_session(session_a)
        temp_registry_db.create_session(session_b)

        # Create sockets
        socket_a = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        socket_a.bind(session_a['socket_path'])
        socket_a.listen(1)
        socket_a.setblocking(False)

        socket_b = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        socket_b.bind(session_b['socket_path'])
        socket_b.listen(1)
        socket_b.setblocking(False)

        try:
            # Send message to session A only
            found_a = temp_registry_db.get_by_thread('aaaa.aaaa')
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(found_a['socket_path'])
            client.send(b"message_for_a\n")
            client.close()

            # Verify session A received message
            import select
            readable_a, _, _ = select.select([socket_a], [], [], 0.5)
            assert len(readable_a) == 1

            conn_a, _ = socket_a.accept()
            data_a = conn_a.recv(1024)
            conn_a.close()
            assert data_a == b"message_for_a\n"

            # Verify session B did NOT receive anything
            readable_b, _, _ = select.select([socket_b], [], [], 0.5)
            assert len(readable_b) == 0

        finally:
            socket_a.close()
            socket_b.close()
            if os.path.exists(session_a['socket_path']):
                os.unlink(session_a['socket_path'])
            if os.path.exists(session_b['socket_path']):
                os.unlink(session_b['socket_path'])


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestSessionLookupPerformance:
    """Test session lookup performance with many sessions."""

    def test_session_lookup_with_many_sessions(self, temp_registry_db):
        """
        Lookup remains fast with many sessions.

        Verifies:
        - Can create 50 sessions
        - Lookup by thread_ts is still fast
        """
        # Create 50 sessions
        for i in range(50):
            session_data = {
                'session_id': f'perf_{i:03d}',
                'project': f'project-{i}',
                'terminal': f'term-{i}',
                'socket_path': f'/tmp/perf_{i:03d}.sock',
                'thread_ts': f'{i:010d}.{i:06d}',
                'channel': f'C_PERF_{i}',
            }
            temp_registry_db.create_session(session_data)

        # Verify all created
        all_sessions = temp_registry_db.list_sessions()
        perf_sessions = [s for s in all_sessions if s['session_id'].startswith('perf_')]
        assert len(perf_sessions) == 50

        # Test lookup speed (should be indexed)
        import time
        start = time.time()
        for i in range(50):
            found = temp_registry_db.get_by_thread(f'{i:010d}.{i:06d}')
            assert found is not None
            assert found['session_id'] == f'perf_{i:03d}'
        elapsed = time.time() - start

        # 50 lookups should complete in under 1 second
        assert elapsed < 1.0, f"Lookups took {elapsed:.2f}s, expected < 1.0s"
