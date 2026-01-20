"""
End-to-end tests for session change handling.

Tests the complete flow: /compact or /resume -> detection -> discovery -> registry update.
Verifies that Slack routing continues working after session changes.
"""

import json
import os
import sys
import time
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, ANY, call
import pytest
import uuid

# Add paths for imports
CLAUDE_SLACK_DIR = Path(__file__).parent.parent.parent
CORE_DIR = CLAUDE_SLACK_DIR / "core"
sys.path.insert(0, str(CORE_DIR))

from line_logger import LineLogger
from session_discovery import find_active_session, extract_session_id_from_filename
from registry_db import RegistryDatabase


class TestSessionChangeE2E:
    """End-to-end tests for session change handling."""

    @pytest.fixture
    def temp_log_dir(self, tmp_path):
        """Create temporary log directory for buffer files."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        return log_dir

    @pytest.fixture
    def temp_registry_db(self, tmp_path):
        """Create temporary registry database."""
        db_path = tmp_path / "test_registry.db"
        db = RegistryDatabase(str(db_path))
        return db

    @pytest.fixture
    def mock_registry_client(self):
        """Mock registry client for wrapper."""
        client = MagicMock()
        client.available = True
        client.thread_ts = "1234567890.123456"
        client.channel = "C123456"

        # Mock _send_command to return session data
        def mock_send_command(command, data=None):
            if command == "GET":
                # Return old session data with Slack metadata
                return {
                    "success": True,
                    "session": {
                        "session_id": data.get("session_id"),
                        "project": "test-project",
                        "project_dir": "/test/project",
                        "terminal": "test-terminal",
                        "socket_path": "/tmp/test.sock",
                        "slack_thread_ts": "1234567890.123456",
                        "slack_channel": "C123456",
                        "permissions_channel": None,
                        "slack_user_id": "U123456",
                        "reply_to_ts": None,
                        "todo_message_ts": None,
                        "buffer_file_path": "/tmp/logs/claude_output_old-session.txt"
                    }
                }
            elif command == "REGISTER_EXISTING":
                # Successful registration
                return {"success": True}
            elif command == "UPDATE":
                return {"success": True}
            return {"success": False}

        client._send_command = mock_send_command
        return client

    @pytest.fixture
    def create_buffer_file(self, temp_log_dir):
        """Helper to create buffer files with modification times."""
        def _create(session_id: str, mtime_offset: float = 0) -> Path:
            """
            Create a buffer file for the given session.

            Args:
                session_id: Session ID for the buffer file
                mtime_offset: Offset in seconds from current time (negative = older)

            Returns:
                Path to created buffer file
            """
            buffer_file = temp_log_dir / f"claude_output_{session_id}.txt"
            buffer_file.write_text("test output")

            # Set modification time
            if mtime_offset != 0:
                current_time = time.time()
                new_time = current_time + mtime_offset
                os.utime(buffer_file, (new_time, new_time))

            return buffer_file

        return _create

    def test_e2e_compact_preserves_slack_routing(
        self,
        temp_log_dir,
        temp_registry_db,
        mock_registry_client,
        create_buffer_file
    ):
        """
        Full flow test for /compact:
        1. Start with old session registered
        2. LineLogger detects /compact command
        3. New buffer file is created (simulating Claude's new session)
        4. Session change is detected
        5. New session is discovered from buffer files
        6. Registry is updated with new session_id
        7. Slack thread_ts is preserved
        """
        # Step 1: Register old session in database
        old_session_id = "old-session-abc123"
        old_session_data = {
            "session_id": old_session_id,
            "project": "test-project",
            "project_dir": "/test/project",
            "terminal": "test-terminal",
            "socket_path": "/tmp/test.sock",
            "thread_ts": "1234567890.123456",
            "channel": "C123456",
            "slack_user_id": "U123456",
            "buffer_file_path": str(temp_log_dir / f"claude_output_{old_session_id}.txt")
        }
        temp_registry_db.create_session(old_session_data)

        # Create old buffer file
        create_buffer_file(old_session_id, mtime_offset=-10)  # 10 seconds old

        # Step 2: Create LineLogger and simulate /compact command
        line_logger = LineLogger(max_lines=500)

        # Simulate terminal output with /compact command
        output = b"/compact\r\nCompacting conversation...\r\n"
        line_logger.add_data(output)

        # Verify session change was detected
        assert line_logger.session_change_pending is True

        # Step 3: Simulate new session creation (Claude creates new buffer file)
        # Wait a bit to ensure different mtime
        time.sleep(0.1)
        new_session_id = str(uuid.uuid4())
        create_buffer_file(new_session_id, mtime_offset=0)  # Most recent

        # Step 4: Simulate wrapper's session change handler
        # This is what _handle_session_change does

        # Acknowledge the session change
        was_pending = line_logger.acknowledge_session_change()
        assert was_pending is True
        assert line_logger.session_change_pending is False

        # Step 5: Discover new session ID from buffer files
        discovered_session_id = find_active_session(temp_log_dir)
        assert discovered_session_id == new_session_id

        # Step 6: Register new session in database with preserved Slack metadata
        # Get old session data
        old_entry = temp_registry_db.get_session(old_session_id)
        assert old_entry is not None

        # Register new session with same Slack metadata
        new_session_data = {
            "session_id": new_session_id,
            "project": old_entry["project"],
            "project_dir": old_entry["project_dir"],
            "terminal": old_entry["terminal"],
            "socket_path": old_entry["socket_path"],
            "thread_ts": old_entry["thread_ts"],  # Preserved!
            "channel": old_entry["channel"],  # Preserved!
            "permissions_channel": old_entry["permissions_channel"],
            "slack_user_id": old_entry["slack_user_id"],
            "buffer_file_path": str(temp_log_dir / f"claude_output_{new_session_id}.txt")
        }
        temp_registry_db.create_session(new_session_data)

        # Step 7: Verify new session is registered with preserved Slack thread
        new_entry = temp_registry_db.get_session(new_session_id)
        assert new_entry is not None
        assert new_entry["thread_ts"] == old_entry["thread_ts"]  # Same thread!
        assert new_entry["channel"] == old_entry["channel"]  # Same channel!
        assert new_entry["session_id"] == new_session_id  # New session ID
        assert new_entry["buffer_file_path"] == str(temp_log_dir / f"claude_output_{new_session_id}.txt")

    def test_e2e_resume_preserves_slack_routing(
        self,
        temp_log_dir,
        temp_registry_db,
        create_buffer_file
    ):
        """
        Full flow test for /resume:
        1. Start with old session registered
        2. LineLogger detects /resume command
        3. Resumed session buffer file exists
        4. Session change is detected
        5. Resumed session is discovered
        6. Registry is updated with resumed session_id
        7. Slack thread_ts is preserved
        """
        # Step 1: Register old session
        old_session_id = "old-session-xyz789"
        old_session_data = {
            "session_id": old_session_id,
            "project": "test-project",
            "project_dir": "/test/project",
            "terminal": "test-terminal",
            "socket_path": "/tmp/test.sock",
            "thread_ts": "9876543210.654321",
            "channel": "C654321",
            "slack_user_id": "U654321",
            "buffer_file_path": str(temp_log_dir / f"claude_output_{old_session_id}.txt")
        }
        temp_registry_db.create_session(old_session_data)
        create_buffer_file(old_session_id, mtime_offset=-20)

        # Step 2: Simulate /resume command
        line_logger = LineLogger(max_lines=500)
        resume_output = b"/resume abc123\r\nResuming session abc123...\r\n"
        line_logger.add_data(resume_output)

        assert line_logger.session_change_pending is True

        # Step 3: Create resumed session buffer file
        time.sleep(0.1)
        resumed_session_id = "abc123-resumed-uuid"
        create_buffer_file(resumed_session_id, mtime_offset=0)

        # Step 4-7: Same flow as compact test
        was_pending = line_logger.acknowledge_session_change()
        assert was_pending is True

        discovered_session_id = find_active_session(temp_log_dir)
        assert discovered_session_id == resumed_session_id

        # Register new session with preserved metadata
        old_entry = temp_registry_db.get_session(old_session_id)
        new_session_data = {
            "session_id": resumed_session_id,
            "project": old_entry["project"],
            "project_dir": old_entry["project_dir"],
            "terminal": old_entry["terminal"],
            "socket_path": old_entry["socket_path"],
            "thread_ts": old_entry["thread_ts"],
            "channel": old_entry["channel"],
            "permissions_channel": old_entry["permissions_channel"],
            "slack_user_id": old_entry["slack_user_id"],
            "buffer_file_path": str(temp_log_dir / f"claude_output_{resumed_session_id}.txt")
        }
        temp_registry_db.create_session(new_session_data)

        # Verify preservation
        new_entry = temp_registry_db.get_session(resumed_session_id)
        assert new_entry["thread_ts"] == old_entry["thread_ts"]
        assert new_entry["channel"] == old_entry["channel"]
        assert new_entry["session_id"] == resumed_session_id

    def test_e2e_session_change_updates_buffer_paths(
        self,
        temp_log_dir,
        temp_registry_db,
        create_buffer_file
    ):
        """
        Verify buffer file paths are updated when session changes.

        Tests:
        1. Old session has buffer file path in registry
        2. After /compact, new session has updated buffer file path
        3. Path points to new session's buffer file
        """
        # Setup old session
        old_session_id = "session-old-123"
        old_buffer_path = str(temp_log_dir / f"claude_output_{old_session_id}.txt")

        old_session_data = {
            "session_id": old_session_id,
            "project": "test-project",
            "project_dir": "/test/project",
            "terminal": "test-terminal",
            "socket_path": "/tmp/test.sock",
            "thread_ts": "1111111111.111111",
            "channel": "C111111",
            "slack_user_id": "U111111",
            "buffer_file_path": old_buffer_path
        }
        temp_registry_db.create_session(old_session_data)
        create_buffer_file(old_session_id, mtime_offset=-5)

        # Verify old buffer path
        old_entry = temp_registry_db.get_session(old_session_id)
        assert old_entry["buffer_file_path"] == old_buffer_path

        # Simulate session change
        line_logger = LineLogger(max_lines=500)
        line_logger.add_data(b"/compact\r\n")
        assert line_logger.session_change_pending is True

        # Create new session buffer
        time.sleep(0.1)
        new_session_id = str(uuid.uuid4())
        new_buffer_path = str(create_buffer_file(new_session_id, mtime_offset=0))

        # Discover and register new session
        line_logger.acknowledge_session_change()
        discovered_session_id = find_active_session(temp_log_dir)
        assert discovered_session_id == new_session_id

        # Update buffer path in registry
        old_entry = temp_registry_db.get_session(old_session_id)
        new_session_data = {
            "session_id": new_session_id,
            "project": old_entry["project"],
            "project_dir": old_entry["project_dir"],
            "terminal": old_entry["terminal"],
            "socket_path": old_entry["socket_path"],
            "thread_ts": old_entry["thread_ts"],
            "channel": old_entry["channel"],
            "permissions_channel": old_entry["permissions_channel"],
            "slack_user_id": old_entry["slack_user_id"],
            "buffer_file_path": new_buffer_path
        }
        temp_registry_db.create_session(new_session_data)

        # Verify new buffer path
        new_entry = temp_registry_db.get_session(new_session_id)
        assert new_entry["buffer_file_path"] == new_buffer_path
        assert new_entry["buffer_file_path"] != old_buffer_path

        # Verify file actually exists
        assert Path(new_entry["buffer_file_path"]).exists()

    def test_e2e_multiple_session_changes(
        self,
        temp_log_dir,
        temp_registry_db,
        create_buffer_file
    ):
        """
        Test multiple /compact commands in sequence.

        Simulates:
        1. Initial session -> /compact -> Session 2
        2. Session 2 -> /compact -> Session 3
        3. Verify Slack thread preserved through all changes
        """
        # Initial session
        session_1_id = "session-1-" + str(uuid.uuid4())[:8]
        thread_ts = "1234567890.123456"
        channel = "C123456"
        user_id = "U123456"

        session_1_data = {
            "session_id": session_1_id,
            "project": "multi-compact-test",
            "project_dir": "/test/multi",
            "terminal": "test-terminal",
            "socket_path": "/tmp/test1.sock",
            "thread_ts": thread_ts,
            "channel": channel,
            "slack_user_id": user_id,
            "buffer_file_path": str(temp_log_dir / f"claude_output_{session_1_id}.txt")
        }
        temp_registry_db.create_session(session_1_data)
        create_buffer_file(session_1_id, mtime_offset=-10)

        # First /compact: Session 1 -> Session 2
        line_logger_1 = LineLogger(max_lines=500)
        line_logger_1.add_data(b"/compact\r\n")
        assert line_logger_1.session_change_pending is True

        time.sleep(0.1)
        session_2_id = "session-2-" + str(uuid.uuid4())[:8]
        create_buffer_file(session_2_id, mtime_offset=0)

        line_logger_1.acknowledge_session_change()
        discovered_2 = find_active_session(temp_log_dir)
        assert discovered_2 == session_2_id

        # Register session 2 with preserved metadata
        session_1_entry = temp_registry_db.get_session(session_1_id)
        session_2_data = {
            "session_id": session_2_id,
            "project": session_1_entry["project"],
            "project_dir": session_1_entry["project_dir"],
            "terminal": session_1_entry["terminal"],
            "socket_path": session_1_entry["socket_path"],
            "thread_ts": session_1_entry["thread_ts"],
            "channel": session_1_entry["channel"],
            "permissions_channel": session_1_entry["permissions_channel"],
            "slack_user_id": session_1_entry["slack_user_id"],
            "buffer_file_path": str(temp_log_dir / f"claude_output_{session_2_id}.txt")
        }
        temp_registry_db.create_session(session_2_data)

        session_2_entry = temp_registry_db.get_session(session_2_id)
        assert session_2_entry["thread_ts"] == thread_ts
        assert session_2_entry["channel"] == channel

        # Second /compact: Session 2 -> Session 3
        line_logger_2 = LineLogger(max_lines=500)
        line_logger_2.add_data(b"/compact\r\nCompacting again...\r\n")
        assert line_logger_2.session_change_pending is True

        time.sleep(0.1)
        session_3_id = "session-3-" + str(uuid.uuid4())[:8]
        create_buffer_file(session_3_id, mtime_offset=0)

        line_logger_2.acknowledge_session_change()
        discovered_3 = find_active_session(temp_log_dir)
        assert discovered_3 == session_3_id

        # Register session 3 with preserved metadata
        session_2_entry = temp_registry_db.get_session(session_2_id)
        session_3_data = {
            "session_id": session_3_id,
            "project": session_2_entry["project"],
            "project_dir": session_2_entry["project_dir"],
            "terminal": session_2_entry["terminal"],
            "socket_path": session_2_entry["socket_path"],
            "thread_ts": session_2_entry["thread_ts"],
            "channel": session_2_entry["channel"],
            "permissions_channel": session_2_entry["permissions_channel"],
            "slack_user_id": session_2_entry["slack_user_id"],
            "buffer_file_path": str(temp_log_dir / f"claude_output_{session_3_id}.txt")
        }
        temp_registry_db.create_session(session_3_data)

        # Verify thread preserved through both compactions
        session_3_entry = temp_registry_db.get_session(session_3_id)
        assert session_3_entry["thread_ts"] == thread_ts  # Same as original!
        assert session_3_entry["channel"] == channel  # Same as original!
        assert session_3_entry["session_id"] == session_3_id  # But new session ID

        # Verify all three sessions exist in registry
        assert temp_registry_db.get_session(session_1_id) is not None
        assert temp_registry_db.get_session(session_2_id) is not None
        assert temp_registry_db.get_session(session_3_id) is not None

    def test_line_logger_detects_compact_case_insensitive(self):
        """Test that /compact detection is case-insensitive."""
        line_logger = LineLogger(max_lines=500)

        # Test various cases
        test_cases = [
            b"/compact\r\n",
            b"/COMPACT\r\n",
            b"/Compact\r\n",
            b"/CoMpAcT\r\n"
        ]

        for test_input in test_cases:
            line_logger = LineLogger(max_lines=500)  # Fresh logger
            line_logger.add_data(test_input)
            assert line_logger.session_change_pending is True, f"Failed for: {test_input}"
            line_logger.acknowledge_session_change()

    def test_line_logger_detects_resume_case_insensitive(self):
        """Test that /resume detection is case-insensitive."""
        line_logger = LineLogger(max_lines=500)

        # Test various cases
        test_cases = [
            b"/resume\r\n",
            b"/RESUME abc123\r\n",
            b"/Resume\r\n",
            b"/ReSuMe session-id\r\n"
        ]

        for test_input in test_cases:
            line_logger = LineLogger(max_lines=500)  # Fresh logger
            line_logger.add_data(test_input)
            assert line_logger.session_change_pending is True, f"Failed for: {test_input}"
            line_logger.acknowledge_session_change()

    def test_session_discovery_finds_most_recent(
        self,
        temp_log_dir,
        create_buffer_file
    ):
        """
        Test that session discovery finds the most recently modified buffer file.
        """
        # Create multiple buffer files with different modification times
        old_session = "old-" + str(uuid.uuid4())[:8]
        medium_session = "medium-" + str(uuid.uuid4())[:8]
        newest_session = "newest-" + str(uuid.uuid4())[:8]

        create_buffer_file(old_session, mtime_offset=-100)
        create_buffer_file(medium_session, mtime_offset=-50)
        create_buffer_file(newest_session, mtime_offset=0)

        # Discovery should find the newest
        discovered = find_active_session(temp_log_dir)
        assert discovered == newest_session

    def test_extract_session_id_from_filename(self):
        """Test session ID extraction from buffer filenames."""
        # Valid patterns
        assert extract_session_id_from_filename("claude_output_abc123.txt") == "abc123"
        assert extract_session_id_from_filename("claude_output_e537eb3d-1234-5678-abcd-ef1234567890.txt") == "e537eb3d-1234-5678-abcd-ef1234567890"
        assert extract_session_id_from_filename("claude_lines_test-session.txt") == "test-session"

        # Invalid patterns
        assert extract_session_id_from_filename("debug.log") is None
        assert extract_session_id_from_filename("claude_output_.txt") is None
        assert extract_session_id_from_filename("random_file.txt") is None

    def test_session_change_with_no_new_buffer_file(
        self,
        temp_log_dir
    ):
        """
        Test handling when /compact is detected but no new buffer file exists yet.

        This simulates the race condition where the command is detected
        before Claude creates the new session file.
        """
        line_logger = LineLogger(max_lines=500)
        line_logger.add_data(b"/compact\r\n")
        assert line_logger.session_change_pending is True

        # Try to discover new session (should return None - no files)
        discovered = find_active_session(temp_log_dir)
        assert discovered is None

        # In real wrapper, this would wait briefly and retry
        # For now just verify None is returned gracefully

    def test_registry_update_preserves_all_metadata(
        self,
        temp_log_dir,
        temp_registry_db,
        create_buffer_file
    ):
        """
        Test that ALL session metadata is preserved during session change,
        not just thread_ts and channel.
        """
        old_session_id = "metadata-test-old"

        # Create session with all metadata fields populated
        full_session_data = {
            "session_id": old_session_id,
            "project": "metadata-project",
            "project_dir": "/test/metadata",
            "terminal": "test-terminal",
            "socket_path": "/tmp/metadata.sock",
            "thread_ts": "1111111111.111111",
            "channel": "C111111",
            "permissions_channel": "C222222",
            "slack_user_id": "U333333",
            "reply_to_ts": "4444444444.444444",
            "todo_message_ts": "5555555555.555555",
            "buffer_file_path": str(temp_log_dir / f"claude_output_{old_session_id}.txt")
        }
        temp_registry_db.create_session(full_session_data)
        create_buffer_file(old_session_id, mtime_offset=-5)

        # Simulate session change
        line_logger = LineLogger(max_lines=500)
        line_logger.add_data(b"/compact\r\n")
        line_logger.acknowledge_session_change()

        # Create new session
        time.sleep(0.1)
        new_session_id = str(uuid.uuid4())
        create_buffer_file(new_session_id, mtime_offset=0)

        # Discover and register with preserved metadata
        discovered = find_active_session(temp_log_dir)
        assert discovered == new_session_id

        old_entry = temp_registry_db.get_session(old_session_id)

        # Preserve ALL metadata
        new_session_data = {
            "session_id": new_session_id,
            "project": old_entry["project"],
            "project_dir": old_entry["project_dir"],
            "terminal": old_entry["terminal"],
            "socket_path": old_entry["socket_path"],
            "thread_ts": old_entry["thread_ts"],
            "channel": old_entry["channel"],
            "permissions_channel": old_entry["permissions_channel"],
            "slack_user_id": old_entry["slack_user_id"],
            "reply_to_ts": old_entry["reply_to_ts"],
            "todo_message_ts": old_entry["todo_message_ts"],
            "buffer_file_path": str(temp_log_dir / f"claude_output_{new_session_id}.txt")
        }
        temp_registry_db.create_session(new_session_data)

        # Verify ALL fields preserved
        new_entry = temp_registry_db.get_session(new_session_id)
        assert new_entry["thread_ts"] == old_entry["thread_ts"]
        assert new_entry["channel"] == old_entry["channel"]
        assert new_entry["permissions_channel"] == old_entry["permissions_channel"]
        assert new_entry["slack_user_id"] == old_entry["slack_user_id"]
        assert new_entry["reply_to_ts"] == old_entry["reply_to_ts"]
        assert new_entry["todo_message_ts"] == old_entry["todo_message_ts"]
        assert new_entry["project"] == old_entry["project"]
        assert new_entry["project_dir"] == old_entry["project_dir"]

        # Only session_id and buffer_file_path should change
        assert new_entry["session_id"] != old_entry["session_id"]
        assert new_entry["buffer_file_path"] != old_entry["buffer_file_path"]
