#!/usr/bin/env python3
"""
Claude Code Wrapper - Phase 2.5: Multi-Session Support

This enhanced wrapper supports multiple concurrent Claude Code sessions with:
1. Unique session IDs and sockets for each session
2. Registration with central session registry
3. Activity detection and status updates
4. Heartbeat mechanism for session health monitoring
5. VibeTunnel remote session support
6. Backward compatibility with single-session mode

Architecture:
    User Terminal <-> claude_wrapper_multi.py <-> Claude Code subprocess
                            ^                           ^
                            |                           |
                    Session-specific socket      Registry socket
                    (SOCKET_DIR/                 (SOCKET_DIR/
                     {session_id}.sock)           registry.sock)

Usage:
    python3 claude_wrapper_multi.py [options] [claude arguments]

    # Auto-generate session ID
    python3 claude_wrapper_multi.py

    # Explicit session ID
    python3 claude_wrapper_multi.py --session-id abc123

    # Remote session via VibeTunnel
    python3 claude_wrapper_multi.py --vibe-tunnel-id remote-xyz

Options:
    --session-id       Unique session ID (8-char hex, auto-generated if not provided)
    --vibe-tunnel-id   VibeTunnel session ID (for remote sessions)
    --project          Project name (auto-detected from cwd if not provided)
    --terminal         Terminal name (auto-detected if not provided)
    --test             Run in test mode (mock session, no Claude)

Environment Variables:
    SLACK_SOCKET_DIR   - Socket directory (default: ~/.claude/slack/sockets)
    SLACK_CHANNEL      - Slack channel (default: #claude-sessions)
    SLACK_BOT_TOKEN    - Bot token for displaying thread URL
    CLAUDE_BIN         - Path to Claude Code binary (default: auto-detect)
"""

import sys
import os
import pty
import select
import termios
import tty
import socket
import threading
import time
import subprocess
import argparse
import json
import hashlib
import uuid
from pathlib import Path
try:
    from core.config import get_socket_dir, get_claude_bin
except ModuleNotFoundError:
    from config import get_socket_dir, get_claude_bin

# Configuration
SOCKET_DIR = os.environ.get("SLACK_SOCKET_DIR", get_socket_dir())
REGISTRY_SOCKET = os.path.join(SOCKET_DIR, "registry.sock")
OUTPUT_SOCKET = os.path.join(SOCKET_DIR, "listener_output.sock")  # Output receiver socket
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "#claude-sessions")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
HEARTBEAT_INTERVAL = 30  # seconds
OUTPUT_BUFFER_SIZE = 2048  # bytes - flush when buffer reaches this size
OUTPUT_BUFFER_TIMEOUT = 0.5  # seconds - flush after this much idle time

# ANSI color codes for terminal output
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
BOLD = "\033[1m"
RESET = "\033[0m"


def generate_session_id():
    """Generate unique 8-character hex session ID"""
    random_bytes = os.urandom(4)
    return hashlib.sha256(random_bytes).hexdigest()[:8]


def detect_project():
    """Auto-detect project name from current directory"""
    return os.path.basename(os.getcwd())


def detect_terminal():
    """Auto-detect terminal name from environment"""
    term_program = os.environ.get("TERM_PROGRAM", "")
    if term_program:
        return term_program

    # Try to get terminal name from parent process
    try:
        result = subprocess.run(
            ["ps", "-p", str(os.getppid()), "-o", "comm="],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return result.stdout.strip() or "Unknown"
    except:
        pass

    return "Unknown"


class RegistryClient:
    """Client for communicating with session registry"""

    def __init__(self, session_id, registry_socket_path=REGISTRY_SOCKET):
        self.session_id = session_id
        self.registry_socket_path = registry_socket_path
        self.thread_ts = None
        self.channel = None
        self.available = self._check_availability()

    def _check_availability(self):
        """Check if registry socket exists and is accessible"""
        return os.path.exists(self.registry_socket_path)

    def _send_command(self, command, data=None, timeout=8):
        """Send command to registry and get response"""
        if not self.available:
            return None

        try:
            # Connect to registry
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(self.registry_socket_path)

            # Prepare message
            message = {
                "command": command,
                "data": data or {}
            }

            # Send command
            sock.sendall(json.dumps(message).encode('utf-8') + b'\n')

            # Receive response
            response_data = b''
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response_data += chunk
                if b'\n' in chunk:
                    break

            sock.close()

            if response_data:
                return json.loads(response_data.decode('utf-8'))
            return None

        except Exception as e:
            print(f"{YELLOW}[Session {self.session_id}] Registry communication error: {e}{RESET}", file=sys.stderr)
            return None

    def register(self, project, terminal, socket_path, vibe_tunnel_id=None):
        """Register session with registry"""
        data = {
            "session_id": self.session_id,
            "project": project,
            "terminal": terminal,
            "socket_path": socket_path,
            "vibe_tunnel_id": vibe_tunnel_id
        }

        response = self._send_command("REGISTER", data)

        if response and response.get("success"):
            # Extract session data from response
            session_data = response.get("session", {})
            self.thread_ts = session_data.get("thread_ts")
            self.channel = session_data.get("channel")
            return True

        return False

    def unregister(self):
        """Unregister session from registry"""
        data = {"session_id": self.session_id}
        self._send_command("UNREGISTER", data)

    def heartbeat(self):
        """Send heartbeat to registry"""
        data = {"session_id": self.session_id}
        self._send_command("HEARTBEAT", data, timeout=2)

    def update_status(self, status):
        """Update session status (thinking/writing/waiting/idle)"""
        data = {
            "session_id": self.session_id,
            "status": status
        }
        self._send_command("UPDATE_STATUS", data, timeout=2)


class OutputBuffer:
    """
    Intelligent output buffer with hybrid flush strategy.

    Flushes on:
    - Newline character (line-based)
    - Buffer size exceeds threshold (size-based)
    - Time since last flush exceeds timeout (time-based)
    """

    def __init__(self, size_threshold=OUTPUT_BUFFER_SIZE, time_threshold=OUTPUT_BUFFER_TIMEOUT):
        self.buffer = bytearray()
        self.size_threshold = size_threshold
        self.time_threshold = time_threshold
        self.last_flush_time = time.time()

    def add(self, data):
        """
        Add data to buffer and return flushed text if flush conditions met.

        Args:
            data: bytes to add to buffer

        Returns:
            str: Flushed text if buffer was flushed, None otherwise
        """
        self.buffer.extend(data)

        # Check flush conditions
        now = time.time()
        time_elapsed = now - self.last_flush_time

        # Flush on: newline, size threshold, or time threshold
        should_flush = (
            b'\n' in self.buffer or
            len(self.buffer) >= self.size_threshold or
            time_elapsed >= self.time_threshold
        )

        if should_flush:
            return self.flush()

        return None

    def flush(self):
        """
        Force flush buffer and return contents.

        Returns:
            str: Decoded buffer contents (empty string if buffer is empty)
        """
        if not self.buffer:
            return ""

        # Decode and clear buffer
        try:
            text = self.buffer.decode('utf-8', errors='replace')
        except Exception:
            # Fallback to latin-1 if utf-8 fails
            text = self.buffer.decode('latin-1', errors='replace')

        self.buffer.clear()
        self.last_flush_time = time.time()

        return text

    def has_data(self):
        """Check if buffer has data"""
        return len(self.buffer) > 0


class ActivityDetector:
    """Detects Claude's activity state from output"""

    def __init__(self):
        self.recent_output = []
        self.max_history = 10
        self.last_io_time = time.time()
        self.io_rate = 0
        self.current_status = "idle"

    def process_output(self, data):
        """Analyze output to detect activity state"""
        text = data.decode('utf-8', errors='ignore').lower()

        # Update I/O rate
        now = time.time()
        elapsed = now - self.last_io_time
        if elapsed > 0:
            self.io_rate = len(data) / elapsed
        self.last_io_time = now

        # Keep recent output history
        self.recent_output.append(text)
        if len(self.recent_output) > self.max_history:
            self.recent_output.pop(0)

        # Detect status
        new_status = self._detect_status(text)

        if new_status != self.current_status:
            self.current_status = new_status
            return new_status

        return None

    def _detect_status(self, text):
        """Determine current status from output"""
        # Check for waiting state (user input needed)
        if any(keyword in text for keyword in ['(y/n)', '?', 'continue?', 'proceed?']):
            return "waiting"

        # Check for thinking state
        if 'thinking' in text or 'analyzing' in text or 'processing' in text:
            return "thinking"

        # Check for high I/O (writing)
        if self.io_rate > 1000:  # bytes/sec threshold
            return "writing"

        # Default to idle
        return "idle"


class ClaudeWrapperMulti:
    """Enhanced wrapper supporting multiple concurrent Claude sessions"""

    def __init__(self, session_id, project, terminal, vibe_tunnel_id=None, claude_args=None):
        self.session_id = session_id
        self.project = project
        self.terminal = terminal
        self.vibe_tunnel_id = vibe_tunnel_id
        self.claude_args = claude_args or []

        # Session-specific socket
        self.socket_path = os.path.join(SOCKET_DIR, f"{session_id}.sock")

        # Runtime state
        self.master_fd = None
        self.socket = None
        self.socket_thread = None
        self.heartbeat_thread = None
        self.running = True

        # Registry client
        self.registry = RegistryClient(session_id)

        # Activity detector
        self.activity = ActivityDetector()

        # Output buffer and sender
        self.output_buffer = OutputBuffer()
        self.output_socket_available = None  # Cache availability check
        self.output_sequence = 0  # Sequence counter for ordering messages

        # Thread info
        self.thread_ts = None
        self.channel = SLACK_CHANNEL

    def setup_socket_directory(self):
        """Create socket directory if it doesn't exist"""
        os.makedirs(SOCKET_DIR, exist_ok=True)
        print(f"{CYAN}[Session {self.session_id}] Created socket directory: {SOCKET_DIR}{RESET}", file=sys.stderr)

    def setup_unix_socket(self):
        """Create session-specific Unix socket"""
        # Remove existing socket if present
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)

        # Create Unix socket
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(self.socket_path)
        self.socket.listen(1)

        print(f"{CYAN}[Session {self.session_id}] Socket created: {self.socket_path}{RESET}", file=sys.stderr)

    def register_with_registry(self):
        """Register this session with the central registry"""
        if not self.registry.available:
            print(f"{YELLOW}[Session {self.session_id}] Registry not found, running in single-session mode{RESET}", file=sys.stderr)
            return False

        success = self.registry.register(
            project=self.project,
            terminal=self.terminal,
            socket_path=self.socket_path,
            vibe_tunnel_id=self.vibe_tunnel_id
        )

        if success:
            self.thread_ts = self.registry.thread_ts
            self.channel = self.registry.channel or SLACK_CHANNEL
            print(f"{GREEN}[Session {self.session_id}] Registered with session registry{RESET}", file=sys.stderr)
            return True
        else:
            print(f"{YELLOW}[Session {self.session_id}] Registration failed, continuing in degraded mode{RESET}", file=sys.stderr)
            return False

    def _check_output_socket_availability(self):
        """Check if output socket is available (cached result)"""
        if self.output_socket_available is None:
            self.output_socket_available = os.path.exists(OUTPUT_SOCKET)
            if self.output_socket_available:
                print(f"{GREEN}[Session {self.session_id}] Output socket available: {OUTPUT_SOCKET}{RESET}", file=sys.stderr)
            else:
                print(f"{YELLOW}[Session {self.session_id}] Output socket not found: {OUTPUT_SOCKET}{RESET}", file=sys.stderr)
                print(f"{YELLOW}[Session {self.session_id}] Output streaming to Slack disabled{RESET}", file=sys.stderr)
        return self.output_socket_available

    def _should_send_to_slack(self, text):
        """
        Determine if output should be sent to Slack (filter out UI noise).

        Args:
            text: Output text to evaluate

        Returns:
            bool: True if output should be sent to Slack
        """
        # Skip empty or whitespace-only
        if not text or not text.strip():
            return False

        # Categorized noise patterns for better organization
        # Each category can be toggled independently if needed
        noise_patterns = {
            # Command autocomplete menu (when user types '/')
            "command_menu": [
                "/add-dir",
                "/agents",
                "/bashes",
                "/clear (reset, new)",
                "/compact",
                "/config (theme)",
                "/context",
                "/terminal-setup",
                "/permissions",
                "/check",
                "/mlx",
                "/ide",
                "/model",
                "Manage agent configurations",
                "List and manage background tasks",
                "Clear conversation history",
                "Open config panel",
                "Visualize current context usage",
            ],

            # Help and suggestion prompts
            "suggestions": [
                "Try \"how do I",
                "? for shortcuts",
            ],

            # Status indicators and spinners
            "status": [
                "Thinking off",
                "Thinking on",
                "Computing…",
                "(esc to interrupt",
                "↓ ",
                " tokens)",
            ],

            # Interactive UI chrome
            "interactive_ui": [
                "(tab to toggle)",
                "tab to toggle",
                "Enter to select",
                "Tab/Arrow keys to navigate",
                "Esc to cancel",
                "←  ☐",
                "✔ Submit  →",
            ],

            # Tool output decorations
            "tool_output": [
                "⎿ ",  # Tree branch
            ],

            # Wrapper status messages
            "wrapper_status": [
                "[Session ",
                "Output socket available:",
                "Output socket not found:",
                "Output streaming to Slack",
                "Session idle at",
            ],

            # ANSI escape fragments and UI decorations
            "ansi_fragments": [
                "(B",
                "[39m",
                "gle)",  # Fragment of "(tab to toggle)"
            ],

            # Horizontal rules and box drawing
            "decorations": [
                "────────",
            ],
        }

        # Check all patterns across all categories
        for category, patterns in noise_patterns.items():
            for pattern in patterns:
                if pattern in text:
                    return False

        # Skip if text is ONLY horizontal rules and whitespace
        clean_text = text.replace("─", "").replace("│", "").replace("┌", "").replace("┐", "").replace("└", "").replace("┘", "")
        if not clean_text.strip():
            return False

        # Skip spinner characters (✽, ✻, ✶, ·, ✢, etc.)
        spinner_chars = ["✽", "✻", "✶", "·", "✢", "❖", "✦", "✧", "✹"]
        # If text starts with a spinner char followed by space, it's likely a status line
        stripped = text.strip()
        if stripped and stripped[0] in spinner_chars:
            return False

        # Skip lines that start with selection cursor (❯)
        if stripped.startswith("❯ "):
            return False

        # Skip lines that are ONLY closing parenthesis (tool output fragments)
        if stripped == ")":
            return False

        # Skip very short outputs (likely UI fragments)
        if len(text.strip()) < 10:
            return False

        return True

    def send_output_to_slack(self, output_text, output_type="stdout"):
        """
        Send output to Slack listener via output socket.

        Args:
            output_text: Text to send to Slack
            output_type: Type of output ("stdout" or "stderr")

        Returns:
            bool: True if sent successfully, False otherwise
        """
        if not output_text:
            return False

        # Filter out UI noise
        if not self._should_send_to_slack(output_text):
            return False

        # Skip if output socket not available
        if not self._check_output_socket_availability():
            return False

        # Create a new connection for each message
        # (listener closes connection after handling each message)
        sock = None
        try:
            # Increment sequence counter
            self.output_sequence += 1

            # Build message with sequence number for ordering
            message = {
                "session_id": self.session_id,
                "output": output_text,
                "type": output_type,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "sequence": self.output_sequence
            }

            # Create new socket connection
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect(OUTPUT_SOCKET)

            # Send as JSON with newline delimiter
            message_json = json.dumps(message) + '\n'
            sock.sendall(message_json.encode('utf-8'))

            return True

        except BrokenPipeError:
            print(f"{YELLOW}[Session {self.session_id}] Output socket disconnected (broken pipe){RESET}", file=sys.stderr)
            return False

        except socket.timeout:
            print(f"{YELLOW}[Session {self.session_id}] Output socket send timeout{RESET}", file=sys.stderr)
            return False

        except Exception as e:
            print(f"{YELLOW}[Session {self.session_id}] Error sending output to Slack: {e}{RESET}", file=sys.stderr)
            return False

        finally:
            # Always close the socket after each message
            if sock:
                try:
                    sock.close()
                except:
                    pass

    def socket_listener(self):
        """Thread that listens for Slack bot connections and injects responses"""
        while self.running:
            try:
                # Accept connection from Slack bot
                conn, addr = self.socket.accept()

                with conn:
                    # Receive message from Slack bot
                    data = conn.recv(4096).decode('utf-8').strip()

                    if data:
                        # Inject into Claude's stdin by writing to master pty
                        # First write the text
                        os.write(self.master_fd, data.encode('utf-8'))
                        # Small delay to let text settle
                        time.sleep(0.1)
                        # Then send Enter key (carriage return)
                        os.write(self.master_fd, b'\r')

            except Exception as e:
                if self.running:
                    print(f"{YELLOW}[Session {self.session_id}] Socket error: {e}{RESET}", file=sys.stderr)

    def heartbeat_loop(self):
        """Thread that sends periodic heartbeats to registry"""
        while self.running:
            try:
                time.sleep(HEARTBEAT_INTERVAL)
                if self.running and self.registry.available:
                    self.registry.heartbeat()
            except Exception as e:
                print(f"{YELLOW}[Session {self.session_id}] Heartbeat error: {e}{RESET}", file=sys.stderr)

    def print_startup_banner(self):
        """Print startup banner with session information"""
        separator = "─" * 50

        print(f"\n{BOLD}{CYAN}{separator}{RESET}", file=sys.stderr)
        print(f"{BOLD}{CYAN}🚀 Claude Code Multi-Session Wrapper{RESET}", file=sys.stderr)
        print(f"{CYAN}📡 Session ID: {BOLD}{self.session_id}{RESET}", file=sys.stderr)
        print(f"{CYAN}📁 Project: {self.project}{RESET}", file=sys.stderr)
        print(f"{CYAN}💻 Terminal: {self.terminal}{RESET}", file=sys.stderr)
        print(f"{CYAN}🔌 Socket: {self.socket_path}{RESET}", file=sys.stderr)

        if self.vibe_tunnel_id:
            print(f"{MAGENTA}🌐 VibeTunnel: {self.vibe_tunnel_id}{RESET}", file=sys.stderr)

        if self.thread_ts and SLACK_BOT_TOKEN:
            # Try to construct Slack thread URL
            # Format: https://workspace.slack.com/archives/{channel_id}/p{timestamp}
            thread_url = f"Slack thread: {self.channel} (ts: {self.thread_ts})"
            print(f"{GREEN}📱 {thread_url}{RESET}", file=sys.stderr)
            print(f"{GREEN}💡 Reply in this thread from mobile{RESET}", file=sys.stderr)

        print(f"{BOLD}{CYAN}{separator}{RESET}\n", file=sys.stderr)

    def cleanup(self):
        """Clean up resources and unregister"""
        self.running = False

        # Flush any remaining buffered output
        if self.output_buffer.has_data():
            flushed_text = self.output_buffer.flush()
            if flushed_text:
                self.send_output_to_slack(flushed_text, output_type="stdout")
                # Give the socket time to send the final message and for Slack to post it
                # Longer delay ensures priority queue processes and posts to Slack
                time.sleep(2.0)

        # Unregister from registry
        if self.registry.available:
            self.registry.unregister()
            print(f"{CYAN}[Session {self.session_id}] Unregistered from registry{RESET}", file=sys.stderr)

        # Close socket
        if self.socket:
            try:
                self.socket.close()
            except:
                pass

        # Remove socket file
        if os.path.exists(self.socket_path):
            try:
                os.remove(self.socket_path)
            except:
                pass

    def run(self):
        """Main wrapper logic - spawn Claude and proxy I/O"""
        # Setup socket directory
        self.setup_socket_directory()

        # Setup session-specific Unix socket
        self.setup_unix_socket()

        # Register with session registry
        self.register_with_registry()

        # Start socket listener thread
        self.socket_thread = threading.Thread(target=self.socket_listener, daemon=True)
        self.socket_thread.start()

        # Start heartbeat thread
        self.heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()

        # Print startup banner
        self.print_startup_banner()

        # Find Claude Code binary using config
        claude_bin = get_claude_bin()

        if not claude_bin:
            print(f"{RED}❌ Claude Code binary not found!{RESET}", file=sys.stderr)
            sys.exit(1)

        # Build Claude Code command
        claude_cmd = [claude_bin] + self.claude_args

        # Save terminal attributes to restore later
        old_tty = termios.tcgetattr(sys.stdin)

        try:
            # Spawn Claude Code in a pseudo-terminal (pty)
            pid, self.master_fd = pty.fork()

            if pid == 0:  # Child process
                # Execute Claude Code
                os.execvp(claude_bin, claude_cmd)

            else:  # Parent process
                # Set terminal to raw mode for proper character handling
                tty.setraw(sys.stdin.fileno())

                try:
                    # Main I/O loop
                    while True:
                        # Wait for input from either user terminal or Claude's output
                        r, w, e = select.select([sys.stdin, self.master_fd], [], [], 1.0)

                        if sys.stdin in r:
                            # User typed something - forward to Claude
                            data = os.read(sys.stdin.fileno(), 1024)
                            if data:
                                os.write(self.master_fd, data)
                            else:
                                break

                        if self.master_fd in r:
                            # Claude output - forward to user terminal
                            data = os.read(self.master_fd, 1024)
                            if data:
                                # Always write to terminal (primary function)
                                os.write(sys.stdout.fileno(), data)

                                # Buffer output for Slack
                                flushed_text = self.output_buffer.add(data)
                                if flushed_text:
                                    # Send buffered output to Slack (non-blocking)
                                    self.send_output_to_slack(flushed_text, output_type="stdout")

                                # Detect activity and update status
                                new_status = self.activity.process_output(data)
                                if new_status and self.registry.available:
                                    self.registry.update_status(new_status)
                            else:
                                break

                except (OSError, KeyboardInterrupt):
                    pass

        finally:
            # Restore terminal settings
            termios.tcsetattr(sys.stdin, termios.TCSAFLUSH, old_tty)

            # Cleanup
            self.cleanup()

            print(f"\n{CYAN}[Session {self.session_id}] 👋 Session ended{RESET}", file=sys.stderr)


def test_mode(session_id):
    """Run in test mode - mock session without Claude"""
    print(f"\n{BOLD}{YELLOW}🧪 TEST MODE{RESET}\n")

    project = "test-project"
    terminal = "test-terminal"

    # Create wrapper
    wrapper = ClaudeWrapperMulti(
        session_id=session_id,
        project=project,
        terminal=terminal
    )

    # Setup
    wrapper.setup_socket_directory()
    wrapper.setup_unix_socket()

    # Register
    success = wrapper.register_with_registry()

    if success:
        print(f"{GREEN}✅ Registration successful{RESET}")
        print(f"{GREEN}   Thread: {wrapper.thread_ts}{RESET}")
        print(f"{GREEN}   Channel: {wrapper.channel}{RESET}")
    else:
        print(f"{YELLOW}⚠️  Registration failed or registry unavailable{RESET}")

    # Send test heartbeats
    print(f"\n{CYAN}Sending 3 test heartbeats...{RESET}")
    for i in range(3):
        time.sleep(2)
        wrapper.registry.heartbeat()
        print(f"{GREEN}  Heartbeat {i+1}/3 sent{RESET}")

    # Test status updates
    print(f"\n{CYAN}Testing status updates...{RESET}")
    for status in ["thinking", "writing", "waiting", "idle"]:
        wrapper.registry.update_status(status)
        print(f"{GREEN}  Status updated: {status}{RESET}")
        time.sleep(1)

    # Unregister
    print(f"\n{CYAN}Unregistering session...{RESET}")
    wrapper.cleanup()

    print(f"{GREEN}✅ Test completed successfully{RESET}\n")


def main():
    """Entry point"""
    # Parse arguments
    parser = argparse.ArgumentParser(
        description="Claude Code Multi-Session Wrapper",
        add_help=False  # We'll handle --help ourselves
    )

    parser.add_argument("--session-id", help="Unique session ID (auto-generated if not provided)")
    parser.add_argument("--vibe-tunnel-id", help="VibeTunnel session ID (if remote)")
    parser.add_argument("--project", help="Project name (auto-detected if not provided)")
    parser.add_argument("--terminal", help="Terminal name (auto-detected if not provided)")
    parser.add_argument("--test", action="store_true", help="Run in test mode")
    parser.add_argument("--help", "-h", action="store_true", help="Show help message")

    # Parse known args, remaining go to Claude
    args, claude_args = parser.parse_known_args()

    # Show help
    if args.help:
        print(__doc__)
        sys.exit(0)

    # Generate or use provided session ID
    session_id = args.session_id or generate_session_id()

    # Auto-detect project and terminal if not provided
    project = args.project or detect_project()
    terminal = args.terminal or detect_terminal()

    # Test mode
    if args.test:
        test_mode(session_id)
        sys.exit(0)

    # Check if claude is available
    if not subprocess.run(["which", "claude"], capture_output=True).returncode == 0:
        print(f"{RED}❌ Error: 'claude' command not found{RESET}", file=sys.stderr)
        print(f"{RED}   Make sure Claude Code is installed and in your PATH{RESET}", file=sys.stderr)
        sys.exit(1)

    # Create wrapper
    wrapper = ClaudeWrapperMulti(
        session_id=session_id,
        project=project,
        terminal=terminal,
        vibe_tunnel_id=args.vibe_tunnel_id,
        claude_args=claude_args
    )

    # Run wrapper
    try:
        wrapper.run()
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}[Session {session_id}] Interrupted by user{RESET}", file=sys.stderr)
        wrapper.cleanup()
        sys.exit(0)
    except Exception as e:
        print(f"\n{RED}[Session {session_id}] ❌ Error: {e}{RESET}", file=sys.stderr)
        wrapper.cleanup()
        sys.exit(1)


if __name__ == "__main__":
    main()
