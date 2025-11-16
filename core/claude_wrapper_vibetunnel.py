"""
VibeTunnel-specific wrapper using PTY Master Write for input injection.

Creates a minimal PTY for reliable Slack input injection:
1. Uses pty.fork() to create master/slave PTY pair
2. Claude runs in child, connected to PTY slave (stdin/stdout/stderr)
3. Parent writes Slack input to PTY master → flows to child's stdin
4. Parent reads Claude output from PTY master → forwards to real stdout

Architecture:
    VibeTunnel PTY (outer)
        └→ Wrapper PTY (inner) ← Minimal, just for I/O control
            ├→ master_fd (parent writes/reads here)
            └→ slave (child's stdin/stdout/stderr)
                └→ Claude process
"""

import subprocess
import sys
import os
import signal

def run_vibetunnel_mode(wrapper):
    """
    Run Claude in VibeTunnel using PTY Master Write for input injection.

    Creates a minimal PTY to enable reliable Slack input injection:
    - Parent writes to master_fd → child reads as stdin
    - Parent reads from master_fd ← child writes to stdout/stderr
    - Proven approach used by pexpect, tmux, screen, etc.

    Args:
        wrapper: HybridPTYWrapper instance (for socket, registry, etc)
    """
    wrapper.logger.info("=== VibeTunnel Mode: PTY Master Write ===")

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

    # Print custom VibeTunnel banner
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    separator = "─" * 50
    print(f"\n{BOLD}{CYAN}{separator}{RESET}", file=sys.stderr)
    print(f"{BOLD}{CYAN}Claude Code Hybrid PTY Wrapper{RESET}", file=sys.stderr)
    print(f"{CYAN}Session ID: {BOLD}{wrapper.session_id}{RESET}", file=sys.stderr)
    print(f"{CYAN}Project: {wrapper.project_dir}{RESET}", file=sys.stderr)
    print(f"{CYAN}Input Socket: {wrapper.socket_path}{RESET}", file=sys.stderr)
    print(f"{YELLOW}VibeTunnel: PTY Master Write Mode{RESET}", file=sys.stderr)
    print(f"{GREEN}Slack input via PTY master → Claude stdin{RESET}", file=sys.stderr)
    print(f"{BOLD}{CYAN}{separator}{RESET}\n", file=sys.stderr)

    # Fork with PTY to enable input injection
    # Child: exec Claude (stdin/stdout/stderr connected to PTY slave)
    # Parent: Write to master_fd for input, read from master_fd for output
    import pty

    pid, master_fd = pty.fork()

    if pid == 0:
        # Child: Change to project dir and exec Claude
        # stdin/stdout/stderr automatically connected to PTY slave by pty.fork()
        os.chdir(wrapper.project_dir)
        os.execvp(claude_bin, claude_cmd)
        # Never reaches here
    else:
        # Parent: Monitor Slack input queue
        wrapper.logger.info(f"Claude forked - PID: {pid}")

        # Don't set raw mode - use default PTY mode (canonical/cooked)
        # Raw mode causes artifacts in VibeTunnel
        # Default mode works fine for input injection via master_fd

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

        # Monitor for both Claude output and Slack input
        wrapper.logger.info("Monitoring PTY master for I/O...")
        import select
        try:
            while True:
                # Check if Claude is still running
                try:
                    os.kill(pid, 0)  # Check if process exists
                except OSError:
                    wrapper.logger.info("Claude process ended")
                    break

                # Use select to check if master_fd has output ready
                # Timeout 0.1s so we can check Slack queue frequently
                readable, _, _ = select.select([master_fd], [], [], 0.1)

                # Forward Claude's output to real stdout
                if master_fd in readable:
                    try:
                        output = os.read(master_fd, 4096)
                        if output:
                            # Write to real stdout so VibeTunnel sees it
                            os.write(sys.stdout.fileno(), output)
                        else:
                            # EOF - Claude closed its output
                            wrapper.logger.info("Claude closed PTY")
                            break
                    except OSError as e:
                        wrapper.logger.error(f"Error reading from PTY: {e}")
                        break

                # Check Slack queue for input to inject
                try:
                    slack_data = wrapper.slack_input_queue.get(timeout=0.1)
                    # Write to PTY master - flows to Claude's stdin
                    # Match standard mode pattern: write text, sleep, write CR
                    bytes_written = os.write(master_fd, slack_data)
                    wrapper.logger.debug(f"Wrote {bytes_written} bytes to PTY master")
                    time.sleep(0.1)  # Give PTY time to process
                    os.write(master_fd, b'\r')
                    wrapper.logger.info(f"Input injected with Enter key ({bytes_written} bytes + CR)")
                except queue.Empty:
                    pass

        except KeyboardInterrupt:
            wrapper.logger.info("Interrupted - terminating Claude")
            os.kill(pid, signal.SIGTERM)

        # Wait for Claude to exit
        os.waitpid(pid, 0)
        wrapper.logger.info("VibeTunnel mode session ended")
