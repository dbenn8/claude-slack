#!/usr/bin/env python3
"""
Claude Code Transcript Parser

Parses Claude Code conversation transcripts (JSONL format) to extract
assistant responses for Slack integration.

Usage:
    # From environment (in a Stop hook)
    python3 transcript_parser.py

    # Manual usage
    python3 transcript_parser.py /path/to/transcript.jsonl
"""

import json
import os
import sys
from typing import Optional, Dict, List, Any
from datetime import datetime


class TranscriptParser:
    """Parse Claude Code JSONL transcripts."""

    def __init__(self, transcript_path: str):
        """
        Initialize parser with transcript file path.

        Args:
            transcript_path: Path to the .jsonl transcript file
        """
        self.transcript_path = transcript_path
        self.messages: List[Dict[str, Any]] = []

    @staticmethod
    def _get_message_content(msg: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Safely extract content list from a message.

        Handles cases where 'message' might be a string instead of dict.

        Args:
            msg: Message dict from transcript

        Returns:
            List of content blocks, or empty list if not available
        """
        message_data = msg.get('message', {})
        if isinstance(message_data, dict):
            content = message_data.get('content', [])
            if isinstance(content, list):
                return content
        return []

    @staticmethod
    def get_transcript_path_from_env() -> Optional[str]:
        """
        Get transcript path from environment variables (set by Claude Code hooks).

        Returns:
            Transcript path if available, None otherwise
        """
        # Claude Code provides this directly
        if 'CLAUDE_TRANSCRIPT_PATH' in os.environ:
            return os.environ['CLAUDE_TRANSCRIPT_PATH']

        # Fallback: construct from session ID and project dir
        session_id = os.environ.get('CLAUDE_SESSION_ID')
        project_dir = os.environ.get('CLAUDE_PROJECT_DIR')

        if session_id and project_dir:
            return TranscriptParser.construct_transcript_path(session_id, project_dir)

        return None

    @staticmethod
    def construct_transcript_path(session_id: str, project_dir: str) -> str:
        """
        Construct transcript path from session ID and project directory.

        Args:
            session_id: Claude session UUID
            project_dir: Absolute path to project directory

        Returns:
            Full path to transcript file
        """
        # Convert project path to slug
        project_slug = project_dir.replace("/", "-")
        if project_slug.startswith("-"):
            project_slug = project_slug[1:]

        # Construct path
        return os.path.join(
            os.path.expanduser("~"),
            ".claude",
            "projects",
            f"-{project_slug}",
            f"{session_id}.jsonl"
        )

    def load(self) -> bool:
        """
        Load and parse the transcript file.

        Returns:
            True if successful, False if file doesn't exist
        """
        if not os.path.exists(self.transcript_path):
            return False

        self.messages = []
        with open(self.transcript_path, 'r') as f:
            for line in f:
                try:
                    self.messages.append(json.loads(line))
                except json.JSONDecodeError:
                    # Skip malformed lines
                    continue

        return True

    def get_assistant_messages(self) -> List[Dict[str, Any]]:
        """
        Get all assistant messages from the transcript.

        Returns:
            List of assistant message objects
        """
        return [
            msg for msg in self.messages
            if msg.get('type') == 'assistant'
        ]

    def get_latest_assistant_response(
        self,
        include_tool_calls: bool = False,
        text_only: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Extract the latest assistant response from transcript.

        Args:
            include_tool_calls: Include tool_calls in result
            text_only: Only return if there's actual text content (skip tool-only messages)

        Returns:
            Dict with:
                - text: Combined text content from all text blocks
                - tool_calls: List of tool calls (if include_tool_calls=True)
                - timestamp: ISO timestamp
                - uuid: Message UUID
                - model: Model name
                - usage: Token usage stats
            None if no assistant messages found
        """
        assistant_messages = self.get_assistant_messages()

        if not assistant_messages:
            return None

        # Get the last assistant message
        latest = assistant_messages[-1]
        message_data = latest.get('message', {})
        if not isinstance(message_data, dict):
            message_data = {}
        content = self._get_message_content(latest)

        # Extract text blocks
        text_blocks = [
            c.get('text', '')
            for c in content
            if isinstance(c, dict) and c.get('type') == 'text' and c.get('text', '').strip()
        ]

        # If text_only mode and no text, return None
        if text_only and not text_blocks:
            return None

        # Build result
        result = {
            'text': '\n\n'.join(text_blocks),
            'timestamp': latest.get('timestamp'),
            'uuid': latest.get('uuid'),
            'model': message_data.get('model'),
            'usage': message_data.get('usage', {}),
            'session_id': latest.get('sessionId'),
            'git_branch': latest.get('gitBranch'),
        }

        # Optionally include tool calls
        if include_tool_calls:
            result['tool_calls'] = [
                {
                    'name': c.get('name'),
                    'id': c.get('id'),
                    'input': c.get('input', {})
                }
                for c in content
                if c.get('type') == 'tool_use'
            ]

        return result

    def get_conversation_summary(self) -> Dict[str, Any]:
        """
        Get summary statistics about the conversation.

        Returns:
            Dict with message counts and metadata
        """
        user_count = sum(1 for m in self.messages if m.get('type') == 'user')
        assistant_count = sum(1 for m in self.messages if m.get('type') == 'assistant')

        return {
            'total_messages': len(self.messages),
            'user_messages': user_count,
            'assistant_messages': assistant_count,
            'session_id': self.messages[0].get('sessionId') if self.messages else None,
        }

    def get_all_tool_calls(self) -> List[Dict[str, Any]]:
        """
        Get all tool calls from the conversation.

        Returns:
            List of tool call dicts with name, input, and result
        """
        tool_calls = []

        for msg in self.messages:
            if msg.get('type') == 'assistant':
                content = self._get_message_content(msg)
                for c in content:
                    if isinstance(c, dict) and c.get('type') == 'tool_use':
                        tool_calls.append({
                            'name': c.get('name'),
                            'id': c.get('id'),
                            'input': c.get('input', {}),
                            'timestamp': msg.get('timestamp')
                        })
            elif msg.get('type') == 'tool_result':
                # Match result to tool call
                tool_use_id = msg.get('tool_use_id')
                for tc in tool_calls:
                    if tc.get('id') == tool_use_id:
                        tc['result'] = msg.get('content')
                        tc['is_error'] = msg.get('is_error', False)
                        break

        return tool_calls

    def get_todo_status(self) -> Optional[Dict[str, Any]]:
        """
        Get the latest todo list status from TodoWrite calls.

        Returns:
            Dict with todos list and counts, or None if no todos
        """
        tool_calls = self.get_all_tool_calls()

        # Find the last TodoWrite call
        todo_calls = [tc for tc in tool_calls if tc.get('name') == 'TodoWrite']

        if not todo_calls:
            return None

        latest_todo = todo_calls[-1]
        todos = latest_todo.get('input', {}).get('todos', [])

        completed = [t for t in todos if t.get('status') == 'completed']
        in_progress = [t for t in todos if t.get('status') == 'in_progress']
        pending = [t for t in todos if t.get('status') == 'pending']

        return {
            'todos': todos,
            'total': len(todos),
            'completed': len(completed),
            'in_progress': len(in_progress),
            'pending': len(pending),
            'completed_items': [t.get('content') for t in completed],
            'in_progress_items': [t.get('content') for t in in_progress],
            'pending_items': [t.get('content') for t in pending],
            'is_complete': len(pending) == 0 and len(in_progress) == 0
        }

    def get_modified_files(self) -> List[str]:
        """
        Get list of files that were modified (via Edit or Write).

        Returns:
            List of unique file paths that were modified
        """
        tool_calls = self.get_all_tool_calls()

        files = set()
        for tc in tool_calls:
            name = tc.get('name')
            input_data = tc.get('input', {})

            if name == 'Edit':
                file_path = input_data.get('file_path')
                if file_path:
                    files.add(file_path)
            elif name == 'Write':
                file_path = input_data.get('file_path')
                if file_path:
                    files.add(file_path)

        return sorted(list(files))

    def get_last_n_messages(self, n: int = 5) -> List[Dict[str, Any]]:
        """
        Get the last N messages from the transcript for DM history.

        Args:
            n: Number of messages to return (default: 5, min: 1, max: 25)

        Returns:
            List of messages formatted for Slack:
            [{'role': 'user'/'assistant', 'text': str, 'timestamp': str}, ...]
            Messages are in chronological order (oldest first).
        """
        # Validate and clamp n
        n = max(1, min(25, n))

        # Get user and assistant messages (skip tool_result)
        relevant_messages = [
            msg for msg in self.messages
            if msg.get('type') in ('user', 'assistant')
        ]

        # Take last n messages
        last_n = relevant_messages[-n:] if relevant_messages else []

        # Format for Slack
        formatted = []
        for msg in last_n:
            role = msg.get('type')
            timestamp = msg.get('timestamp', '')

            # Extract text content
            content = self._get_message_content(msg)
            text_parts = [
                c.get('text', '')
                for c in content
                if isinstance(c, dict) and c.get('type') == 'text'
            ]
            text = '\n'.join(text_parts).strip()

            if text:  # Only include messages with actual text
                formatted.append({
                    'role': role,
                    'text': text,
                    'timestamp': timestamp
                })

        return formatted

    def get_stop_reason(self) -> str:
        """
        Determine the stop reason from the transcript.

        Returns:
            One of: 'completed', 'interrupted', 'error', 'unknown'
        """
        if not self.messages:
            return 'unknown'

        # Check last message for clues
        last_msg = self.messages[-1]

        # If last message is assistant with text, likely completed
        if last_msg.get('type') == 'assistant':
            content = self._get_message_content(last_msg)
            has_text = any(isinstance(c, dict) and c.get('type') == 'text' for c in content)
            if has_text:
                return 'completed'

        # If last message is tool_result with error, might be error
        if last_msg.get('type') == 'tool_result' and last_msg.get('is_error'):
            return 'error'

        # Check if there are pending todos
        todo_status = self.get_todo_status()
        if todo_status and not todo_status.get('is_complete'):
            return 'interrupted'

        return 'completed'

    def get_rich_summary(self) -> Dict[str, Any]:
        """
        Generate a rich summary of the session for Slack.

        Returns:
            Dict with all summary information
        """
        conv_summary = self.get_conversation_summary()
        todo_status = self.get_todo_status()
        modified_files = self.get_modified_files()
        stop_reason = self.get_stop_reason()
        latest_response = self.get_latest_assistant_response(text_only=False)

        # Get first user message as "task"
        user_messages = [m for m in self.messages if m.get('type') == 'user']
        initial_task = None
        if user_messages:
            first_user = user_messages[0]
            message_data = first_user.get('message', {})
            # Handle case where message might be a string instead of dict
            if isinstance(message_data, dict):
                content = message_data.get('content', [])
            else:
                content = []
            for c in content:
                if isinstance(c, dict) and c.get('type') == 'text':
                    initial_task = c.get('text', '')[:200]  # First 200 chars
                    if len(c.get('text', '')) > 200:
                        initial_task += '...'
                    break

        return {
            'stop_reason': stop_reason,
            'is_complete': stop_reason == 'completed' and (not todo_status or todo_status.get('is_complete', True)),
            'initial_task': initial_task,
            'conversation': conv_summary,
            'todos': todo_status,
            'modified_files': modified_files,
            'model': latest_response.get('model') if latest_response else None,
            'usage': latest_response.get('usage') if latest_response else None,
        }


def main():
    """Main entry point for CLI usage."""
    # Get transcript path
    if len(sys.argv) > 1:
        # From command line argument
        transcript_path = sys.argv[1]
    else:
        # From environment (hook context)
        transcript_path = TranscriptParser.get_transcript_path_from_env()

    if not transcript_path:
        print("Error: No transcript path provided", file=sys.stderr)
        print("Usage: python3 transcript_parser.py [transcript_path]", file=sys.stderr)
        print("   Or: Set CLAUDE_TRANSCRIPT_PATH environment variable", file=sys.stderr)
        sys.exit(1)

    # Parse transcript
    parser = TranscriptParser(transcript_path)

    if not parser.load():
        print(f"Error: Transcript file not found: {transcript_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded transcript: {transcript_path}")
    print()

    # Show summary
    summary = parser.get_conversation_summary()
    print("Conversation Summary:")
    print(f"  Total messages: {summary['total_messages']}")
    print(f"  User messages: {summary['user_messages']}")
    print(f"  Assistant messages: {summary['assistant_messages']}")
    print(f"  Session ID: {summary['session_id']}")
    print()

    # Get latest response
    response = parser.get_latest_assistant_response(include_tool_calls=True)

    if response:
        print("Latest Assistant Response:")
        print("=" * 80)
        print(f"Model: {response['model']}")
        print(f"Timestamp: {response['timestamp']}")
        print(f"UUID: {response['uuid']}")
        print(f"Git branch: {response['git_branch']}")
        print()

        if response['usage']:
            usage = response['usage']
            print(f"Token usage:")
            print(f"  Input tokens: {usage.get('input_tokens', 0)}")
            print(f"  Output tokens: {usage.get('output_tokens', 0)}")
            print(f"  Cache read: {usage.get('cache_read_input_tokens', 0)}")
            print()

        if response.get('tool_calls'):
            print(f"Tool calls: {len(response['tool_calls'])}")
            for tc in response['tool_calls']:
                print(f"  - {tc['name']} (id: {tc['id'][:20]}...)")
            print()

        print("Text content:")
        print("-" * 80)
        print(response['text'])
    else:
        print("No assistant response with text found in transcript")


if __name__ == "__main__":
    main()
