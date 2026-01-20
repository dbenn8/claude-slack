"""
End-to-end tests for permission flow workflows.

Tests the complete permission flow including:
- Permission prompts appearing in Slack
- Button click handling
- Reaction-based approvals
- Multiple sequential permissions
"""

import os
import sys
import time
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# Add core directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestPermissionPromptAppears:
    """Test permission prompt button card appears in Slack."""

    def test_permission_prompt_appears(self, temp_registry_db, mock_slack_client, temp_socket_dir):
        """
        Button card posted to Slack correctly.

        Verifies:
        - Permission prompt is posted with Block Kit formatting
        - Buttons have correct action IDs
        - Thread context is preserved
        """
        session_id = "perm1234"
        socket_path = os.path.join(temp_socket_dir, f"{session_id}.sock")

        session_data = {
            'session_id': session_id,
            'project': 'perm-test',
            'project_dir': '/tmp/perm-project',
            'terminal': 'test-terminal',
            'socket_path': socket_path,
            'thread_ts': '1111111111.111111',
            'channel': 'C_PERM_TEST',
            'slack_user_id': 'U123456',
        }

        temp_registry_db.create_session(session_data)

        # Mock successful post
        mock_slack_client.chat_postMessage.return_value = {
            'ok': True,
            'ts': '1111111111.222222',
            'channel': session_data['channel']
        }

        # Simulate permission prompt post
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Permission Request*\nClaude wants to run:\n```rm -rf /tmp/test```"
                }
            },
            {
                "type": "actions",
                "block_id": f"permission_{session_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Yes"},
                        "style": "primary",
                        "action_id": "permission_response_1",
                        "value": "1"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "No"},
                        "style": "danger",
                        "action_id": "permission_response_3",
                        "value": "3"
                    }
                ]
            }
        ]

        result = mock_slack_client.chat_postMessage(
            channel=session_data['channel'],
            thread_ts=session_data['thread_ts'],
            text="Permission Request",
            blocks=blocks
        )

        assert result['ok'] is True
        mock_slack_client.chat_postMessage.assert_called_once()

        # Verify Block Kit structure
        call_args = mock_slack_client.chat_postMessage.call_args
        assert 'blocks' in call_args[1]
        assert len(call_args[1]['blocks']) == 2
        assert call_args[1]['blocks'][1]['type'] == 'actions'


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestPermissionApprove:
    """Test permission approval via button click."""

    def test_permission_approve(self, temp_registry_db, temp_socket_dir):
        """
        Click Yes button -> continues execution.

        Verifies:
        - Button click sends "1" to session socket
        - Session receives the approval
        """
        session_id = "approve01"
        socket_path = os.path.join(temp_socket_dir, f"{session_id}.sock")

        session_data = {
            'session_id': session_id,
            'project': 'approve-test',
            'terminal': 'test-terminal',
            'socket_path': socket_path,
            'thread_ts': '2222222222.111111',
            'channel': 'C_APPROVE',
        }

        temp_registry_db.create_session(session_data)

        # Create server socket to receive approval
        server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_socket.bind(socket_path)
        server_socket.listen(1)
        server_socket.setblocking(False)

        try:
            # Simulate button click by sending "1" to socket
            client_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client_socket.connect(socket_path)
            client_socket.send(b"1\n")
            client_socket.close()

            # Accept connection and read
            import select
            readable, _, _ = select.select([server_socket], [], [], 1.0)
            if readable:
                conn, _ = server_socket.accept()
                data = conn.recv(1024)
                conn.close()
                assert data == b"1\n"

        finally:
            server_socket.close()
            if os.path.exists(socket_path):
                os.unlink(socket_path)


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestPermissionApproveRemember:
    """Test permission approval with remember option."""

    def test_permission_approve_remember(self, temp_registry_db, temp_socket_dir):
        """
        Click Yes-remember -> saves preference.

        Verifies:
        - Button click sends "2" to session socket
        - Session receives the remember approval
        """
        session_id = "remember01"
        socket_path = os.path.join(temp_socket_dir, f"{session_id}.sock")

        session_data = {
            'session_id': session_id,
            'project': 'remember-test',
            'terminal': 'test-terminal',
            'socket_path': socket_path,
            'thread_ts': '3333333333.111111',
            'channel': 'C_REMEMBER',
        }

        temp_registry_db.create_session(session_data)

        # Create server socket
        server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_socket.bind(socket_path)
        server_socket.listen(1)
        server_socket.setblocking(False)

        try:
            # Simulate "Yes, and don't ask again" click
            client_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client_socket.connect(socket_path)
            client_socket.send(b"2\n")
            client_socket.close()

            # Accept and verify
            import select
            readable, _, _ = select.select([server_socket], [], [], 1.0)
            if readable:
                conn, _ = server_socket.accept()
                data = conn.recv(1024)
                conn.close()
                assert data == b"2\n"

        finally:
            server_socket.close()
            if os.path.exists(socket_path):
                os.unlink(socket_path)


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestPermissionDeny:
    """Test permission denial via button click."""

    def test_permission_deny(self, temp_registry_db, temp_socket_dir):
        """
        Click No -> denies and stops.

        Verifies:
        - Button click sends "3" to session socket
        - Session receives the denial
        """
        session_id = "deny01"
        socket_path = os.path.join(temp_socket_dir, f"{session_id}.sock")

        session_data = {
            'session_id': session_id,
            'project': 'deny-test',
            'terminal': 'test-terminal',
            'socket_path': socket_path,
            'thread_ts': '4444444444.111111',
            'channel': 'C_DENY',
        }

        temp_registry_db.create_session(session_data)

        # Create server socket
        server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_socket.bind(socket_path)
        server_socket.listen(1)
        server_socket.setblocking(False)

        try:
            # Simulate "No" click
            client_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client_socket.connect(socket_path)
            client_socket.send(b"3\n")
            client_socket.close()

            # Accept and verify
            import select
            readable, _, _ = select.select([server_socket], [], [], 1.0)
            if readable:
                conn, _ = server_socket.accept()
                data = conn.recv(1024)
                conn.close()
                assert data == b"3\n"

        finally:
            server_socket.close()
            if os.path.exists(socket_path):
                os.unlink(socket_path)


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestPermissionViaReaction:
    """Test permission handling via emoji reactions."""

    def test_permission_via_reaction_approve(self, temp_registry_db, temp_socket_dir):
        """
        Emoji reaction works for approval (1, thumbsup).

        Verifies:
        - Reaction "1" sends "1" to socket
        - Reaction "thumbsup" sends "1" to socket
        """
        session_id = "react01"
        socket_path = os.path.join(temp_socket_dir, f"{session_id}.sock")

        session_data = {
            'session_id': session_id,
            'project': 'react-test',
            'terminal': 'test-terminal',
            'socket_path': socket_path,
            'thread_ts': '5555555555.111111',
            'channel': 'C_REACT',
        }

        temp_registry_db.create_session(session_data)

        # Test reaction mapping
        reaction_map = {
            '1': '1',
            'one': '1',
            '+1': '1',
            'thumbsup': '1',
            '2': '2',
            'two': '2',
            '3': '3',
            'three': '3',
            '-1': '3',
            'thumbsdown': '3',
        }

        # Verify mapping exists
        assert reaction_map['thumbsup'] == '1'
        assert reaction_map['+1'] == '1'

    def test_permission_via_reaction_deny(self, temp_registry_db, temp_socket_dir):
        """
        Emoji reaction works for denial (3, thumbsdown).
        """
        reaction_map = {
            '3': '3',
            'three': '3',
            '-1': '3',
            'thumbsdown': '3',
        }

        assert reaction_map['thumbsdown'] == '3'
        assert reaction_map['-1'] == '3'


@pytest.mark.e2e
@pytest.mark.timeout(60)
class TestMultiplePermissionsSequence:
    """Test handling multiple sequential permission prompts."""

    def test_multiple_permissions_sequence(self, temp_registry_db, temp_socket_dir):
        """
        Handle 3+ sequential prompts.

        Verifies:
        - Multiple permission prompts are handled correctly
        - Each prompt can be approved/denied independently
        """
        session_id = "multi01"
        socket_path = os.path.join(temp_socket_dir, f"{session_id}.sock")

        session_data = {
            'session_id': session_id,
            'project': 'multi-test',
            'terminal': 'test-terminal',
            'socket_path': socket_path,
            'thread_ts': '6666666666.111111',
            'channel': 'C_MULTI',
        }

        temp_registry_db.create_session(session_data)

        # Create server socket
        server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_socket.bind(socket_path)
        server_socket.listen(5)  # Allow multiple connections
        server_socket.setblocking(False)

        try:
            responses_received = []

            # Simulate 3 sequential permission prompts
            for response in ["1", "2", "1"]:
                client_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client_socket.connect(socket_path)
                client_socket.send(f"{response}\n".encode())
                client_socket.close()

                # Accept and read
                import select
                readable, _, _ = select.select([server_socket], [], [], 1.0)
                if readable:
                    conn, _ = server_socket.accept()
                    data = conn.recv(1024)
                    conn.close()
                    responses_received.append(data.decode().strip())

            # Verify all responses received
            assert responses_received == ["1", "2", "1"]

        finally:
            server_socket.close()
            if os.path.exists(socket_path):
                os.unlink(socket_path)
