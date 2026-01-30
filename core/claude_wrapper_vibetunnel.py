"""
VibeTunnel-specific wrapper that avoids nested PTY.

Instead of creating a PTY (which VibeTunnel already has), this:
1. Runs Claude with output capture via PTY for permission detection
2. Forwards all output to stdout while detecting permissions
3. Socket listener writes Slack input to VibeTunnel's terminal
4. Claude reads from the same terminal and sees the injected input

Architecture:
    VibeTunnel PTY (only PTY) ← Claude reads from here
        ↑
        └→ Slack input written here

    Output flow:
        Claude → PTY → Wrapper (detects permissions) → stdout (VibeTunnel displays)
"""

import subprocess
import sys
import os
import pty
import select
import signal
import threading


def should_preserve_scrollback():
    """
    Check if we should preserve terminal scrollback by disabling alternate screen.

    Reads PRESERVE_SCROLLBACK from environment:
      - 'auto' (default): Preserve when in VibeTunnel (this module is only called in VibeTunnel)
      - 'on': Always preserve
      - 'off': Never preserve

    Returns:
        bool: True if we should set TERM_PROGRAM_INHIBIT_ALTSCREEN=1
    """
    setting = os.environ.get('PRESERVE_SCROLLBACK', 'auto').lower().strip()

    if setting == 'off':
        return False
    elif setting == 'on':
        return True
    else:  # 'auto' or any other value defaults to auto
        # In VibeTunnel mode (this module), auto means ON
        return True

def run_vibetunnel_mode(wrapper):
    """
    Run Claude in VibeTunnel with output capture for permission detection.

    Uses PTY to capture Claude's output for permission detection while
    forwarding everything to stdout for VibeTunnel to display.
    Uses TIOCSTI to inject Slack input into the terminal.

    Args:
        wrapper: HybridPTYWrapper instance (for socket, registry, etc)
    """
    import queue
    import time
    import fcntl
    import termios
    import struct

    wrapper.logger.info("=== VibeTunnel Mode: PTY capture with permission detection ===")

    # Setup
    wrapper.setup_socket_directory()
    wrapper.setup_unix_socket()
    wrapper.setup_environment()
    wrapper.register_with_registry()

    # Create queue for Slack input
    wrapper.slack_input_queue = queue.Queue()

    # Start socket listener for Slack input
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

    wrapper.logger.info(f"Executing Claude with PTY capture: {' '.join(claude_cmd)}")

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
    print(f"{YELLOW}VibeTunnel: PTY Mode with permission detection{RESET}", file=sys.stderr)
    print(f"{GREEN}Permission prompts will be captured and sent to Slack{RESET}", file=sys.stderr)
    print(f"{BOLD}{CYAN}{separator}{RESET}\n", file=sys.stderr)

    # Fork with PTY for output capture
    pid, master_fd = pty.fork()

    if pid == 0:
        # Child: Change to project dir and exec Claude
        os.chdir(wrapper.project_dir)

        # Preserve scrollback by disabling Claude's alternate screen mode
        if should_preserve_scrollback():
            os.environ['TERM_PROGRAM_INHIBIT_ALTSCREEN'] = '1'
            print(f"{YELLOW}[VibeTunnel] Scrollback preservation enabled{RESET}", file=sys.stderr)

        os.execvp(claude_bin, claude_cmd)
        # Never reaches here
    else:
        # Parent: Capture output for permission detection, forward to stdout
        wrapper.logger.info(f"Claude forked with PTY - PID: {pid}, master_fd: {master_fd}")

        # Try to sync PTY window size with terminal
        try:
            size = struct.unpack('HHHH', fcntl.ioctl(sys.stdin, termios.TIOCGWINSZ, struct.pack('HHHH', 0, 0, 0, 0)))
            rows, cols = size[0], size[1]
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
            wrapper.logger.debug(f"PTY window size set: {cols}x{rows}")
        except Exception as e:
            wrapper.logger.debug(f"Could not set PTY size: {e}")

        # Wait for thread_ts (needed for Slack communication)
        wrapper.logger.info("Waiting for Slack thread creation...")
        max_wait = 10
        start_time = time.time()

        while time.time() - start_time < max_wait:
            try:
                import sqlite3
                db_path = os.environ.get("REGISTRY_DB_PATH", os.path.expanduser("~/.claude/slack/registry.db"))
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

        # Main I/O loop: capture output, detect permissions, forward to stdout
        wrapper.logger.info("Starting I/O loop with permission detection...")
        try:
            while True:
                # Wait for output from Claude's PTY or timeout for Slack input check
                try:
                    r, w, e = select.select([master_fd], [], [], 0.1)
                except (ValueError, OSError):
                    # master_fd closed
                    wrapper.logger.info("PTY closed")
                    break

                if master_fd in r:
                    try:
                        data = os.read(master_fd, 4096)
                        if data:
                            # Add to output buffer for hook fallback
                            wrapper.add_to_output_buffer(data)

                            # Detect and queue permission prompts
                            wrapper.permission_detector.process_chunk(data)

                            # Forward to stdout (VibeTunnel displays it)
                            os.write(sys.stdout.fileno(), data)
                        else:
                            # EOF - Claude exited
                            wrapper.logger.info("Claude process ended (EOF)")
                            break
                    except OSError as e:
                        wrapper.logger.info(f"Read error: {e}")
                        break

                # Check Slack input queue
                try:
                    slack_data = wrapper.slack_input_queue.get_nowait()
                    # Inject using TIOCSTI (works with VibeTunnel's terminal)
                    for byte in slack_data:
                        fcntl.ioctl(sys.stdin, termios.TIOCSTI, bytes([byte]))
                    wrapper.logger.debug(f"Injected {len(slack_data)} bytes to terminal")

                    time.sleep(0.1)

                    # Inject Enter key
                    fcntl.ioctl(sys.stdin, termios.TIOCSTI, b'\r')
                    wrapper.logger.info(f"Input injected ({len(slack_data)} bytes + Enter)")
                except queue.Empty:
                    pass

        except KeyboardInterrupt:
            wrapper.logger.info("Interrupted - terminating Claude")
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

        # Cleanup
        try:
            os.close(master_fd)
        except OSError:
            pass

        try:
            os.waitpid(pid, 0)
        except OSError:
            pass

        wrapper.logger.info("VibeTunnel mode session ended")
