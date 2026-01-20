"""
Unit tests for core/session_registry.py

Tests multi-session management including session registration,
socket server operations, and Slack thread integration.
"""

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

# Add core directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))


class TestSessionRegistryInit:
    """Tests for SessionRegistry initialization."""

    def test_init_creates_directories(self, tmp_path, clean_env):
        """Creates registry and socket directories."""
        registry_dir = tmp_path / "registry"
        socket_path = tmp_path / "sockets" / "registry.sock"

        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            # Reset singleton
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(registry_dir),
                socket_path=str(socket_path)
            )
            assert registry_dir.exists()
            assert socket_path.parent.exists()

    def test_init_singleton_pattern(self, tmp_path, clean_env):
        """Only one registry instance per system."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            # Reset singleton
            SessionRegistry._instance = None

            registry1 = SessionRegistry(
                registry_dir=str(tmp_path / "r1"),
                socket_path=str(tmp_path / "s1" / "registry.sock")
            )
            registry2 = SessionRegistry(
                registry_dir=str(tmp_path / "r2"),
                socket_path=str(tmp_path / "s2" / "registry.sock")
            )
            assert registry1 is registry2


class TestRegisterSession:
    """Tests for register_session()."""

    def test_register_session(self, tmp_path, clean_env, sample_session_data):
        """Creates session with Slack thread."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            session = registry.register_session(sample_session_data)
            assert session['session_id'] == sample_session_data['session_id']
            assert session['status'] == 'active'

    def test_register_session_validates_required_fields(self, tmp_path, clean_env):
        """Raises ValueError for missing required fields."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            with pytest.raises(ValueError, match="Missing required field"):
                registry.register_session({'session_id': 'test'})  # Missing other fields

    def test_register_session_rejects_duplicates(self, tmp_path, clean_env, sample_session_data):
        """Raises ValueError for duplicate session ID."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            registry.register_session(sample_session_data)

            with pytest.raises(ValueError, match="already registered"):
                registry.register_session(sample_session_data)


class TestRegisterSessionSimple:
    """Tests for register_session_simple()."""

    def test_register_session_simple(self, tmp_path, clean_env):
        """Simplified registration with positional args."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            session = registry.register_session_simple(
                session_id='simple01',
                project='simple-project',
                terminal='simple-terminal',
                socket_path='/tmp/simple.sock'
            )
            assert session['session_id'] == 'simple01'
            assert session['project'] == 'simple-project'


class TestUnregisterSession:
    """Tests for unregister_session()."""

    def test_unregister_session(self, tmp_path, clean_env, sample_session_data):
        """Removes session and cleans up."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            registry.register_session(sample_session_data)
            result = registry.unregister_session(sample_session_data['session_id'])
            assert result is True

            # Should be gone
            session = registry.get_session(sample_session_data['session_id'])
            assert session is None

    def test_unregister_session_not_found(self, tmp_path, clean_env):
        """Returns False for non-existent session."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            result = registry.unregister_session('nonexistent')
            assert result is False


class TestGetSession:
    """Tests for get_session()."""

    def test_get_session_exists(self, tmp_path, clean_env, sample_session_data):
        """Retrieves session by ID."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            registry.register_session(sample_session_data)
            session = registry.get_session(sample_session_data['session_id'])
            assert session is not None
            assert session['session_id'] == sample_session_data['session_id']

    def test_get_session_not_found(self, tmp_path, clean_env):
        """Returns None for missing session."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            session = registry.get_session('nonexistent')
            assert session is None


class TestGetByThread:
    """Tests for get_by_thread()."""

    def test_get_by_thread_exists(self, tmp_path, clean_env, sample_session_data):
        """Finds session by thread_ts."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            registry.register_session(sample_session_data)
            # Update with thread info
            registry.db.update_session(sample_session_data['session_id'], {
                'slack_thread_ts': '12345.67890',
                'slack_channel': 'C123'
            })

            session = registry.get_by_thread('12345.67890')
            assert session is not None


class TestListSessions:
    """Tests for list_sessions()."""

    def test_list_sessions(self, tmp_path, clean_env, sample_session_data):
        """Lists sessions with optional status filter."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            registry.register_session(sample_session_data)

            sessions = registry.list_sessions()
            assert len(sessions) == 1

            active_sessions = registry.list_sessions(status='active')
            assert len(active_sessions) == 1


class TestDeactivateSession:
    """Tests for deactivate_session()."""

    def test_deactivate_session(self, tmp_path, clean_env, sample_session_data):
        """Marks session as inactive."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            registry.register_session(sample_session_data)
            result = registry.deactivate_session(sample_session_data['session_id'])
            assert result is True

            session = registry.get_session(sample_session_data['session_id'])
            assert session['status'] == 'inactive'

    def test_deactivate_session_not_found(self, tmp_path, clean_env):
        """Returns False for missing session."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            result = registry.deactivate_session('nonexistent')
            assert result is False


class TestSocketServer:
    """Tests for Unix socket server functionality."""

    def test_server_start_stop(self, tmp_path, clean_env):
        """Socket server lifecycle."""
        socket_path = str(tmp_path / "sockets" / "registry.sock")

        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=socket_path
            )

            registry.start_server()
            assert registry.running is True
            assert os.path.exists(socket_path)

            registry.stop_server()
            assert registry.running is False

    def test_process_command_list(self, tmp_path, clean_env, sample_session_data):
        """Socket protocol: LIST command."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            registry.register_session(sample_session_data)

            response = registry._process_command({
                'command': 'LIST',
                'data': {}
            })
            assert response['success'] is True
            assert len(response['sessions']) == 1

    def test_process_command_get(self, tmp_path, clean_env, sample_session_data):
        """Socket protocol: GET command."""
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

    def test_process_command_register(self, tmp_path, clean_env, sample_session_data):
        """Socket protocol: REGISTER command."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            response = registry._process_command({
                'command': 'REGISTER',
                'data': sample_session_data
            })
            assert response['success'] is True
            assert response['session']['session_id'] == sample_session_data['session_id']

    def test_process_command_register_existing(self, tmp_path, clean_env):
        """Socket protocol: REGISTER_EXISTING command."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            response = registry._process_command({
                'command': 'REGISTER_EXISTING',
                'data': {
                    'session_id': 'uuid-12345',
                    'thread_ts': '111.222',
                    'channel': 'C123',
                    'project': 'test',
                    'terminal': 'term'
                }
            })
            assert response['success'] is True

    def test_process_command_invalid(self, tmp_path, clean_env):
        """Handles unknown commands."""
        with patch.dict(os.environ, {}, clear=False):
            from session_registry import SessionRegistry
            SessionRegistry._instance = None

            registry = SessionRegistry(
                registry_dir=str(tmp_path / "registry"),
                socket_path=str(tmp_path / "sockets" / "registry.sock")
            )

            response = registry._process_command({
                'command': 'INVALID_COMMAND',
                'data': {}
            })
            assert response['success'] is False
            assert 'error' in response


class TestSlackIntegration:
    """Tests for Slack thread creation."""

    def test_create_slack_thread_custom_channel(self, tmp_path, clean_env, mock_slack_client):
        """Custom channel mode: no thread_ts."""
        with patch.dict(os.environ, {'SLACK_BOT_TOKEN': 'xoxb-test'}, clear=False):
            with patch('session_registry.WebClient', return_value=mock_slack_client):
                from session_registry import SessionRegistry
                SessionRegistry._instance = None

                registry = SessionRegistry(
                    registry_dir=str(tmp_path / "registry"),
                    socket_path=str(tmp_path / "sockets" / "registry.sock"),
                    slack_token='xoxb-test',
                    slack_channel='default-channel'
                )

                result = registry._create_slack_thread({
                    'session_id': 'test123',
                    'project': 'test-project',
                    'terminal': 'test-terminal',
                    'custom_channel': 'custom-channel'
                })

                assert result['slack_thread_ts'] is None  # Custom channel = no thread
                # Channel is returned as ID (CNEW123) since it was auto-created
                assert result['slack_channel'] == 'CNEW123'

    def test_create_slack_thread_with_description(self, tmp_path, clean_env, mock_slack_client):
        """Thread message includes description."""
        with patch.dict(os.environ, {'SLACK_BOT_TOKEN': 'xoxb-test'}, clear=False):
            with patch('session_registry.WebClient', return_value=mock_slack_client):
                from session_registry import SessionRegistry
                SessionRegistry._instance = None

                registry = SessionRegistry(
                    registry_dir=str(tmp_path / "registry"),
                    socket_path=str(tmp_path / "sockets" / "registry.sock"),
                    slack_token='xoxb-test',
                    slack_channel='default-channel'
                )

                result = registry._create_slack_thread({
                    'session_id': 'test123',
                    'project': 'test-project',
                    'terminal': 'test-terminal',
                    'description': 'Working on auth feature'
                })

                # Should have called chat_postMessage
                mock_slack_client.chat_postMessage.assert_called()


class TestAsyncSlackThreadCreation:
    """Tests for async thread creation."""

    def test_async_slack_thread_creation(self, tmp_path, clean_env, mock_slack_client, sample_session_data):
        """Thread creation runs asynchronously."""
        with patch.dict(os.environ, {'SLACK_BOT_TOKEN': 'xoxb-test'}, clear=False):
            with patch('session_registry.WebClient', return_value=mock_slack_client):
                from session_registry import SessionRegistry
                SessionRegistry._instance = None

                registry = SessionRegistry(
                    registry_dir=str(tmp_path / "registry"),
                    socket_path=str(tmp_path / "sockets" / "registry.sock"),
                    slack_token='xoxb-test',
                    slack_channel='default-channel'
                )

                # Register should return immediately without blocking
                start = time.time()
                session = registry.register_session(sample_session_data)
                elapsed = time.time() - start

                # Should complete quickly (async thread creation)
                assert elapsed < 1.0
                assert session['session_id'] == sample_session_data['session_id']
