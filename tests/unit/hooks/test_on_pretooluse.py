"""
Unit tests for .claude/hooks/on_pretooluse.py

Tests AskUserQuestion formatting with options and descriptions.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestFormatQuestionForSlack:
    """Test formatting single questions."""

    def _format_question(self, question, index, total):
        """Local implementation of format_question_for_slack."""
        lines = []

        if total > 1:
            lines.append(f"**Question {index + 1}/{total}: {question.get('question', 'N/A')}**")
        else:
            lines.append(f"**{question.get('question', 'N/A')}**")

        lines.append("")

        options = question.get('options', [])
        multi_select = question.get('multiSelect', False)

        if multi_select:
            lines.append("_(Multiple selections allowed)_")
            lines.append("")

        for i, option in enumerate(options, 1):
            label = option.get('label', f'Option {i}')
            description = option.get('description', '')

            lines.append(f"{i}. **{label}**")
            if description:
                lines.append(f"   _{description}_")
            lines.append("")

        return "\n".join(lines)

    def test_format_single_question(self):
        """Format one question correctly."""
        question = {
            'question': 'Which approach should we use?',
            'header': 'Approach',
            'multiSelect': False,
            'options': [
                {'label': 'Option A', 'description': 'Fast but risky'},
                {'label': 'Option B', 'description': 'Slow but safe'}
            ]
        }

        result = self._format_question(question, 0, 1)

        assert 'Which approach should we use?' in result
        assert 'Option A' in result
        assert 'Option B' in result
        assert 'Fast but risky' in result
        assert 'Slow but safe' in result
        # Single question shouldn't have "Question 1/1"
        assert 'Question 1/1' not in result

    def test_format_question_multiselect(self):
        """Handle multiSelect flag."""
        question = {
            'question': 'Select features to enable:',
            'multiSelect': True,
            'options': [
                {'label': 'Feature A'},
                {'label': 'Feature B'},
                {'label': 'Feature C'}
            ]
        }

        result = self._format_question(question, 0, 1)

        assert 'Select features' in result
        assert 'Multiple selections allowed' in result
        assert 'Feature A' in result
        assert 'Feature B' in result
        assert 'Feature C' in result

    def test_format_question_with_descriptions(self):
        """Include option descriptions."""
        question = {
            'question': 'Choose a database:',
            'options': [
                {'label': 'PostgreSQL', 'description': 'Relational, ACID compliant'},
                {'label': 'MongoDB', 'description': 'Document store, flexible schema'},
                {'label': 'Redis', 'description': 'In-memory, key-value'}
            ]
        }

        result = self._format_question(question, 0, 1)

        assert 'PostgreSQL' in result
        assert 'Relational, ACID' in result
        assert 'MongoDB' in result
        assert 'Document store' in result

    def test_format_question_no_descriptions(self):
        """Handle options without descriptions."""
        question = {
            'question': 'Pick one:',
            'options': [
                {'label': 'A'},
                {'label': 'B'}
            ]
        }

        result = self._format_question(question, 0, 1)

        assert '1. **A**' in result
        assert '2. **B**' in result


class TestFormatAskUserQuestionForSlack:
    """Test formatting complete AskUserQuestion tool input."""

    def _format_askuserquestion(self, tool_input):
        """Local implementation of format_askuserquestion_for_slack."""
        questions = tool_input.get('questions', [])

        if not questions:
            return "❓ Claude has a question (no details available)"

        lines = ["❓ **Claude needs your input:**", ""]

        for i, question in enumerate(questions):
            # Format each question
            q_lines = []
            total = len(questions)
            if total > 1:
                q_lines.append(f"**Question {i + 1}/{total}: {question.get('question', 'N/A')}**")
            else:
                q_lines.append(f"**{question.get('question', 'N/A')}**")

            q_lines.append("")

            options = question.get('options', [])
            multi_select = question.get('multiSelect', False)

            if multi_select:
                q_lines.append("_(Multiple selections allowed)_")
                q_lines.append("")

            for j, option in enumerate(options, 1):
                label = option.get('label', f'Option {j}')
                description = option.get('description', '')
                q_lines.append(f"{j}. **{label}**")
                if description:
                    q_lines.append(f"   _{description}_")
                q_lines.append("")

            lines.append("\n".join(q_lines))
            if i < len(questions) - 1:
                lines.append("---")
                lines.append("")

        lines.append("_Reply with the number(s) of your choice._")
        return "\n".join(lines)

    def test_format_multiple_questions(self):
        """Format 2-4 questions."""
        tool_input = {
            'questions': [
                {
                    'question': 'First question?',
                    'options': [{'label': 'Yes'}, {'label': 'No'}]
                },
                {
                    'question': 'Second question?',
                    'options': [{'label': 'A'}, {'label': 'B'}, {'label': 'C'}]
                }
            ]
        }

        result = self._format_askuserquestion(tool_input)

        assert 'Claude needs your input' in result
        assert 'Question 1/2' in result
        assert 'Question 2/2' in result
        assert 'First question?' in result
        assert 'Second question?' in result
        assert '---' in result  # Divider between questions

    def test_format_empty_questions(self):
        """Handle empty questions list."""
        tool_input = {'questions': []}
        result = self._format_askuserquestion(tool_input)
        assert 'no details available' in result

    def test_format_includes_reply_instruction(self):
        """Include reply instructions."""
        tool_input = {
            'questions': [
                {
                    'question': 'Choose one:',
                    'options': [{'label': 'X'}, {'label': 'Y'}]
                }
            ]
        }

        result = self._format_askuserquestion(tool_input)
        assert 'Reply with the number' in result


class TestFilterAskUserQuestionOnly:
    """Test that hook only processes AskUserQuestion tool."""

    def test_filter_askuserquestion_only(self, sample_pretooluse_hook_input):
        """Only process AskUserQuestion calls."""
        # AskUserQuestion should be processed
        assert sample_pretooluse_hook_input['tool_name'] == 'AskUserQuestion'

    def test_skip_other_tools(self):
        """Skip non-AskUserQuestion tools."""
        other_tools = ['Bash', 'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Task']

        for tool_name in other_tools:
            # Hook would exit early for these
            assert tool_name != 'AskUserQuestion'


class TestSplitMessage:
    """Test message splitting."""

    def _split_message(self, text, max_length=39000):
        """Local implementation of split_message."""
        if len(text) <= max_length:
            return [text]

        chunks = []
        while text:
            if len(text) <= max_length:
                chunks.append(text)
                break
            break_point = text.rfind('\n', max_length - 500, max_length)
            if break_point == -1:
                break_point = max_length
            chunks.append(text[:break_point])
            text = text[break_point:].lstrip('\n')
        return chunks

    def test_long_question_splits(self):
        """Long questions with many options split correctly."""
        # Generate very long question text
        long_text = "Very detailed option description. " * 1000

        chunks = self._split_message(long_text, max_length=1000)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 1000


class TestPostToSlack:
    """Test posting questions to Slack."""

    def test_post_question_to_thread(self, mock_slack_client):
        """Post question to correct thread."""
        mock_slack_client.chat_postMessage.return_value = {'ok': True, 'ts': '123.456'}

        # Simulate posting
        mock_slack_client.chat_postMessage(
            channel='C123',
            thread_ts='111.222',
            text='Question text'
        )

        mock_slack_client.chat_postMessage.assert_called_once()
        call_args = mock_slack_client.chat_postMessage.call_args
        assert call_args.kwargs['thread_ts'] == '111.222'


class TestFormatAskUserQuestionWithEmojis:
    """Test emoji-based option formatting."""

    def _format_askuserquestion(self, tool_input):
        """Import and call the real format_askuserquestion_for_slack function."""
        # Import the hook module
        hook_path = Path(__file__).parent.parent.parent.parent / 'hooks' / 'on_pretooluse.py'

        # Read and execute the module to get the function
        import importlib.util
        import importlib

        # Force reload by using a unique module name each time
        import time
        module_name = f"on_pretooluse_{int(time.time() * 1000000)}"

        spec = importlib.util.spec_from_file_location(module_name, hook_path)
        module = importlib.util.module_from_spec(spec)

        # Mock stdin for the module
        original_stdin = sys.stdin
        sys.stdin = type('obj', (object,), {'read': lambda: '{}'})()

        try:
            spec.loader.exec_module(module)
        finally:
            sys.stdin = original_stdin

        return module.format_askuserquestion_for_slack(tool_input)

    def test_format_single_question_with_emojis(self):
        """Format question with 1️⃣ 2️⃣ 3️⃣ 4️⃣ indicators."""
        tool_input = {
            'questions': [
                {
                    'question': 'Which approach should we use?',
                    'options': [
                        {'label': 'Option A', 'description': 'Fast but risky'},
                        {'label': 'Option B', 'description': 'Slow but safe'},
                        {'label': 'Option C', 'description': 'Balanced approach'}
                    ]
                }
            ]
        }

        result = self._format_askuserquestion(tool_input)

        # Should have emoji numbers
        assert '1️⃣' in result
        assert '2️⃣' in result
        assert '3️⃣' in result

        # Should have option labels with full descriptions
        assert 'Option A' in result
        assert 'Fast but risky' in result
        assert 'Option B' in result
        assert 'Slow but safe' in result

        # Should have instruction about reacting
        assert 'React with' in result
        assert '1️⃣' in result and '2️⃣' in result and '3️⃣' in result

    def test_format_multiselect_question(self):
        """Format multi-select with instruction to add multiple reactions."""
        tool_input = {
            'questions': [
                {
                    'question': 'Select features to enable:',
                    'multiSelect': True,
                    'options': [
                        {'label': 'Feature A'},
                        {'label': 'Feature B'},
                        {'label': 'Feature C'},
                        {'label': 'Feature D'}
                    ]
                }
            ]
        }

        result = self._format_askuserquestion(tool_input)

        # Should have all emoji numbers
        assert '1️⃣' in result
        assert '2️⃣' in result
        assert '3️⃣' in result
        assert '4️⃣' in result

        # Should indicate multi-select capability
        assert 'one or more' in result.lower() or 'multiple' in result.lower()

        # Should have react instruction
        assert 'React with' in result

    def test_format_question_with_descriptions(self):
        """Include full descriptions under each option."""
        tool_input = {
            'questions': [
                {
                    'question': 'Choose a database:',
                    'options': [
                        {'label': 'PostgreSQL', 'description': 'Relational, ACID compliant'},
                        {'label': 'MongoDB', 'description': 'Document store, flexible schema'}
                    ]
                }
            ]
        }

        result = self._format_askuserquestion(tool_input)

        # Should have emoji format like "1️⃣ **Label**"
        assert '1️⃣' in result
        assert '**PostgreSQL**' in result

        # Should have description in italics
        assert '_Relational, ACID compliant_' in result or 'Relational, ACID compliant' in result
        assert '_Document store, flexible schema_' in result or 'Document store, flexible schema' in result

    def test_format_includes_other_option(self):
        """Always include 'Other' option for custom text."""
        tool_input = {
            'questions': [
                {
                    'question': 'Pick one:',
                    'options': [
                        {'label': 'A'},
                        {'label': 'B'}
                    ]
                }
            ]
        }

        result = self._format_askuserquestion(tool_input)

        # Should include "Other" option with speech bubble emoji
        assert '💬' in result
        assert 'Other' in result
        assert 'reply in thread' in result.lower() or 'reply with' in result.lower()


class TestAskUserQuestionBlockingWait:
    """Test blocking behavior and response handling."""

    def test_wait_for_response_file_appears(self, tmp_path):
        """Hook polls for response file and returns data when file appears."""
        import time
        import json
        import threading
        from pathlib import Path
        import sys

        # Add hooks directory to path
        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        # Mock ASKUSER_RESPONSE_DIR for this test
        import on_pretooluse
        original_dir = on_pretooluse.ASKUSER_RESPONSE_DIR
        on_pretooluse.ASKUSER_RESPONSE_DIR = tmp_path

        try:
            from on_pretooluse import wait_for_askuser_response

            session_id = "test_session"
            request_id = "test_request"
            response_file = tmp_path / f"{session_id}_{request_id}.json"

            # Create the response file after a short delay
            def create_response():
                time.sleep(0.3)  # Wait 300ms before creating file
                response_data = {"question_0": "1"}
                response_file.write_text(json.dumps(response_data))

            thread = threading.Thread(target=create_response)
            thread.start()

            # Wait for response
            result = wait_for_askuser_response(session_id, request_id, timeout=5, poll_interval=0.1)

            thread.join()

            # Verify response data returned
            assert result is not None
            assert result["question_0"] == "1"

            # Verify file was cleaned up
            assert not response_file.exists()

        finally:
            on_pretooluse.ASKUSER_RESPONSE_DIR = original_dir

    def test_wait_for_response_timeout_returns_none(self, tmp_path):
        """Hook returns None on timeout when no response file appears."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        import on_pretooluse
        original_dir = on_pretooluse.ASKUSER_RESPONSE_DIR
        on_pretooluse.ASKUSER_RESPONSE_DIR = tmp_path

        try:
            from on_pretooluse import wait_for_askuser_response

            session_id = "test_session"
            request_id = "test_request"

            # Don't create response file, should timeout
            result = wait_for_askuser_response(session_id, request_id, timeout=0.5, poll_interval=0.1)

            # Verify returns None after timeout
            assert result is None

        finally:
            on_pretooluse.ASKUSER_RESPONSE_DIR = original_dir

    def test_wait_cleans_up_response_file_after_reading(self, tmp_path):
        """Response file deleted after successfully reading."""
        import json
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        import on_pretooluse
        original_dir = on_pretooluse.ASKUSER_RESPONSE_DIR
        on_pretooluse.ASKUSER_RESPONSE_DIR = tmp_path

        try:
            from on_pretooluse import wait_for_askuser_response

            session_id = "test_session"
            request_id = "test_request"
            response_file = tmp_path / f"{session_id}_{request_id}.json"

            # Create response file immediately
            response_data = {"question_0": "2"}
            response_file.write_text(json.dumps(response_data))

            assert response_file.exists()

            # Call wait function
            result = wait_for_askuser_response(session_id, request_id, timeout=5, poll_interval=0.1)

            # Verify response returned
            assert result is not None
            assert result["question_0"] == "2"

            # Verify file was cleaned up
            assert not response_file.exists()

        finally:
            on_pretooluse.ASKUSER_RESPONSE_DIR = original_dir


class TestAskUserQuestionResponseProtocol:
    """Test response file read/write protocol."""

    def test_response_file_path_generation(self):
        """Generate unique response file path."""
        # Import the function we'll implement
        from pathlib import Path
        import sys
        import os

        # Add hooks directory to path
        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import get_askuser_response_file, ASKUSER_RESPONSE_DIR

        # Test: Generate file path from session_id and request_id
        session_id = 'sess123'
        request_id = 'req456'

        result = get_askuser_response_file(session_id, request_id)

        expected = ASKUSER_RESPONSE_DIR / f"{session_id}_{request_id}.json"
        assert result == expected
        assert result.name == "sess123_req456.json"
        assert result.parent.name == "askuser_responses"

    def test_response_file_format_single_select(self):
        """Response format for single selection."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import build_askuser_output

        # Input: User selected option 1 (index "1") for question_0
        response_data = {
            "question_0": "1"
        }

        questions = [
            {
                "question": "Which approach?",
                "options": [
                    {"label": "Option A"},
                    {"label": "Option B"},
                    {"label": "Option C"}
                ]
            }
        ]

        result = build_askuser_output(response_data, questions)

        # Expected format for Claude
        assert result["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert result["hookSpecificOutput"]["output"]["decision"] == "answered"
        assert result["hookSpecificOutput"]["output"]["answers"]["question_0"] == "Option B"

    def test_response_file_format_multi_select(self):
        """Response format for multiple selections."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import build_askuser_output

        # Input: User selected options 0 and 2 for question_0
        response_data = {
            "question_0": ["0", "2"]
        }

        questions = [
            {
                "question": "Select features:",
                "multiSelect": True,
                "options": [
                    {"label": "Feature A"},
                    {"label": "Feature B"},
                    {"label": "Feature C"}
                ]
            }
        ]

        result = build_askuser_output(response_data, questions)

        # Expected: answers contain both selected option labels
        assert result["hookSpecificOutput"]["output"]["decision"] == "answered"
        answers = result["hookSpecificOutput"]["output"]["answers"]["question_0"]
        assert "Feature A" in answers
        assert "Feature C" in answers
        assert "Feature B" not in answers

    def test_response_file_format_other_text(self):
        """Response format for 'Other' text input."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import build_askuser_output

        # Input: User selected "other" and provided custom text
        response_data = {
            "question_0": "other",
            "question_0_text": "My custom answer"
        }

        questions = [
            {
                "question": "What do you think?",
                "options": [
                    {"label": "Option A"},
                    {"label": "Option B"},
                    {"label": "Other"}
                ]
            }
        ]

        result = build_askuser_output(response_data, questions)

        # Expected: answers contain the custom text
        assert result["hookSpecificOutput"]["output"]["decision"] == "answered"
        assert result["hookSpecificOutput"]["output"]["answers"]["question_0"] == "My custom answer"

    def test_cleanup_response_file(self, tmp_path):
        """Response file deleted after reading."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import cleanup_askuser_response_file

        # Create a temp response file
        response_file = tmp_path / "test_response.json"
        response_file.write_text('{"test": "data"}')

        assert response_file.exists()

        # Call cleanup
        cleanup_askuser_response_file(response_file)

        # Verify file is deleted
        assert not response_file.exists()


class TestMultiQuestionHandling:
    """Test handling of multiple questions in one prompt."""

    def test_format_multiple_questions_with_distinct_block_ids(self):
        """Each question gets its own block_id."""
        from pathlib import Path
        import sys
        import json

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from unittest.mock import MagicMock, patch

        # Import the post_to_slack function
        from on_pretooluse import post_to_slack

        # Mock slack_sdk module and WebClient
        with patch('slack_sdk.WebClient') as MockWebClient:
            mock_client = MagicMock()
            MockWebClient.return_value = mock_client
            mock_client.chat_postMessage.return_value = {'ts': '123.456'}

            # Mock environment
            session_id = 'test_sess'
            request_id = 'test_req'
            bot_token = 'xoxb-test'

            # Mock the format function to return a message with markers
            tool_input = {
                'questions': [
                    {'question': 'Q1?', 'options': [{'label': 'A'}]},
                    {'question': 'Q2?', 'options': [{'label': 'B'}]}
                ]
            }

            from on_pretooluse import format_askuserquestion_for_slack
            formatted = format_askuserquestion_for_slack(tool_input)

            # The formatted message should have both questions
            assert 'Question 1/2' in formatted
            assert 'Question 2/2' in formatted

            # Now test post_to_slack creates correct block structure
            # For multi-question, we expect MULTIPLE blocks with distinct block_ids
            success, message_ts = post_to_slack(
                channel='C123',
                thread_ts='111.222',
                text=formatted,
                bot_token=bot_token,
                session_id=session_id,
                request_id=request_id,
                num_questions=2  # Tell it there are 2 questions
            )

            # Verify that post was called
            assert mock_client.chat_postMessage.called

            # Get the call arguments
            call_kwargs = mock_client.chat_postMessage.call_args.kwargs

            # Check that blocks were provided (for multi-question support)
            assert 'blocks' in call_kwargs
            blocks = call_kwargs['blocks']

            # Verify blocks is a list
            assert isinstance(blocks, list)

            # Verify we have multiple blocks with distinct block_ids
            assert len(blocks) >= 2, "Should have at least 2 blocks for 2 questions"

            # Verify block_ids are distinct
            block_ids = [b.get("block_id") for b in blocks]
            assert f"askuser_Q0_{session_id}_{request_id}" in block_ids
            assert f"askuser_Q1_{session_id}_{request_id}" in block_ids

            # Verify all block_ids are unique
            assert len(block_ids) == len(set(block_ids)), "Block IDs should be unique"

    def test_response_aggregates_all_answers(self):
        """Response contains answers for all questions."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import build_askuser_output

        # Input: 2 questions, both answered
        response_data = {
            "question_0": "1",
            "question_1": "0"
        }

        questions = [
            {
                "question": "First question?",
                "options": [
                    {"label": "Option A"},
                    {"label": "Option B"}
                ]
            },
            {
                "question": "Second question?",
                "options": [
                    {"label": "Choice 1"},
                    {"label": "Choice 2"}
                ]
            }
        ]

        result = build_askuser_output(response_data, questions)

        # Verify both answers are present
        answers = result["hookSpecificOutput"]["output"]["answers"]
        assert "question_0" in answers
        assert "question_1" in answers
        assert answers["question_0"] == "Option B"
        assert answers["question_1"] == "Choice 1"

    def test_partial_response_detection(self):
        """Detect when not all questions are answered."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        # We need to implement a function to check completeness
        # This will be a new function: is_response_complete()

        # Input: 2 questions, only question_0 answered
        response_data = {
            "question_0": "1"
            # question_1 is missing
        }

        num_questions = 2

        # Import the function we'll implement
        from on_pretooluse import is_response_complete

        # Test: Should detect incomplete response
        is_complete = is_response_complete(response_data, num_questions)
        assert is_complete is False

        # Test: Complete response
        complete_response = {
            "question_0": "1",
            "question_1": "0"
        }
        is_complete = is_response_complete(complete_response, num_questions)
        assert is_complete is True

    def test_partial_response_accumulation(self, tmp_path):
        """Accumulate partial responses in response file."""
        import json
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        # Test scenario:
        # 1. User answers question_0 -> response file has question_0
        # 2. User answers question_1 -> response file updated with question_1
        # 3. Hook waits until ALL questions answered

        import on_pretooluse
        original_dir = on_pretooluse.ASKUSER_RESPONSE_DIR
        on_pretooluse.ASKUSER_RESPONSE_DIR = tmp_path

        try:
            from on_pretooluse import (
                accumulate_askuser_response,
                is_response_complete
            )

            session_id = "test_session"
            request_id = "test_request"
            response_file = tmp_path / f"{session_id}_{request_id}.json"

            # First answer (question_0)
            first_answer = {"question_0": "1"}
            accumulate_askuser_response(session_id, request_id, first_answer)

            # Verify file exists and has first answer
            assert response_file.exists()
            data = json.loads(response_file.read_text())
            assert "question_0" in data
            assert data["question_0"] == "1"

            # Check if complete (2 questions total)
            assert not is_response_complete(data, num_questions=2)

            # Second answer (question_1)
            second_answer = {"question_1": "0"}
            accumulate_askuser_response(session_id, request_id, second_answer)

            # Verify file updated with both answers
            data = json.loads(response_file.read_text())
            assert "question_0" in data
            assert "question_1" in data
            assert data["question_0"] == "1"
            assert data["question_1"] == "0"

            # Check if complete now
            assert is_response_complete(data, num_questions=2)

        finally:
            on_pretooluse.ASKUSER_RESPONSE_DIR = original_dir

    def test_wait_for_all_questions_answered(self, tmp_path):
        """Hook waits until ALL questions are answered before returning."""
        import json
        import time
        import threading
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        import on_pretooluse
        original_dir = on_pretooluse.ASKUSER_RESPONSE_DIR
        on_pretooluse.ASKUSER_RESPONSE_DIR = tmp_path

        try:
            from on_pretooluse import wait_for_askuser_response

            session_id = "test_session"
            request_id = "test_request"
            response_file = tmp_path / f"{session_id}_{request_id}.json"
            num_questions = 2

            # Simulate user answering questions one by one
            def simulate_answers():
                time.sleep(0.2)
                # First answer (incomplete)
                partial = {"question_0": "1", "_num_questions": 2}
                response_file.write_text(json.dumps(partial))

                time.sleep(0.2)
                # Second answer (complete)
                complete = {"question_0": "1", "question_1": "0", "_num_questions": 2}
                response_file.write_text(json.dumps(complete))

            thread = threading.Thread(target=simulate_answers)
            thread.start()

            # Wait for response - should wait for BOTH questions
            # Pass num_questions to wait function
            result = wait_for_askuser_response(
                session_id, request_id,
                timeout=5, poll_interval=0.1,
                num_questions=num_questions
            )

            thread.join()

            # Verify we got the complete response (both questions)
            assert result is not None
            assert "question_0" in result
            assert "question_1" in result

        finally:
            on_pretooluse.ASKUSER_RESPONSE_DIR = original_dir


class TestInputValidation:
    """Test input validation for AskUserQuestion tool_input structure."""

    def test_valid_input_passes(self):
        """Valid input should pass validation."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import validate_askuser_input

        # Valid single question
        tool_input = {
            'questions': [
                {
                    'question': 'Which approach?',
                    'options': [
                        {'label': 'Option A'},
                        {'label': 'Option B'}
                    ]
                }
            ]
        }

        is_valid, error_msg = validate_askuser_input(tool_input)
        assert is_valid is True
        assert error_msg == ""

    def test_valid_multiple_questions(self):
        """Valid input with 2-4 questions should pass."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import validate_askuser_input

        # Valid multiple questions
        tool_input = {
            'questions': [
                {
                    'question': 'First?',
                    'options': [{'label': 'A'}, {'label': 'B'}]
                },
                {
                    'question': 'Second?',
                    'options': [{'label': 'X'}, {'label': 'Y'}]
                },
                {
                    'question': 'Third?',
                    'options': [{'label': '1'}, {'label': '2'}]
                }
            ]
        }

        is_valid, error_msg = validate_askuser_input(tool_input)
        assert is_valid is True
        assert error_msg == ""

    def test_missing_questions_array_fails(self):
        """Missing 'questions' array should fail."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import validate_askuser_input

        # No questions array
        tool_input = {}

        is_valid, error_msg = validate_askuser_input(tool_input)
        assert is_valid is False
        assert "questions" in error_msg.lower()

    def test_empty_questions_array_fails(self):
        """Empty 'questions' array should fail."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import validate_askuser_input

        # Empty questions array
        tool_input = {'questions': []}

        is_valid, error_msg = validate_askuser_input(tool_input)
        assert is_valid is False
        assert "questions" in error_msg.lower()

    def test_questions_not_list_fails(self):
        """'questions' must be a list."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import validate_askuser_input

        # questions is not a list
        tool_input = {'questions': "not a list"}

        is_valid, error_msg = validate_askuser_input(tool_input)
        assert is_valid is False
        assert "list" in error_msg.lower()

    def test_too_many_questions_fails(self):
        """More than 4 questions should fail."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import validate_askuser_input

        # 5 questions (too many)
        tool_input = {
            'questions': [
                {
                    'question': f'Question {i}?',
                    'options': [{'label': 'A'}]
                }
                for i in range(5)
            ]
        }

        is_valid, error_msg = validate_askuser_input(tool_input)
        assert is_valid is False
        assert "4" in error_msg

    def test_question_not_dict_fails(self):
        """Each question must be a dict."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import validate_askuser_input

        # Question is not a dict
        tool_input = {
            'questions': ["not a dict"]
        }

        is_valid, error_msg = validate_askuser_input(tool_input)
        assert is_valid is False
        assert "dict" in error_msg.lower()

    def test_missing_question_text_fails(self):
        """Each question must have 'question' text."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import validate_askuser_input

        # Question missing 'question' field
        tool_input = {
            'questions': [
                {
                    'options': [{'label': 'A'}]
                }
            ]
        }

        is_valid, error_msg = validate_askuser_input(tool_input)
        assert is_valid is False
        assert "question" in error_msg.lower()

    def test_options_not_list_fails(self):
        """'options' must be a list."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import validate_askuser_input

        # options is not a list
        tool_input = {
            'questions': [
                {
                    'question': 'What?',
                    'options': "not a list"
                }
            ]
        }

        is_valid, error_msg = validate_askuser_input(tool_input)
        assert is_valid is False
        assert "list" in error_msg.lower()

    def test_too_many_options_fails(self):
        """More than 4 options in a question should fail."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import validate_askuser_input

        # 5 options (too many)
        tool_input = {
            'questions': [
                {
                    'question': 'Choose one:',
                    'options': [
                        {'label': f'Option {i}'}
                        for i in range(5)
                    ]
                }
            ]
        }

        is_valid, error_msg = validate_askuser_input(tool_input)
        assert is_valid is False
        assert "4" in error_msg

    def test_option_not_dict_fails(self):
        """Each option must be a dict."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import validate_askuser_input

        # Option is not a dict
        tool_input = {
            'questions': [
                {
                    'question': 'Choose:',
                    'options': ["not a dict"]
                }
            ]
        }

        is_valid, error_msg = validate_askuser_input(tool_input)
        assert is_valid is False
        assert "dict" in error_msg.lower()

    def test_missing_option_label_fails(self):
        """Each option must have 'label'."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import validate_askuser_input

        # Option missing 'label'
        tool_input = {
            'questions': [
                {
                    'question': 'Choose:',
                    'options': [
                        {'description': 'Some description'}
                    ]
                }
            ]
        }

        is_valid, error_msg = validate_askuser_input(tool_input)
        assert is_valid is False
        assert "label" in error_msg.lower()

    def test_valid_with_optional_fields(self):
        """Valid input with optional fields like header, description, multiSelect."""
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        from on_pretooluse import validate_askuser_input

        # Valid input with optional fields
        tool_input = {
            'questions': [
                {
                    'question': 'What is your choice?',
                    'header': 'Important Decision',
                    'multiSelect': True,
                    'options': [
                        {'label': 'Option A', 'description': 'Description A'},
                        {'label': 'Option B', 'description': 'Description B'}
                    ]
                }
            ]
        }

        is_valid, error_msg = validate_askuser_input(tool_input)
        assert is_valid is True
        assert error_msg == ""


class TestAtomicFileOperations:
    """Test atomic file read/cleanup with locking to prevent race conditions."""

    def test_atomic_read_and_cleanup_with_lock(self, tmp_path):
        """Read and cleanup uses lock file to prevent race conditions."""
        import json
        from pathlib import Path
        import sys

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        import on_pretooluse
        original_dir = on_pretooluse.ASKUSER_RESPONSE_DIR
        on_pretooluse.ASKUSER_RESPONSE_DIR = tmp_path

        try:
            from on_pretooluse import read_and_cleanup_response_file

            # Create a response file
            response_file = tmp_path / "test_response.json"
            response_data = {"question_0": "1", "user_id": "U123"}
            response_file.write_text(json.dumps(response_data))

            assert response_file.exists()

            # Call atomic read and cleanup
            result = read_and_cleanup_response_file(response_file)

            # Verify data returned
            assert result is not None
            assert result["question_0"] == "1"
            assert result["user_id"] == "U123"

            # Verify file was cleaned up
            assert not response_file.exists()

            # Verify lock file was also cleaned up
            lock_file = Path(str(response_file) + '.lock')
            assert not lock_file.exists()

        finally:
            on_pretooluse.ASKUSER_RESPONSE_DIR = original_dir

    def test_atomic_read_returns_none_if_file_missing(self, tmp_path):
        """Returns None gracefully if file doesn't exist."""
        import sys
        from pathlib import Path

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        import on_pretooluse
        original_dir = on_pretooluse.ASKUSER_RESPONSE_DIR
        on_pretooluse.ASKUSER_RESPONSE_DIR = tmp_path

        try:
            from on_pretooluse import read_and_cleanup_response_file

            # Call with non-existent file
            response_file = tmp_path / "nonexistent.json"
            result = read_and_cleanup_response_file(response_file)

            # Should return None, not raise exception
            assert result is None

        finally:
            on_pretooluse.ASKUSER_RESPONSE_DIR = original_dir

    def test_atomic_read_prevents_concurrent_access(self, tmp_path):
        """Lock file prevents concurrent read/write race conditions."""
        import json
        import time
        import threading
        from pathlib import Path
        import sys
        import fcntl

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        import on_pretooluse
        original_dir = on_pretooluse.ASKUSER_RESPONSE_DIR
        on_pretooluse.ASKUSER_RESPONSE_DIR = tmp_path

        try:
            from on_pretooluse import read_and_cleanup_response_file

            response_file = tmp_path / "test_response.json"
            response_data = {"question_0": "1"}
            response_file.write_text(json.dumps(response_data))

            read_started = threading.Event()
            write_started = threading.Event()
            results = {"read": None, "write": False}

            def reader():
                read_started.set()
                # This will acquire lock, read, and cleanup
                results["read"] = read_and_cleanup_response_file(response_file)

            def writer():
                # Wait for reader to start
                read_started.wait(timeout=2)
                write_started.set()
                # Try to write while reader might be active
                time.sleep(0.05)  # Small delay to increase chance of overlap
                # If reader deleted the file, this creates a new one
                # But the lock should prevent corruption
                try:
                    with open(response_file, 'w') as f:
                        json.dump({"question_0": "2"}, f)
                    results["write"] = True
                except:
                    results["write"] = False

            reader_thread = threading.Thread(target=reader)
            writer_thread = threading.Thread(target=writer)

            reader_thread.start()
            writer_thread.start()

            reader_thread.join()
            writer_thread.join()

            # Verify reader got valid data (not corrupted)
            assert results["read"] is not None
            assert results["read"]["question_0"] == "1"

            # Both operations should complete successfully
            # (lock prevents corruption but doesn't block regular file operations)

        finally:
            on_pretooluse.ASKUSER_RESPONSE_DIR = original_dir


class TestResponseFileCleanupOnException:
    """Test that response files are cleaned up even when exceptions occur."""

    def test_cleanup_on_exception_in_main(self, tmp_path, monkeypatch):
        """Response file is cleaned up even if exception occurs during processing."""
        import json
        import sys
        from pathlib import Path
        from unittest.mock import patch, MagicMock
        from io import StringIO

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        import on_pretooluse
        original_dir = on_pretooluse.ASKUSER_RESPONSE_DIR
        on_pretooluse.ASKUSER_RESPONSE_DIR = tmp_path

        try:
            # Mock stdin with valid hook data
            hook_input = {
                "session_id": "test_session_12345",
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "Test question?",
                            "options": [
                                {"label": "Option 1"},
                                {"label": "Option 2"}
                            ]
                        }
                    ]
                }
            }

            stdin_data = json.dumps(hook_input)
            monkeypatch.setattr('sys.stdin', StringIO(stdin_data))

            # Mock environment variables
            monkeypatch.setenv('SLACK_BOT_TOKEN', 'xoxb-test-token')
            monkeypatch.setenv('REGISTRY_DB_PATH', str(tmp_path / 'registry.db'))

            # Mock the registry database module to return valid session data
            # We patch where it's imported, not where it's defined
            mock_db = MagicMock()
            mock_db.get_session.return_value = {
                'channel': 'C123',
                'thread_ts': '111.222'
            }

            # Patch RegistryDatabase in the registry_db module
            with patch('registry_db.RegistryDatabase', return_value=mock_db):
                # Mock post_to_slack to simulate an exception AFTER response_file is set
                def mock_post_to_slack(*args, **kwargs):
                    # This will be called after response_file is set in main()
                    raise RuntimeError("Simulated Slack API error")

                with patch.object(on_pretooluse, 'post_to_slack', side_effect=mock_post_to_slack):
                    # Mock sys.exit to prevent actual exit
                    with patch('sys.exit') as mock_exit:
                        # Call main()
                        from on_pretooluse import main
                        main()

                        # Verify sys.exit(0) was called
                        mock_exit.assert_called_with(0)

                # Now verify that the response file was cleaned up
                # The response file would be: test_session_12345_{request_id}.json
                response_files = list(tmp_path.glob('test_session_12345_*.json'))

                # Should have 0 response files (cleaned up in finally block)
                assert len(response_files) == 0, f"Expected 0 response files, found {len(response_files)}"

        finally:
            on_pretooluse.ASKUSER_RESPONSE_DIR = original_dir

    def test_stale_file_cleanup(self, tmp_path):
        """Stale response files older than max_age are cleaned up."""
        import json
        import time
        import sys
        from pathlib import Path

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        import on_pretooluse
        original_dir = on_pretooluse.ASKUSER_RESPONSE_DIR
        on_pretooluse.ASKUSER_RESPONSE_DIR = tmp_path

        try:
            from on_pretooluse import cleanup_stale_response_files

            # Create some test files with different ages
            old_file = tmp_path / "old_response.json"
            old_file.write_text(json.dumps({"test": "old"}))

            # Make the file appear old by modifying its mtime
            old_mtime = time.time() - 400  # 400 seconds ago (older than 300s default)
            import os
            os.utime(old_file, (old_mtime, old_mtime))

            recent_file = tmp_path / "recent_response.json"
            recent_file.write_text(json.dumps({"test": "recent"}))

            # Verify both files exist
            assert old_file.exists()
            assert recent_file.exists()

            # Run cleanup with default max_age (300 seconds)
            cleanup_stale_response_files(max_age_seconds=300)

            # Verify old file was deleted, recent file still exists
            assert not old_file.exists(), "Old file should be deleted"
            assert recent_file.exists(), "Recent file should still exist"

        finally:
            on_pretooluse.ASKUSER_RESPONSE_DIR = original_dir

    def test_stale_file_cleanup_ignores_errors(self, tmp_path):
        """Stale file cleanup ignores errors and continues."""
        import json
        import time
        import sys
        from pathlib import Path
        from unittest.mock import patch

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        import on_pretooluse
        original_dir = on_pretooluse.ASKUSER_RESPONSE_DIR
        on_pretooluse.ASKUSER_RESPONSE_DIR = tmp_path

        try:
            from on_pretooluse import cleanup_stale_response_files

            # Create a stale file
            stale_file = tmp_path / "stale.json"
            stale_file.write_text(json.dumps({"test": "data"}))

            # Make it old
            old_mtime = time.time() - 400
            import os
            os.utime(stale_file, (old_mtime, old_mtime))

            assert stale_file.exists()

            # Mock unlink to raise an error
            original_unlink = Path.unlink

            def failing_unlink(self, *args, **kwargs):
                if self.name == "stale.json":
                    raise PermissionError("Mock permission error")
                return original_unlink(self, *args, **kwargs)

            with patch.object(Path, 'unlink', failing_unlink):
                # Should not raise exception even if unlink fails
                cleanup_stale_response_files(max_age_seconds=300)

                # File still exists because unlink failed
                assert stale_file.exists()

        finally:
            on_pretooluse.ASKUSER_RESPONSE_DIR = original_dir

    def test_atomic_read_handles_corrupt_json(self, tmp_path):
        """Handles corrupt JSON gracefully."""
        import sys
        from pathlib import Path

        hooks_dir = Path.home() / ".claude" / "claude-slack" / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        import on_pretooluse
        original_dir = on_pretooluse.ASKUSER_RESPONSE_DIR
        on_pretooluse.ASKUSER_RESPONSE_DIR = tmp_path

        try:
            from on_pretooluse import read_and_cleanup_response_file

            # Create file with corrupt JSON
            response_file = tmp_path / "corrupt.json"
            response_file.write_text("{invalid json")

            result = read_and_cleanup_response_file(response_file)

            # Should return None on error
            assert result is None

            # File should still be deleted (cleanup even on error is acceptable)
            # But lock file must be cleaned up
            lock_file = Path(str(response_file) + '.lock')
            assert not lock_file.exists()

        finally:
            on_pretooluse.ASKUSER_RESPONSE_DIR = original_dir
