#!/usr/bin/env python3
"""
Demo script showing timing instrumentation in action.

Simulates the buffer write/read race condition and logs timing metrics.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Add core directory to path
SCRIPT_DIR = Path(__file__).parent
CLAUDE_SLACK_DIR = SCRIPT_DIR.parent.parent
CORE_DIR = CLAUDE_SLACK_DIR / "core"
sys.path.insert(0, str(CORE_DIR))

def demo_timing_flow():
    """Demonstrate complete timing instrumentation flow."""
    print("=" * 60)
    print("Timing Instrumentation Demo")
    print("=" * 60)
    print()

    # Create temporary directory for demo
    with tempfile.TemporaryDirectory() as tmp_dir:
        session_id = "demo-timing-abc123"

        # Step 1: Simulate buffer write (claude_wrapper_hybrid.py)
        print("Step 1: Buffer write (wrapper)")
        print("-" * 60)

        buffer_file = os.path.join(tmp_dir, f"claude_output_{session_id}.txt")
        metadata_file = os.path.join(tmp_dir, f"claude_output_{session_id}.meta")

        # Simulate permission prompt data
        buffer_data = b"Claude needs your permission to use Bash\n1. Yes\n2. No\n"
        buffer_write_time = time.time()

        # Write buffer file
        with open(buffer_file, 'wb') as f:
            f.write(buffer_data)

        # Write metadata file
        metadata = {
            'buffer_write_time': buffer_write_time,
            'session_id': session_id
        }
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f)

        print(f"  Buffer file: {buffer_file}")
        print(f"  Metadata file: {metadata_file}")
        print(f"  Write time: {buffer_write_time:.6f}")
        print(f"  [TIMING] session_id={session_id[:8]} buffer_write={buffer_write_time:.6f}")
        print()

        # Step 2: Simulate realistic delay
        print("Step 2: Simulating race condition delay...")
        print("-" * 60)
        delay_ms = 75  # 75ms delay
        time.sleep(delay_ms / 1000.0)
        print(f"  Delayed for {delay_ms}ms")
        print()

        # Step 3: Simulate buffer read (on_notification.py hook)
        print("Step 3: Buffer read (hook)")
        print("-" * 60)

        # Read metadata
        with open(metadata_file, 'r') as f:
            loaded_metadata = json.load(f)

        # Capture read time
        hook_read_time = time.time()

        # Read buffer content
        with open(buffer_file, 'rb') as f:
            buffer_content = f.read()

        # Calculate timing delta
        delta_ms = (hook_read_time - loaded_metadata['buffer_write_time']) * 1000

        print(f"  Read time: {hook_read_time:.6f}")
        print(f"  Buffer size: {len(buffer_content)} bytes")
        print(f"  Delta: {delta_ms:.2f}ms")
        print(f"  [TIMING] session_id={session_id[:8]} buffer_write={buffer_write_time:.6f} hook_read={hook_read_time:.6f} delta_ms={delta_ms:.2f}")
        print()

        # Step 4: Parse timing log
        print("Step 4: Parse timing log")
        print("-" * 60)

        timing_log = f"[TIMING] session_id={session_id[:8]} buffer_write={buffer_write_time:.6f} hook_read={hook_read_time:.6f} delta_ms={delta_ms:.2f}"

        import re
        write_match = re.search(r'buffer_write=([\d.]+)', timing_log)
        read_match = re.search(r'hook_read=([\d.]+)', timing_log)
        delta_match = re.search(r'delta_ms=([\d.]+)', timing_log)

        print(f"  Log entry: {timing_log}")
        print()
        print("  Parsed values:")
        print(f"    buffer_write: {float(write_match.group(1)):.6f}")
        print(f"    hook_read: {float(read_match.group(1)):.6f}")
        print(f"    delta_ms: {float(delta_match.group(1)):.2f}")
        print()

        # Step 5: Verification
        print("Step 5: Verification")
        print("-" * 60)

        parsed_delta = float(delta_match.group(1))
        expected_delta = delay_ms

        print(f"  Expected delay: {expected_delta}ms")
        print(f"  Measured delta: {parsed_delta:.2f}ms")
        print(f"  Difference: {abs(parsed_delta - expected_delta):.2f}ms")

        if abs(parsed_delta - expected_delta) < 10:  # Within 10ms tolerance
            print("  ✓ PASS: Timing measurement accurate")
        else:
            print("  ✗ FAIL: Timing measurement inaccurate")

        print()
        print("=" * 60)
        print("Demo completed successfully!")
        print("=" * 60)


def demo_multiple_scenarios():
    """Demonstrate timing instrumentation across multiple delay scenarios."""
    print()
    print("=" * 60)
    print("Multiple Delay Scenarios")
    print("=" * 60)
    print()

    delays = [25, 50, 100, 150, 200, 300]  # Various realistic delays in ms

    with tempfile.TemporaryDirectory() as tmp_dir:
        for delay_ms in delays:
            session_id = f"scenario-{delay_ms}ms"

            buffer_file = os.path.join(tmp_dir, f"claude_output_{session_id}.txt")
            metadata_file = os.path.join(tmp_dir, f"claude_output_{session_id}.meta")

            # Write buffer
            buffer_write_time = time.time()
            with open(buffer_file, 'wb') as f:
                f.write(b"Permission prompt")

            metadata = {
                'buffer_write_time': buffer_write_time,
                'session_id': session_id
            }
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f)

            # Simulate delay
            time.sleep(delay_ms / 1000.0)

            # Read buffer
            hook_read_time = time.time()
            with open(metadata_file, 'r') as f:
                loaded_metadata = json.load(f)

            delta_ms = (hook_read_time - loaded_metadata['buffer_write_time']) * 1000

            # Log timing
            print(f"Delay {delay_ms:3d}ms: delta_ms={delta_ms:6.2f}ms  [TIMING] buffer_write={buffer_write_time:.6f} hook_read={hook_read_time:.6f}")

    print()
    print("All scenarios completed!")
    print()


if __name__ == "__main__":
    demo_timing_flow()
    demo_multiple_scenarios()
