"""
Permission Detector - Queue-Based Permission Capture

Detects permission prompts from Claude's PTY output in real-time and queues them
for the notification hook to consume. This eliminates timing issues where the
hook fires before the buffer is ready.

Architecture:
- PermissionDetector processes PTY chunks as they arrive
- Permissions are queued to the unified OutputQueue
- Notification hook reads queue instantly (no retry loop needed)
- Queue entries are marked consumed after use
"""

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from typing import Optional

try:
    from .output_queue import OutputQueue
except ImportError:
    from output_queue import OutputQueue


# ANSI escape code pattern
ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

# Permission anchor keywords
PERMISSION_ANCHORS = [
    r'needs permission',
    r'permission to use',
    r'wants to',
    r'Choose an option',
    r'Select one',
]

# Keywords that indicate permission options
PERMISSION_KEYWORDS = [
    'approve', 'deny', 'allow', 'yes', 'no', 'reject',
    'permit', 'grant', 'refuse', 'accept', 'decline'
]

# Option pattern: "1. Some text" or "1) Some text"
OPTION_PATTERN = re.compile(r'^\s*(\d+)[\.\)]\s*(.+)$', re.MULTILINE)


def strip_ansi_codes(text: str) -> str:
    """Strip ANSI escape codes from text."""
    return ANSI_ESCAPE.sub('', text)


class PermissionDetector:
    """
    Detects and queues permission prompts from PTY output.

    Usage:
        detector = PermissionDetector(session_id="abc123")

        # In PTY read loop:
        data = os.read(master_fd, 1024)
        detector.process_chunk(data)

        # In notification hook:
        permission = detector.get_unconsumed_permission()
    """

    def __init__(self, session_id: str, output_queue: 'OutputQueue' = None, logger=None):
        """
        Initialize the permission detector.

        Args:
            session_id: Session ID for queue file naming
            output_queue: Optional OutputQueue instance (creates one if not provided)
            logger: Optional logger for debug output
        """
        self.session_id = session_id
        self.logger = logger
        self.buffer = ""  # Rolling buffer for multi-chunk detection
        self.buffer_lock = threading.Lock()

        # Use provided OutputQueue or create new one
        self.output_queue = output_queue or OutputQueue(session_id)

        # Keep old queue_file path for backward compatibility during transition
        self.queue_file = f"/tmp/claude_permission_queue_{session_id}.json"

        # Track detected permissions to avoid duplicates
        self.last_permission_hash = None

    def _log(self, message: str) -> None:
        """Log a message if logger is available."""
        if self.logger:
            self.logger.debug(f"[PermissionDetector] {message}")

    def process_chunk(self, data: bytes) -> Optional[dict]:
        """
        Process a chunk of PTY output, detect and queue any permissions.

        Args:
            data: Raw bytes from PTY read

        Returns:
            Permission dict if one was detected and queued, None otherwise
        """
        with self.buffer_lock:
            try:
                # Decode and add to rolling buffer
                text = data.decode('utf-8', errors='replace')
                self.buffer += text

                # Keep buffer manageable (last 8KB to capture full permission prompts)
                if len(self.buffer) > 8192:
                    self.buffer = self.buffer[-8192:]

                # Check for complete permission prompt
                permission = self._detect_permission(self.buffer)
                if permission:
                    # Generate hash to detect duplicates
                    perm_hash = hash(tuple(permission.get("options", [])))

                    if perm_hash != self.last_permission_hash:
                        self._queue_permission(permission)
                        self.last_permission_hash = perm_hash

                        # Clear buffer after successful detection
                        self.buffer = ""

                        return permission

                return None

            except Exception as e:
                self._log(f"Error processing chunk: {e}")
                return None

    def _detect_permission(self, text: str) -> Optional[dict]:
        """
        Detect a permission prompt in the text.

        Args:
            text: Text to search for permission prompts

        Returns:
            Permission dict with tool_name, options, raw_text or None
        """
        # Strip ANSI codes
        clean_text = strip_ansi_codes(text)

        # STEP 1: Find permission-specific anchor keywords
        anchor_pos = -1
        matched_anchor = None
        for anchor in PERMISSION_ANCHORS:
            match = re.search(anchor, clean_text, re.IGNORECASE)
            if match:
                anchor_pos = match.start()
                matched_anchor = anchor
                break

        # If no anchor found, return None (not a permission prompt)
        if anchor_pos < 0:
            return None

        # Search from anchor position
        search_text = clean_text[anchor_pos:]

        # STEP 2: Find all numbered list patterns
        matches = OPTION_PATTERN.findall(search_text)

        if not matches:
            return None

        # STEP 3: Extract consecutive numbered lists
        option_groups = []
        current_group = []
        current_start_num = None
        expected_next = None

        for num_str, option_text in matches:
            num = int(num_str)

            if expected_next is None:
                if current_group:
                    option_groups.append((current_group, current_start_num))
                current_group = [option_text.strip()]
                current_start_num = num
                expected_next = num + 1
            elif num == expected_next:
                current_group.append(option_text.strip())
                expected_next = num + 1
            else:
                if current_group:
                    option_groups.append((current_group, current_start_num))
                current_group = [option_text.strip()]
                current_start_num = num
                expected_next = num + 1

        if current_group:
            option_groups.append((current_group, current_start_num))

        # STEP 4: Find first group that looks like permission options
        for group, start_num in option_groups:
            # Only consider groups with 2-3 options
            if len(group) < 2 or len(group) > 3:
                continue

            # Check if options contain permission keywords
            group_text = ' '.join(group).lower()
            has_permission_keywords = any(kw in group_text for kw in PERMISSION_KEYWORDS)

            if has_permission_keywords:
                # Reconstruct missing option 1 if needed
                options = self._reconstruct_options(group, start_num)

                # Extract tool name from text (best effort)
                tool_name = self._extract_tool_name(clean_text)

                # Get raw text from anchor to end of options
                raw_text = self._extract_raw_text(clean_text, anchor_pos)

                return {
                    "tool_name": tool_name,
                    "options": options,
                    "raw_text": raw_text,
                    "anchor": matched_anchor,
                    "start_num": start_num
                }

        # STEP 5: Fallback - take first 2-3 item group
        for group, start_num in option_groups:
            if 2 <= len(group) <= 3:
                options = self._reconstruct_options(group, start_num)
                tool_name = self._extract_tool_name(clean_text)
                raw_text = self._extract_raw_text(clean_text, anchor_pos)

                return {
                    "tool_name": tool_name,
                    "options": options,
                    "raw_text": raw_text,
                    "anchor": matched_anchor,
                    "start_num": start_num
                }

        return None

    def _reconstruct_options(self, group: list, start_num: int) -> list:
        """
        Reconstruct missing option 1 or 2 if they scrolled off screen.

        Args:
            group: List of captured option strings
            start_num: Number of first captured option

        Returns:
            Complete list of options with reconstructed missing ones
        """
        if start_num == 2:
            return ["Approve this time"] + group
        elif start_num == 3:
            return ["Approve this time", "Approve commands like this for this project"] + group
        return group

    def _extract_tool_name(self, text: str) -> Optional[str]:
        """
        Extract tool name from permission prompt text.

        Args:
            text: Clean text to search

        Returns:
            Tool name if found, None otherwise
        """
        # Common patterns:
        # "Claude wants to use Bash to run: ..."
        # "Claude needs permission to use Read"
        patterns = [
            r'(?:wants to |needs permission to )use (\w+)',
            r'(?:wants to |needs permission to )run (\w+)',
            r'use the (\w+) tool',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)

        return None

    def _extract_raw_text(self, text: str, anchor_pos: int) -> str:
        """
        Extract the raw permission prompt text.

        Args:
            text: Full text
            anchor_pos: Position of permission anchor

        Returns:
            Raw text from anchor to end of options (limited to 500 chars)
        """
        # Get text from anchor, limit to reasonable length
        raw = text[anchor_pos:anchor_pos + 500]

        # Try to truncate at end of last option (look for newline after "3.")
        lines = raw.split('\n')
        result_lines = []
        found_options = False

        for line in lines:
            result_lines.append(line)
            if re.match(r'^\s*[123][\.\)]', line):
                found_options = True
            elif found_options and not line.strip():
                # Empty line after options - stop here
                break

        return '\n'.join(result_lines).strip()

    def _queue_permission(self, permission: dict) -> None:
        """
        Add permission to the unified output queue.

        Args:
            permission: Permission dict to queue
        """
        # Build payload for OutputQueue
        payload = {
            "tool_name": permission.get("tool_name", "unknown"),
            "options": permission.get("options", []),
            "raw_text": permission.get("raw_text", ""),
            "detected_at": permission.get("detected_at", datetime.now(timezone.utc).isoformat()),
        }

        # Enqueue with priority 100 (blocking event)
        event_id = self.output_queue.enqueue(
            event_type="permission",
            priority=100,
            payload=payload
        )

        if self.logger:
            self.logger.info(f"Queued permission for {payload['tool_name']}: {event_id}")

    def get_unconsumed_permission(self) -> Optional[dict]:
        """
        Get the first unconsumed permission from the queue.

        This is called by the notification hook.

        Returns:
            Permission dict if found, None otherwise
        """
        events = self.output_queue.get_unconsumed(event_type="permission")
        if events:
            event = events[0]
            return {
                "event_id": event["id"],
                "tool_name": event["payload"].get("tool_name", "unknown"),
                "options": event["payload"].get("options", []),
                "raw_text": event["payload"].get("raw_text", ""),
                "detected_at": event["payload"].get("detected_at"),
            }
        return None

    def mark_consumed(self, event_id: str, slack_ts: str = None) -> None:
        """
        Mark a permission as consumed after posting to Slack.

        Args:
            event_id: Event ID from get_unconsumed_permission
            slack_ts: Optional Slack message timestamp
        """
        self.output_queue.mark_consumed(event_id, slack_ts)

    def cleanup(self) -> None:
        """Clean up queue files on session end."""
        # OutputQueue handles its own cleanup
        self.output_queue.cleanup()

        # Also clean up old-style queue file if it exists (backward compat)
        try:
            if os.path.exists(self.queue_file):
                os.remove(self.queue_file)
        except OSError:
            pass


# Standalone function for use by notification hook
def get_permission_from_queue(session_id: str) -> Optional[dict]:
    """
    Get unconsumed permission for a session.

    Standalone function for use by hooks.

    Args:
        session_id: Session ID to check

    Returns:
        Permission dict with event_id, tool_name, options, raw_text
    """
    try:
        from .output_queue import OutputQueue
    except ImportError:
        from output_queue import OutputQueue
    queue = OutputQueue(session_id)
    events = queue.get_unconsumed(event_type="permission")
    if events:
        event = events[0]
        return {
            "event_id": event["id"],
            "tool_name": event["payload"].get("tool_name", "unknown"),
            "options": event["payload"].get("options", []),
            "raw_text": event["payload"].get("raw_text", ""),
        }
    return None
