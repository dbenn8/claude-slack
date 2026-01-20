"""
Integration tests for DM Mode functionality.

These tests verify the complete workflow of DM mode:
- Listing sessions
- Attaching to sessions with history
- Receiving output while attached
- Detaching from sessions
- Multiple users subscribing to the same session
- User switching between sessions
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import json

import pytest

# Add core directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))

from registry_db import RegistryDatabase
from dm_mode import (
    parse_dm_command,
    format_session_list_for_slack,
    list_active_sessions,
    attach_to_session,
    detach_from_session,
    forward_to_dm_subscribers,
    handle_session_end,
)
from slack_listener import handle_dm_message


@pytest.fixture
def integration_db(tmp_path):
    """Create a database for integration tests."""
    db_path = tmp_path / "integration_test.db"
    return RegistryDatabase(str(db_path))


@pytest.fixture
def mock_slack_client():
    """Create a mock Slack client for integration tests."""
    client = MagicMock()
    client.chat_postMessage.return_value = {'ok': True, 'ts': '123.456'}
    return client


@pytest.fixture
def session_with_transcript(integration_db, tmp_path):
    """Create a session with a mock transcript file."""
    session_data = {
        'session_id': 'test-sess-1234',
        'project': 'integration-test',
        'project_dir': str(tmp_path / 'project'),
        'socket_path': '/tmp/test.sock',
        'status': 'active',
    }
    integration_db.create_session(session_data)

    # Create mock transcript directory and file
    project_slug = str(tmp_path / 'project').replace('/', '-')
    if project_slug.startswith('-'):
        project_slug = project_slug[1:]
    transcript_dir = tmp_path / '.claude' / 'projects' / f'-{project_slug}'
    transcript_dir.mkdir(parents=True, exist_ok=True)

    transcript_path = transcript_dir / f"{session_data['session_id']}.jsonl"
    with open(transcript_path, 'w') as f:
        for i in range(10):
            msg = {
                'type': 'user' if i % 2 == 0 else 'assistant',
                'timestamp': f'2025-01-01T00:00:{i:02d}Z',
                'message': {'content': [{'type': 'text', 'text': f'Message {i}'}]}
            }
            f.write(json.dumps(msg) + '\n')

    return session_data


class TestDMModeIntegration:
    """Integration tests for complete DM mode workflows."""

    def test_full_dm_workflow(self, integration_db, session_with_transcript, mock_slack_client, tmp_path, monkeypatch):
        """list -> attach with history -> receive output -> detach"""
        # Set HOME to tmp_path so transcript path construction works
        monkeypatch.setenv('HOME', str(tmp_path))

        user_id = 'U_WORKFLOW'
        dm_channel = 'D_WORKFLOW'
        session_id = session_with_transcript['session_id']

        # Step 1: List sessions - should show our session
        sessions = list_active_sessions(integration_db)
        assert len(sessions) == 1
        assert sessions[0]['session_id'] == session_id

        formatted = format_session_list_for_slack(integration_db)
        assert session_id in formatted
        assert '/attach' in formatted

        # Step 2: Attach to session with history
        result = attach_to_session(
            integration_db,
            user_id=user_id,
            session_id=session_id,
            dm_channel_id=dm_channel,
            slack_client=mock_slack_client,
            history_count=3
        )
        assert result['success'] is True

        # Verify subscription exists
        sub = integration_db.get_dm_subscription_for_user(user_id)
        assert sub is not None
        assert sub['session_id'] == session_id

        # Step 3: Forward output to subscriber
        mock_slack_client.reset_mock()
        forward_to_dm_subscribers(
            integration_db,
            session_id,
            'Claude says: Hello!',
            mock_slack_client
        )

        # Should have sent to DM
        assert mock_slack_client.chat_postMessage.called
        call = mock_slack_client.chat_postMessage.call_args
        assert call.kwargs.get('channel') == dm_channel

        # Step 4: Detach from session
        result = detach_from_session(
            integration_db,
            user_id=user_id,
            slack_client=mock_slack_client,
            dm_channel_id=dm_channel
        )
        assert result['success'] is True

        # Verify subscription removed
        sub = integration_db.get_dm_subscription_for_user(user_id)
        assert sub is None

        # Step 5: Forwarding should no longer reach the user
        mock_slack_client.reset_mock()
        forward_to_dm_subscribers(
            integration_db,
            session_id,
            'Claude says: Goodbye!',
            mock_slack_client
        )
        assert not mock_slack_client.chat_postMessage.called

    def test_multiple_users_same_session(self, integration_db, session_with_transcript, mock_slack_client):
        """Two users subscribe, both receive output."""
        session_id = session_with_transcript['session_id']

        # User 1 attaches
        result1 = attach_to_session(
            integration_db,
            user_id='U_USER1',
            session_id=session_id,
            dm_channel_id='D_USER1',
            slack_client=mock_slack_client
        )
        assert result1['success'] is True

        # User 2 attaches
        result2 = attach_to_session(
            integration_db,
            user_id='U_USER2',
            session_id=session_id,
            dm_channel_id='D_USER2',
            slack_client=mock_slack_client
        )
        assert result2['success'] is True

        # Verify both subscriptions exist
        subs = integration_db.get_dm_subscriptions_for_session(session_id)
        assert len(subs) == 2

        # Forward a message
        mock_slack_client.reset_mock()
        forward_to_dm_subscribers(
            integration_db,
            session_id,
            'Message for both users',
            mock_slack_client
        )

        # Both users should receive the message
        assert mock_slack_client.chat_postMessage.call_count == 2

        channels_called = {
            call.kwargs.get('channel')
            for call in mock_slack_client.chat_postMessage.call_args_list
        }
        assert channels_called == {'D_USER1', 'D_USER2'}

    def test_user_switches_sessions(self, integration_db, mock_slack_client):
        """User attaches to session2, stops receiving session1 output."""
        # Create two sessions
        session1 = {
            'session_id': 'sess-1111',
            'project': 'project-1',
            'socket_path': '/tmp/s1.sock',
            'status': 'active',
        }
        session2 = {
            'session_id': 'sess-2222',
            'project': 'project-2',
            'socket_path': '/tmp/s2.sock',
            'status': 'active',
        }
        integration_db.create_session(session1)
        integration_db.create_session(session2)

        user_id = 'U_SWITCHER'
        dm_channel = 'D_SWITCHER'

        # Attach to session 1
        attach_to_session(
            integration_db,
            user_id=user_id,
            session_id='sess-1111',
            dm_channel_id=dm_channel,
            slack_client=mock_slack_client
        )

        # Verify subscribed to session 1
        sub = integration_db.get_dm_subscription_for_user(user_id)
        assert sub['session_id'] == 'sess-1111'

        # Attach to session 2 (should auto-detach from session 1)
        attach_to_session(
            integration_db,
            user_id=user_id,
            session_id='sess-2222',
            dm_channel_id=dm_channel,
            slack_client=mock_slack_client
        )

        # Verify now subscribed to session 2 only
        sub = integration_db.get_dm_subscription_for_user(user_id)
        assert sub['session_id'] == 'sess-2222'

        # Session 1 should have no subscribers
        subs1 = integration_db.get_dm_subscriptions_for_session('sess-1111')
        assert len(subs1) == 0

        # Session 2 should have our user
        subs2 = integration_db.get_dm_subscriptions_for_session('sess-2222')
        assert len(subs2) == 1

        # Forward to session 1 - user should NOT receive
        mock_slack_client.reset_mock()
        forward_to_dm_subscribers(
            integration_db,
            'sess-1111',
            'Message to session 1',
            mock_slack_client
        )
        assert not mock_slack_client.chat_postMessage.called

        # Forward to session 2 - user SHOULD receive
        forward_to_dm_subscribers(
            integration_db,
            'sess-2222',
            'Message to session 2',
            mock_slack_client
        )
        assert mock_slack_client.chat_postMessage.called
        assert mock_slack_client.chat_postMessage.call_args.kwargs['channel'] == dm_channel


class TestSessionEndCleanupIntegration:
    """Integration tests for session end cleanup."""

    def test_session_end_notifies_and_cleans(self, integration_db, session_with_transcript, mock_slack_client):
        """Session end notifies all subscribers and removes subscriptions."""
        session_id = session_with_transcript['session_id']

        # Multiple users subscribe
        for i in range(3):
            integration_db.create_dm_subscription(
                user_id=f'U_END_{i}',
                session_id=session_id,
                dm_channel_id=f'D_END_{i}'
            )

        # Verify subscriptions exist
        subs = integration_db.get_dm_subscriptions_for_session(session_id)
        assert len(subs) == 3

        # Handle session end
        handle_session_end(integration_db, session_id, mock_slack_client)

        # All users should have been notified
        assert mock_slack_client.chat_postMessage.call_count == 3

        # All subscriptions should be cleaned up
        subs = integration_db.get_dm_subscriptions_for_session(session_id)
        assert len(subs) == 0


class TestDMCommandHandlingIntegration:
    """Integration tests for DM command handling via Slack listener."""

    def test_dm_command_flow(self, integration_db, session_with_transcript, mock_slack_client):
        """Test the full command flow through handle_dm_message."""
        session_id = session_with_transcript['session_id']
        user_id = 'U_CMD_TEST'
        dm_channel = 'D_CMD_TEST'

        say = MagicMock()

        # Test /sessions command
        result = handle_dm_message(
            text='/sessions',
            user_id=user_id,
            dm_channel_id=dm_channel,
            db=integration_db,
            slack_client=mock_slack_client,
            say=say
        )
        assert result is True
        assert say.called
        # Should mention the session
        call_text = say.call_args.kwargs.get('text', '')
        assert session_id in call_text or 'session' in call_text.lower()

        say.reset_mock()

        # Test /attach command
        result = handle_dm_message(
            text=f'/attach {session_id}',
            user_id=user_id,
            dm_channel_id=dm_channel,
            db=integration_db,
            slack_client=mock_slack_client,
            say=say
        )
        assert result is True
        assert say.called

        # Verify subscription created
        sub = integration_db.get_dm_subscription_for_user(user_id)
        assert sub is not None

        say.reset_mock()

        # Test /detach command
        result = handle_dm_message(
            text='/detach',
            user_id=user_id,
            dm_channel_id=dm_channel,
            db=integration_db,
            slack_client=mock_slack_client,
            say=say
        )
        assert result is True
        assert say.called

        # Verify subscription removed
        sub = integration_db.get_dm_subscription_for_user(user_id)
        assert sub is None

    def test_non_command_tells_user_to_attach(self, integration_db, mock_slack_client):
        """Regular messages tell user to attach when not subscribed to a session."""
        say = MagicMock()

        result = handle_dm_message(
            text='Hello there!',
            user_id='U123',
            dm_channel_id='D123',
            db=integration_db,
            slack_client=mock_slack_client,
            say=say
        )

        # Now handled - user is told how to attach
        assert result is True
        assert say.called
        call_args = say.call_args
        assert '/sessions' in call_args.kwargs['text']
        assert '/attach' in call_args.kwargs['text']
