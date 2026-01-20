"""
Unit tests for session change detection and handling in HybridPTYWrapper.

Tests the integration of LineLogger session change detection with the
wrapper's session discovery and registry update logic.
"""

import os
import sys
import time
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, PropertyMock

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.claude_wrapper_hybrid import HybridPTYWrapper
from core.line_logger import LineLogger
from core.session_discovery import find_active_session


class TestWrapperDetectsSessionChangeFlag:
    """Test that wrapper detects session_change_pending flag from LineLogger"""

    def test_wrapper_detects_session_change_flag(self, tmp_path):
        """When line_logger.session_change_pending=True, wrapper calls handle_session_change()"""

        # Create minimal wrapper instance
        with patch('core.claude_wrapper_hybrid.setup_logging') as mock_logging:
            mock_logger = Mock()
            mock_logging.return_value = mock_logger

            wrapper = HybridPTYWrapper(
                session_id="test1234",
                project_dir=str(tmp_path),
                claude_args=[]
            )

            # Mock the registry to avoid real socket connections
            wrapper.registry = Mock()
            wrapper.registry.available = False

            # Mock _handle_session_change to verify it's called
            wrapper._handle_session_change = Mock()

            # Set session change pending flag
            wrapper.line_logger.session_change_pending = True

            # Call _check_session_change
            wrapper._check_session_change()

            # Verify _handle_session_change was called
            wrapper._handle_session_change.assert_called_once()

    def test_wrapper_ignores_no_session_change(self, tmp_path):
        """When session_change_pending=False, wrapper does not call handler"""

        with patch('core.claude_wrapper_hybrid.setup_logging') as mock_logging:
            mock_logger = Mock()
            mock_logging.return_value = mock_logger

            wrapper = HybridPTYWrapper(
                session_id="test1234",
                project_dir=str(tmp_path),
                claude_args=[]
            )

            wrapper.registry = Mock()
            wrapper.registry.available = False

            # Mock _handle_session_change
            wrapper._handle_session_change = Mock()

            # Session change flag is False by default
            assert wrapper.line_logger.session_change_pending is False

            # Call _check_session_change
            wrapper._check_session_change()

            # Verify _handle_session_change was NOT called
            wrapper._handle_session_change.assert_not_called()


class TestHandleSessionChangeDiscoversNewSession:
    """Test that _handle_session_change discovers new session ID"""

    def test_handle_session_change_discovers_new_session(self, tmp_path):
        """When new buffer file exists, new session_id is discovered"""

        # Create log directory
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        with patch('core.claude_wrapper_hybrid.setup_logging') as mock_logging:
            mock_logger = Mock()
            mock_logging.return_value = mock_logger

            with patch('core.claude_wrapper_hybrid.LOG_DIR', str(log_dir)):
                wrapper = HybridPTYWrapper(
                    session_id="old12345",
                    project_dir=str(tmp_path),
                    claude_args=[]
                )

                wrapper.registry = Mock()
                wrapper.registry.available = False
                wrapper.claude_session_uuid = "old-uuid-1234"
                wrapper.log_dir = log_dir

                # Set session change pending
                wrapper.line_logger.session_change_pending = True

                # Create a new buffer file with more recent timestamp
                old_buffer = log_dir / "claude_output_old-uuid-1234.txt"
                new_buffer = log_dir / "claude_output_new-uuid-5678.txt"

                old_buffer.write_text("old output")
                time.sleep(0.01)  # Ensure different mtime
                new_buffer.write_text("new output")

                # Mock update_buffer_file_path to avoid file operations
                wrapper.update_buffer_file_path = Mock()

                # Call _handle_session_change
                wrapper._handle_session_change()

                # Verify new session ID was discovered
                assert wrapper.claude_session_uuid == "new-uuid-5678"

                # Verify buffer file path was updated
                wrapper.update_buffer_file_path.assert_called_once_with("new-uuid-5678")

    def test_handle_session_change_no_new_session(self, tmp_path):
        """When no new buffer file exists, session ID unchanged"""

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        with patch('core.claude_wrapper_hybrid.setup_logging') as mock_logging:
            mock_logger = Mock()
            mock_logging.return_value = mock_logger

            with patch('core.claude_wrapper_hybrid.LOG_DIR', str(log_dir)):
                wrapper = HybridPTYWrapper(
                    session_id="test1234",
                    project_dir=str(tmp_path),
                    claude_args=[]
                )

                wrapper.registry = Mock()
                wrapper.registry.available = False
                wrapper.claude_session_uuid = "old-uuid-1234"
                wrapper.log_dir = log_dir

                # Set session change pending
                wrapper.line_logger.session_change_pending = True

                # No buffer files exist

                # Call _handle_session_change
                wrapper._handle_session_change()

                # Verify session ID unchanged
                assert wrapper.claude_session_uuid == "old-uuid-1234"


class TestHandleSessionChangeUpdatesRegistry:
    """Test that session change updates registry with new session ID"""

    def test_handle_session_change_updates_registry(self, tmp_path):
        """Session change with new ID updates registry"""

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        with patch('core.claude_wrapper_hybrid.setup_logging') as mock_logging:
            mock_logger = Mock()
            mock_logging.return_value = mock_logger

            with patch('core.claude_wrapper_hybrid.LOG_DIR', str(log_dir)):
                wrapper = HybridPTYWrapper(
                    session_id="wrapper123",
                    project_dir=str(tmp_path),
                    claude_args=[]
                )

                # Mock registry
                wrapper.registry = Mock()
                wrapper.registry.available = True
                wrapper.claude_session_uuid = "old-uuid-1234"
                wrapper.log_dir = log_dir
                wrapper.thread_ts = "1234567890.123456"
                wrapper.channel = "C123456"

                # Mock registry responses
                old_session_data = {
                    "session_id": "old-uuid-1234",
                    "project": "test-project",
                    "project_dir": str(tmp_path),
                    "terminal": "test-terminal",
                    "socket_path": "/tmp/test.sock",
                    "slack_thread_ts": "1234567890.123456",
                    "slack_channel": "C123456",
                    "permissions_channel": "C789012",
                    "slack_user_id": "U123456",
                    "reply_to_ts": "1234567890.111111",
                    "todo_message_ts": "1234567890.222222"
                }

                wrapper.registry._send_command = Mock(side_effect=[
                    {"success": True, "session": old_session_data},  # GET response
                    {"success": True}  # REGISTER_EXISTING response
                ])

                # Set session change pending
                wrapper.line_logger.session_change_pending = True

                # Create new buffer file
                old_buffer = log_dir / "claude_output_old-uuid-1234.txt"
                new_buffer = log_dir / "claude_output_new-uuid-5678.txt"
                old_buffer.write_text("old")
                time.sleep(0.01)
                new_buffer.write_text("new")

                # Mock update_buffer_file_path
                wrapper.update_buffer_file_path = Mock()
                wrapper.buffer_file = str(log_dir / "claude_output_new-uuid-5678.txt")

                # Call _handle_session_change
                wrapper._handle_session_change()

                # Verify registry was called to GET old session
                get_call = wrapper.registry._send_command.call_args_list[0]
                assert get_call[0][0] == "GET"
                assert get_call[0][1]["session_id"] == "old-uuid-1234"

                # Verify registry was called to REGISTER_EXISTING with new session
                register_call = wrapper.registry._send_command.call_args_list[1]
                assert register_call[0][0] == "REGISTER_EXISTING"
                register_data = register_call[0][1]["data"]
                assert register_data["session_id"] == "new-uuid-5678"
                assert register_data["thread_ts"] == "1234567890.123456"
                assert register_data["channel"] == "C123456"


class TestHandleSessionChangeUpdatesBufferPaths:
    """Test that session change updates buffer file and line log file paths"""

    def test_session_change_updates_buffer_paths(self, tmp_path):
        """Session change updates buffer_file and line_log_file paths"""

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        with patch('core.claude_wrapper_hybrid.setup_logging') as mock_logging:
            mock_logger = Mock()
            mock_logging.return_value = mock_logger

            with patch('core.claude_wrapper_hybrid.LOG_DIR', str(log_dir)):
                wrapper = HybridPTYWrapper(
                    session_id="wrapper123",
                    project_dir=str(tmp_path),
                    claude_args=[]
                )

                wrapper.registry = Mock()
                wrapper.registry.available = False
                wrapper.claude_session_uuid = "old-uuid-1234"
                wrapper.log_dir = log_dir

                # Set initial paths
                old_line_log = wrapper.line_log_file
                assert "old-uuid-1234" not in str(old_line_log)  # Uses wrapper session initially

                # Set session change pending
                wrapper.line_logger.session_change_pending = True

                # Create new buffer file
                old_buffer = log_dir / "claude_output_old-uuid-1234.txt"
                new_buffer = log_dir / "claude_output_new-uuid-5678.txt"
                old_buffer.write_text("old")
                time.sleep(0.01)
                new_buffer.write_text("new")

                # Mock update_buffer_file_path to avoid complex file operations
                # but track that it was called
                original_update = wrapper.update_buffer_file_path
                wrapper.update_buffer_file_path = Mock(side_effect=lambda sid: (
                    setattr(wrapper, 'buffer_file', str(log_dir / f"claude_output_{sid}.txt"))
                ))

                # Call _handle_session_change
                wrapper._handle_session_change()

                # Verify buffer_file was updated
                assert "new-uuid-5678" in wrapper.buffer_file

                # Verify line_log_file was updated
                assert "new-uuid-5678" in str(wrapper.line_log_file)


class TestSessionChangePreservesSlackThread:
    """Test that session change preserves slack_thread_ts in registry"""

    def test_session_change_preserves_slack_thread(self, tmp_path):
        """Session change preserves slack_thread_ts in registry update"""

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        with patch('core.claude_wrapper_hybrid.setup_logging') as mock_logging:
            mock_logger = Mock()
            mock_logging.return_value = mock_logger

            with patch('core.claude_wrapper_hybrid.LOG_DIR', str(log_dir)):
                wrapper = HybridPTYWrapper(
                    session_id="wrapper123",
                    project_dir=str(tmp_path),
                    claude_args=[]
                )

                # Mock registry
                wrapper.registry = Mock()
                wrapper.registry.available = True
                wrapper.claude_session_uuid = "old-uuid-1234"
                wrapper.log_dir = log_dir
                wrapper.thread_ts = "1234567890.123456"
                wrapper.channel = "C123456"

                # Original session data with Slack thread
                original_thread_ts = "1234567890.123456"
                original_channel = "C123456"

                old_session_data = {
                    "session_id": "old-uuid-1234",
                    "project": "test-project",
                    "project_dir": str(tmp_path),
                    "terminal": "test-terminal",
                    "socket_path": "/tmp/test.sock",
                    "slack_thread_ts": original_thread_ts,
                    "slack_channel": original_channel,
                    "permissions_channel": "C789012",
                    "slack_user_id": "U123456",
                    "reply_to_ts": "1234567890.111111",
                    "todo_message_ts": "1234567890.222222"
                }

                # Track registry calls
                registry_calls = []

                def mock_send_command(cmd, data):
                    registry_calls.append((cmd, data))
                    if cmd == "GET":
                        return {"success": True, "session": old_session_data}
                    elif cmd == "REGISTER_EXISTING":
                        return {"success": True}
                    return {"success": False}

                wrapper.registry._send_command = mock_send_command

                # Set session change pending
                wrapper.line_logger.session_change_pending = True

                # Create new buffer file
                old_buffer = log_dir / "claude_output_old-uuid-1234.txt"
                new_buffer = log_dir / "claude_output_new-uuid-5678.txt"
                old_buffer.write_text("old")
                time.sleep(0.01)
                new_buffer.write_text("new")

                # Mock update_buffer_file_path
                wrapper.update_buffer_file_path = Mock()
                wrapper.buffer_file = str(log_dir / "claude_output_new-uuid-5678.txt")

                # Call _handle_session_change
                wrapper._handle_session_change()

                # Find REGISTER_EXISTING call
                register_call = None
                for cmd, data in registry_calls:
                    if cmd == "REGISTER_EXISTING":
                        register_call = data
                        break

                assert register_call is not None, "REGISTER_EXISTING not called"

                # Verify thread_ts and channel were preserved
                register_data = register_call["data"]
                assert register_data["thread_ts"] == original_thread_ts, \
                    f"Expected thread_ts {original_thread_ts}, got {register_data['thread_ts']}"
                assert register_data["channel"] == original_channel, \
                    f"Expected channel {original_channel}, got {register_data['channel']}"

                # Verify new session_id was set
                assert register_data["session_id"] == "new-uuid-5678"

    def test_session_change_preserves_all_metadata(self, tmp_path):
        """Session change preserves all Slack metadata fields"""

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        with patch('core.claude_wrapper_hybrid.setup_logging') as mock_logging:
            mock_logger = Mock()
            mock_logging.return_value = mock_logger

            with patch('core.claude_wrapper_hybrid.LOG_DIR', str(log_dir)):
                wrapper = HybridPTYWrapper(
                    session_id="wrapper123",
                    project_dir=str(tmp_path),
                    claude_args=[]
                )

                # Mock registry
                wrapper.registry = Mock()
                wrapper.registry.available = True
                wrapper.claude_session_uuid = "old-uuid-1234"
                wrapper.log_dir = log_dir
                wrapper.thread_ts = "1234567890.123456"
                wrapper.channel = "C123456"

                # Complete session data
                old_session_data = {
                    "session_id": "old-uuid-1234",
                    "project": "test-project",
                    "project_dir": str(tmp_path),
                    "terminal": "test-terminal",
                    "socket_path": "/tmp/test.sock",
                    "slack_thread_ts": "1234567890.123456",
                    "slack_channel": "C123456",
                    "permissions_channel": "C789012",
                    "slack_user_id": "U123456",
                    "reply_to_ts": "1234567890.111111",
                    "todo_message_ts": "1234567890.222222"
                }

                registry_calls = []

                def mock_send_command(cmd, data):
                    registry_calls.append((cmd, data))
                    if cmd == "GET":
                        return {"success": True, "session": old_session_data}
                    elif cmd == "REGISTER_EXISTING":
                        return {"success": True}
                    return {"success": False}

                wrapper.registry._send_command = mock_send_command

                # Set session change pending
                wrapper.line_logger.session_change_pending = True

                # Create new buffer file
                old_buffer = log_dir / "claude_output_old-uuid-1234.txt"
                new_buffer = log_dir / "claude_output_new-uuid-5678.txt"
                old_buffer.write_text("old")
                time.sleep(0.01)
                new_buffer.write_text("new")

                # Mock update_buffer_file_path
                wrapper.update_buffer_file_path = Mock()
                wrapper.buffer_file = str(log_dir / "claude_output_new-uuid-5678.txt")

                # Call _handle_session_change
                wrapper._handle_session_change()

                # Find REGISTER_EXISTING call
                register_call = None
                for cmd, data in registry_calls:
                    if cmd == "REGISTER_EXISTING":
                        register_call = data
                        break

                assert register_call is not None
                register_data = register_call["data"]

                # Verify all metadata fields preserved
                assert register_data["permissions_channel"] == "C789012"
                assert register_data["slack_user_id"] == "U123456"
                assert register_data["reply_to_ts"] == "1234567890.111111"
                assert register_data["todo_message_ts"] == "1234567890.222222"
                assert register_data["project"] == "test-project"
                assert register_data["project_dir"] == str(tmp_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
