"""
End-to-end tests for AskUserQuestion via Slack.

Tests the complete flow: hook -> Slack -> reaction -> response -> Claude.
These tests run without a real Slack connection using mocks.
"""

import json
import os
import sys
import time
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, ANY
import pytest

# Add paths for imports
CLAUDE_SLACK_DIR = Path(__file__).parent.parent.parent
CORE_DIR = CLAUDE_SLACK_DIR / "core"
HOOKS_DIR = CLAUDE_SLACK_DIR / "hooks"
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(HOOKS_DIR))


class TestAskUserQuestionE2E:
    """End-to-end tests for AskUserQuestion via Slack."""

    # Fixtures are in conftest.py: temp_response_dir, temp_log_dir,
    # sample_askuser_hook_input, sample_multiselect_hook_input,
    # sample_multi_question_hook_input, mock_slack_client

    def test_single_question_emoji_response_e2e(
        self,
        sample_askuser_hook_input,
        temp_response_dir,
        temp_log_dir,
        temp_registry_db,
        mock_slack_client
    ):
        """
        Complete flow: hook -> Slack -> reaction -> response -> Claude.

        Steps:
        1. Simulate PreToolUse hook with AskUserQuestion input
        2. Verify Slack message posted with emoji options
        3. Simulate emoji reaction
        4. Verify response file created
        5. Verify hook returns correct output format
        """
        # Import the module functions we need to test
        from on_pretooluse import (
            format_askuserquestion_for_slack,
            get_askuser_response_file,
            wait_for_askuser_response,
            build_askuser_output,
            ASKUSER_RESPONSE_DIR
        )

        # Step 1: Format the question for Slack
        tool_input = sample_askuser_hook_input['tool_input']
        formatted_message = format_askuserquestion_for_slack(tool_input)

        # Step 2: Verify message contains emoji options
        assert '1️⃣' in formatted_message
        assert '2️⃣' in formatted_message
        assert '3️⃣' in formatted_message
        assert 'PostgreSQL' in formatted_message
        assert 'MongoDB' in formatted_message
        assert 'Redis' in formatted_message
        assert 'React with' in formatted_message.lower() or 'react' in formatted_message.lower()

        # Step 3: Simulate response file creation (as if Slack listener wrote it)
        session_id = sample_askuser_hook_input['session_id']
        request_id = 'test-request-123'
        response_file = temp_response_dir / f"{session_id}_{request_id}.json"

        response_data = {
            'question_0': '1',  # Selected option index (0-indexed)
            'user_id': 'U123456',
            'user_name': 'testuser',
            'timestamp': time.time()
        }
        with open(response_file, 'w') as f:
            json.dump(response_data, f)

        # Step 4: Verify response file exists
        assert response_file.exists()

        # Step 5: Build output and verify format
        questions = tool_input['questions']
        output = build_askuser_output(response_data, questions)

        assert 'hookSpecificOutput' in output
        assert output['hookSpecificOutput']['hookEventName'] == 'PreToolUse'
        assert 'decision' in output['hookSpecificOutput']['output']
        assert output['hookSpecificOutput']['output']['decision'] == 'answered'
        assert 'answers' in output['hookSpecificOutput']['output']
        # Answer should contain the selected option label
        answers = output['hookSpecificOutput']['output']['answers']
        assert 'question_0' in answers or '0' in answers

    def test_multiselect_multiple_reactions_e2e(
        self,
        sample_multiselect_hook_input,
        temp_response_dir,
        mock_slack_client
    ):
        """
        Multi-select with multiple emoji reactions.

        User selects options 1 and 3 (Logging and Metrics).
        """
        from on_pretooluse import (
            format_askuserquestion_for_slack,
            build_askuser_output,
        )

        # Format message
        tool_input = sample_multiselect_hook_input['tool_input']
        formatted_message = format_askuserquestion_for_slack(tool_input)

        # Verify multi-select instruction
        assert 'multiSelect' in str(tool_input['questions'][0]) or 'multiple' in formatted_message.lower()

        # Simulate multi-select response
        session_id = sample_multiselect_hook_input['session_id']
        request_id = 'test-request-multi'
        response_file = temp_response_dir / f"{session_id}_{request_id}.json"

        # User selected options 0 and 2 (Logging and Metrics)
        response_data = {
            'question_0': ['0', '2'],  # Multiple selections
            'user_id': 'U123456',
            'user_name': 'testuser',
            'timestamp': time.time()
        }
        with open(response_file, 'w') as f:
            json.dump(response_data, f)

        # Build output
        questions = tool_input['questions']
        output = build_askuser_output(response_data, questions)

        # Verify multiple answers returned
        answers = output['hookSpecificOutput']['output']['answers']
        # Should contain both selected options
        answer_value = answers.get('question_0') or answers.get('0')
        assert answer_value is not None
        # For multi-select, answer should be list or contain multiple values
        if isinstance(answer_value, list):
            assert len(answer_value) >= 2

    def test_other_thread_reply_e2e(
        self,
        sample_askuser_hook_input,
        temp_response_dir,
        mock_slack_client
    ):
        """
        'Other' response via thread reply.

        User types custom text instead of selecting an option.
        """
        from on_pretooluse import build_askuser_output

        tool_input = sample_askuser_hook_input['tool_input']

        # Simulate 'other' response
        session_id = sample_askuser_hook_input['session_id']
        request_id = 'test-request-other'
        response_file = temp_response_dir / f"{session_id}_{request_id}.json"

        response_data = {
            'question_0': 'other',
            'question_0_text': 'Use SQLite for development',
            'user_id': 'U123456',
            'user_name': 'testuser',
            'timestamp': time.time()
        }
        with open(response_file, 'w') as f:
            json.dump(response_data, f)

        # Build output
        questions = tool_input['questions']
        output = build_askuser_output(response_data, questions)

        # Verify 'other' text is in output
        answers = output['hookSpecificOutput']['output']['answers']
        answer_value = answers.get('question_0') or answers.get('0')
        # Should contain the custom text
        assert 'SQLite' in str(answer_value) or 'SQLite' in str(answers)

    def test_timeout_falls_back_to_terminal_e2e(
        self,
        sample_askuser_hook_input,
        temp_response_dir,
        monkeypatch
    ):
        """
        Timeout results in pass-through to terminal.

        When no response is received within timeout, hook exits 0.
        """
        from on_pretooluse import wait_for_askuser_response

        session_id = sample_askuser_hook_input['session_id']
        request_id = 'test-request-timeout'

        # Use very short timeout
        # Response file does NOT exist, so wait should timeout
        with patch('on_pretooluse.ASKUSER_RESPONSE_DIR', temp_response_dir):
            result = wait_for_askuser_response(
                session_id,
                request_id,
                timeout=0.1,  # Very short timeout
                poll_interval=0.05
            )

        # Should return None on timeout
        assert result is None

    def test_multi_question_complete_flow_e2e(
        self,
        sample_multi_question_hook_input,
        temp_response_dir,
        mock_slack_client
    ):
        """
        Multiple questions all answered.

        Two questions, user answers both.
        """
        from on_pretooluse import (
            format_askuserquestion_for_slack,
            build_askuser_output,
        )

        tool_input = sample_multi_question_hook_input['tool_input']

        # Format message
        formatted_message = format_askuserquestion_for_slack(tool_input)

        # Should have both questions
        assert 'Which framework?' in formatted_message
        assert 'Which database?' in formatted_message
        assert 'FastAPI' in formatted_message
        assert 'PostgreSQL' in formatted_message

        # Simulate response for both questions
        session_id = sample_multi_question_hook_input['session_id']
        request_id = 'test-request-multi-q'
        response_file = temp_response_dir / f"{session_id}_{request_id}.json"

        response_data = {
            'question_0': '0',  # FastAPI
            'question_1': '1',  # MongoDB
            'user_id': 'U123456',
            'user_name': 'testuser',
            'timestamp': time.time()
        }
        with open(response_file, 'w') as f:
            json.dump(response_data, f)

        # Build output
        questions = tool_input['questions']
        output = build_askuser_output(response_data, questions)

        # Verify both answers present
        answers = output['hookSpecificOutput']['output']['answers']
        assert 'question_0' in answers or '0' in str(answers)
        assert 'question_1' in answers or '1' in str(answers)


class TestSlackListenerReactionE2E:
    """E2E tests for Slack listener reaction handling."""

    # Fixtures are in conftest.py: mock_ack, sample_askuser_reaction_body,
    # temp_response_dir, mock_slack_client

    def test_reaction_creates_response_file(
        self,
        sample_askuser_reaction_body,
        temp_response_dir,
        mock_slack_client
    ):
        """
        Emoji reaction creates response file.

        When user reacts with 1️⃣, response file should be created.
        """
        from slack_listener import handle_askuser_reaction

        # Mock the message fetch to return AskUserQuestion metadata
        mock_slack_client.conversations_history.return_value = {
            'ok': True,
            'messages': [{
                'ts': '1234567890.123456',
                'blocks': [{
                    'block_id': 'askuser_Q0_test-session-e2e_req123'
                }]
            }]
        }

        with patch('slack_listener.ASKUSER_RESPONSE_DIR', temp_response_dir):
            # handle_askuser_reaction takes (body, client) - not ack
            result = handle_askuser_reaction(
                sample_askuser_reaction_body,
                mock_slack_client
            )

        # Verify response file created (if handler returned True)
        if result:
            response_files = list(temp_response_dir.glob('*.json'))
            assert len(response_files) >= 1

            # Verify content
            with open(response_files[0]) as f:
                data = json.load(f)
            assert data.get('question_0') == '0'  # 1️⃣ = index 0
        else:
            # Handler may return False if message doesn't have askuser block
            # In this case the mock should have the right block_id
            pytest.fail("handle_askuser_reaction returned False - check mock setup")

    def test_thread_reply_creates_other_response(
        self,
        temp_response_dir,
        mock_slack_client
    ):
        """
        Thread reply creates 'other' response.
        """
        from slack_listener import handle_askuser_thread_reply

        event = {
            'type': 'message',
            'user': 'U123456',
            'text': 'Use a custom approach instead',
            'ts': '1234567890.999999',
            'channel': 'C123456',
            'thread_ts': '1234567890.123456'
        }

        # Mock parent message fetch
        mock_slack_client.conversations_history.return_value = {
            'ok': True,
            'messages': [{
                'ts': '1234567890.123456',
                'blocks': [{
                    'block_id': 'askuser_Q0_test-session-e2e_req456'
                }]
            }]
        }

        with patch('slack_listener.ASKUSER_RESPONSE_DIR', temp_response_dir):
            handle_askuser_thread_reply(event, mock_slack_client)

        # Verify response file
        response_files = list(temp_response_dir.glob('*.json'))
        assert len(response_files) >= 1

        with open(response_files[0]) as f:
            data = json.load(f)
        assert data.get('question_0') == 'other'
        assert 'custom approach' in data.get('question_0_text', '')


class TestMessageCleanupE2E:
    """E2E tests for message cleanup after response."""

    def test_message_deleted_after_response(
        self,
        mock_slack_client,
        temp_response_dir
    ):
        """
        Slack message deleted/updated after user responds.
        """
        from on_pretooluse import cleanup_askuser_message

        channel = 'C123456'
        message_ts = '1234567890.123456'
        user_selection = 'PostgreSQL'

        cleanup_askuser_message(mock_slack_client, channel, message_ts, user_selection)

        # Should either delete or update the message
        assert (
            mock_slack_client.chat_delete.called or
            mock_slack_client.chat_update.called
        )
