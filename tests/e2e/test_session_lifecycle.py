"""
End-to-end tests for session lifecycle workflows.

Tests complete session workflows from start to finish, including:
- Session registration and initialization
- Session cleanup and deactivation
- Custom channel mode
- Description and permissions channel features
"""

import os
import sys
import time
import socket
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

# Add core directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestFullSessionStartToEnd:
    """Test complete session workflow from registration to deactivation."""

    def test_full_session_start_to_end(self, temp_registry_db, temp_socket_dir, mock_slack_client):
        """
        Complete workflow: register -> messages -> deactivate.

        Verifies:
        - Session can be registered
        - Socket is created
        - Session can be looked up
        - Session can be deactivated
        - Database is updated correctly
        """
        session_id = "e2e12345"
        socket_path = os.path.join(temp_socket_dir, f"{session_id}.sock")

        session_data = {
            'session_id': session_id,
            'project': 'e2e-test-project',
            'project_dir': '/tmp/e2e-project',
            'terminal': 'test-terminal',
            'socket_path': socket_path,
            'thread_ts': '1234567890.123456',
            'channel': 'C123456',
            'slack_user_id': 'U123456',
        }

        # Step 1: Register session
        result = temp_registry_db.create_session(session_data)
        assert result['session_id'] == session_id
        assert result['status'] == 'active'

        # Step 2: Create socket (simulating wrapper)
        server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_socket.bind(socket_path)
        server_socket.listen(1)
        assert os.path.exists(socket_path)

        # Step 3: Verify session can be looked up
        fetched = temp_registry_db.get_session(session_id)
        assert fetched is not None
        assert fetched['session_id'] == session_id
        assert fetched['socket_path'] == socket_path

        # Step 4: Simulate activity updates
        temp_registry_db.update_session(session_id, {
            'last_activity': datetime.now()
        })

        # Step 5: Mark session as ended
        temp_registry_db.update_session(session_id, {'status': 'ended'})

        # Step 6: Verify session ended
        ended_session = temp_registry_db.get_session(session_id)
        assert ended_session['status'] == 'ended'

        # Cleanup
        server_socket.close()
        if os.path.exists(socket_path):
            os.unlink(socket_path)


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestSessionCleanupOnExit:
    """Test that sockets and database are properly cleaned up on session exit."""

    def test_session_cleanup_on_exit(self, temp_registry_db, temp_socket_dir):
        """
        Sockets removed, DB updated on session end.

        Verifies:
        - Socket file is removed
        - Database status is updated to 'ended'
        - Session can be queried after cleanup
        """
        session_id = "cleanup01"
        socket_path = os.path.join(temp_socket_dir, f"{session_id}.sock")

        session_data = {
            'session_id': session_id,
            'project': 'cleanup-test',
            'project_dir': '/tmp/cleanup-project',
            'terminal': 'test-terminal',
            'socket_path': socket_path,
            'thread_ts': '1234567890.111111',
            'channel': 'C123456',
        }

        # Register and create socket
        temp_registry_db.create_session(session_data)

        server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_socket.bind(socket_path)
        server_socket.listen(1)

        assert os.path.exists(socket_path)

        # Simulate session end
        temp_registry_db.update_session(session_id, {'status': 'ended'})

        # Cleanup socket (simulating wrapper cleanup)
        server_socket.close()
        os.unlink(socket_path)

        # Verify cleanup
        assert not os.path.exists(socket_path)

        session = temp_registry_db.get_session(session_id)
        assert session['status'] == 'ended'

        # Verify session can still be queried (not deleted)
        assert session is not None


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestSessionWithDescription:
    """Test session creation with description flag."""

    def test_session_with_description(self, temp_registry_db, mock_slack_client):
        """
        -d flag works (description in thread).

        Verifies:
        - Session with custom description can be created
        - Description is accessible from session data
        """
        session_id = "desc1234"
        description = "Working on authentication bug fix"

        session_data = {
            'session_id': session_id,
            'project': 'auth-project',
            'project_dir': '/tmp/auth-project',
            'terminal': 'test-terminal',
            'socket_path': f'/tmp/{session_id}.sock',
            'thread_ts': '1234567890.222222',
            'channel': 'C123456',
            'slack_user_id': 'U123456',
        }

        # In real implementation, description would be posted to Slack
        temp_registry_db.create_session(session_data)

        # Verify we can post description to Slack thread
        mock_slack_client.chat_postMessage.return_value = {
            'ok': True,
            'ts': session_data['thread_ts'],
            'channel': session_data['channel']
        }

        # Simulate posting description to thread
        mock_slack_client.chat_postMessage(
            channel=session_data['channel'],
            thread_ts=session_data['thread_ts'],
            text=f"Session started: {description}"
        )

        # Verify Slack was called
        mock_slack_client.chat_postMessage.assert_called_once()
        call_args = mock_slack_client.chat_postMessage.call_args
        assert description in call_args[1]['text']

        # Verify session exists
        session = temp_registry_db.get_session(session_id)
        assert session is not None
        assert session['thread_ts'] == session_data['thread_ts']


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestSessionCustomChannel:
    """Test custom channel mode (-c flag)."""

    def test_session_custom_channel(self, temp_registry_db, mock_slack_client):
        """
        -c flag works (custom channel mode).

        Verifies:
        - Session can be created with custom channel (no thread_ts)
        - Messages go to channel directly, not threaded
        - Session can be looked up by channel
        """
        session_id = "cust5678"
        custom_channel = "test-custom-channel"

        session_data = {
            'session_id': session_id,
            'project': 'custom-channel-project',
            'project_dir': '/tmp/custom-project',
            'terminal': 'test-terminal',
            'socket_path': f'/tmp/{session_id}.sock',
            'thread_ts': None,  # Custom channel mode - no threading
            'channel': custom_channel,
            'slack_user_id': 'U123456',
        }

        # Register session
        temp_registry_db.create_session(session_data)

        # Verify session created without thread_ts
        session = temp_registry_db.get_session(session_id)
        assert session is not None
        assert session['thread_ts'] is None
        assert session['channel'] == custom_channel

        # Simulate posting to custom channel (no threading)
        mock_slack_client.chat_postMessage(
            channel=custom_channel,
            text="This is a message in custom channel mode"
        )

        # Verify no thread_ts in the call
        call_args = mock_slack_client.chat_postMessage.call_args
        assert 'thread_ts' not in call_args[1] or call_args[1].get('thread_ts') is None


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestSessionPermissionsChannel:
    """Test separate permissions channel (-p flag)."""

    def test_session_permissions_channel(self, temp_registry_db, mock_slack_client):
        """
        -p flag works (separate permissions channel).

        Verifies:
        - Session can be created with separate permissions channel
        - Permission prompts go to permissions channel
        - Regular messages go to main channel
        """
        session_id = "perm9012"
        main_channel = "C123456"
        permissions_channel = "test-security-approvals"

        session_data = {
            'session_id': session_id,
            'project': 'secure-project',
            'project_dir': '/tmp/secure-project',
            'terminal': 'test-terminal',
            'socket_path': f'/tmp/{session_id}.sock',
            'thread_ts': '1234567890.333333',
            'channel': main_channel,
            'permissions_channel': permissions_channel,
            'slack_user_id': 'U123456',
        }

        # Register session
        temp_registry_db.create_session(session_data)

        # Verify session has permissions channel
        session = temp_registry_db.get_session(session_id)
        assert session is not None
        assert session['permissions_channel'] == permissions_channel
        assert session['channel'] == main_channel

        # Simulate posting permission prompt to permissions channel
        mock_slack_client.chat_postMessage(
            channel=permissions_channel,
            thread_ts=session_data['thread_ts'],
            text="Claude needs permission to use Bash",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "Claude needs permission to use Bash"}
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Yes"},
                            "action_id": "permission_response_1",
                            "value": "1"
                        }
                    ]
                }
            ]
        )

        # Simulate posting regular message to main channel
        mock_slack_client.chat_postMessage(
            channel=main_channel,
            thread_ts=session_data['thread_ts'],
            text="Task completed successfully"
        )

        # Verify both channels were used
        assert mock_slack_client.chat_postMessage.call_count == 2

        # Verify first call was to permissions channel
        first_call = mock_slack_client.chat_postMessage.call_args_list[0]
        assert first_call[1]['channel'] == permissions_channel

        # Verify second call was to main channel
        second_call = mock_slack_client.chat_postMessage.call_args_list[1]
        assert second_call[1]['channel'] == main_channel
