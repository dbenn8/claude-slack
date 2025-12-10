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
        content = message_data.get('content', [])

        # Extract text blocks
        text_blocks = [
            c.get('text', '')
            for c in content
            if c.get('type') == 'text' and c.get('text', '').strip()
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
