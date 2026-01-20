"""
End-to-end tests for failure recovery scenarios.

Tests system resilience including:
- Listener restart recovery
- Registry restart recovery
- Stale socket cleanup
- Health check detection
- Auto-recovery mechanisms
"""

import os
import sys
import time
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add core directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestListenerRestartRecovery:
    """Test listener survives restart."""

    def test_listener_restart_recovery(self, temp_registry_db, temp_socket_dir):
        """
        Kill and restart listener, sessions survive.

        Verifies:
        - Sessions persist in database during listener restart
        - Sessions can be queried after restart
        - Socket paths remain valid
        """
        # Create session before "restart"
        session_data = {
            'session_id': 'survive01',
            'project': 'survive-project',
            'terminal': 'term',
            'socket_path': os.path.join(temp_socket_dir, 'survive01.sock'),
            'thread_ts': '9999999999.111111',
            'channel': 'C_SURVIVE',
        }
        temp_registry_db.create_session(session_data)

        # Verify session exists
        before = temp_registry_db.get_session('survive01')
        assert before is not None

        # Simulate "restart" by creating new database connection
        # (In real scenario, listener process would restart)
        from registry_db import RegistryDatabase
        db_path = temp_registry_db.db_path

        # Create new connection (simulating new listener process)
        new_db = RegistryDatabase(db_path)

        # Verify session still exists after "restart"
        after = new_db.get_session('survive01')
        assert after is not None
        assert after['session_id'] == 'survive01'
        assert after['thread_ts'] == '9999999999.111111'


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestRegistryRestartRecovery:
    """Test registry daemon restart."""

    def test_registry_restart_recovery(self, tmp_path, clean_env):
        """
        Registry daemon restart preserves data.

        Verifies:
        - Data persists across registry restarts
        - Sessions can be recovered
        """
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry_dir = str(tmp_path / "registry")
            socket_path = str(tmp_path / "sockets" / "registry.sock")

            # Create first registry instance
            registry1 = SessionRegistry(
                registry_dir=registry_dir,
                socket_path=socket_path
            )

            # Register session
            session_data = {
                'session_id': 'persist01',
                'project': 'persistent',
                'terminal': 'term',
                'socket_path': '/tmp/persist.sock'
            }
            registry1.register_session(session_data)

            # Verify session exists
            before = registry1.get_session('persist01')
            assert before is not None

            # "Restart" registry
            SessionRegistry._instance = None
            registry2 = SessionRegistry(
                registry_dir=registry_dir,
                socket_path=socket_path
            )

            # Verify session persisted
            after = registry2.get_session('persist01')
            assert after is not None
            assert after['project'] == 'persistent'


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestStaleSocketCleanup:
    """Test stale socket detection and cleanup."""

    def test_stale_socket_cleanup(self, temp_registry_db, temp_socket_dir):
        """
        Old sockets detected and handled.

        Verifies:
        - Stale socket files are detected
        - Sessions with stale sockets can be identified
        """
        # Create session with socket
        socket_path = os.path.join(temp_socket_dir, 'stale.sock')
        session_data = {
            'session_id': 'stale01',
            'project': 'stale-project',
            'terminal': 'term',
            'socket_path': socket_path,
            'thread_ts': '8888888888.111111',
            'channel': 'C_STALE',
        }
        temp_registry_db.create_session(session_data)

        # Create socket file but don't bind (simulating stale socket)
        Path(socket_path).touch()
        assert os.path.exists(socket_path)

        # Verify session exists but socket is stale (can't connect)
        session = temp_registry_db.get_session('stale01')
        assert session is not None
        assert session['socket_path'] == socket_path

        # Try to connect to stale socket - should fail
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with pytest.raises(Exception):
            client.connect(socket_path)
        client.close()

        # Cleanup stale socket
        os.unlink(socket_path)
        assert not os.path.exists(socket_path)

    def test_detects_missing_socket_file(self, temp_registry_db):
        """
        Detect sessions with missing socket files.

        Verifies:
        - Session with non-existent socket is detected
        """
        session_data = {
            'session_id': 'missing01',
            'project': 'missing-socket',
            'terminal': 'term',
            'socket_path': '/nonexistent/path/missing.sock',
            'thread_ts': '7777777777.111111',
            'channel': 'C_MISSING',
        }
        temp_registry_db.create_session(session_data)

        # Session exists in DB
        session = temp_registry_db.get_session('missing01')
        assert session is not None

        # But socket file doesn't exist
        assert not os.path.exists(session['socket_path'])


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestHealthCheckDetectsIssues:
    """Test health check script finds problems."""

    def test_health_check_detects_stale_session(self, temp_registry_db, temp_socket_dir):
        """
        Health check identifies stale sessions.

        Verifies:
        - Active session with missing socket is flagged
        """
        # Create "healthy" session with real socket
        healthy_socket = os.path.join(temp_socket_dir, 'healthy.sock')
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(healthy_socket)
        server.listen(1)

        temp_registry_db.create_session({
            'session_id': 'healthy01',
            'project': 'healthy',
            'terminal': 'term',
            'socket_path': healthy_socket,
            'thread_ts': '1111.1111',
            'channel': 'C_HEALTHY',
        })

        # Create "unhealthy" session with missing socket
        temp_registry_db.create_session({
            'session_id': 'unhealthy01',
            'project': 'unhealthy',
            'terminal': 'term',
            'socket_path': '/missing/unhealthy.sock',
            'thread_ts': '2222.2222',
            'channel': 'C_UNHEALTHY',
        })

        # Health check: find sessions with missing sockets
        active_sessions = temp_registry_db.list_sessions(status='active')
        unhealthy_sessions = []

        for session in active_sessions:
            if not os.path.exists(session['socket_path']):
                unhealthy_sessions.append(session)

        assert len(unhealthy_sessions) == 1
        assert unhealthy_sessions[0]['session_id'] == 'unhealthy01'

        # Cleanup
        server.close()
        if os.path.exists(healthy_socket):
            os.unlink(healthy_socket)


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestFixAutoRecovery:
    """Test automatic recovery mechanisms."""

    def test_fix_marks_stale_sessions_ended(self, temp_registry_db):
        """
        Fix script marks stale sessions as ended.

        Verifies:
        - Stale sessions can be marked as ended
        - Healthy sessions are unaffected
        """
        # Create stale session
        temp_registry_db.create_session({
            'session_id': 'to_fix01',
            'project': 'fix-me',
            'terminal': 'term',
            'socket_path': '/nonexistent/fix.sock',
            'thread_ts': '3333.3333',
            'channel': 'C_FIX',
        })

        # Verify it's active
        session = temp_registry_db.get_session('to_fix01')
        assert session['status'] == 'active'

        # Simulate fix: mark stale sessions as ended
        active_sessions = temp_registry_db.list_sessions(status='active')
        for session in active_sessions:
            if not os.path.exists(session['socket_path']):
                temp_registry_db.update_session(session['session_id'], {'status': 'ended'})

        # Verify session is now ended
        fixed_session = temp_registry_db.get_session('to_fix01')
        assert fixed_session['status'] == 'ended'

    def test_fix_cleans_orphan_sockets(self, temp_socket_dir, temp_registry_db):
        """
        Fix script removes orphan socket files.

        Verifies:
        - Socket files without DB entries are detected
        - Orphan sockets can be removed
        """
        # Create orphan socket file (no DB entry)
        orphan_socket = os.path.join(temp_socket_dir, 'orphan.sock')
        Path(orphan_socket).touch()
        assert os.path.exists(orphan_socket)

        # Get all registered socket paths
        sessions = temp_registry_db.list_sessions()
        registered_sockets = {s['socket_path'] for s in sessions}

        # Find orphan sockets in directory
        socket_files = list(Path(temp_socket_dir).glob('*.sock'))
        orphan_sockets = [f for f in socket_files if str(f) not in registered_sockets]

        assert len(orphan_sockets) == 1
        assert str(orphan_sockets[0]) == orphan_socket

        # Remove orphan
        os.unlink(orphan_socket)
        assert not os.path.exists(orphan_socket)


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestMonitorDetectsStarvation:
    """Test monitor detects event timeout."""

    def test_monitor_detects_idle_session(self, temp_registry_db):
        """
        Monitor detects sessions with no recent activity.

        Verifies:
        - Sessions without recent activity can be detected
        """
        from datetime import datetime, timedelta

        # Create session with old last_activity
        old_time = datetime.now() - timedelta(hours=2)
        temp_registry_db.create_session({
            'session_id': 'idle01',
            'project': 'idle-project',
            'terminal': 'term',
            'socket_path': '/tmp/idle.sock',
            'thread_ts': '4444.4444',
            'channel': 'C_IDLE',
        })

        # Update last_activity to old time
        temp_registry_db.update_session('idle01', {'last_activity': old_time})

        # Monitor: find idle sessions
        sessions = temp_registry_db.list_sessions(status='active')
        idle_threshold = datetime.now() - timedelta(hours=1)

        idle_sessions = []
        for session in sessions:
            last_activity = session.get('last_activity')
            if last_activity:
                # Handle both datetime objects and string representations
                if isinstance(last_activity, str):
                    last_activity = datetime.fromisoformat(last_activity)
                if last_activity < idle_threshold:
                    idle_sessions.append(session)

        # Should find the idle session
        idle_ids = [s['session_id'] for s in idle_sessions]
        assert 'idle01' in idle_ids

    def test_monitor_ignores_active_sessions(self, temp_registry_db):
        """
        Monitor ignores recently active sessions.

        Verifies:
        - Sessions with recent activity are not flagged
        """
        from datetime import datetime, timedelta

        # Create session with recent activity
        temp_registry_db.create_session({
            'session_id': 'active01',
            'project': 'active-project',
            'terminal': 'term',
            'socket_path': '/tmp/active.sock',
            'thread_ts': '5555.5555',
            'channel': 'C_ACTIVE',
        })

        # Update last_activity to now
        temp_registry_db.update_session('active01', {'last_activity': datetime.now()})

        # Monitor: find idle sessions
        sessions = temp_registry_db.list_sessions(status='active')
        idle_threshold = datetime.now() - timedelta(hours=1)

        idle_sessions = []
        for session in sessions:
            last_activity = session.get('last_activity')
            if last_activity:
                # Handle both datetime objects and string representations
                if isinstance(last_activity, str):
                    last_activity = datetime.fromisoformat(last_activity)
                if last_activity < idle_threshold:
                    idle_sessions.append(session)

        # Should NOT find the active session
        idle_ids = [s['session_id'] for s in idle_sessions]
        assert 'active01' not in idle_ids
