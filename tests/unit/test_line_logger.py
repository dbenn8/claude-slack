"""
Unit tests for core/line_logger.py

Tests the LineLogger class that maintains a deque of cleaned terminal output lines.
"""

import sys
from pathlib import Path
from threading import Thread
import time

import pytest

# Add core directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core"))

from line_logger import LineLogger, strip_ansi


class TestStripAnsi:
    """Tests for strip_ansi() helper function."""

    def test_strip_ansi_plain_text(self):
        """Plain text passes through unchanged."""
        result = strip_ansi("Hello World")
        assert result == "Hello World"

    def test_strip_ansi_color_codes(self):
        """Strips color codes."""
        result = strip_ansi("\x1b[31mRed\x1b[0m")
        assert result == "Red"

    def test_strip_ansi_bold(self):
        """Strips bold formatting."""
        result = strip_ansi("\x1b[1mBold\x1b[0m")
        assert result == "Bold"

    def test_strip_ansi_complex(self):
        """Strips complex ANSI sequences."""
        result = strip_ansi("\x1b[1;31;42mComplex\x1b[0m formatting\x1b[34m here\x1b[0m")
        assert result == "Complex formatting here"

    def test_strip_ansi_cursor_movement(self):
        """Strips cursor movement codes."""
        result = strip_ansi("\x1b[2A\x1b[3CText after cursor move")
        assert result == "Text after cursor move"

    def test_strip_ansi_clear_line(self):
        """Strips clear line codes."""
        result = strip_ansi("\x1b[2KCleared line")
        assert result == "Cleared line"


class TestLineLoggerInit:
    """Tests for LineLogger initialization."""

    def test_line_logger_init_creates_empty_deque(self):
        """LineLogger() creates empty deque."""
        logger = LineLogger()
        assert len(logger.get_all_lines()) == 0

    def test_line_logger_init_default_max_lines(self):
        """LineLogger() has default max_lines of 500."""
        logger = LineLogger()
        # We can verify this by adding 501 lines and checking we have exactly 500
        for i in range(501):
            logger.add_data(f"line {i}\n".encode())
        assert len(logger.get_all_lines()) == 500

    def test_line_logger_init_custom_max_lines(self):
        """LineLogger(max_lines=100) respects custom max."""
        logger = LineLogger(max_lines=100)
        for i in range(150):
            logger.add_data(f"line {i}\n".encode())
        assert len(logger.get_all_lines()) == 100


class TestLineLoggerAddData:
    """Tests for LineLogger.add_data()."""

    def test_line_logger_add_data_extracts_lines(self):
        """add_data(b"line1\\nline2\\n") results in 2 lines."""
        logger = LineLogger()
        logger.add_data(b"line1\nline2\n")
        lines = logger.get_all_lines()
        assert len(lines) == 2
        assert lines[0] == "line1"
        assert lines[1] == "line2"

    def test_line_logger_add_data_handles_crlf(self):
        """add_data handles CRLF line endings."""
        logger = LineLogger()
        logger.add_data(b"line1\r\nline2\r\n")
        lines = logger.get_all_lines()
        assert len(lines) == 2
        assert lines[0] == "line1"
        assert lines[1] == "line2"

    def test_line_logger_add_data_handles_cr(self):
        """add_data handles CR line endings."""
        logger = LineLogger()
        logger.add_data(b"line1\rline2\r")
        lines = logger.get_all_lines()
        assert len(lines) == 2
        assert lines[0] == "line1"
        assert lines[1] == "line2"

    def test_line_logger_strips_ansi(self):
        """add_data(b"\\x1b[31mRed\\x1b[0m") stores "Red"."""
        logger = LineLogger()
        logger.add_data(b"\x1b[31mRed\x1b[0m\n")
        lines = logger.get_all_lines()
        assert len(lines) == 1
        assert lines[0] == "Red"

    def test_line_logger_handles_partial_lines(self):
        """Partial lines (no trailing newline) are buffered."""
        logger = LineLogger()
        logger.add_data(b"partial")
        assert len(logger.get_all_lines()) == 0  # No complete line yet

        logger.add_data(b" line\n")
        lines = logger.get_all_lines()
        assert len(lines) == 1
        assert lines[0] == "partial line"

    def test_line_logger_handles_empty_data(self):
        """add_data handles empty bytes."""
        logger = LineLogger()
        logger.add_data(b"")
        assert len(logger.get_all_lines()) == 0

    def test_line_logger_handles_utf8_decode_errors(self):
        """add_data handles invalid UTF-8 gracefully."""
        logger = LineLogger()
        # Invalid UTF-8 sequence
        logger.add_data(b"valid\xff\xfe\ninvalid\n")
        lines = logger.get_all_lines()
        # Should have 2 lines, even if some bytes are replaced
        assert len(lines) == 2

    def test_line_logger_skips_empty_lines(self):
        """add_data skips empty lines."""
        logger = LineLogger()
        logger.add_data(b"line1\n\n\nline2\n")
        lines = logger.get_all_lines()
        assert len(lines) == 2
        assert lines[0] == "line1"
        assert lines[1] == "line2"

    def test_line_logger_strips_whitespace(self):
        """add_data strips leading/trailing whitespace from lines."""
        logger = LineLogger()
        logger.add_data(b"  line1  \n\tline2\t\n")
        lines = logger.get_all_lines()
        assert len(lines) == 2
        assert lines[0] == "line1"
        assert lines[1] == "line2"


class TestLineLoggerMaxLines:
    """Tests for LineLogger max_lines behavior."""

    def test_line_logger_respects_max_lines(self):
        """Adding 600 lines to LineLogger(max_lines=500) keeps only 500."""
        logger = LineLogger(max_lines=500)
        for i in range(600):
            logger.add_data(f"line {i}\n".encode())

        lines = logger.get_all_lines()
        assert len(lines) == 500

        # Should have lines 100-599 (the last 500)
        assert lines[0] == "line 100"
        assert lines[-1] == "line 599"

    def test_line_logger_fifo_ordering(self):
        """Old lines are dropped in FIFO order."""
        logger = LineLogger(max_lines=3)
        logger.add_data(b"line1\nline2\nline3\n")
        assert len(logger.get_all_lines()) == 3

        logger.add_data(b"line4\n")
        lines = logger.get_all_lines()
        assert len(lines) == 3
        assert lines == ["line2", "line3", "line4"]


class TestLineLoggerGetLastN:
    """Tests for LineLogger.get_last_n()."""

    def test_line_logger_get_last_n(self):
        """Adding 100 lines, get_last_n(10) returns last 10."""
        logger = LineLogger()
        for i in range(100):
            logger.add_data(f"line {i}\n".encode())

        last_10 = logger.get_last_n(10)
        assert len(last_10) == 10
        assert last_10[0] == "line 90"
        assert last_10[-1] == "line 99"

    def test_line_logger_get_last_n_more_than_available(self):
        """get_last_n(100) when only 50 lines returns all 50."""
        logger = LineLogger()
        for i in range(50):
            logger.add_data(f"line {i}\n".encode())

        last_100 = logger.get_last_n(100)
        assert len(last_100) == 50
        assert last_100[0] == "line 0"
        assert last_100[-1] == "line 49"

    def test_line_logger_get_last_n_zero(self):
        """get_last_n(0) returns empty list."""
        logger = LineLogger()
        logger.add_data(b"line1\nline2\n")
        assert logger.get_last_n(0) == []

    def test_line_logger_get_last_n_negative(self):
        """get_last_n with negative number returns empty list."""
        logger = LineLogger()
        logger.add_data(b"line1\nline2\n")
        # Python list slicing with negative start from end works, but we want last N
        # Actually [-5:] would give us all if less than 5, which is reasonable
        result = logger.get_last_n(-5)
        assert result == []


class TestLineLoggerGetAllLines:
    """Tests for LineLogger.get_all_lines()."""

    def test_line_logger_get_all_lines_empty(self):
        """get_all_lines() returns empty list when no lines."""
        logger = LineLogger()
        assert logger.get_all_lines() == []

    def test_line_logger_get_all_lines(self):
        """get_all_lines() returns all stored lines."""
        logger = LineLogger()
        logger.add_data(b"line1\nline2\nline3\n")
        lines = logger.get_all_lines()
        assert len(lines) == 3
        assert lines == ["line1", "line2", "line3"]


class TestLineLoggerSaveToFile:
    """Tests for LineLogger.save_to_file()."""

    def test_line_logger_save_to_file(self, tmp_path):
        """save_to_file(path) creates file with numbered lines."""
        logger = LineLogger()
        for i in range(10):
            logger.add_data(f"line {i}\n".encode())

        output_file = tmp_path / "output.txt"
        logger.save_to_file(output_file)

        assert output_file.exists()

        content = output_file.read_text()
        lines = content.splitlines()

        # Should have 10 numbered lines
        assert len(lines) == 10
        assert lines[0] == "   0: line 0"
        assert lines[9] == "   9: line 9"

    def test_line_logger_save_to_file_empty(self, tmp_path):
        """save_to_file() creates empty file when no lines."""
        logger = LineLogger()
        output_file = tmp_path / "empty.txt"
        logger.save_to_file(output_file)

        assert output_file.exists()
        content = output_file.read_text()
        assert content == ""

    def test_line_logger_save_to_file_overwrites(self, tmp_path):
        """save_to_file() overwrites existing file."""
        logger = LineLogger()
        logger.add_data(b"new line\n")

        output_file = tmp_path / "overwrite.txt"
        output_file.write_text("old content")

        logger.save_to_file(output_file)

        content = output_file.read_text()
        assert "old content" not in content
        assert "new line" in content

    def test_line_logger_save_to_file_creates_parent_dirs(self, tmp_path):
        """save_to_file() creates parent directories if needed."""
        logger = LineLogger()
        logger.add_data(b"test line\n")

        nested_file = tmp_path / "subdir" / "nested" / "output.txt"
        logger.save_to_file(nested_file)

        assert nested_file.exists()
        content = nested_file.read_text()
        assert "test line" in content


class TestLineLoggerThreadSafety:
    """Tests for thread-safe operation."""

    def test_line_logger_thread_safe_add_data(self):
        """Multiple threads can safely add_data concurrently."""
        # Use max_lines large enough to hold all test data
        logger = LineLogger(max_lines=2000)
        num_threads = 10
        lines_per_thread = 100

        def add_lines(thread_id):
            for i in range(lines_per_thread):
                logger.add_data(f"thread{thread_id}-line{i}\n".encode())

        threads = [Thread(target=add_lines, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have all lines from all threads
        lines = logger.get_all_lines()
        assert len(lines) == num_threads * lines_per_thread

    def test_line_logger_thread_safe_read_while_writing(self):
        """Can safely read lines while another thread is writing."""
        # Use max_lines large enough to hold all test data
        logger = LineLogger(max_lines=2000)
        stop_flag = []

        def writer():
            for i in range(1000):
                logger.add_data(f"line {i}\n".encode())
                time.sleep(0.001)
            stop_flag.append(True)

        def reader():
            while not stop_flag:
                _ = logger.get_all_lines()
                _ = logger.get_last_n(10)
                time.sleep(0.001)

        write_thread = Thread(target=writer)
        read_thread = Thread(target=reader)

        write_thread.start()
        read_thread.start()

        write_thread.join()
        read_thread.join()

        # Should complete without deadlock or errors
        lines = logger.get_all_lines()
        assert len(lines) == 1000


class TestLineLoggerCursorPrefixHandling:
    """Tests for cursor prefix and box drawing character handling."""

    def test_clean_line_strips_cursor_prefix(self):
        """❯ 1. Yes -> 1. Yes"""
        logger = LineLogger()
        logger.add_data("❯ 1. Yes\n".encode('utf-8'))
        lines = logger.get_all_lines()
        assert len(lines) == 1
        assert lines[0] == "1. Yes"

    def test_clean_line_handles_no_cursor(self):
        """1. Yes -> 1. Yes"""
        logger = LineLogger()
        logger.add_data(b"1. Yes\n")
        lines = logger.get_all_lines()
        assert len(lines) == 1
        assert lines[0] == "1. Yes"

    def test_clean_line_strips_box_drawing(self):
        """───1. Yes -> 1. Yes"""
        logger = LineLogger()
        logger.add_data("───1. Yes\n".encode('utf-8'))
        lines = logger.get_all_lines()
        assert len(lines) == 1
        assert lines[0] == "1. Yes"

    def test_clean_line_strips_multiple_cursors(self):
        """❯❯ 1. Yes -> 1. Yes"""
        logger = LineLogger()
        logger.add_data("❯❯ 1. Yes\n".encode('utf-8'))
        lines = logger.get_all_lines()
        assert len(lines) == 1
        assert lines[0] == "1. Yes"

    def test_clean_line_strips_ascii_arrow(self):
        """> 1. Yes -> 1. Yes"""
        logger = LineLogger()
        logger.add_data(b"> 1. Yes\n")
        lines = logger.get_all_lines()
        assert len(lines) == 1
        assert lines[0] == "1. Yes"


class TestLineLoggerNoiseFiltering:
    """Tests for noise filtering functionality."""

    def test_line_logger_filters_spinner_chars(self):
        """add_data with spinner chars (***) -> not stored."""
        logger = LineLogger()
        logger.add_data(b"***\n")
        lines = logger.get_all_lines()
        assert len(lines) == 0

    def test_line_logger_filters_status_messages(self):
        """add_data with status message (Prestidigitating...) -> not stored."""
        logger = LineLogger()
        logger.add_data(b"Prestidigitating...\n")
        lines = logger.get_all_lines()
        assert len(lines) == 0

    def test_line_logger_filters_token_count(self):
        """add_data with token count (1.7k tokens thinking)) -> not stored."""
        logger = LineLogger()
        logger.add_data(b"1.7k tokens thinking)\n")
        lines = logger.get_all_lines()
        assert len(lines) == 0

    def test_line_logger_keeps_permission_lines(self):
        """add_data with permission option (1. Yes) -> stored."""
        logger = LineLogger()
        logger.add_data(b"1. Yes\n")
        lines = logger.get_all_lines()
        assert len(lines) == 1
        assert lines[0] == "1. Yes"

    def test_line_logger_custom_skip_patterns(self):
        """LineLogger(skip_patterns=[r'^DEBUG']) filters DEBUG: test."""
        logger = LineLogger(skip_patterns=[r'^DEBUG'])
        logger.add_data(b"DEBUG: test\n")
        logger.add_data(b"INFO: test\n")
        lines = logger.get_all_lines()
        assert len(lines) == 1
        assert lines[0] == "INFO: test"


class TestLineLoggerEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_line_logger_very_long_line(self):
        """Handles very long lines without issues."""
        logger = LineLogger()
        long_line = "x" * 10000
        logger.add_data(f"{long_line}\n".encode())

        lines = logger.get_all_lines()
        assert len(lines) == 1
        assert len(lines[0]) == 10000

    def test_line_logger_unicode_content(self):
        """Handles unicode content correctly."""
        logger = LineLogger()
        logger.add_data("Hello 世界 🌍\n".encode('utf-8'))

        lines = logger.get_all_lines()
        assert len(lines) == 1
        assert lines[0] == "Hello 世界 🌍"

    def test_line_logger_mixed_line_endings_in_single_add(self):
        """Handles mixed line endings in a single add_data call."""
        logger = LineLogger()
        logger.add_data(b"line1\nline2\r\nline3\rline4\n")

        lines = logger.get_all_lines()
        assert len(lines) == 4
        assert lines == ["line1", "line2", "line3", "line4"]

    def test_line_logger_partial_line_persists_across_calls(self):
        """Partial line buffer persists across multiple add_data calls."""
        logger = LineLogger()
        logger.add_data(b"start")
        logger.add_data(b" middle")
        logger.add_data(b" end\n")

        lines = logger.get_all_lines()
        assert len(lines) == 1
        assert lines[0] == "start middle end"

    def test_line_logger_ansi_in_partial_line(self):
        """ANSI codes in partial lines are stripped correctly."""
        logger = LineLogger()
        logger.add_data(b"\x1b[31mRed")
        logger.add_data(b" text\x1b[0m\n")

        lines = logger.get_all_lines()
        assert len(lines) == 1
        assert lines[0] == "Red text"


class TestLineLoggerSessionChangeDetection:
    """Tests for session change command detection."""

    def test_detect_compact_command(self):
        """add_data(b"/compact\\n") -> session_change_pending=True."""
        logger = LineLogger()
        assert logger.session_change_pending is False

        logger.add_data(b"/compact\n")
        assert logger.session_change_pending is True

    def test_detect_resume_command(self):
        """add_data(b"/resume\\n") -> session_change_pending=True."""
        logger = LineLogger()
        assert logger.session_change_pending is False

        logger.add_data(b"/resume\n")
        assert logger.session_change_pending is True

    def test_reset_session_change_flag(self):
        """call acknowledge_session_change() -> flag reset to False."""
        logger = LineLogger()
        logger.add_data(b"/compact\n")
        assert logger.session_change_pending is True

        # Acknowledge and verify it was pending
        was_pending = logger.acknowledge_session_change()
        assert was_pending is True
        assert logger.session_change_pending is False

        # Calling again should return False
        was_pending = logger.acknowledge_session_change()
        assert was_pending is False
        assert logger.session_change_pending is False

    def test_compact_detection_case_insensitive(self):
        """add_data(b"/COMPACT\\n") -> detected."""
        logger = LineLogger()
        logger.add_data(b"/COMPACT\n")
        assert logger.session_change_pending is True

        logger = LineLogger()
        logger.add_data(b"/Compact\n")
        assert logger.session_change_pending is True

        logger = LineLogger()
        logger.add_data(b"/Resume\n")
        assert logger.session_change_pending is True

    def test_no_false_positive_compact_in_text(self):
        """add_data(b"discussing /compact command\\n") -> NOT detected (must be start of line)."""
        logger = LineLogger()
        logger.add_data(b"discussing /compact command\n")
        assert logger.session_change_pending is False

        logger = LineLogger()
        logger.add_data(b"The /resume feature is useful\n")
        assert logger.session_change_pending is False

    def test_compact_with_arguments(self):
        """add_data(b"/compact some args\\n") -> detected."""
        logger = LineLogger()
        logger.add_data(b"/compact some args\n")
        assert logger.session_change_pending is True

        logger = LineLogger()
        logger.add_data(b"/resume session123\n")
        assert logger.session_change_pending is True

    def test_session_change_persists_across_multiple_lines(self):
        """Session change flag persists until acknowledged."""
        logger = LineLogger()
        logger.add_data(b"/compact\n")
        assert logger.session_change_pending is True

        # Add more data, flag should still be True
        logger.add_data(b"some other line\n")
        assert logger.session_change_pending is True

        # Only resets when acknowledged
        logger.acknowledge_session_change()
        assert logger.session_change_pending is False

    def test_multiple_session_changes_before_acknowledge(self):
        """Multiple session change commands set flag only once."""
        logger = LineLogger()
        logger.add_data(b"/compact\n")
        logger.add_data(b"/resume\n")
        assert logger.session_change_pending is True

        # Acknowledge once
        was_pending = logger.acknowledge_session_change()
        assert was_pending is True
        assert logger.session_change_pending is False

    def test_session_change_with_ansi_codes(self):
        """Session change commands are detected even with ANSI codes."""
        logger = LineLogger()
        logger.add_data(b"\x1b[31m/compact\x1b[0m\n")
        assert logger.session_change_pending is True

    def test_acknowledge_when_not_pending(self):
        """acknowledge_session_change() when nothing pending returns False."""
        logger = LineLogger()
        was_pending = logger.acknowledge_session_change()
        assert was_pending is False
        assert logger.session_change_pending is False
