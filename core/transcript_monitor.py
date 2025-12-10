"""
Transcript Monitor for Claude Code Sessions

Monitors Claude's transcript JSONL file to detect thinking blocks, tool calls,
and tool results. This complements PTY-based permission detection.

Claude Code writes transcripts to ~/.claude/projects/{project_path_hash}/{session_id}.jsonl
Each line is a JSON object representing an event (thinking, tool_use, tool_result, text, etc.)

Design:
- NOT a background thread (piggyback approach)
- Called when hooks fire to scan for recent events
- Tracks file position to avoid re-processing old events
- Enqueues events to OutputQueue with appropriate priorities
"""

import json
import glob
from pathlib import Path
from typing import Optional, List, Dict, Any
try:
    from .output_queue import OutputQueue
except ImportError:
    from output_queue import OutputQueue


# Priority mapping from design doc
EVENT_PRIORITIES = {
    "thinking": 20,
    "tool_call": 40,  # Maps to tool_use in transcript
    "tool_result": 50,
}


class TranscriptMonitor:
    """
    Monitor Claude's transcript JSONL file for thinking blocks, tool calls, and results.

    NOT a background thread - called when hooks fire to scan for recent events.
    Tracks position in file to avoid re-processing old events.
    """

    def __init__(self, session_id: str, output_queue: OutputQueue):
        """
        Initialize transcript monitor.

        Args:
            session_id: Claude session UUID (36-char)
            output_queue: OutputQueue instance to enqueue events to
        """
        self.session_id = session_id
        self.output_queue = output_queue
        self.transcript_path: Optional[Path] = None
        self.last_position = 0  # File position of last read
        self.processed_ids = set()  # Track processed tool_use IDs to avoid dupes
        self.tool_use_cache: Dict[str, str] = {}  # Map tool_id -> tool_name

    def find_transcript(self) -> Optional[Path]:
        """
        Find the transcript JSONL file for this session.
        Searches ~/.claude/projects/*/{session_id}.jsonl

        Returns:
            Path to transcript file, or None if not found
        """
        home = Path.home()
        claude_projects = home / ".claude" / "projects"

        if not claude_projects.exists():
            return None

        # Search for {session_id}.jsonl in all project subdirectories
        pattern = str(claude_projects / "*" / f"{self.session_id}.jsonl")
        matches = glob.glob(pattern)

        if matches:
            # Return the first match (should only be one)
            return Path(matches[0])

        return None

    def scan_recent_events(self, event_types: Optional[List[str]] = None) -> List[dict]:
        """
        Scan transcript for new events since last check.

        Args:
            event_types: Types to scan for (default: ['thinking', 'tool_use', 'tool_result'])

        Returns:
            List of new events found (already enqueued to OutputQueue)
        """
        if event_types is None:
            event_types = ['thinking', 'tool_use', 'tool_result']

        # Find transcript if we haven't already
        if self.transcript_path is None:
            self.transcript_path = self.find_transcript()
            if self.transcript_path is None:
                return []  # Transcript doesn't exist yet

        # Check if file exists (may have been found but deleted)
        if not self.transcript_path.exists():
            return []

        events = []

        try:
            with open(self.transcript_path, 'r', encoding='utf-8') as f:
                # Seek to last read position
                f.seek(self.last_position)

                # Read all new lines
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    event = self._parse_line(line)
                    if event and event.get('type') in event_types:
                        events.append(event)

                        # Enqueue to OutputQueue
                        self._enqueue_event(event)

                # Update position
                self.last_position = f.tell()

        except (IOError, OSError) as e:
            # File may be locked or inaccessible, skip this scan
            pass
        except Exception as e:
            # Unexpected error, but don't crash
            pass

        return events

    def _parse_line(self, line: str) -> Optional[dict]:
        """
        Parse a single JSONL line, return event dict or None if not relevant.

        Args:
            line: Raw JSONL line

        Returns:
            Event dict with type and payload, or None if not relevant/malformed
        """
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            # Malformed JSON, skip
            return None

        event_type = data.get('type')

        if event_type == 'thinking':
            return {
                'type': 'thinking',
                'payload': self._extract_thinking_payload(data)
            }
        elif event_type == 'tool_use':
            return {
                'type': 'tool_use',
                'payload': self._extract_tool_use_payload(data)
            }
        elif event_type == 'tool_result':
            return {
                'type': 'tool_result',
                'payload': self._extract_tool_result_payload(data)
            }

        return None

    def _extract_thinking_payload(self, data: dict) -> dict:
        """
        Extract payload for thinking event.

        Args:
            data: Raw JSON event data

        Returns:
            Payload dict with text, word_count, signature
        """
        text = data.get('thinking', '')
        word_count = len(text.split()) if text else 0

        return {
            'text': text,
            'word_count': word_count,
            'signature': data.get('signature', '')
        }

    def _extract_tool_use_payload(self, data: dict) -> dict:
        """
        Extract payload for tool_use event.

        Args:
            data: Raw JSON event data

        Returns:
            Payload dict with tool_id, tool_name, input
        """
        tool_id = data.get('id', '')
        tool_name = data.get('name', '')
        tool_input = data.get('input', {})

        # Cache tool_name for later tool_result lookup
        if tool_id and tool_name:
            self.tool_use_cache[tool_id] = tool_name

        return {
            'tool_id': tool_id,
            'tool_name': tool_name,
            'input': tool_input
        }

    def _extract_tool_result_payload(self, data: dict) -> dict:
        """
        Extract payload for tool_result event.

        Args:
            data: Raw JSON event data

        Returns:
            Payload dict with tool_id, tool_name, content, is_error, line_count
        """
        tool_id = data.get('tool_use_id', '')
        content = data.get('content', '')
        is_error = data.get('is_error', False)

        # Look up tool_name from cache
        tool_name = self.tool_use_cache.get(tool_id, 'Unknown')

        # Count lines in content
        line_count = len(content.split('\n')) if content else 0

        return {
            'tool_id': tool_id,
            'tool_name': tool_name,
            'content': content,
            'is_error': is_error,
            'line_count': line_count
        }

    def _enqueue_event(self, event: dict) -> None:
        """
        Enqueue event to OutputQueue with appropriate priority.

        Args:
            event: Event dict with type and payload
        """
        event_type = event['type']
        payload = event['payload']

        # Map transcript event types to queue event types
        if event_type == 'thinking':
            queue_type = 'thinking'
            priority = EVENT_PRIORITIES['thinking']
        elif event_type == 'tool_use':
            queue_type = 'tool_call'
            priority = EVENT_PRIORITIES['tool_call']

            # Check if already processed
            tool_id = payload.get('tool_id')
            if tool_id in self.processed_ids:
                return
            self.processed_ids.add(tool_id)
        elif event_type == 'tool_result':
            queue_type = 'tool_result'
            priority = EVENT_PRIORITIES['tool_result']

            # Check if already processed (use tool_id for deduplication)
            tool_id = payload.get('tool_id')
            result_id = f"result_{tool_id}"
            if result_id in self.processed_ids:
                return
            self.processed_ids.add(result_id)
        else:
            # Unknown event type, skip
            return

        # Enqueue to OutputQueue
        self.output_queue.enqueue(
            event_type=queue_type,
            payload=payload,
            priority=priority
        )


def get_transcript_monitor(session_id: str, output_queue: OutputQueue) -> TranscriptMonitor:
    """
    Get TranscriptMonitor instance for session.

    Args:
        session_id: Claude session UUID (36-char)
        output_queue: OutputQueue instance to enqueue events to

    Returns:
        TranscriptMonitor instance
    """
    return TranscriptMonitor(session_id, output_queue)
