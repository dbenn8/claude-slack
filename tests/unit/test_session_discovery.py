"""
Unit tests for core/session_discovery.py

Tests session discovery by buffer file modification time,
enabling discovery of active sessions after /compact or /resume.
"""

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Add core directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))


class TestExtractSessionIdFromFilename:
    """Tests for extract_session_id_from_filename()."""

    def test_extract_session_id_from_filename(self):
        """Extracts session_id from valid buffer filename."""
        from session_discovery import extract_session_id_from_filename

        filename = "claude_output_abc12345.txt"
        session_id = extract_session_id_from_filename(filename)
        assert session_id == "abc12345"

    def test_extract_session_id_handles_uuid_format(self):
        """Extracts full UUID-like session IDs."""
        from session_discovery import extract_session_id_from_filename

        filename = "claude_output_e537eb3d-1234-5678-abcd-ef1234567890.txt"
        session_id = extract_session_id_from_filename(filename)
        assert session_id == "e537eb3d-1234-5678-abcd-ef1234567890"

    def test_extract_session_id_handles_invalid_filename(self):
        """Returns None for filenames that don't match pattern."""
        from session_discovery import extract_session_id_from_filename

        # Wrong prefix
        assert extract_session_id_from_filename("debug.log") is None
        assert extract_session_id_from_filename("output_abc123.txt") is None

        # Wrong extension
        assert extract_session_id_from_filename("claude_output_abc123.log") is None

        # Missing session ID
        assert extract_session_id_from_filename("claude_output_.txt") is None

    def test_extract_session_id_handles_line_log_files(self):
        """Extracts session_id from claude_lines_ files too."""
        from session_discovery import extract_session_id_from_filename

        filename = "claude_lines_abc12345.txt"
        session_id = extract_session_id_from_filename(filename)
        assert session_id == "abc12345"


class TestFindActiveSession:
    """Tests for find_active_session()."""

    def test_find_active_session_returns_most_recent(self, tmp_path):
        """Returns session_id of most recently modified buffer file."""
        from session_discovery import find_active_session

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create buffer files with different modification times
        file1 = log_dir / "claude_output_session1.txt"
        file2 = log_dir / "claude_output_session2.txt"
        file3 = log_dir / "claude_output_session3.txt"

        file1.write_text("session 1")
        time.sleep(0.01)  # Small delay to ensure different mtimes
        file2.write_text("session 2")
        time.sleep(0.01)
        file3.write_text("session 3")  # Most recent

        session_id = find_active_session(log_dir)
        assert session_id == "session3"

    def test_find_active_session_handles_no_files(self, tmp_path):
        """Returns None when log directory is empty."""
        from session_discovery import find_active_session

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        session_id = find_active_session(log_dir)
        assert session_id is None

    def test_find_active_session_ignores_non_buffer_files(self, tmp_path):
        """Ignores non-buffer files like debug.log."""
        from session_discovery import find_active_session

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create non-buffer file first
        debug_log = log_dir / "debug.log"
        debug_log.write_text("debug messages")
        time.sleep(0.01)

        # Create buffer file (older than debug.log)
        buffer_file = log_dir / "claude_output_mysession.txt"
        buffer_file.write_text("buffer content")

        session_id = find_active_session(log_dir)
        assert session_id == "mysession"

    def test_find_active_session_handles_line_log_files(self, tmp_path):
        """Uses claude_output_ files for discovery, not claude_lines_."""
        from session_discovery import find_active_session

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create both output and lines files
        output_file = log_dir / "claude_output_session1.txt"
        lines_file = log_dir / "claude_lines_session2.txt"

        output_file.write_text("output")
        time.sleep(0.01)
        lines_file.write_text("lines")  # More recent

        # Should only consider claude_output_ files
        session_id = find_active_session(log_dir)
        assert session_id == "session1"

    def test_find_active_session_handles_nonexistent_directory(self, tmp_path):
        """Returns None when log directory doesn't exist."""
        from session_discovery import find_active_session

        log_dir = tmp_path / "nonexistent"

        session_id = find_active_session(log_dir)
        assert session_id is None

    def test_find_active_session_handles_path_string(self, tmp_path):
        """Accepts both Path and string for log_dir."""
        from session_discovery import find_active_session

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        buffer_file = log_dir / "claude_output_test123.txt"
        buffer_file.write_text("content")

        # Test with string path
        session_id = find_active_session(str(log_dir))
        assert session_id == "test123"

        # Test with Path object
        session_id = find_active_session(log_dir)
        assert session_id == "test123"

    def test_find_active_session_handles_multiple_files_same_time(self, tmp_path):
        """Returns one session when multiple files have same mtime."""
        from session_discovery import find_active_session

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create files quickly (may have same mtime on some systems)
        file1 = log_dir / "claude_output_alpha.txt"
        file2 = log_dir / "claude_output_beta.txt"
        file1.write_text("alpha")
        file2.write_text("beta")

        session_id = find_active_session(log_dir)
        # Should return one of them (doesn't matter which)
        assert session_id in ["alpha", "beta"]
