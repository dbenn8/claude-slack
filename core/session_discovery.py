"""
Session discovery by buffer file modification time.

Enables discovery of the active session after /compact or /resume
by finding the most recently modified buffer file in the logs directory.
"""

import os
import re
from pathlib import Path
from typing import Optional


def extract_session_id_from_filename(filename: str) -> Optional[str]:
    """
    Extract session_id from buffer filename.

    Buffer file pattern: claude_output_{session_id}.txt
    Also supports: claude_lines_{session_id}.txt

    Args:
        filename: Filename to extract session_id from (not full path)

    Returns:
        Session ID if filename matches pattern, None otherwise

    Examples:
        >>> extract_session_id_from_filename("claude_output_abc12345.txt")
        'abc12345'
        >>> extract_session_id_from_filename("claude_output_e537eb3d-1234-5678-abcd-ef1234567890.txt")
        'e537eb3d-1234-5678-abcd-ef1234567890'
        >>> extract_session_id_from_filename("debug.log")
        None
    """
    # Pattern matches: claude_output_{session_id}.txt or claude_lines_{session_id}.txt
    pattern = r'^claude_(?:output|lines)_(.+)\.txt$'
    match = re.match(pattern, filename)

    if match:
        session_id = match.group(1)
        # Validate that session_id is not empty
        if session_id:
            return session_id

    return None


def find_active_session(log_dir: Path | str) -> Optional[str]:
    """
    Find the most recently modified buffer file in logs directory.

    Searches for claude_output_*.txt files and returns the session_id
    of the most recently modified file. This enables discovery of the
    active session after /compact or /resume.

    Args:
        log_dir: Path to the logs directory (Path object or string)

    Returns:
        Session ID of most recent buffer file, or None if no files found

    Examples:
        >>> find_active_session("/var/home/user/.claude/slack/logs")
        'e537eb3d-1234-5678-abcd-ef1234567890'
    """
    # Convert string to Path if needed
    if isinstance(log_dir, str):
        log_dir = Path(log_dir)

    # Check if directory exists
    if not log_dir.exists() or not log_dir.is_dir():
        return None

    # Find all claude_output_*.txt files
    buffer_files = list(log_dir.glob("claude_output_*.txt"))

    if not buffer_files:
        return None

    # Sort by modification time (most recent first)
    try:
        buffer_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        # Handle case where file was deleted between glob and stat
        return None

    # Get the most recent file
    most_recent_file = buffer_files[0]

    # Extract and return session_id
    session_id = extract_session_id_from_filename(most_recent_file.name)
    return session_id
