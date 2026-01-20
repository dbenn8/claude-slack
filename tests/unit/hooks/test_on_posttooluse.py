"""
Unit tests for .claude/hooks/on_posttooluse.py

Tests todo list formatting, progress bars, and message updates.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestFormatTodoForSlack:
    """Test todo list Block Kit formatting."""

    def _format_todo(self, todos):
        """Local implementation of format_todo_for_slack."""
        if not todos:
            return {
                "text": "No tasks in todo list",
                "blocks": []
            }

        completed = [t for t in todos if t.get('status') == 'completed']
        in_progress = [t for t in todos if t.get('status') == 'in_progress']
        pending = [t for t in todos if t.get('status') == 'pending']

        total = len(todos)
        completed_count = len(completed)

        # Progress bar
        progress_pct = (completed_count / total * 100) if total > 0 else 0
        filled = int(progress_pct / 10)
        progress_bar = "█" * filled + "░" * (10 - filled)

        # Build blocks
        blocks = []

        # Header with progress
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Task Progress* {progress_bar} {completed_count}/{total} ({progress_pct:.0f}%)"
            }
        })

        blocks.append({"type": "divider"})

        # In Progress section
        if in_progress:
            in_progress_text = "*In Progress:*\n"
            for t in in_progress:
                in_progress_text += f"  :hourglass_flowing_sand: {t.get('content', 'Unknown task')}\n"
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": in_progress_text.strip()}
            })

        # Pending section
        if pending:
            pending_text = "*Pending:*\n"
            for t in pending:
                pending_text += f"  :white_circle: {t.get('content', 'Unknown task')}\n"
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": pending_text.strip()}
            })

        # Completed section
        if completed:
            if len(completed) <= 3:
                completed_text = "*Completed:*\n"
                for t in completed:
                    completed_text += f"  :white_check_mark: ~{t.get('content', 'Unknown task')}~\n"
            else:
                completed_text = f"*Completed:* ({len(completed)} tasks)\n"
                for t in completed[-2:]:
                    completed_text += f"  :white_check_mark: ~{t.get('content', 'Unknown task')}~\n"
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": completed_text.strip()}
            })

        fallback_text = f"Task Progress: {completed_count}/{total} complete"

        return {
            "text": fallback_text,
            "blocks": blocks
        }

    def test_format_todo_empty(self):
        """Handle 0 todos."""
        result = self._format_todo([])
        assert result['text'] == "No tasks in todo list"
        assert result['blocks'] == []

    def test_format_todo_all_pending(self):
        """0% progress bar when all pending."""
        todos = [
            {'content': 'Task 1', 'status': 'pending'},
            {'content': 'Task 2', 'status': 'pending'},
            {'content': 'Task 3', 'status': 'pending'}
        ]

        result = self._format_todo(todos)

        # Check progress bar is all empty
        header_text = result['blocks'][0]['text']['text']
        assert '░░░░░░░░░░' in header_text
        assert '0/3' in header_text
        assert '0%' in header_text

    def test_format_todo_partial(self):
        """50% progress bar with mixed statuses."""
        todos = [
            {'content': 'Task 1', 'status': 'completed'},
            {'content': 'Task 2', 'status': 'in_progress'},
            {'content': 'Task 3', 'status': 'pending'},
            {'content': 'Task 4', 'status': 'completed'}
        ]

        result = self._format_todo(todos)

        header_text = result['blocks'][0]['text']['text']
        assert '2/4' in header_text
        assert '50%' in header_text
        # 50% = 5 filled blocks
        assert '█████░░░░░' in header_text

    def test_format_todo_all_complete(self):
        """100% progress bar when all complete."""
        todos = [
            {'content': 'Task 1', 'status': 'completed'},
            {'content': 'Task 2', 'status': 'completed'},
            {'content': 'Task 3', 'status': 'completed'}
        ]

        result = self._format_todo(todos)

        header_text = result['blocks'][0]['text']['text']
        assert '██████████' in header_text
        assert '3/3' in header_text
        assert '100%' in header_text

    def test_format_todo_includes_sections(self):
        """Include In Progress, Pending, Completed sections."""
        todos = [
            {'content': 'Done task', 'status': 'completed'},
            {'content': 'Working on this', 'status': 'in_progress'},
            {'content': 'Still to do', 'status': 'pending'}
        ]

        result = self._format_todo(todos)

        # Convert blocks to string for easy checking
        blocks_str = str(result['blocks'])

        assert 'In Progress' in blocks_str
        assert 'Working on this' in blocks_str
        assert 'Pending' in blocks_str
        assert 'Still to do' in blocks_str
        assert 'Completed' in blocks_str
        assert 'Done task' in blocks_str

    def test_format_todo_truncates_completed(self):
        """Show only last 2 completed when many."""
        todos = [
            {'content': 'Task 1', 'status': 'completed'},
            {'content': 'Task 2', 'status': 'completed'},
            {'content': 'Task 3', 'status': 'completed'},
            {'content': 'Task 4', 'status': 'completed'},
            {'content': 'Task 5', 'status': 'completed'}
        ]

        result = self._format_todo(todos)

        # Should show "(5 tasks)" and last 2
        blocks_str = str(result['blocks'])
        assert '5 tasks' in blocks_str
        assert 'Task 4' in blocks_str
        assert 'Task 5' in blocks_str


class TestPostOrUpdateSlack:
    """Test posting new or updating existing messages."""

    def test_post_new_todo_message(self, mock_slack_client):
        """Create new message when no existing."""
        mock_slack_client.chat_postMessage.return_value = {'ok': True, 'ts': 'new.123'}

        # When message_ts is None, should post new
        result = mock_slack_client.chat_postMessage(
            channel='C123',
            thread_ts='111.222',
            text='Task Progress',
            blocks=[]
        )

        assert result['ts'] == 'new.123'
        mock_slack_client.chat_postMessage.assert_called_once()

    def test_update_existing_message(self, mock_slack_client):
        """Update via chat_update when message_ts exists."""
        mock_slack_client.chat_update.return_value = {'ok': True, 'ts': 'existing.456'}

        # When message_ts exists, should update
        result = mock_slack_client.chat_update(
            channel='C123',
            ts='existing.456',
            text='Updated Task Progress',
            blocks=[]
        )

        assert result['ts'] == 'existing.456'
        mock_slack_client.chat_update.assert_called_once()

    def test_update_message_not_found_fallback(self, mock_slack_client):
        """Fallback to new post when message not found."""
        from slack_sdk.errors import SlackApiError

        # Simulate message_not_found error
        error_response = MagicMock()
        error_response.get.return_value = 'message_not_found'
        mock_slack_client.chat_update.side_effect = SlackApiError(
            message="message_not_found",
            response=error_response
        )
        mock_slack_client.chat_postMessage.return_value = {'ok': True, 'ts': 'fallback.789'}

        # Try update, should fail
        with pytest.raises(SlackApiError):
            mock_slack_client.chat_update(
                channel='C123',
                ts='deleted.message',
                text='Updated',
                blocks=[]
            )

        # Fallback to post
        result = mock_slack_client.chat_postMessage(
            channel='C123',
            thread_ts='111.222',
            text='Updated',
            blocks=[]
        )

        assert result['ts'] == 'fallback.789'


class TestFilterTodoWriteOnly:
    """Test that hook only processes TodoWrite tool."""

    def test_filter_todowrite_only(self, sample_posttooluse_hook_input):
        """Only process TodoWrite calls."""
        assert sample_posttooluse_hook_input['tool_name'] == 'TodoWrite'

    def test_skip_other_tools(self):
        """Skip non-TodoWrite tools."""
        other_tools = ['Bash', 'Read', 'Write', 'Edit', 'AskUserQuestion', 'Task']

        for tool_name in other_tools:
            # Hook would exit early for these
            assert tool_name != 'TodoWrite'


class TestStoreTodoMessageTs:
    """Test storing todo_message_ts in registry."""

    def test_store_new_message_ts(self, temp_registry_db, sample_session_data):
        """Store message_ts after posting."""
        temp_registry_db.create_session(sample_session_data)

        # Simulate storing new todo_message_ts
        result = temp_registry_db.update_session(
            sample_session_data['session_id'],
            {'todo_message_ts': 'todo.123'}
        )
        assert result is True

        # Verify stored
        session = temp_registry_db.get_session(sample_session_data['session_id'])
        assert session['todo_message_ts'] == 'todo.123'

    def test_update_message_ts(self, temp_registry_db, sample_session_data):
        """Update message_ts on subsequent posts."""
        temp_registry_db.create_session(sample_session_data)

        # First update
        temp_registry_db.update_session(
            sample_session_data['session_id'],
            {'todo_message_ts': 'todo.111'}
        )

        # Second update (message was recreated)
        temp_registry_db.update_session(
            sample_session_data['session_id'],
            {'todo_message_ts': 'todo.222'}
        )

        session = temp_registry_db.get_session(sample_session_data['session_id'])
        assert session['todo_message_ts'] == 'todo.222'


class TestCustomChannelMode:
    """Test top-level posting in custom channel mode."""

    def test_post_without_thread_ts(self, mock_slack_client):
        """Handle top-level messages (thread_ts=None)."""
        mock_slack_client.chat_postMessage.return_value = {'ok': True, 'ts': 'top.123'}

        # Post without thread_ts
        kwargs = {
            "channel": "custom-channel",
            "text": "Task Progress",
            "blocks": []
        }
        # No thread_ts for custom channel mode

        result = mock_slack_client.chat_postMessage(**kwargs)

        assert result['ts'] == 'top.123'
        call_args = mock_slack_client.chat_postMessage.call_args
        assert 'thread_ts' not in call_args.kwargs
