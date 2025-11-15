"""
VibeTunnel-specific wrapper that avoids nested PTY.

Instead of creating a PTY (which VibeTunnel already has), this:
1. Runs Claude directly (it inherits VibeTunnel's stdin/stdout/stderr)
2. Socket listener writes Slack input to VibeTunnel's terminal
3. Claude reads from the same terminal and sees the injected input

Architecture:
    VibeTunnel PTY (only PTY) ← Claude reads from here
        ↑
        └→ Slack input written here
"""

import subprocess
import sys
import os
import signal

def run_vibetunnel_mode(wrapper):
    """
    Run Claude in VibeTunnel without creating nested PTY.

    Simple approach: Just exec Claude directly.
    Let VibeTunnel handle all terminal I/O.
    Socket listener will write to terminal which Claude reads.

    Args:
        wrapper: HybridPTYWrapper instance (for socket, registry, etc)
    """
    wrapper.logger.info("=== VibeTunnel Mode: Direct execution (no nested PTY) ===")

    # Setup (but don't create PTY)
    wrapper.setup_socket_directory()
    wrapper.setup_unix_socket()
    wrapper.setup_environment()
    wrapper.register_with_registry()

    # Create queue for Slack input
    import queue
    wrapper.slack_input_queue = queue.Queue()

    # Start socket listener for Slack input
    # It will write to the queue, and we'll write queue items to terminal
    import threading
    wrapper.socket_thread = threading.Thread(target=wrapper.socket_listener, daemon=True)
    wrapper.socket_thread.start()

    # Get Claude binary
    from config import get_claude_bin
    claude_bin = get_claude_bin()

    # Generate session ID for Claude
    import uuid
    claude_session_uuid = str(uuid.uuid4())
    wrapper.claude_session_uuid = claude_session_uuid

    # Build command
    claude_cmd = [claude_bin, '--session-id', claude_session_uuid] + wrapper.claude_args

    wrapper.logger.info(f"Executing Claude directly: {' '.join(claude_cmd)}")
    wrapper.print_startup_banner()

    # Fork and exec Claude
    # Child: exec Claude (inherits VibeTunnel's stdin/stdout/stderr)
    # Parent: Monitor Slack queue and inject input to terminal
    pid = os.fork()

    if pid == 0:
        # Child: Change to project dir and exec Claude
        os.chdir(wrapper.project_dir)
        os.execvp(claude_bin, claude_cmd)
        # Never reaches here
    else:
        # Parent: Monitor Slack input queue
        wrapper.logger.info(f"Claude forked - PID: {pid}")

        # Wait for thread_ts (needed for Slack communication)
        wrapper.logger.info("Waiting for Slack thread creation...")
        import time
        max_wait = 10
        start_time = time.time()

        while time.time() - start_time < max_wait:
            try:
                import sqlite3
                db_path = "/tmp/claude_sessions/registry.db"
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT slack_thread_ts, slack_channel FROM sessions WHERE session_id = ?",
                    (wrapper.session_id,)
                )
                row = cursor.fetchone()
                conn.close()

                if row and row[0] and row[1]:
                    wrapper.thread_ts = row[0]
                    wrapper.channel = row[1]
                    wrapper.logger.info(f"Slack thread created: {wrapper.thread_ts}")
                    break
            except Exception as e:
                wrapper.logger.debug(f"Waiting for thread: {e}")
            time.sleep(0.5)

        # Register Claude's session ID once we have thread
        if wrapper.thread_ts and hasattr(wrapper, 'claude_session_uuid'):
            wrapper.register_claude_session(wrapper.claude_session_uuid)

        # Monitor for Slack input and write to terminal
        wrapper.logger.info("Monitoring Slack input queue...")
        try:
            while True:
                # Check if Claude is still running
                try:
                    os.kill(pid, 0)  # Check if process exists
                except OSError:
                    wrapper.logger.info("Claude process ended")
                    break

                # Check Slack queue
                try:
                    slack_data = wrapper.slack_input_queue.get(timeout=0.5)
                    # Write to terminal - Claude will read it
                    # Use ioctl to inject into terminal input buffer
                    import fcntl
                    import termios as term
                    for byte in slack_data:
                        fcntl.ioctl(sys.stdin, term.TIOCSTI, bytes([byte]))
                    wrapper.logger.info(f"Injected {len(slack_data)} bytes to terminal")
                except queue.Empty:
                    pass

        except KeyboardInterrupt:
            wrapper.logger.info("Interrupted - terminating Claude")
            os.kill(pid, signal.SIGTERM)

        # Wait for Claude to exit
        os.waitpid(pid, 0)
        wrapper.logger.info("VibeTunnel mode session ended")
