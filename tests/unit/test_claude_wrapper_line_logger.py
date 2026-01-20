"""
Unit tests for LineLogger integration in HybridPTYWrapper.

Tests that the wrapper properly initializes, updates, and maintains
the LineLogger for session change detection.
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.claude_wrapper_hybrid import HybridPTYWrapper
from core.line_logger import LineLogger


class TestWrapperCreatesLineLogger:
    """Test that wrapper initializes LineLogger during __init__"""

    def test_wrapper_creates_line_logger(self, tmp_path):
        """Wrapper init creates self.line_logger as LineLogger instance"""

        with patch('core.claude_wrapper_hybrid.setup_logging') as mock_logging:
            mock_logger = Mock()
            mock_logging.return_value = mock_logger

            wrapper = HybridPTYWrapper(
                session_id="test1234",
                project_dir=str(tmp_path),
                claude_args=[]
            )

            # Verify line_logger exists and is correct type
            assert hasattr(wrapper, 'line_logger')
            assert isinstance(wrapper.line_logger, LineLogger)

            # Verify line_logger has expected max_lines
            assert wrapper.line_logger.max_lines == 500


class TestWrapperUpdatesLineLogger:
    """Test that wrapper updates LineLogger when buffer is updated"""

    def test_wrapper_updates_line_logger(self, tmp_path):
        """update_output_buffer(data) calls line_logger.add_data()"""

        with patch('core.claude_wrapper_hybrid.setup_logging') as mock_logging:
            mock_logger = Mock()
            mock_logging.return_value = mock_logger

            wrapper = HybridPTYWrapper(
                session_id="test1234",
                project_dir=str(tmp_path),
                claude_args=[]
            )

            # Mock the line_logger.add_data method
            wrapper.line_logger.add_data = Mock()
            wrapper.line_logger.save_to_file = Mock()

            # Add some data to buffer
            test_data = b"Hello, World!\n"
            wrapper.add_to_output_buffer(test_data)

            # Verify add_data was called with the correct data
            wrapper.line_logger.add_data.assert_called_once_with(test_data)


class TestWrapperWritesLineLogFile:
    """Test that wrapper writes line log file when buffer is updated"""

    def test_wrapper_writes_line_log_file(self, tmp_path):
        """update_output_buffer(data) creates line log file with content"""

        # Create log directory
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        with patch('core.claude_wrapper_hybrid.setup_logging') as mock_logging, \
             patch('core.claude_wrapper_hybrid.LOG_DIR', str(log_dir)):
            mock_logger = Mock()
            mock_logging.return_value = mock_logger

            wrapper = HybridPTYWrapper(
                session_id="test1234",
                project_dir=str(tmp_path),
                claude_args=[]
            )

            # Verify line_log_file path is set correctly
            expected_file = log_dir / "claude_lines_test1234.txt"
            assert wrapper.line_log_file == expected_file

            # Add some data with actual content (including newline)
            test_data = b"Test line 1\nTest line 2\n"
            wrapper.add_to_output_buffer(test_data)

            # Verify file exists
            assert wrapper.line_log_file.exists()

            # Verify file contains expected content (numbered lines)
            content = wrapper.line_log_file.read_text()
            assert "Test line 1" in content
            assert "Test line 2" in content
            # Check for line numbering format (e.g., "   0: Test line 1")
            assert ":" in content


class TestWrapperLineLogPathUsesSessionId:
    """Test that line log file path uses session_id"""

    def test_wrapper_line_log_path_uses_session_id(self, tmp_path):
        """Wrapper with session_id='abc123' creates claude_lines_abc123.txt"""

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        with patch('core.claude_wrapper_hybrid.setup_logging') as mock_logging, \
             patch('core.claude_wrapper_hybrid.LOG_DIR', str(log_dir)):
            mock_logger = Mock()
            mock_logging.return_value = mock_logger

            wrapper = HybridPTYWrapper(
                session_id="abc123",
                project_dir=str(tmp_path),
                claude_args=[]
            )

            # Verify line_log_file path uses session_id
            expected_file = log_dir / "claude_lines_abc123.txt"
            assert wrapper.line_log_file == expected_file


class TestWrapperUpdatesLineLogPathOnSessionChange:
    """Test that line log file path is updated when session changes"""

    def test_wrapper_updates_line_log_path_on_session_change(self, tmp_path):
        """update_buffer_file_path(new_id) updates line_log_file path"""

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        with patch('core.claude_wrapper_hybrid.setup_logging') as mock_logging, \
             patch('core.claude_wrapper_hybrid.LOG_DIR', str(log_dir)):
            mock_logger = Mock()
            mock_logging.return_value = mock_logger

            wrapper = HybridPTYWrapper(
                session_id="old123",
                project_dir=str(tmp_path),
                claude_args=[]
            )

            # Mock registry to avoid real socket connections
            wrapper.registry = Mock()
            wrapper.registry.available = False

            # Initial line log file path
            initial_path = wrapper.line_log_file
            assert "old123" in str(initial_path)

            # Update to new session ID
            new_session_id = "new456"
            wrapper.update_buffer_file_path(new_session_id)

            # Verify line_log_file path was updated
            assert wrapper.line_log_file == log_dir / f"claude_lines_{new_session_id}.txt"
            assert "new456" in str(wrapper.line_log_file)
