"""
Unit tests for core/slack_listener.py

Tests Slack event handling including message routing,
permission button handling, and reaction responses.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

# Add core directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))


class TestGetSocketForThread:
    """Tests for get_socket_for_thread()."""

    def test_get_socket_for_thread_exists(self, temp_registry_db, sample_session_data):
        """Registry lookup by thread_ts."""
        # Create session with thread_ts
        temp_registry_db.create_session(sample_session_data)

        with patch('slack_listener.registry_db', temp_registry_db):
            from slack_listener import get_socket_for_thread
            socket_path = get_socket_for_thread(sample_session_data['thread_ts'])
            assert socket_path == sample_session_data['socket_path']

    def test_get_socket_for_thread_prefers_wrapper(self, temp_registry_db, sample_session_data):
        """Prefer wrapper session (8 chars) over Claude UUID (36 chars)."""
        # Create wrapper session (8 char ID)
        wrapper_data = sample_session_data.copy()
        wrapper_data['session_id'] = 'wrap1234'  # 8 chars
        temp_registry_db.create_session(wrapper_data)

        # Create Claude UUID session (36 chars) with same thread
        uuid_data = sample_session_data.copy()
        uuid_data['session_id'] = '12345678-1234-5678-1234-567812345678'  # 36 chars UUID
        uuid_data['socket_path'] = '/tmp/uuid.sock'
        temp_registry_db.create_session(uuid_data)

        with patch('slack_listener.registry_db', temp_registry_db):
            from slack_listener import get_socket_for_thread
            socket_path = get_socket_for_thread(sample_session_data['thread_ts'])
            # Should prefer wrapper's socket
            assert socket_path == wrapper_data['socket_path']

    def test_get_socket_for_thread_not_found(self, temp_registry_db):
        """Returns None when thread not found."""
        with patch('slack_listener.registry_db', temp_registry_db):
            from slack_listener import get_socket_for_thread
            socket_path = get_socket_for_thread('nonexistent.thread')
            assert socket_path is None

    def test_get_socket_for_thread_no_registry(self):
        """Returns None when no registry database."""
        with patch('slack_listener.registry_db', None):
            from slack_listener import get_socket_for_thread
            socket_path = get_socket_for_thread('any.thread')
            assert socket_path is None


class TestGetSocketForChannel:
    """Tests for get_socket_for_channel()."""

    def test_get_socket_for_channel_exists(self, temp_registry_db, sample_session_data_custom_channel, mock_slack_client):
        """Custom channel mode lookup."""
        temp_registry_db.create_session(sample_session_data_custom_channel)

        # Create socket file
        socket_path = sample_session_data_custom_channel['socket_path']
        Path(socket_path).parent.mkdir(parents=True, exist_ok=True)
        Path(socket_path).touch()

        try:
            with patch('slack_listener.registry_db', temp_registry_db):
                with patch('slack_listener.app') as mock_app:
                    mock_app.client = mock_slack_client
                    from slack_listener import get_socket_for_channel
                    result = get_socket_for_channel(sample_session_data_custom_channel['channel'])
                    assert result == socket_path
        finally:
            if Path(socket_path).exists():
                Path(socket_path).unlink()

    def test_get_socket_for_channel_skips_stale(self, temp_registry_db, sample_session_data_custom_channel, mock_slack_client):
        """Skips sessions with missing socket files."""
        temp_registry_db.create_session(sample_session_data_custom_channel)
        # Don't create the socket file - it's stale

        with patch('slack_listener.registry_db', temp_registry_db):
            with patch('slack_listener.app') as mock_app:
                mock_app.client = mock_slack_client
                from slack_listener import get_socket_for_channel
                result = get_socket_for_channel(sample_session_data_custom_channel['channel'])
                assert result is None


class TestSendResponse:
    """Tests for send_response()."""

    def test_send_response_registry_mode(self, temp_registry_db, sample_session_data, tmp_path):
        """Routes via registry socket when thread found."""
        temp_registry_db.create_session(sample_session_data)

        # Create actual socket
        socket_path = tmp_path / "test.sock"

        import socket as sock_module
        server = sock_module.socket(sock_module.AF_UNIX, sock_module.SOCK_STREAM)
        server.bind(str(socket_path))
        server.listen(1)
        server.setblocking(False)

        # Update session with real socket path
        temp_registry_db.update_session(sample_session_data['session_id'], {
            'socket_path': str(socket_path),
            'slack_thread_ts': '123.456',
            'slack_channel': 'C123'
        })

        try:
            with patch('slack_listener.registry_db', temp_registry_db):
                with patch('slack_listener.get_socket_for_thread', return_value=str(socket_path)):
                    from slack_listener import send_response
                    mode = send_response("test message", thread_ts='123.456')
                    assert mode == "registry_socket"
        finally:
            server.close()

    def test_send_response_file_fallback(self, tmp_path):
        """Falls back to file write when socket unavailable."""
        response_file = tmp_path / "slack_response.txt"

        with patch('slack_listener.registry_db', None):
            with patch('slack_listener.SOCKET_PATH', '/nonexistent/socket'):
                with patch('slack_listener.RESPONSE_FILE', response_file):
                    from slack_listener import send_response
                    mode = send_response("test message")
                    assert mode == "file"
                    assert response_file.read_text() == "test message"


class TestHandleMessage:
    """Tests for handle_message event handler."""

    def test_handle_message_threaded(self, temp_registry_db, sample_session_data, tmp_path):
        """Routes threaded message to correct session."""
        temp_registry_db.create_session(sample_session_data)

        # Create mock socket
        socket_path = tmp_path / "test.sock"

        with patch('slack_listener.registry_db', temp_registry_db):
            with patch('slack_listener.get_socket_for_thread', return_value=str(socket_path)):
                with patch('slack_listener.send_response') as mock_send:
                    mock_send.return_value = "registry_socket"

                    from slack_listener import handle_message

                    event = {
                        'type': 'message',
                        'user': 'U123',
                        'text': 'Hello Claude',
                        'ts': '111.222',
                        'channel': 'C123',
                        'channel_type': 'channel',
                        'thread_ts': sample_session_data['thread_ts']
                    }

                    say = MagicMock()
                    handle_message(event, say)

                    mock_send.assert_called_once()

    def test_handle_message_ignores_bot(self):
        """Ignores messages from bots."""
        from slack_listener import handle_message

        event = {
            'type': 'message',
            'bot_id': 'B123',  # Bot message
            'text': 'Bot message',
            'channel': 'C123'
        }

        say = MagicMock()
        handle_message(event, say)

        say.assert_not_called()

    def test_handle_message_channel_requires_prefix(self):
        """Channel messages need command prefix."""
        with patch('slack_listener.send_response') as mock_send:
            from slack_listener import handle_message

            # Regular message without prefix
            event = {
                'type': 'message',
                'user': 'U123',
                'text': 'just chatting',  # No prefix
                'ts': '111.222',
                'channel': 'C123',
                'channel_type': 'channel'
                # No thread_ts = not threaded
            }

            # Mock to return None for channel lookup
            with patch('slack_listener.get_socket_for_channel', return_value=None):
                say = MagicMock()
                handle_message(event, say)

                mock_send.assert_not_called()


class TestHandleReaction:
    """Tests for handle_reaction event handler."""

    def test_handle_reaction_approve(self, mock_slack_client):
        """1 emoji maps to '1' response."""
        with patch('slack_listener.send_response') as mock_send:
            mock_send.return_value = "registry_socket"

            from slack_listener import handle_reaction

            body = {
                'event': {
                    'type': 'reaction_added',
                    'user': 'U123',
                    'reaction': 'one',
                    'item': {
                        'channel': 'C123',
                        'ts': '111.222'
                    }
                }
            }

            mock_slack_client.conversations_history.return_value = {
                'ok': True,
                'messages': [{'ts': '111.222', 'thread_ts': '100.000'}]
            }

            handle_reaction(body, mock_slack_client)

            mock_send.assert_called_once()
            assert mock_send.call_args[0][0] == "1"

    def test_handle_reaction_approve_thumbsup(self, mock_slack_client):
        """Thumbsup emoji maps to '1'."""
        with patch('slack_listener.send_response') as mock_send:
            mock_send.return_value = "registry_socket"

            from slack_listener import handle_reaction

            body = {
                'event': {
                    'type': 'reaction_added',
                    'user': 'U123',
                    'reaction': '+1',
                    'item': {
                        'channel': 'C123',
                        'ts': '111.222'
                    }
                }
            }

            mock_slack_client.conversations_history.return_value = {
                'ok': True,
                'messages': [{'ts': '111.222'}]
            }

            handle_reaction(body, mock_slack_client)

            mock_send.assert_called_once()
            assert mock_send.call_args[0][0] == "1"

    def test_handle_reaction_approve_remember(self, mock_slack_client):
        """2 emoji maps to '2'."""
        with patch('slack_listener.send_response') as mock_send:
            mock_send.return_value = "registry_socket"

            from slack_listener import handle_reaction

            body = {
                'event': {
                    'type': 'reaction_added',
                    'user': 'U123',
                    'reaction': 'two',
                    'item': {
                        'channel': 'C123',
                        'ts': '111.222'
                    }
                }
            }

            mock_slack_client.conversations_history.return_value = {
                'ok': True,
                'messages': [{'ts': '111.222'}]
            }

            handle_reaction(body, mock_slack_client)

            mock_send.assert_called_once()
            assert mock_send.call_args[0][0] == "2"

    def test_handle_reaction_deny(self, mock_slack_client):
        """3 emoji maps to '3'."""
        with patch('slack_listener.send_response') as mock_send:
            mock_send.return_value = "registry_socket"

            from slack_listener import handle_reaction

            body = {
                'event': {
                    'type': 'reaction_added',
                    'user': 'U123',
                    'reaction': 'three',
                    'item': {
                        'channel': 'C123',
                        'ts': '111.222'
                    }
                }
            }

            mock_slack_client.conversations_history.return_value = {
                'ok': True,
                'messages': [{'ts': '111.222'}]
            }

            handle_reaction(body, mock_slack_client)

            mock_send.assert_called_once()
            assert mock_send.call_args[0][0] == "3"

    def test_handle_reaction_deny_thumbsdown(self, mock_slack_client):
        """Thumbsdown emoji maps to '3'."""
        with patch('slack_listener.send_response') as mock_send:
            mock_send.return_value = "registry_socket"

            from slack_listener import handle_reaction

            body = {
                'event': {
                    'type': 'reaction_added',
                    'user': 'U123',
                    'reaction': '-1',
                    'item': {
                        'channel': 'C123',
                        'ts': '111.222'
                    }
                }
            }

            mock_slack_client.conversations_history.return_value = {
                'ok': True,
                'messages': [{'ts': '111.222'}]
            }

            handle_reaction(body, mock_slack_client)

            mock_send.assert_called_once()
            assert mock_send.call_args[0][0] == "3"

    def test_handle_reaction_unmapped(self, mock_slack_client):
        """Ignores unknown emoji."""
        with patch('slack_listener.send_response') as mock_send:
            from slack_listener import handle_reaction

            body = {
                'event': {
                    'type': 'reaction_added',
                    'user': 'U123',
                    'reaction': 'smile',  # Not mapped
                    'item': {
                        'channel': 'C123',
                        'ts': '111.222'
                    }
                }
            }

            handle_reaction(body, mock_slack_client)

            mock_send.assert_not_called()


class TestHandlePermissionButton:
    """Tests for handle_permission_button."""

    def test_handle_permission_button_approve(self, mock_slack_client):
        """Button click sends response to Claude."""
        with patch('slack_listener.send_response') as mock_send:
            mock_send.return_value = "registry_socket"

            from slack_listener import handle_permission_button

            ack = MagicMock()
            body = {
                'user': {'id': 'U123', 'name': 'testuser'},
                'channel': {'id': 'C123'},
                'message': {'ts': '111.222', 'thread_ts': '100.000'},
                'actions': [
                    {'action_id': 'permission_response_1', 'value': '1', 'style': 'primary'}
                ]
            }

            with patch('slack_listener.get_socket_for_channel', return_value=None):
                handle_permission_button(ack, body, mock_slack_client)

            ack.assert_called_once()
            mock_send.assert_called_once()
            assert mock_send.call_args[0][0] == "1"

    def test_handle_permission_button_deny_prompts_feedback(self, mock_slack_client):
        """Deny button prompts for feedback in thread mode."""
        with patch('slack_listener.send_response') as mock_send:
            from slack_listener import handle_permission_button

            ack = MagicMock()
            body = {
                'user': {'id': 'U123', 'name': 'testuser'},
                'channel': {'id': 'C123'},
                'message': {'ts': '111.222', 'thread_ts': '100.000'},
                'actions': [
                    {'action_id': 'permission_response_3', 'value': '3', 'style': 'danger'}
                ]
            }

            with patch('slack_listener.get_socket_for_channel', return_value=None):
                handle_permission_button(ack, body, mock_slack_client)

            ack.assert_called_once()
            # In thread mode with deny, should update message for feedback
            mock_slack_client.chat_update.assert_called_once()
            # send_response should NOT be called yet (waiting for feedback)
            mock_send.assert_not_called()


class TestHandleDMCommands:
    """Tests for DM command handling in slack_listener."""

    def test_handle_dm_sessions_command(self, temp_registry_db, sample_session_data, mock_slack_client):
        """/sessions in DM lists active sessions."""
        from slack_listener import handle_dm_message

        temp_registry_db.create_session(sample_session_data)

        # Create a mock say function
        say = MagicMock()

        # Handle /sessions command in DM
        result = handle_dm_message(
            text='/sessions',
            user_id='U123456',
            dm_channel_id='D123456',
            db=temp_registry_db,
            slack_client=mock_slack_client,
            say=say
        )

        # Should have called say with session list
        assert say.called
        call_text = say.call_args[1].get('text', '') or say.call_args[0][0]
        assert sample_session_data['session_id'] in call_text or 'session' in call_text.lower()

    def test_handle_dm_attach_command(self, temp_registry_db, sample_session_data, mock_slack_client):
        """/attach creates subscription."""
        from slack_listener import handle_dm_message

        temp_registry_db.create_session(sample_session_data)
        say = MagicMock()

        result = handle_dm_message(
            text=f'/attach {sample_session_data["session_id"]}',
            user_id='U123456',
            dm_channel_id='D123456',
            db=temp_registry_db,
            slack_client=mock_slack_client,
            say=say
        )

        # Should have called say with success message
        assert say.called

        # Subscription should be created
        sub = temp_registry_db.get_dm_subscription_for_user('U123456')
        assert sub is not None
        assert sub['session_id'] == sample_session_data['session_id']

    def test_handle_dm_attach_with_history(self, temp_registry_db, sample_session_data, mock_slack_client):
        """/attach <id> 5 sends 5 messages history."""
        from slack_listener import handle_dm_message

        temp_registry_db.create_session(sample_session_data)
        say = MagicMock()

        # Attach with history
        result = handle_dm_message(
            text=f'/attach {sample_session_data["session_id"]} 5',
            user_id='U123456',
            dm_channel_id='D123456',
            db=temp_registry_db,
            slack_client=mock_slack_client,
            say=say
        )

        # Should have created subscription
        sub = temp_registry_db.get_dm_subscription_for_user('U123456')
        assert sub is not None

    def test_handle_dm_detach_command(self, temp_registry_db, sample_session_data, mock_slack_client):
        """/detach removes subscription."""
        from slack_listener import handle_dm_message

        temp_registry_db.create_session(sample_session_data)

        # First attach
        temp_registry_db.create_dm_subscription(
            user_id='U123456',
            session_id=sample_session_data['session_id'],
            dm_channel_id='D123456'
        )

        say = MagicMock()

        # Then detach
        result = handle_dm_message(
            text='/detach',
            user_id='U123456',
            dm_channel_id='D123456',
            db=temp_registry_db,
            slack_client=mock_slack_client,
            say=say
        )

        # Should have called say
        assert say.called

        # Subscription should be removed
        sub = temp_registry_db.get_dm_subscription_for_user('U123456')
        assert sub is None

    def test_dm_non_command_guides_user(self, temp_registry_db, sample_session_data):
        """Non-command DMs now return True and guide user to attach."""
        from slack_listener import handle_dm_message

        # handle_dm_message now handles non-commands by guiding users to attach
        say = MagicMock()

        # Regular message (not a command) returns True and provides guidance
        result = handle_dm_message(
            text='hello',
            user_id='U123456',
            dm_channel_id='D123456',
            db=temp_registry_db,
            slack_client=None,
            say=say
        )

        assert result is True
        assert say.called
        # Should tell user how to attach
        call_args = say.call_args
        assert '/sessions' in call_args.kwargs['text']
        assert '/attach' in call_args.kwargs['text']


class TestAskUserQuestionReactionHandler:
    """Test reaction handling for AskUserQuestion."""

    def test_reaction_maps_emoji_to_option_index(self):
        """Map 1️⃣ 2️⃣ 3️⃣ 4️⃣ to option indices."""
        from slack_listener import ASKUSER_EMOJI_MAP

        # Verify emoji mappings
        assert ASKUSER_EMOJI_MAP['one'] == '0'
        assert ASKUSER_EMOJI_MAP['two'] == '1'
        assert ASKUSER_EMOJI_MAP['three'] == '2'
        assert ASKUSER_EMOJI_MAP['four'] == '3'

        # Unicode emoji versions
        assert ASKUSER_EMOJI_MAP['1️⃣'] == '0'
        assert ASKUSER_EMOJI_MAP['2️⃣'] == '1'
        assert ASKUSER_EMOJI_MAP['3️⃣'] == '2'
        assert ASKUSER_EMOJI_MAP['4️⃣'] == '3'

    def test_reaction_extracts_metadata_from_block_id(self, tmp_path, mock_slack_client):
        """Extract session_id, request_id, question_index from block_id."""
        from slack_listener import handle_askuser_reaction

        # Create temporary response directory
        response_dir = tmp_path / "askuser_responses"
        response_dir.mkdir()

        # Mock message with AskUserQuestion block_id
        mock_slack_client.conversations_history.return_value = {
            'ok': True,
            'messages': [{
                'ts': '111.222',
                'thread_ts': '100.000',
                'blocks': [
                    {
                        'type': 'section',
                        'block_id': 'askuser_Q0_sess123_req456',
                        'text': {'type': 'mrkdwn', 'text': 'Question here'}
                    }
                ]
            }]
        }

        body = {
            'event': {
                'type': 'reaction_added',
                'user': 'U123',
                'reaction': 'one',
                'item': {
                    'channel': 'C123',
                    'ts': '111.222'
                }
            }
        }

        with patch('slack_listener.ASKUSER_RESPONSE_DIR', response_dir):
            result = handle_askuser_reaction(body, mock_slack_client)

            # Should return True for successful handling
            assert result is True

            # Verify response file was created with correct metadata
            response_file = response_dir / "sess123_req456.json"
            assert response_file.exists()

    def test_reaction_writes_response_file(self, tmp_path, mock_slack_client):
        """Write response file on valid reaction."""
        from slack_listener import handle_askuser_reaction

        # Set up temporary response directory
        response_dir = tmp_path / "askuser_responses"
        response_dir.mkdir()

        # Mock message with AskUserQuestion block_id
        mock_slack_client.conversations_history.return_value = {
            'ok': True,
            'messages': [{
                'ts': '111.222',
                'thread_ts': '100.000',
                'blocks': [
                    {
                        'type': 'section',
                        'block_id': 'askuser_Q0_sess123_req456',
                        'text': {'type': 'mrkdwn', 'text': 'Question here'}
                    }
                ]
            }]
        }

        body = {
            'event': {
                'type': 'reaction_added',
                'user': 'U123',
                'reaction': 'one',  # Maps to '0'
                'item': {
                    'channel': 'C123',
                    'ts': '111.222'
                }
            }
        }

        with patch('slack_listener.ASKUSER_RESPONSE_DIR', response_dir):
            handle_askuser_reaction(body, mock_slack_client)

        # Verify response file was created
        response_file = response_dir / "sess123_req456.json"
        assert response_file.exists()

        # Verify content
        import json
        with open(response_file) as f:
            data = json.load(f)

        assert data['question_0'] == '0'
        assert data['user_id'] == 'U123'
        assert 'timestamp' in data

    def test_reaction_ignores_invalid_emoji(self, mock_slack_client):
        """Ignore non-number emojis like 👍."""
        from slack_listener import handle_askuser_reaction

        # Mock message with AskUserQuestion block_id
        mock_slack_client.conversations_history.return_value = {
            'ok': True,
            'messages': [{
                'ts': '111.222',
                'blocks': [
                    {
                        'type': 'section',
                        'block_id': 'askuser_Q0_sess123_req456',
                        'text': {'type': 'mrkdwn', 'text': 'Question here'}
                    }
                ]
            }]
        }

        body = {
            'event': {
                'type': 'reaction_added',
                'user': 'U123',
                'reaction': 'thumbsup',  # Not mapped for AskUser
                'item': {
                    'channel': 'C123',
                    'ts': '111.222'
                }
            }
        }

        # Should return False for unmapped emoji
        result = handle_askuser_reaction(body, mock_slack_client)
        assert result is False

    def test_updates_message_on_selection(self, tmp_path, mock_slack_client):
        """Update Slack message to show selection."""
        from slack_listener import handle_askuser_reaction

        response_dir = tmp_path / "askuser_responses"
        response_dir.mkdir()

        # Mock message with AskUserQuestion block_id
        mock_slack_client.conversations_history.return_value = {
            'ok': True,
            'messages': [{
                'ts': '111.222',
                'thread_ts': '100.000',
                'blocks': [
                    {
                        'type': 'section',
                        'block_id': 'askuser_Q0_sess123_req456',
                        'text': {'type': 'mrkdwn', 'text': 'Question here'}
                    }
                ]
            }]
        }

        body = {
            'event': {
                'type': 'reaction_added',
                'user': 'U123',
                'reaction': 'two',  # Maps to '1'
                'item': {
                    'channel': 'C123',
                    'ts': '111.222'
                }
            }
        }

        with patch('slack_listener.ASKUSER_RESPONSE_DIR', response_dir):
            handle_askuser_reaction(body, mock_slack_client)

        # Verify chat_update was called
        mock_slack_client.chat_update.assert_called_once()

        # Verify the update shows the selection
        call_kwargs = mock_slack_client.chat_update.call_args.kwargs
        assert call_kwargs['channel'] == 'C123'
        assert call_kwargs['ts'] == '111.222'


class TestAskUserQuestionThreadReply:
    """Test thread reply handling for 'Other' responses."""

    def test_thread_reply_to_askuser_message(self, tmp_path, mock_slack_client):
        """Thread reply treated as 'Other' response."""
        from slack_listener import handle_askuser_thread_reply
        import json

        # Setup: mock parent message with askuser block_id
        parent_message = {
            'ts': '1234567890.123456',
            'thread_ts': '1234567890.123456',
            'blocks': [
                {
                    'type': 'section',
                    'block_id': 'askuser_Q0_test-session_req-123',
                    'text': {'type': 'mrkdwn', 'text': 'Which option?'}
                }
            ]
        }

        mock_slack_client.conversations_history.return_value = {
            'ok': True,
            'messages': [parent_message]
        }

        # Create temporary response directory
        response_dir = tmp_path / "askuser_responses"
        response_dir.mkdir()

        # Thread reply event
        event = {
            'type': 'message',
            'user': 'U123456',
            'text': 'I prefer a custom approach',
            'ts': '1234567890.123457',
            'channel': 'C123456',
            'thread_ts': '1234567890.123456'
        }

        with patch('slack_listener.ASKUSER_RESPONSE_DIR', response_dir):
            with patch('slack_listener.app') as mock_app:
                mock_app.client = mock_slack_client
                # Call the handler
                handle_askuser_thread_reply(event, mock_slack_client)

        # Verify response file created
        response_files = list(response_dir.glob("*.json"))
        assert len(response_files) == 1

        # Check response file content
        with open(response_files[0], 'r') as f:
            response_data = json.load(f)

        assert response_data['question_0'] == 'other'
        assert response_data['question_0_text'] == 'I prefer a custom approach'
        assert response_data['user_id'] == 'U123456'
        assert 'timestamp' in response_data

    def test_thread_reply_extracts_metadata_from_parent(self, mock_slack_client):
        """Get session/request ID from parent message."""
        from slack_listener import handle_askuser_thread_reply

        # Parent message with metadata in block_id
        parent_message = {
            'ts': '1234567890.123456',
            'blocks': [
                {
                    'type': 'section',
                    'block_id': 'askuser_Q0_my-session_my-request',
                    'text': {'type': 'mrkdwn', 'text': 'Choose one'}
                }
            ]
        }

        mock_slack_client.conversations_history.return_value = {
            'ok': True,
            'messages': [parent_message]
        }

        event = {
            'type': 'message',
            'user': 'U123456',
            'text': 'My custom reply',
            'ts': '1234567890.123457',
            'channel': 'C123456',
            'thread_ts': '1234567890.123456'
        }

        with patch('slack_listener.ASKUSER_RESPONSE_DIR', Path('/tmp/test_askuser')):
            try:
                handle_askuser_thread_reply(event, mock_slack_client)
            except Exception:
                pass  # May fail when writing file, but we check API calls

        # Verify conversations_history was called to fetch parent
        mock_slack_client.conversations_history.assert_called_once()
        call_kwargs = mock_slack_client.conversations_history.call_args.kwargs
        assert call_kwargs['channel'] == 'C123456'
        assert call_kwargs['latest'] == '1234567890.123456'
        assert call_kwargs['inclusive'] is True
        assert call_kwargs['limit'] == 1

    def test_thread_reply_updates_parent_message(self, tmp_path, mock_slack_client):
        """Update parent message to show 'Other' selection."""
        from slack_listener import handle_askuser_thread_reply

        # Parent message
        parent_message = {
            'ts': '1234567890.123456',
            'blocks': [
                {
                    'type': 'section',
                    'block_id': 'askuser_Q0_sess-id_req-id',
                    'text': {'type': 'mrkdwn', 'text': 'Question?'}
                }
            ]
        }

        mock_slack_client.conversations_history.return_value = {
            'ok': True,
            'messages': [parent_message]
        }

        event = {
            'type': 'message',
            'user': 'U123456',
            'text': 'This is my detailed custom answer',
            'ts': '1234567890.123457',
            'channel': 'C123456',
            'thread_ts': '1234567890.123456'
        }

        response_dir = tmp_path / "askuser_responses"
        response_dir.mkdir()

        with patch('slack_listener.ASKUSER_RESPONSE_DIR', response_dir):
            with patch('slack_listener.app') as mock_app:
                mock_app.client = mock_slack_client
                handle_askuser_thread_reply(event, mock_slack_client)

        # Verify chat_update was called to update parent message
        mock_slack_client.chat_update.assert_called_once()
        call_kwargs = mock_slack_client.chat_update.call_args.kwargs
        assert call_kwargs['channel'] == 'C123456'
        assert call_kwargs['ts'] == '1234567890.123456'
        # Check that the update shows "Other" selection with preview
        assert 'Other' in str(call_kwargs['blocks'])
        assert 'This is my detailed' in str(call_kwargs['blocks'])

    def test_thread_reply_ignores_non_askuser_messages(self, mock_slack_client):
        """Non-AskUser thread replies are ignored."""
        from slack_listener import handle_askuser_thread_reply

        # Parent message WITHOUT askuser block_id
        parent_message = {
            'ts': '1234567890.123456',
            'blocks': [
                {
                    'type': 'section',
                    'block_id': 'regular_message_block',
                    'text': {'type': 'mrkdwn', 'text': 'Regular message'}
                }
            ]
        }

        mock_slack_client.conversations_history.return_value = {
            'ok': True,
            'messages': [parent_message]
        }

        event = {
            'type': 'message',
            'user': 'U123456',
            'text': 'A reply',
            'ts': '1234567890.123457',
            'channel': 'C123456',
            'thread_ts': '1234567890.123456'
        }

        with patch('slack_listener.ASKUSER_RESPONSE_DIR', Path('/tmp/test')):
            # Should return without creating response file
            result = handle_askuser_thread_reply(event, mock_slack_client)
            assert result is None

        # chat_update should NOT be called for non-askuser messages
        mock_slack_client.chat_update.assert_not_called()
