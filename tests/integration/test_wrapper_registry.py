"""
Integration tests for Wrapper <-> Registry

Tests the integration between wrapper scripts and the session registry,
verifying session registration, health checks, and auto-recovery.
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


class TestWrapperRegistersSession:
    """Test session created in DB on startup."""

    def test_wrapper_registers_session(self, tmp_path, clean_env):
        """Wrapper creates session in registry database."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            session_data = {
                'session_id': 'wrap1234',
                'project': 'test-project',
                'terminal': 'test-terminal',
                'socket_path': str(tmp_path / "wrap1234.sock")
            }

            session = registry.register_session(session_data)

            assert session['session_id'] == 'wrap1234'
            assert session['status'] == 'active'

            # Verify in database
            stored = registry.get_session('wrap1234')
            assert stored is not None

    def test_wrapper_registers_with_project_dir(self, tmp_path, clean_env):
        """Wrapper includes project_dir in registration."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            session_data = {
                'session_id': 'projdir1',
                'project': 'my-project',
                'project_dir': '/path/to/my-project',
                'terminal': 'terminal-1',
                'socket_path': str(tmp_path / "projdir.sock")
            }

            session = registry.register_session(session_data)

            stored = registry.get_session('projdir1')
            assert stored['project_dir'] == '/path/to/my-project'


class TestWrapperRegistersClaudeUUID:
    """Test UUID linked to same thread."""

    def test_wrapper_registers_claude_uuid(self, tmp_path, clean_env):
        """Claude's UUID session links to same Slack thread."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            # Register wrapper session first
            registry.register_session({
                'session_id': 'wrapper01',
                'project': 'test',
                'terminal': 'term',
                'socket_path': '/tmp/wrapper.sock'
            })

            # Update with Slack metadata
            registry.db.update_session('wrapper01', {
                'slack_thread_ts': '123.456',
                'slack_channel': 'C123'
            })

            # Register Claude's UUID with same thread
            response = registry._process_command({
                'command': 'REGISTER_EXISTING',
                'data': {
                    'session_id': '12345678-1234-5678-1234-567812345678',
                    'thread_ts': '123.456',
                    'channel': 'C123',
                    'project': 'test',
                    'terminal': 'term'
                }
            })

            assert response['success'] is True

            # Verify UUID session has same thread
            uuid_session = registry.get_session('12345678-1234-5678-1234-567812345678')
            assert uuid_session['thread_ts'] == '123.456'


class TestWrapperHealthCheck:
    """Test registry responds to ping."""

    def test_wrapper_health_check_get(self, tmp_path, clean_env, sample_session_data):
        """Health check via GET command."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            registry.register_session(sample_session_data)

            response = registry._process_command({
                'command': 'GET',
                'data': {'session_id': sample_session_data['session_id']}
            })

            assert response['success'] is True
            assert response['session'] is not None
            assert response['session']['status'] == 'active'

    def test_wrapper_health_check_list(self, tmp_path, clean_env):
        """Health check via LIST command."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            # Register multiple sessions
            for i in range(3):
                registry.register_session({
                    'session_id': f'health{i}',
                    'project': f'project{i}',
                    'terminal': f'term{i}',
                    'socket_path': f'/tmp/health{i}.sock'
                })

            response = registry._process_command({
                'command': 'LIST',
                'data': {}
            })

            assert response['success'] is True
            assert len(response['sessions']) == 3


class TestWrapperAutoRecovery:
    """Test restart registry if dead."""

    def test_wrapper_detects_dead_registry(self, tmp_path):
        """Wrapper detects when registry socket is unavailable."""
        socket_path = tmp_path / "dead_registry.sock"

        with pytest.raises(Exception):
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(0.5)
            client.connect(str(socket_path))

    def test_wrapper_persists_data_across_restarts(self, tmp_path, clean_env):
        """Session data persists across registry restarts."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry

            # Create and populate registry
            SessionRegistry._instance = None
            registry1 = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            session_data = {
                'session_id': 'persist01',
                'project': 'persistent',
                'terminal': 'term',
                'socket_path': '/tmp/persist.sock'
            }
            registry1.register_session(session_data)

            # Restart registry
            SessionRegistry._instance = None
            registry2 = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            # Verify data persisted
            recovered = registry2.get_session('persist01')
            assert recovered is not None
            assert recovered['project'] == 'persistent'

    def test_wrapper_handles_stale_socket(self, tmp_path, clean_env):
        """Wrapper handles stale socket file from previous crash."""
        socket_path = tmp_path / "stale.sock"
        socket_path.touch()  # Create stale file

        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            # Should handle stale socket and start
            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(socket_path)
            )

            # Should be functional
            registry.start_server()
            time.sleep(0.1)

            try:
                assert registry.running is True
            finally:
                registry.stop_server()
