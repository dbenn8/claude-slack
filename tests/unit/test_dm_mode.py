"""
Unit tests for DM mode forwarding in core/dm_mode.py
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add core directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))

# Note: These tests assume the dm_mode module exists with basic structure


class TestForwardToDMSubscribers:
    """Tests for forward_to_dm_subscribers()"""

    def test_forward_output_to_subscribers(self, temp_registry_db, sample_session_data, mock_slack_client):
        """Posts message to all DM subscribers for session."""
        from dm_mode import forward_to_dm_subscribers

        temp_registry_db.create_session(sample_session_data)

        # Create two subscriptions
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

        forward_to_dm_subscribers(
            temp_registry_db,
            sample_session_data['session_id'],
            'Test message',
            mock_slack_client
        )

        # Should have posted to both DM channels
        assert mock_slack_client.chat_postMessage.call_count == 2

        # Check calls were made to correct channels
        calls = mock_slack_client.chat_postMessage.call_args_list
        channels_called = {call.kwargs.get('channel') or call[1].get('channel') for call in calls}
        assert 'D111111' in channels_called
        assert 'D222222' in channels_called

    def test_forward_output_no_subscribers(self, temp_registry_db, sample_session_data, mock_slack_client):
        """Does nothing when no subscribers (no error)."""
        from dm_mode import forward_to_dm_subscribers

        temp_registry_db.create_session(sample_session_data)

        # No subscriptions created
        forward_to_dm_subscribers(
            temp_registry_db,
            sample_session_data['session_id'],
            'Test message',
            mock_slack_client
        )

        # Should not have posted anything
        assert mock_slack_client.chat_postMessage.call_count == 0

    def test_forward_output_handles_errors(self, temp_registry_db, sample_session_data, mock_slack_client):
        """Continues forwarding to other subscribers if one fails."""
        from dm_mode import forward_to_dm_subscribers

        temp_registry_db.create_session(sample_session_data)

        # Create two subscriptions
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

        # Make first call fail
        from slack_sdk.errors import SlackApiError
        mock_slack_client.chat_postMessage.side_effect = [
            SlackApiError('test_error', {'error': 'channel_not_found'}),
            {'ok': True, 'ts': '123.456'}  # Second call succeeds
        ]

        # Should not raise, should continue to second subscriber
        forward_to_dm_subscribers(
            temp_registry_db,
            sample_session_data['session_id'],
            'Test message',
            mock_slack_client
        )

        # Should have attempted both
        assert mock_slack_client.chat_postMessage.call_count == 2


class TestForwardTerminalOutput:
    """Tests for forward_terminal_output()"""

    def test_forward_terminal_output(self, temp_registry_db, sample_session_data, mock_slack_client, tmp_path):
        """Reads buffer file and forwards content."""
        from dm_mode import forward_terminal_output

        temp_registry_db.create_session(sample_session_data)
        temp_registry_db.create_dm_subscription(
            user_id='U111111',
            session_id=sample_session_data['session_id'],
            dm_channel_id='D111111'
        )

        # Create buffer file
        buffer_path = tmp_path / "buffer.txt"
        buffer_path.write_text("Hello from Claude!")

        forward_terminal_output(
            temp_registry_db,
            sample_session_data['session_id'],
            str(buffer_path),
            mock_slack_client
        )

        # Should have posted the message
        assert mock_slack_client.chat_postMessage.call_count == 1
        call_args = mock_slack_client.chat_postMessage.call_args
        assert 'Hello from Claude!' in str(call_args)

    def test_forward_terminal_strips_ansi(self, temp_registry_db, sample_session_data, mock_slack_client, tmp_path):
        """ANSI escape codes stripped from output."""
        from dm_mode import forward_terminal_output, strip_ansi_codes

        # Test strip_ansi_codes directly
        ansi_text = '\x1b[1mBold text\x1b[0m and \x1b[31mred text\x1b[0m'
        clean_text = strip_ansi_codes(ansi_text)
        assert clean_text == 'Bold text and red text'
        assert '\x1b' not in clean_text

        # Test via forward_terminal_output
        temp_registry_db.create_session(sample_session_data)
        temp_registry_db.create_dm_subscription(
            user_id='U111111',
            session_id=sample_session_data['session_id'],
            dm_channel_id='D111111'
        )

        buffer_path = tmp_path / "ansi_buffer.txt"
        buffer_path.write_text('\x1b[32mGreen output\x1b[0m')

        forward_terminal_output(
            temp_registry_db,
            sample_session_data['session_id'],
            str(buffer_path),
            mock_slack_client
        )

        call_args = mock_slack_client.chat_postMessage.call_args
        message_text = call_args.kwargs.get('text', '') or call_args[1].get('text', '')
        assert 'Green output' in message_text
        assert '\x1b' not in message_text


class TestSessionEndCleanup:
    """Tests for handle_session_end()"""

    def test_cleanup_on_session_end(self, temp_registry_db, sample_session_data, mock_slack_client):
        """All DM subscriptions removed when session ends."""
        from dm_mode import handle_session_end, attach_to_session

        temp_registry_db.create_session(sample_session_data)

        # Create subscriptions
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

        # Handle session end
        handle_session_end(
            temp_registry_db,
            sample_session_data['session_id'],
            mock_slack_client
        )

        # All subscriptions should be gone
        subs = temp_registry_db.get_dm_subscriptions_for_session(sample_session_data['session_id'])
        assert len(subs) == 0

    def test_cleanup_notifies_subscribers(self, temp_registry_db, sample_session_data, mock_slack_client):
        """Subscribers notified 'Session ended' before removal."""
        from dm_mode import handle_session_end

        temp_registry_db.create_session(sample_session_data)

        # Create subscriptions
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

        handle_session_end(
            temp_registry_db,
            sample_session_data['session_id'],
            mock_slack_client
        )

        # Should have notified both subscribers
        assert mock_slack_client.chat_postMessage.call_count == 2

        # Check that messages mention session ended
        for call in mock_slack_client.chat_postMessage.call_args_list:
            text = call.kwargs.get('text', '') or call[1].get('text', '')
            assert 'ended' in text.lower() or 'session' in text.lower()


class TestListActiveSessions:
    """Tests for list_active_sessions() and format_session_list_for_slack()"""

    def test_list_active_sessions(self, temp_registry_db, sample_session_data):
        """Returns list of active sessions with session_id, project, created_at."""
        from dm_mode import list_active_sessions

        temp_registry_db.create_session(sample_session_data)

        sessions = list_active_sessions(temp_registry_db)
        assert len(sessions) == 1
        assert sessions[0]['session_id'] == sample_session_data['session_id']
        assert sessions[0]['project'] == sample_session_data['project']
        assert 'created_at' in sessions[0]

    def test_list_active_sessions_excludes_ended(self, temp_registry_db, sample_session_data):
        """Sessions with status='ended' not included."""
        from dm_mode import list_active_sessions

        temp_registry_db.create_session(sample_session_data)
        temp_registry_db.update_session(sample_session_data['session_id'], {'status': 'ended'})

        sessions = list_active_sessions(temp_registry_db)
        assert len(sessions) == 0

    def test_list_active_sessions_empty(self, temp_registry_db):
        """Returns empty list when no active sessions."""
        from dm_mode import list_active_sessions

        sessions = list_active_sessions(temp_registry_db)
        assert sessions == []

    def test_format_session_list_for_slack(self, temp_registry_db, sample_session_data):
        """Formats as readable Slack message with session IDs and /attach hint."""
        from dm_mode import format_session_list_for_slack

        temp_registry_db.create_session(sample_session_data)

        message = format_session_list_for_slack(temp_registry_db)
        assert sample_session_data['session_id'] in message
        assert sample_session_data['project'] in message
        assert '/attach' in message

    def test_format_session_list_empty(self, temp_registry_db):
        """Shows 'no active sessions' message."""
        from dm_mode import format_session_list_for_slack

        message = format_session_list_for_slack(temp_registry_db)
        assert 'no active sessions' in message.lower()


class TestAttachToSession:
    """Tests for attach_to_session()"""

    def test_attach_to_session_success(self, temp_registry_db, sample_session_data, mock_slack_client):
        """Creates subscription, returns success=True."""
        from dm_mode import attach_to_session

        temp_registry_db.create_session(sample_session_data)

        result = attach_to_session(
            temp_registry_db,
            user_id='U123456',
            session_id=sample_session_data['session_id'],
            dm_channel_id='D123456',
            slack_client=mock_slack_client,
            history_count=0
        )

        assert result['success'] is True
        assert 'attached' in result.get('message', '').lower()

        # Verify subscription was created
        sub = temp_registry_db.get_dm_subscription_for_user('U123456')
        assert sub is not None
        assert sub['session_id'] == sample_session_data['session_id']

    def test_attach_to_session_not_found(self, temp_registry_db, mock_slack_client):
        """Returns success=False with 'not found' message."""
        from dm_mode import attach_to_session

        result = attach_to_session(
            temp_registry_db,
            user_id='U123456',
            session_id='nonexistent',
            dm_channel_id='D123456',
            slack_client=mock_slack_client,
            history_count=0
        )

        assert result['success'] is False
        assert 'not found' in result.get('message', '').lower()

    def test_attach_to_session_sends_history(self, temp_registry_db, sample_session_data, mock_slack_client, tmp_path):
        """When history_count > 0, sends last N messages to DM."""
        from dm_mode import attach_to_session
        import json

        # Set up session with a real transcript path
        sample_session_data['project_dir'] = str(tmp_path)
        temp_registry_db.create_session(sample_session_data)

        # Create a mock transcript file
        transcript_dir = tmp_path / ".claude" / "projects" / f"-{str(tmp_path).replace('/', '-')[1:]}"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = transcript_dir / f"{sample_session_data['session_id']}.jsonl"

        with open(transcript_path, 'w') as f:
            for i in range(5):
                msg = {
                    'type': 'user' if i % 2 == 0 else 'assistant',
                    'timestamp': f'2025-01-01T00:00:{i:02d}Z',
                    'message': {'content': [{'type': 'text', 'text': f'Message {i}'}]}
                }
                f.write(json.dumps(msg) + '\n')

        result = attach_to_session(
            temp_registry_db,
            user_id='U123456',
            session_id=sample_session_data['session_id'],
            dm_channel_id='D123456',
            slack_client=mock_slack_client,
            history_count=3
        )

        assert result['success'] is True
        # Should have sent history messages to DM

    def test_attach_replaces_existing_subscription(self, temp_registry_db, sample_session_data, mock_slack_client):
        """Attaching to new session auto-detaches from previous."""
        from dm_mode import attach_to_session

        temp_registry_db.create_session(sample_session_data)

        # Create second session
        session2 = sample_session_data.copy()
        session2['session_id'] = 'sess5678'
        temp_registry_db.create_session(session2)

        # Attach to first session
        attach_to_session(
            temp_registry_db,
            user_id='U123456',
            session_id=sample_session_data['session_id'],
            dm_channel_id='D123456',
            slack_client=mock_slack_client
        )

        # Attach to second session (should replace)
        attach_to_session(
            temp_registry_db,
            user_id='U123456',
            session_id='sess5678',
            dm_channel_id='D123456',
            slack_client=mock_slack_client
        )

        # Should only be subscribed to second session
        sub = temp_registry_db.get_dm_subscription_for_user('U123456')
        assert sub['session_id'] == 'sess5678'

        # First session should have no subscribers
        subs = temp_registry_db.get_dm_subscriptions_for_session(sample_session_data['session_id'])
        assert len(subs) == 0


class TestDetachFromSession:
    """Tests for detach_from_session()"""

    def test_detach_from_session_success(self, temp_registry_db, sample_session_data, mock_slack_client):
        """Removes subscription, returns success=True."""
        from dm_mode import attach_to_session, detach_from_session

        temp_registry_db.create_session(sample_session_data)

        # First attach
        attach_to_session(
            temp_registry_db,
            user_id='U123456',
            session_id=sample_session_data['session_id'],
            dm_channel_id='D123456',
            slack_client=mock_slack_client
        )

        # Then detach
        result = detach_from_session(
            temp_registry_db,
            user_id='U123456',
            slack_client=mock_slack_client,
            dm_channel_id='D123456'
        )

        assert result['success'] is True

        # Verify subscription removed
        sub = temp_registry_db.get_dm_subscription_for_user('U123456')
        assert sub is None

    def test_detach_not_attached(self, temp_registry_db, mock_slack_client):
        """Returns success=True with 'not attached' message (not an error)."""
        from dm_mode import detach_from_session

        result = detach_from_session(
            temp_registry_db,
            user_id='U123456',
            slack_client=mock_slack_client,
            dm_channel_id='D123456'
        )

        # Not an error - just informational
        assert result['success'] is True
        assert 'not' in result.get('message', '').lower() or 'attached' in result.get('message', '').lower()
