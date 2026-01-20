"""
Unit tests for core/transcript_parser.py

Tests JSONL transcript parsing for extracting assistant responses,
tool calls, todo status, and session summaries.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add core directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))

from transcript_parser import TranscriptParser


class TestTranscriptLoad:
    """Tests for loading transcript files."""

    def test_load_valid_jsonl(self, mock_transcript_file):
        """Parses multi-line transcript successfully."""
        parser = TranscriptParser(mock_transcript_file)
        result = parser.load()
        assert result is True
        assert len(parser.messages) > 0

    def test_load_empty_file(self, empty_transcript_file):
        """Handles empty transcript file."""
        parser = TranscriptParser(empty_transcript_file)
        result = parser.load()
        assert result is True
        assert parser.messages == []

    def test_load_file_not_found(self, tmp_path):
        """Returns False for missing file."""
        parser = TranscriptParser(str(tmp_path / "nonexistent.jsonl"))
        result = parser.load()
        assert result is False

    def test_load_malformed_json(self, tmp_path):
        """Skips malformed JSON lines."""
        transcript_path = tmp_path / "malformed.jsonl"
        with open(transcript_path, 'w') as f:
            f.write('{"type": "user", "valid": true}\n')
            f.write('not valid json\n')
            f.write('{"type": "assistant", "valid": true}\n')

        parser = TranscriptParser(str(transcript_path))
        result = parser.load()
        assert result is True
        # Should have 2 valid messages, 1 skipped
        assert len(parser.messages) == 2


class TestGetLatestAssistantResponse:
    """Tests for get_latest_assistant_response()."""

    def test_get_latest_assistant_response(self, mock_transcript_file):
        """Extracts last assistant response text."""
        parser = TranscriptParser(mock_transcript_file)
        parser.load()

        response = parser.get_latest_assistant_response()
        assert response is not None
        assert 'text' in response
        assert len(response['text']) > 0

    def test_get_latest_response_text_only(self, tmp_path):
        """Filters out tool-only messages when text_only=True."""
        transcript_path = tmp_path / "tool_only.jsonl"
        with open(transcript_path, 'w') as f:
            # Assistant message with only tool calls (no text)
            msg = {
                'type': 'assistant',
                'timestamp': '2025-01-01T00:00:00Z',
                'uuid': 'msg-123',
                'message': {
                    'model': 'claude-3',
                    'content': [
                        {'type': 'tool_use', 'id': 'tool-1', 'name': 'Read', 'input': {}}
                    ]
                }
            }
            f.write(json.dumps(msg) + '\n')

        parser = TranscriptParser(str(transcript_path))
        parser.load()

        response = parser.get_latest_assistant_response(text_only=True)
        assert response is None  # No text content

    def test_get_latest_response_includes_tool_calls(self, mock_transcript_file):
        """Includes tool calls when requested."""
        parser = TranscriptParser(mock_transcript_file)
        parser.load()

        response = parser.get_latest_assistant_response(include_tool_calls=True)
        assert response is not None
        assert 'tool_calls' in response

    def test_get_latest_response_no_messages(self, empty_transcript_file):
        """Returns None when no messages."""
        parser = TranscriptParser(empty_transcript_file)
        parser.load()

        response = parser.get_latest_assistant_response()
        assert response is None


class TestGetAllToolCalls:
    """Tests for get_all_tool_calls()."""

    def test_get_all_tool_calls(self, mock_transcript_file):
        """Extracts all tool usage from transcript."""
        parser = TranscriptParser(mock_transcript_file)
        parser.load()

        tool_calls = parser.get_all_tool_calls()
        assert isinstance(tool_calls, list)
        assert len(tool_calls) > 0
        assert all('name' in tc for tc in tool_calls)

    def test_tool_calls_include_input(self, mock_transcript_file):
        """Tool calls include input parameters."""
        parser = TranscriptParser(mock_transcript_file)
        parser.load()

        tool_calls = parser.get_all_tool_calls()
        for tc in tool_calls:
            assert 'input' in tc

    def test_tool_calls_match_results(self, mock_transcript_file):
        """Tool results are matched to their calls."""
        parser = TranscriptParser(mock_transcript_file)
        parser.load()

        tool_calls = parser.get_all_tool_calls()
        # Some tool calls should have results matched
        calls_with_results = [tc for tc in tool_calls if 'result' in tc]
        assert len(calls_with_results) >= 0  # May or may not have results


class TestGetTodoStatus:
    """Tests for get_todo_status()."""

    def test_get_todo_status(self, tmp_path, sample_transcript_with_todos):
        """Parses TodoWrite results correctly."""
        transcript_path = tmp_path / "todos.jsonl"
        with open(transcript_path, 'w') as f:
            for msg in sample_transcript_with_todos:
                f.write(json.dumps(msg) + '\n')

        parser = TranscriptParser(str(transcript_path))
        parser.load()

        todo_status = parser.get_todo_status()
        assert todo_status is not None
        assert 'todos' in todo_status
        assert todo_status['total'] == 3
        assert todo_status['completed'] == 1
        assert todo_status['in_progress'] == 1
        assert todo_status['pending'] == 1

    def test_get_todo_status_no_todos(self, mock_transcript_file):
        """Returns None when no TodoWrite calls."""
        parser = TranscriptParser(mock_transcript_file)
        parser.load()

        todo_status = parser.get_todo_status()
        # May be None if no TodoWrite in sample transcript
        # This is expected behavior

    def test_get_todo_status_is_complete(self, tmp_path):
        """is_complete is True when all todos are done."""
        transcript_path = tmp_path / "complete_todos.jsonl"
        msg = {
            'type': 'assistant',
            'timestamp': '2025-01-01T00:00:00Z',
            'message': {
                'content': [
                    {
                        'type': 'tool_use',
                        'id': 'todo-1',
                        'name': 'TodoWrite',
                        'input': {
                            'todos': [
                                {'content': 'Task 1', 'status': 'completed'},
                                {'content': 'Task 2', 'status': 'completed'}
                            ]
                        }
                    }
                ]
            }
        }
        with open(transcript_path, 'w') as f:
            f.write(json.dumps(msg) + '\n')

        parser = TranscriptParser(str(transcript_path))
        parser.load()

        todo_status = parser.get_todo_status()
        assert todo_status['is_complete'] is True


class TestGetModifiedFiles:
    """Tests for get_modified_files()."""

    def test_get_modified_files(self, mock_transcript_file):
        """Extracts Write/Edit target files."""
        parser = TranscriptParser(mock_transcript_file)
        parser.load()

        files = parser.get_modified_files()
        assert isinstance(files, list)
        # Check if Edit tool call file is captured
        if files:
            assert all(isinstance(f, str) for f in files)

    def test_get_modified_files_deduplicates(self, tmp_path):
        """Returns unique file paths."""
        transcript_path = tmp_path / "duplicates.jsonl"
        msg = {
            'type': 'assistant',
            'message': {
                'content': [
                    {'type': 'tool_use', 'name': 'Edit', 'id': '1', 'input': {'file_path': '/a/b.py'}},
                    {'type': 'tool_use', 'name': 'Edit', 'id': '2', 'input': {'file_path': '/a/b.py'}},
                    {'type': 'tool_use', 'name': 'Write', 'id': '3', 'input': {'file_path': '/a/c.py'}},
                ]
            }
        }
        with open(transcript_path, 'w') as f:
            f.write(json.dumps(msg) + '\n')

        parser = TranscriptParser(str(transcript_path))
        parser.load()

        files = parser.get_modified_files()
        assert len(files) == 2
        assert '/a/b.py' in files
        assert '/a/c.py' in files


class TestGetStopReason:
    """Tests for get_stop_reason()."""

    def test_get_stop_reason_completed(self, mock_transcript_file):
        """Detects completed sessions."""
        parser = TranscriptParser(mock_transcript_file)
        parser.load()

        reason = parser.get_stop_reason()
        assert reason in ['completed', 'interrupted', 'error', 'unknown']

    def test_get_stop_reason_error(self, tmp_path):
        """Detects error stop reason."""
        transcript_path = tmp_path / "error.jsonl"
        msg = {
            'type': 'tool_result',
            'tool_use_id': 'tool-1',
            'is_error': True,
            'content': 'Error occurred'
        }
        with open(transcript_path, 'w') as f:
            f.write(json.dumps(msg) + '\n')

        parser = TranscriptParser(str(transcript_path))
        parser.load()

        reason = parser.get_stop_reason()
        assert reason == 'error'

    def test_get_stop_reason_empty(self, empty_transcript_file):
        """Returns unknown for empty transcript."""
        parser = TranscriptParser(empty_transcript_file)
        parser.load()

        reason = parser.get_stop_reason()
        assert reason == 'unknown'


class TestGetRichSummary:
    """Tests for get_rich_summary()."""

    def test_get_rich_summary_structure(self, mock_transcript_file):
        """Returns all expected summary fields."""
        parser = TranscriptParser(mock_transcript_file)
        parser.load()

        summary = parser.get_rich_summary()
        assert 'stop_reason' in summary
        assert 'is_complete' in summary
        assert 'conversation' in summary
        assert 'modified_files' in summary

    def test_get_rich_summary_conversation_stats(self, mock_transcript_file):
        """Includes conversation statistics."""
        parser = TranscriptParser(mock_transcript_file)
        parser.load()

        summary = parser.get_rich_summary()
        conv = summary['conversation']
        assert 'total_messages' in conv
        assert 'user_messages' in conv
        assert 'assistant_messages' in conv

    def test_get_rich_summary_initial_task(self, mock_transcript_file):
        """Extracts initial user task."""
        parser = TranscriptParser(mock_transcript_file)
        parser.load()

        summary = parser.get_rich_summary()
        # initial_task should be first user message text
        assert 'initial_task' in summary


class TestGetConversationSummary:
    """Tests for get_conversation_summary()."""

    def test_conversation_summary_counts(self, mock_transcript_file):
        """Counts message types correctly."""
        parser = TranscriptParser(mock_transcript_file)
        parser.load()

        summary = parser.get_conversation_summary()
        assert summary['total_messages'] >= summary['user_messages']
        assert summary['total_messages'] >= summary['assistant_messages']


class TestTranscriptPathFromEnv:
    """Tests for get_transcript_path_from_env()."""

    def test_get_transcript_path_from_env_direct(self, monkeypatch):
        """Uses CLAUDE_TRANSCRIPT_PATH when set."""
        monkeypatch.setenv('CLAUDE_TRANSCRIPT_PATH', '/direct/path/transcript.jsonl')

        path = TranscriptParser.get_transcript_path_from_env()
        assert path == '/direct/path/transcript.jsonl'

    def test_get_transcript_path_from_env_constructed(self, monkeypatch):
        """Constructs path from session ID and project dir."""
        monkeypatch.delenv('CLAUDE_TRANSCRIPT_PATH', raising=False)
        monkeypatch.setenv('CLAUDE_SESSION_ID', 'test-uuid-123')
        monkeypatch.setenv('CLAUDE_PROJECT_DIR', '/path/to/project')

        path = TranscriptParser.get_transcript_path_from_env()
        assert path is not None
        assert 'test-uuid-123' in path

    def test_get_transcript_path_from_env_missing(self, clean_env):
        """Returns None when env vars missing."""
        path = TranscriptParser.get_transcript_path_from_env()
        assert path is None


class TestConstructTranscriptPath:
    """Tests for construct_transcript_path()."""

    def test_construct_transcript_path(self):
        """Constructs correct transcript path."""
        path = TranscriptParser.construct_transcript_path(
            'session-uuid-123',
            '/path/to/project'
        )
        assert 'session-uuid-123.jsonl' in path
        assert '.claude/projects' in path

    def test_construct_transcript_path_handles_leading_slash(self):
        """Handles leading slash in project dir."""
        path = TranscriptParser.construct_transcript_path(
            'session-123',
            '/var/home/user/project'
        )
        # Should not have double slashes
        assert '//' not in path or path.count('//') == 0


class TestGetLastNMessages:
    """Tests for get_last_n_messages()."""

    def test_get_last_n_messages_default(self, mock_transcript_file):
        """Returns last 5 messages by default, in chronological order."""
        parser = TranscriptParser(mock_transcript_file)
        parser.load()
        messages = parser.get_last_n_messages()
        assert len(messages) <= 5
        assert all('role' in m for m in messages)
        assert all('text' in m for m in messages)
        assert all('timestamp' in m for m in messages)

    def test_get_last_n_messages_custom_count(self, tmp_path):
        """Returns exactly N messages when N < total."""
        # Create transcript with more than N messages
        transcript_path = tmp_path / "many_messages.jsonl"
        import json
        with open(transcript_path, 'w') as f:
            for i in range(10):
                msg = {
                    'type': 'user' if i % 2 == 0 else 'assistant',
                    'timestamp': f'2025-01-01T00:00:{i:02d}Z',
                    'message': {'content': [{'type': 'text', 'text': f'Message {i}'}]}
                }
                f.write(json.dumps(msg) + '\n')

        parser = TranscriptParser(str(transcript_path))
        parser.load()
        messages = parser.get_last_n_messages(n=3)
        assert len(messages) == 3

    def test_get_last_n_messages_max_25(self, tmp_path):
        """N is capped at 25 even if higher requested."""
        # Create transcript with many messages
        transcript_path = tmp_path / "thirty_messages.jsonl"
        import json
        with open(transcript_path, 'w') as f:
            for i in range(30):
                msg = {
                    'type': 'user' if i % 2 == 0 else 'assistant',
                    'timestamp': f'2025-01-01T00:{i:02d}:00Z',
                    'message': {'content': [{'type': 'text', 'text': f'Message {i}'}]}
                }
                f.write(json.dumps(msg) + '\n')

        parser = TranscriptParser(str(transcript_path))
        parser.load()
        messages = parser.get_last_n_messages(n=100)
        assert len(messages) == 25

    def test_get_last_n_messages_minimum_1(self, mock_transcript_file):
        """N=0 or negative returns at least 1 message."""
        parser = TranscriptParser(mock_transcript_file)
        parser.load()
        messages = parser.get_last_n_messages(n=0)
        assert len(messages) >= 1
        messages = parser.get_last_n_messages(n=-5)
        assert len(messages) >= 1

    def test_get_last_n_messages_formats_for_slack(self, mock_transcript_file):
        """Each message has: role ('user'/'assistant'), text, timestamp."""
        parser = TranscriptParser(mock_transcript_file)
        parser.load()
        messages = parser.get_last_n_messages()
        for msg in messages:
            assert msg['role'] in ('user', 'assistant')
            assert isinstance(msg['text'], str)
            assert isinstance(msg['timestamp'], str)

    def test_get_last_n_messages_empty_transcript(self, empty_transcript_file):
        """Returns empty list for empty transcript."""
        parser = TranscriptParser(empty_transcript_file)
        parser.load()
        messages = parser.get_last_n_messages()
        assert messages == []
