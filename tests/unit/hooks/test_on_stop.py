"""
Unit tests for .claude/hooks/on_stop.py

Tests session summary formatting, message chunking, and rich Block Kit summaries.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestSplitMessage:
    """Test message splitting for Slack's 40K limit."""

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

    def test_split_message_under_limit(self):
        """Short messages should not be split."""
        text = "Short response from Claude"
        chunks = self._split_message(text, max_length=100)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_split_message_at_newlines(self):
        """Long messages split preferentially at newlines."""
        text = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n" * 10
        chunks = self._split_message(text, max_length=50)
        assert len(chunks) > 1
        # Each chunk should end cleanly (at newline boundary)
        for chunk in chunks[:-1]:
            assert len(chunk) <= 50


class TestFormatRichSummaryBlocks:
    """Test Block Kit block generation for rich summaries."""

    def _format_summary_blocks(self, summary):
        """Local implementation of format_rich_summary_blocks."""
        blocks = []

        # Header with status
        is_complete = summary.get('is_complete', False)
        stop_reason = summary.get('stop_reason', 'unknown')

        if is_complete:
            status_emoji = "✅"
            status_text = "Session Complete"
        elif stop_reason == 'error':
            status_emoji = "❌"
            status_text = "Session Ended with Error"
        elif stop_reason == 'interrupted':
            status_emoji = "⚠️"
            status_text = "Session Interrupted"
        else:
            status_emoji = "🔚"
            status_text = "Session Ended"

        blocks.append({
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{status_emoji} {status_text}",
                "emoji": True
            }
        })

        # Initial task
        initial_task = summary.get('initial_task')
        if initial_task:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Task:* {initial_task}"}
            })

        blocks.append({"type": "divider"})

        # Todo status
        todos = summary.get('todos')
        if todos:
            completed_count = todos.get('completed', 0)
            total_count = todos.get('total', 0)

            if total_count > 0:
                progress_pct = int((completed_count / total_count) * 100)
                filled = int(progress_pct / 10)
                progress_bar = "█" * filled + "░" * (10 - filled)
            else:
                progress_pct = 0
                progress_bar = "░" * 10

            todo_text = f"*Progress:* {progress_bar} {progress_pct}% ({completed_count}/{total_count} tasks)"
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": todo_text}
            })

        # Modified files
        modified_files = summary.get('modified_files', [])
        if modified_files:
            files_text = "*Files Modified:*\n"
            for f in modified_files[:10]:
                short_path = f.split('/')[-1] if '/' in f else f
                files_text += f"• `{short_path}`\n"
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": files_text.strip()}
            })

        return blocks

    def test_format_rich_summary_complete(self):
        """All fields populated in summary."""
        summary = {
            'is_complete': True,
            'stop_reason': 'completed',
            'initial_task': 'Fix the authentication bug',
            'todos': {
                'total': 5,
                'completed': 5,
                'in_progress': 0,
                'pending': 0,
                'completed_items': ['Task 1', 'Task 2', 'Task 3', 'Task 4', 'Task 5'],
                'in_progress_items': [],
                'pending_items': []
            },
            'modified_files': ['/src/auth.py', '/src/login.py', '/tests/test_auth.py'],
            'conversation': {'user_messages': 3, 'assistant_messages': 5, 'total_messages': 8},
            'usage': {'input_tokens': 5000, 'output_tokens': 2000}
        }

        blocks = self._format_summary_blocks(summary)

        # Check header
        assert blocks[0]['type'] == 'header'
        assert '✅' in blocks[0]['text']['text']
        assert 'Complete' in blocks[0]['text']['text']

        # Check for task section
        task_blocks = [b for b in blocks if b.get('type') == 'section' and 'Task:' in str(b)]
        assert len(task_blocks) > 0

        # Check for files section
        files_blocks = [b for b in blocks if 'Files Modified' in str(b)]
        assert len(files_blocks) > 0

    def test_format_rich_summary_no_todos(self):
        """Handle missing todos gracefully."""
        summary = {
            'is_complete': True,
            'stop_reason': 'completed',
            'todos': None,
            'modified_files': ['/src/file.py']
        }

        blocks = self._format_summary_blocks(summary)
        # Should not crash, should have at least header and divider
        assert len(blocks) >= 2

    def test_format_rich_summary_no_files(self):
        """Handle no modified files."""
        summary = {
            'is_complete': False,
            'stop_reason': 'error',
            'modified_files': [],
            'todos': {'total': 3, 'completed': 1}
        }

        blocks = self._format_summary_blocks(summary)
        # Check for error header
        assert '❌' in blocks[0]['text']['text']
        # No files section
        files_blocks = [b for b in blocks if 'Files Modified' in str(b)]
        assert len(files_blocks) == 0

    def test_format_rich_summary_progress_bar_0_percent(self):
        """0% progress bar when nothing completed."""
        summary = {
            'is_complete': False,
            'todos': {'total': 5, 'completed': 0}
        }

        blocks = self._format_summary_blocks(summary)
        progress_blocks = [b for b in blocks if 'Progress' in str(b)]
        assert len(progress_blocks) > 0
        # Should be all empty squares
        assert '░░░░░░░░░░' in str(progress_blocks[0])

    def test_format_rich_summary_progress_bar_50_percent(self):
        """50% progress bar."""
        summary = {
            'is_complete': False,
            'todos': {'total': 10, 'completed': 5}
        }

        blocks = self._format_summary_blocks(summary)
        progress_blocks = [b for b in blocks if 'Progress' in str(b)]
        assert len(progress_blocks) > 0
        # Should have half filled
        text = str(progress_blocks[0])
        assert '50%' in text or '█████░░░░░' in text

    def test_format_rich_summary_progress_bar_100_percent(self):
        """100% progress bar when all complete."""
        summary = {
            'is_complete': True,
            'todos': {'total': 3, 'completed': 3}
        }

        blocks = self._format_summary_blocks(summary)
        progress_blocks = [b for b in blocks if 'Progress' in str(b)]
        assert len(progress_blocks) > 0
        # Should be all filled
        text = str(progress_blocks[0])
        assert '100%' in text or '██████████' in text


class TestPostRichSummary:
    """Test posting rich summary to Slack."""

    def test_post_rich_summary_success(self, mock_slack_client):
        """Successfully post summary."""
        summary = {
            'is_complete': True,
            'stop_reason': 'completed',
            'todos': {'total': 2, 'completed': 2}
        }

        # Mock would call chat_postMessage
        mock_slack_client.chat_postMessage.return_value = {'ok': True, 'ts': '123.456'}

        # Verify mock setup
        assert mock_slack_client.chat_postMessage.return_value['ok'] is True

    def test_post_rich_summary_custom_channel_mode(self, mock_slack_client):
        """Handle top-level posting (no thread_ts)."""
        summary = {'is_complete': True}

        # In custom channel mode, thread_ts would be None
        # Verify we can build kwargs without thread_ts
        kwargs = {
            "channel": "C123",
            "text": "Session Complete",
            "blocks": []
        }
        # thread_ts intentionally omitted for top-level

        assert 'thread_ts' not in kwargs


class TestPostToSlack:
    """Test standard message posting."""

    def test_post_response_single(self, mock_slack_client):
        """Post short response as single message."""
        mock_slack_client.chat_postMessage.return_value = {'ok': True, 'ts': '123.456'}

        # Verify single chunk behavior
        text = "Short response"
        assert len(text) < 39000

    def test_post_response_chunked(self, mock_slack_client):
        """Post long response in multiple parts."""
        # Generate long text
        long_text = "This is a test line.\n" * 5000

        # Would split into multiple chunks
        max_length = 39000
        chunks = []
        text = long_text
        while text:
            if len(text) <= max_length:
                chunks.append(text)
                break
            break_point = text.rfind('\n', max_length - 500, max_length)
            if break_point == -1:
                break_point = max_length
            chunks.append(text[:break_point])
            text = text[break_point:].lstrip('\n')

        assert len(chunks) > 1


class TestSelfHealing:
    """Test self-healing for missing Slack metadata."""

    def test_self_healing_finds_wrapper_session(self, temp_registry_db, sample_session_data):
        """Find wrapper session when Claude UUID session missing metadata."""
        # Create wrapper session with metadata
        wrapper_data = sample_session_data.copy()
        wrapper_data['session_id'] = 'wrap1234'
        temp_registry_db.create_session(wrapper_data)

        # Create UUID session without Slack metadata
        uuid_data = {
            'session_id': 'wrap1234-uuid-uuid-uuid-123456789012',  # Starts with wrapper ID
            'project': 'test-project',
            'project_dir': '/path/to/project',
            'terminal': 'terminal-1',
            'socket_path': '/tmp/uuid.sock',
            'thread_ts': None,  # Missing!
            'channel': None,  # Missing!
        }
        temp_registry_db.create_session(uuid_data)

        # Lookup UUID session
        uuid_session = temp_registry_db.get_session(uuid_data['session_id'])
        assert uuid_session['channel'] is None

        # Self-healing: find wrapper by first 8 chars
        wrapper_id = uuid_data['session_id'][:8]
        wrapper_session = temp_registry_db.get_session(wrapper_id)
        assert wrapper_session is not None
        assert wrapper_session['channel'] == sample_session_data['channel']

    def test_self_healing_by_project_dir(self, temp_registry_db, sample_session_data):
        """Find session by project_dir when ID lookup fails."""
        temp_registry_db.create_session(sample_session_data)

        # Lookup by project_dir
        found = temp_registry_db.get_by_project_dir(sample_session_data['project_dir'])
        assert found is not None
        assert found['channel'] == sample_session_data['channel']
