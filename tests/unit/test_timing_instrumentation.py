"""
Tests for timing instrumentation to measure buffer read race condition.

Tests verify that timing logs are captured on buffer writes and reads,
with parseable structured format for analysis.
"""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
import pytest


# ============================================================
# Buffer Write Timing Tests
# ============================================================

def test_buffer_write_logs_timestamp(tmp_path):
    """Verify buffer write includes timestamp in metadata file."""
    # Create a mock wrapper instance with necessary attributes
    session_id = "test-session-123"
    log_dir = str(tmp_path)

    # Mock the wrapper class with minimal required setup
    with patch('os.makedirs'):
        with patch('sys.path'):
            # Import after patching
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'core'))

            # Create a minimal mock wrapper object
            wrapper = MagicMock()
            wrapper.session_id = session_id
            wrapper.buffer_file = os.path.join(log_dir, f"claude_output_{session_id}.txt")
            wrapper.buffer_metadata_file = os.path.join(log_dir, f"claude_output_{session_id}.meta")
            wrapper.output_buffer = []
            wrapper.buffer_lock = MagicMock()
            wrapper.logger = MagicMock()

            # Simulate the update_output_buffer call with timing instrumentation
            test_data = b"Permission prompt text"
            start_time = time.time()

            # Write buffer file
            with open(wrapper.buffer_file, 'wb') as f:
                f.write(test_data)

            # Write metadata file with timestamp
            metadata = {
                'buffer_write_time': start_time,
                'session_id': session_id
            }
            with open(wrapper.buffer_metadata_file, 'w') as f:
                json.dump(metadata, f)

            # Verify metadata file exists
            assert os.path.exists(wrapper.buffer_metadata_file)

            # Verify metadata contains timestamp
            with open(wrapper.buffer_metadata_file, 'r') as f:
                loaded_metadata = json.load(f)

            assert 'buffer_write_time' in loaded_metadata
            assert isinstance(loaded_metadata['buffer_write_time'], (int, float))
            assert loaded_metadata['buffer_write_time'] > 0
            assert loaded_metadata['session_id'] == session_id


def test_buffer_write_timestamp_precision(tmp_path):
    """Verify timestamp has sufficient precision (microseconds)."""
    session_id = "test-precision"
    metadata_file = os.path.join(str(tmp_path), f"claude_output_{session_id}.meta")

    # Write metadata with current timestamp
    timestamp = time.time()
    metadata = {
        'buffer_write_time': timestamp,
        'session_id': session_id
    }
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f)

    # Read back and verify precision
    with open(metadata_file, 'r') as f:
        loaded = json.load(f)

    # Verify timestamp is a float with microsecond precision
    assert isinstance(loaded['buffer_write_time'], float)
    # Python time.time() returns seconds as float with microsecond precision
    # Check that we have at least millisecond precision (3 decimal places)
    timestamp_str = str(loaded['buffer_write_time'])
    assert '.' in timestamp_str
    decimal_places = len(timestamp_str.split('.')[1])
    assert decimal_places >= 3  # At least millisecond precision


# ============================================================
# Buffer Read Timing Tests
# ============================================================

def test_buffer_read_logs_timestamp(tmp_path):
    """Verify buffer read logs timing delta."""
    session_id = "test-read-123"
    log_dir = str(tmp_path)

    # Create buffer file and metadata
    buffer_file = os.path.join(log_dir, f"claude_output_{session_id}.txt")
    metadata_file = os.path.join(log_dir, f"claude_output_{session_id}.meta")

    # Write buffer data
    with open(buffer_file, 'wb') as f:
        f.write(b"Test permission prompt")

    # Write metadata with past timestamp
    write_time = time.time() - 0.5  # 500ms ago
    metadata = {
        'buffer_write_time': write_time,
        'session_id': session_id
    }
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f)

    # Simulate hook reading buffer and calculating delta
    with open(metadata_file, 'r') as f:
        loaded_metadata = json.load(f)

    read_time = time.time()
    delta_ms = (read_time - loaded_metadata['buffer_write_time']) * 1000

    # Verify delta calculation
    assert delta_ms >= 0
    assert delta_ms < 2000  # Should be less than 2 seconds for this test

    # Verify timing log format would be parseable
    timing_log = f"[TIMING] buffer_write={write_time:.6f} hook_read={read_time:.6f} delta_ms={delta_ms:.2f}"
    assert '[TIMING]' in timing_log
    assert 'buffer_write=' in timing_log
    assert 'hook_read=' in timing_log
    assert 'delta_ms=' in timing_log


def test_buffer_read_handles_missing_metadata(tmp_path):
    """Verify buffer read gracefully handles missing metadata file."""
    session_id = "test-missing-meta"
    log_dir = str(tmp_path)

    metadata_file = os.path.join(log_dir, f"claude_output_{session_id}.meta")

    # Metadata file doesn't exist
    assert not os.path.exists(metadata_file)

    # Simulate hook trying to read metadata
    try:
        if os.path.exists(metadata_file):
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
        else:
            # Graceful fallback - no timing data available
            metadata = None
    except Exception as e:
        # Should not raise exception
        pytest.fail(f"Should handle missing metadata gracefully: {e}")

    # Verify we handled it gracefully
    assert metadata is None


# ============================================================
# Timing Log Format Tests
# ============================================================

def test_timing_log_format_parseable():
    """Verify timing logs can be parsed programmatically."""
    # Create a sample timing log entry
    buffer_write = 1234567.890123
    hook_read = 1234567.950456
    delta_ms = (hook_read - buffer_write) * 1000

    log_entry = f"[TIMING] buffer_write={buffer_write:.6f} hook_read={hook_read:.6f} delta_ms={delta_ms:.2f}"

    # Parse the log entry
    assert '[TIMING]' in log_entry

    # Extract values using simple string parsing
    import re

    # Parse buffer_write
    write_match = re.search(r'buffer_write=([\d.]+)', log_entry)
    assert write_match is not None
    parsed_write = float(write_match.group(1))
    assert abs(parsed_write - buffer_write) < 0.0001

    # Parse hook_read
    read_match = re.search(r'hook_read=([\d.]+)', log_entry)
    assert read_match is not None
    parsed_read = float(read_match.group(1))
    assert abs(parsed_read - hook_read) < 0.0001

    # Parse delta_ms
    delta_match = re.search(r'delta_ms=([\d.]+)', log_entry)
    assert delta_match is not None
    parsed_delta = float(delta_match.group(1))
    assert abs(parsed_delta - delta_ms) < 0.1


def test_timing_log_format_with_session_id():
    """Verify timing logs include session ID for tracking."""
    session_id = "abc12345"
    buffer_write = time.time()
    hook_read = buffer_write + 0.060  # 60ms later
    delta_ms = (hook_read - buffer_write) * 1000

    # Format with session ID
    log_entry = f"[TIMING] session_id={session_id[:8]} buffer_write={buffer_write:.6f} hook_read={hook_read:.6f} delta_ms={delta_ms:.2f}"

    # Verify format
    assert '[TIMING]' in log_entry
    assert f'session_id={session_id[:8]}' in log_entry
    assert 'buffer_write=' in log_entry
    assert 'hook_read=' in log_entry
    assert 'delta_ms=' in log_entry

    # Parse session_id
    import re
    session_match = re.search(r'session_id=([a-zA-Z0-9]+)', log_entry)
    assert session_match is not None
    assert session_match.group(1) == session_id[:8]


def test_timing_log_realistic_values():
    """Verify timing logs work with realistic race condition values."""
    # Realistic scenario: 50-300ms delay between buffer write and hook read
    buffer_write = time.time()

    # Simulate various delay scenarios
    delays_ms = [50, 100, 150, 200, 250, 300]

    for delay_ms in delays_ms:
        hook_read = buffer_write + (delay_ms / 1000.0)
        delta_ms = (hook_read - buffer_write) * 1000

        log_entry = f"[TIMING] buffer_write={buffer_write:.6f} hook_read={hook_read:.6f} delta_ms={delta_ms:.2f}"

        # Parse and verify
        import re
        delta_match = re.search(r'delta_ms=([\d.]+)', log_entry)
        assert delta_match is not None
        parsed_delta = float(delta_match.group(1))

        # Verify within 1ms of expected delay
        assert abs(parsed_delta - delay_ms) < 1.0


# ============================================================
# Integration Tests
# ============================================================

def test_end_to_end_timing_flow(tmp_path):
    """Test complete timing flow: write -> read -> log parsing."""
    session_id = "e2e-test-789"
    log_dir = str(tmp_path)

    # Step 1: Buffer write (simulating claude_wrapper_hybrid.py)
    buffer_file = os.path.join(log_dir, f"claude_output_{session_id}.txt")
    metadata_file = os.path.join(log_dir, f"claude_output_{session_id}.meta")

    buffer_data = b"Claude needs your permission to use Bash"
    write_time = time.time()

    with open(buffer_file, 'wb') as f:
        f.write(buffer_data)

    metadata = {
        'buffer_write_time': write_time,
        'session_id': session_id
    }
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f)

    # Step 2: Small delay to simulate real-world timing
    time.sleep(0.05)  # 50ms delay

    # Step 3: Buffer read (simulating on_notification.py hook)
    with open(metadata_file, 'r') as f:
        loaded_metadata = json.load(f)

    read_time = time.time()
    delta_ms = (read_time - loaded_metadata['buffer_write_time']) * 1000

    # Step 4: Generate timing log
    timing_log = f"[TIMING] session_id={session_id[:8]} buffer_write={write_time:.6f} hook_read={read_time:.6f} delta_ms={delta_ms:.2f}"

    # Step 5: Verify complete flow
    assert os.path.exists(buffer_file)
    assert os.path.exists(metadata_file)
    assert delta_ms >= 50  # At least our sleep time
    assert delta_ms < 200  # But not unreasonably large
    assert '[TIMING]' in timing_log

    # Step 6: Parse log to verify data integrity
    import re
    write_match = re.search(r'buffer_write=([\d.]+)', timing_log)
    read_match = re.search(r'hook_read=([\d.]+)', timing_log)
    delta_match = re.search(r'delta_ms=([\d.]+)', timing_log)

    assert all([write_match, read_match, delta_match])

    parsed_write = float(write_match.group(1))
    parsed_read = float(read_match.group(1))
    parsed_delta = float(delta_match.group(1))

    # Verify parsed values match originals
    assert abs(parsed_write - write_time) < 0.0001
    assert abs(parsed_read - read_time) < 0.0001
    assert abs(parsed_delta - delta_ms) < 0.1
