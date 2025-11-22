#!/usr/bin/env python3
"""
Claude Code Hybrid PTY Wrapper

Combines PTY input control with hooks-based output handling.

This wrapper:
1. Runs Claude Code in a PTY for full terminal control
2. Listens on Unix socket for input from Slack
3. Injects input into PTY via os.write()
4. Passes terminal output unchanged (hooks handle Slack output)
5. Sets up environment variables for hooks integration

Architecture:
    Slack Bot -> Unix Socket -> PTY Wrapper -> Claude Code
                                      |
                                      v
                                  Terminal Output (unchanged)
                                      |
                                      v
                                  Hooks capture output
                                      |
                                      v
                                  Slack Bot

Usage:
    python3 claude_wrapper_hybrid.py [--session-id abc123] [claude arguments]

Environment Variables Set:
    CLAUDE_SESSION_ID    - Unique session identifier
    CLAUDE_PROJECT_DIR   - Project directory path
"""

import sys
import os
import pty
import select
import termios
import tty
import socket
import signal
import threading
import time
import argparse
import hashlib
import json
import logging
import logging.handlers
import fcntl
import struct
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from collections import deque

# Load environment variables from .env file (in parent directory)
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
load_dotenv(env_path)

try:
    from core.config import get_socket_dir, get_log_dir, get_claude_bin
except ModuleNotFoundError:
    from config import get_socket_dir, get_log_dir, get_claude_bin

# Configuration
SOCKET_DIR = os.environ.get("SLACK_SOCKET_DIR", get_socket_dir())
REGISTRY_SOCKET = os.path.join(SOCKET_DIR, "registry.sock")
DEBUG = os.environ.get("DEBUG_WRAPPER", "0") == "1"
LOG_DIR = os.environ.get("SLACK_LOG_DIR", get_log_dir())

# ANSI color codes for terminal output
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
RESET = "\033[0m"

# Setup logging
def setup_logging(session_id):
    """Setup comprehensive logging with rotation"""
    # Create log directory
    os.makedirs(LOG_DIR, exist_ok=True)

    # Create logger
    logger = logging.getLogger('wrapper')
    logger.setLevel(logging.DEBUG)

    # File handler with rotation (max 10MB, keep 5 files)
    log_file = os.path.join(LOG_DIR, f"wrapper_{session_id}.log")
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10*1024*1024, backupCount=5
    )
    file_handler.setLevel(logging.DEBUG)

    # Console handler for stderr (only errors)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.ERROR)

    # Format with timestamp, level, and session ID
    formatter = logging.Formatter(
        f'[%(asctime)s.%(msecs)03d] [%(levelname)s] [WRAPPER] [{session_id[:8]}] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


def debug_log(message):
    """Print debug message if DEBUG mode enabled"""
    if DEBUG:
        print(f"{CYAN}[DEBUG] {message}{RESET}", file=sys.stderr)


def generate_session_id():
    """Generate unique 8-character hex session ID"""
    random_bytes = os.urandom(4)
    return hashlib.sha256(random_bytes).hexdigest()[:8]


def detect_project_dir():
    """Detect project directory (current working directory)"""
    return os.getcwd()


class RegistryClient:
    """Client for communicating with session registry"""

    def __init__(self, session_id, registry_socket_path=REGISTRY_SOCKET, logger=None):
        self.session_id = session_id
        self.registry_socket_path = registry_socket_path
        self.logger = logger
        self.thread_ts = None
        self.channel = None
        self.available = self._check_availability()

    def _log(self, message, level="info"):
        """Log message if logger is available"""
        if self.logger:
            if level == "error":
                self.logger.error(message)
            elif level == "warning":
                self.logger.warning(message)
            elif level == "debug":
                self.logger.debug(message)
            else:
                self.logger.info(message)
        debug_log(message)

    def _check_availability(self):
        """Check if registry socket exists and is accessible"""
        return os.path.exists(self.registry_socket_path)

    def _is_registry_responsive(self, timeout=2):
        """
        Check if registry is responsive by sending a PING (LIST) command.

        Returns:
            True if registry responds within timeout, False otherwise
        """
        if not os.path.exists(self.registry_socket_path):
            return False

        try:
            # Try to connect and send a simple LIST command
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(self.registry_socket_path)

            # Send LIST command (lightweight health check)
            message = {"command": "LIST", "data": {}}
            sock.sendall(json.dumps(message).encode('utf-8') + b'\n')

            # Try to receive response
            response_data = b''
            start_time = time.time()
            while time.time() - start_time < timeout:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response_data += chunk
                if b'\n' in chunk:
                    break

            sock.close()

            # If we got a response, registry is healthy
            if response_data:
                response = json.loads(response_data.decode('utf-8'))
                return response.get("success", False)

            return False

        except (socket.timeout, ConnectionRefusedError, FileNotFoundError):
            return False
        except Exception as e:
            self._log(f"Registry health check error: {e}", "debug")
            return False

    def _kill_registry_process(self):
        """Kill any existing registry processes"""
        import subprocess
        try:
            self._log("Killing existing registry processes...", "info")
            # Kill any session_registry.py processes
            subprocess.run(
                ["pkill", "-f", "session_registry.py"],
                capture_output=True,
                timeout=5
            )
            # Give processes time to die
            time.sleep(0.5)
            self._log("Registry processes killed", "debug")
            return True
        except Exception as e:
            self._log(f"Error killing registry processes: {e}", "warning")
            return False

    def _remove_stale_socket(self):
        """Remove stale registry socket file"""
        try:
            if os.path.exists(self.registry_socket_path):
                self._log(f"Removing stale socket: {self.registry_socket_path}", "info")
                os.remove(self.registry_socket_path)
                self._log("Stale socket removed", "debug")
                return True
        except Exception as e:
            self._log(f"Error removing stale socket: {e}", "warning")
            return False
        return True

    def _start_registry_process(self):
        """Start a new registry process"""
        import subprocess
        try:
            self._log("Starting new registry process...", "info")

            # Find the session_registry.py script
            registry_script = os.path.join(
                os.path.dirname(__file__),
                "session_registry.py"
            )

            if not os.path.exists(registry_script):
                self._log(f"Registry script not found: {registry_script}", "error")
                return False

            # Start registry in background
            subprocess.Popen(
                [sys.executable, registry_script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )

            # Wait for registry to start and create socket
            max_wait = 5
            start_time = time.time()
            while time.time() - start_time < max_wait:
                if os.path.exists(self.registry_socket_path):
                    self._log("Registry socket detected", "debug")
                    # Give it a moment to fully initialize
                    time.sleep(0.5)
                    # Verify it's responsive
                    if self._is_registry_responsive(timeout=2):
                        self._log("Registry started successfully", "info")
                        return True
                time.sleep(0.2)

            self._log("Registry failed to start (timeout)", "error")
            return False

        except Exception as e:
            self._log(f"Error starting registry: {e}", "error")
            return False

    def ensure_healthy(self):
        """
        Ensure registry is healthy and responsive.

        This method detects and fixes hung registries:
        - Registry process dead, socket gone -> starts registry
        - Registry process dead, socket exists (stale) -> removes socket, starts registry
        - Registry process hung, socket exists -> kills process, removes socket, starts registry
        - Registry healthy -> does nothing

        Returns:
            True if registry is healthy after this call, False otherwise
        """
        self._log("Performing registry health check...", "info")

        # Test 1: Check if socket exists
        socket_exists = os.path.exists(self.registry_socket_path)
        self._log(f"Socket exists: {socket_exists}", "debug")

        # Test 2: Check if registry is responsive
        if socket_exists:
            responsive = self._is_registry_responsive(timeout=2)
            self._log(f"Registry responsive: {responsive}", "debug")

            if responsive:
                self._log("Registry is healthy", "info")
                self.available = True
                return True

            # Socket exists but registry not responsive - need to fix
            self._log("Registry is hung or dead (socket exists but not responsive)", "warning")

            # Kill any running processes
            self._kill_registry_process()

            # Remove stale socket
            self._remove_stale_socket()
        else:
            self._log("Registry socket not found", "info")

        # At this point, either socket didn't exist or we cleaned up a hung registry
        # Try to start a new registry
        self._log("Attempting to start registry...", "info")
        success = self._start_registry_process()

        if success:
            self._log("Registry health restored", "info")
            self.available = True
            return True
        else:
            self._log("Failed to restore registry health", "error")
            self.available = False
            return False

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
            debug_log(f"Registry communication error: {e}")
            return None

    def register(self, project, terminal, socket_path):
        """Register session with registry and create Slack thread"""
        data = {
            "session_id": self.session_id,
            "project": project,
            "terminal": terminal,
            "socket_path": socket_path
        }

        response = self._send_command("REGISTER", data)

        if response and response.get("success"):
            # Extract session data from response
            session_data = response.get("session", {})
            # Registry uses slack_thread_ts and slack_channel field names
            self.thread_ts = session_data.get("slack_thread_ts")
            self.channel = session_data.get("slack_channel")
            return True

        return False


class HybridPTYWrapper:
    """Hybrid PTY wrapper combining input control with hooks output"""

    def __init__(self, session_id, project_dir, claude_args=None):
        self.session_id = session_id
        self.project_dir = project_dir
        self.claude_args = claude_args or []

        # Setup logging
        self.logger = setup_logging(session_id)
        self.logger.info("="*60)
        self.logger.info("WRAPPER STARTUP")
        self.logger.info(f"Session ID: {session_id}")
        self.logger.info(f"Project directory: {project_dir}")
        self.logger.info(f"Claude args: {claude_args}")
        self.logger.info(f"Python version: {sys.version}")
        self.logger.info(f"Working directory: {os.getcwd()}")

        # VibeTunnel detection
        if self.is_vibetunnel():
            self.logger.info("VibeTunnel detected - will use no-PTY mode")

        self.logger.info("="*60)

        # Session-specific socket
        self.socket_path = os.path.join(SOCKET_DIR, f"{session_id}.sock")
        self.logger.info(f"Socket path: {self.socket_path}")

        # Runtime state
        self.master_fd = None
        self.socket = None
        self.socket_thread = None
        self.running = True
        self.using_alternate_screen = False

        # Registry client (pass logger for health check logging)
        self.registry = RegistryClient(session_id, logger=self.logger)
        self.logger.info(f"Registry client created, available: {self.registry.available}")

        # Thread info
        self.thread_ts = None
        self.channel = None
        self.claude_session_registered = False  # Track if Claude's UUID was registered

        # Output buffer for capturing exact permission prompts (4KB ring buffer)
        # Increased from 1KB to 4KB to capture all 3 permission options
        self.output_buffer = deque(maxlen=4096)
        self.buffer_file = f"/tmp/claude_output_{session_id}.txt"
        self.buffer_lock = threading.Lock()
        self.logger.info(f"Output buffer initialized: {self.buffer_file}")

    def setup_socket_directory(self):
        """Create socket directory if it doesn't exist"""
        os.makedirs(SOCKET_DIR, exist_ok=True)
        self.logger.debug(f"Socket directory created/verified: {SOCKET_DIR}")
        debug_log(f"Socket directory: {SOCKET_DIR}")

    def setup_unix_socket(self):
        """Create session-specific Unix socket for input"""
        self.logger.info("Creating Unix socket for input")

        # Remove existing socket if present
        if os.path.exists(self.socket_path):
            self.logger.debug(f"Removing existing socket: {self.socket_path}")
            os.remove(self.socket_path)

        # Create Unix socket
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.socket_path)
        self.socket.listen(128)
        self.socket.settimeout(1.0)  # Allow shutdown checks
        self.logger.info(f"Unix socket created and listening: {self.socket_path}")

        print(f"{GREEN}[Session {self.session_id}] Input socket: {self.socket_path}{RESET}", file=sys.stderr)

    def setup_environment(self):
        """Set up environment variables for Claude Code and hooks"""
        # Export session ID for hooks
        os.environ["CLAUDE_SESSION_ID"] = self.session_id

        # Export project directory for hooks
        os.environ["CLAUDE_PROJECT_DIR"] = self.project_dir

        # Verify .claude/settings.local.json is accessible
        settings_path = os.path.join(self.project_dir, ".claude", "settings.local.json")
        if os.path.exists(settings_path):
            debug_log(f"Found settings file: {settings_path}")
        else:
            debug_log(f"Warning: settings file not found at {settings_path}")

        debug_log(f"Environment: CLAUDE_SESSION_ID={self.session_id}")
        debug_log(f"Environment: CLAUDE_PROJECT_DIR={self.project_dir}")

    def detect_claude_session_id(self, timeout=5):
        """
        Detect Claude's actual session ID from transcript file.

        Claude generates its own UUID session ID and creates a transcript file.
        We need to detect this ID so we can register it with the correct Slack metadata.

        Args:
            timeout: Max seconds to wait for transcript file

        Returns:
            Claude's session ID (full UUID) or None if not found
        """
        import glob
        import time

        self.logger.info("Detecting Claude session ID from transcript file")

        # Claude stores transcripts in ~/.claude/projects/<escaped-project-path>/
        # Claude replaces both / and _ with - in directory names
        claude_dir = Path.home() / ".claude" / "projects"
        project_escaped = str(self.project_dir).replace("/", "-").replace("_", "-")
        transcript_dir = claude_dir / project_escaped

        self.logger.debug(f"Transcript directory: {transcript_dir}")
        debug_log(f"Looking for Claude transcript in: {transcript_dir}")

        # Wait for Claude to create transcript file
        start_time = time.time()
        attempt = 0
        while time.time() - start_time < timeout:
            attempt += 1
            if transcript_dir.exists():
                # Get all transcript files sorted by modification time (newest first)
                # Filter out agent files (they start with "agent-")
                # Note: Claude creates empty transcript files initially, so don't filter by size
                transcript_files = sorted(
                    [p for p in transcript_dir.glob("*.jsonl")
                     if not p.stem.startswith("agent-")],
                    key=lambda p: p.stat().st_mtime,
                    reverse=True
                )

                if transcript_files:
                    # Get the newest non-agent transcript file with content
                    latest = transcript_files[0]
                    # Extract session ID from filename (remove .jsonl extension)
                    claude_session_id = latest.stem
                    elapsed = time.time() - start_time
                    self.logger.info(f"Detected Claude session ID: {claude_session_id} (took {elapsed:.2f}s, {attempt} attempts)")
                    debug_log(f"Detected Claude session ID: {claude_session_id}")
                    return claude_session_id

            time.sleep(0.1)

        self.logger.warning(f"Could not detect Claude session ID after {timeout}s ({attempt} attempts)")
        debug_log(f"Could not detect Claude session ID after {timeout}s")
        return None

    def register_with_registry(self):
        """Register this session with the central registry to create Slack thread"""
        self.logger.info("Registering with session registry")

        # Perform health check and fix registry if needed
        self.logger.info("Running registry health check before registration...")
        if not self.registry.ensure_healthy():
            self.logger.error("Registry health check failed - cannot register session")
            debug_log("Registry health check failed, running in single-session mode")
            return False

        self.logger.info("Registry is healthy, proceeding with registration")

        # Get terminal name
        terminal = os.environ.get("TERM_PROGRAM", "Unknown")
        self.logger.debug(f"Terminal: {terminal}")

        # Register with wrapper's session ID and create Slack thread
        self.logger.info(f"Sending REGISTER command to registry (will create Slack thread)")
        success = self.registry.register(
            project=os.path.basename(self.project_dir),
            terminal=terminal,
            socket_path=self.socket_path
        )

        if success:
            self.thread_ts = self.registry.thread_ts
            self.channel = self.registry.channel
            self.logger.info(f"Registration successful - thread_ts: {self.thread_ts}, channel: {self.channel}")
            print(f"{GREEN}[Session {self.session_id}] Registered with session registry{RESET}", file=sys.stderr)
            if self.thread_ts:
                debug_log(f"Created Slack thread: ts={self.thread_ts}, channel={self.channel}")
            return True
        else:
            self.logger.error("Registration failed, continuing in degraded mode")
            debug_log("Registration failed, continuing in degraded mode")
            return False

    def register_claude_session(self, claude_session_id):
        """
        Register Claude's actual session ID with the same Slack metadata.

        This ensures the Stop hook can find the session when it fires with Claude's ID.

        Args:
            claude_session_id: Claude's full UUID session ID
        """
        self.logger.info(f"Attempting to register Claude session ID: {claude_session_id}")
        self.logger.debug(f"Registry available: {self.registry.available}, thread_ts: {self.thread_ts}, channel: {self.channel}")

        if not self.registry.available or not self.thread_ts:
            self.logger.warning(f"Cannot register Claude session - registry: {self.registry.available}, thread_ts: {self.thread_ts}")
            return False

        self.logger.info(f"Registering Claude session ID: {claude_session_id}")

        try:
            # Connect to registry and send REGISTER_EXISTING command
            # This registers a new session ID pointing to the same Slack thread
            self.logger.debug(f"Creating socket to connect to: {self.registry.registry_socket_path}")
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(8)
            self.logger.debug(f"Attempting to connect to registry socket...")
            sock.connect(self.registry.registry_socket_path)
            self.logger.debug(f"Connected to registry socket successfully")

            # Send command to register Claude's session ID with existing thread
            message = {
                "command": "REGISTER_EXISTING",
                "data": {
                    "session_id": claude_session_id,
                    "project": os.path.basename(self.project_dir),
                    "terminal": os.environ.get("TERM_PROGRAM", "Unknown"),
                    "socket_path": self.socket_path,
                    "thread_ts": self.thread_ts,
                    "channel": self.channel
                }
            }

            self.logger.debug(f"Sending REGISTER_EXISTING command: {json.dumps(message)[:200]}")
            sock.sendall(json.dumps(message).encode('utf-8') + b'\n')
            self.logger.debug(f"Command sent successfully")

            # Receive response
            self.logger.debug(f"Waiting for response from registry...")
            response_data = b''
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response_data += chunk
                if b'\n' in chunk:
                    break

            sock.close()
            self.logger.debug(f"Received response data: {len(response_data)} bytes")

            if response_data:
                try:
                    response = json.loads(response_data.decode('utf-8'))
                    self.logger.debug(f"Parsed response: {response}")
                    if response.get("success"):
                        self.logger.info(f"Claude session {claude_session_id[:8]} registered successfully")
                        print(f"{GREEN}[Session {self.session_id}] Claude session ID {claude_session_id[:8]} registered{RESET}", file=sys.stderr)
                        debug_log(f"Claude session registered with thread: {self.thread_ts}")
                        self.claude_session_registered = True  # Mark as registered

                        # Update buffer file path to use Claude's UUID
                        self.update_buffer_file_path(claude_session_id)

                        return True
                    else:
                        self.logger.error(f"Registration failed: {response.get('error', 'Unknown error')}")
                        debug_log(f"Registration failed: {response}")
                except json.JSONDecodeError as e:
                    self.logger.error(f"Failed to parse registry response: {e}")
                    self.logger.error(f"Raw response: {response_data}")
            else:
                self.logger.error("No response received from registry")

            debug_log("Failed to register Claude session ID")
            return False

        except socket.timeout:
            self.logger.error(f"Timeout connecting to registry at {self.registry.registry_socket_path}")
            debug_log(f"Timeout connecting to registry")
            return False
        except socket.error as e:
            self.logger.error(f"Socket error connecting to registry: {e}")
            debug_log(f"Socket error: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error registering Claude session: {e}")
            debug_log(f"Error registering Claude session: {e}")
            return False

    def socket_listener(self):
        """Thread that listens for Slack bot connections and injects input to PTY"""
        self.logger.info("Socket listener thread started")
        connections_received = 0

        while self.running:
            try:
                # Accept connection from Slack bot
                self.logger.debug("Waiting for socket connection...")
                conn, addr = self.socket.accept()
                connections_received += 1
                self.logger.info(f"Socket connection #{connections_received} accepted")

                with conn:
                    # Receive message from Slack bot
                    data = conn.recv(4096).decode('utf-8').strip()

                    if data:
                        self.logger.info(f"Received input from Slack: {len(data)} chars")
                        self.logger.debug(f"Input content: {data[:100]}...")
                        debug_log(f"Received input from Slack ({len(data)} chars)")

                        # On first message, retry Claude session registration if needed
                        # This handles the case where thread_ts was None at startup
                        if connections_received == 1 and not self.claude_session_registered:
                            if hasattr(self, 'claude_session_uuid') and self.thread_ts:
                                self.logger.info("First message received - retrying Claude session registration")
                                self.register_claude_session(self.claude_session_uuid)

                        # Inject into Claude's stdin
                        # VibeTunnel mode: use queue (no PTY)
                        if hasattr(self, 'slack_input_queue'):
                            # VibeTunnel mode - queue just the text (Enter added in two-step pattern)
                            # Matches standard mode: text, sleep, \r
                            self.slack_input_queue.put(data.encode('utf-8'))
                            self.logger.info("Input queued for VibeTunnel mode")
                        else:
                            # Standard mode - write to PTY
                            bytes_written = os.write(self.master_fd, data.encode('utf-8'))
                            self.logger.debug(f"Wrote {bytes_written} bytes to PTY master")
                            time.sleep(0.1)
                            os.write(self.master_fd, b'\r')
                            self.logger.info("Input injected successfully with Enter key")

                        debug_log("Input injected successfully")

            except socket.timeout:
                # Timeout allows periodic check of self.running flag
                continue
            except Exception as e:
                if self.running:
                    self.logger.error(f"Socket listener error: {e}", exc_info=True)
                    print(f"{YELLOW}[Session {self.session_id}] Socket error: {e}{RESET}", file=sys.stderr)

        self.logger.info("Socket listener thread ending")

    def is_vibetunnel(self):
        """Check if running in VibeTunnel environment"""
        return 'VIBETUNNEL_SESSION_ID' in os.environ

    def handle_window_size_change(self, signum, frame):
        """Signal handler for terminal window size changes (SIGWINCH)"""
        if self.master_fd is None:
            return

        try:
            # Get current terminal size
            size = struct.unpack('HHHH', fcntl.ioctl(sys.stdin, termios.TIOCGWINSZ, struct.pack('HHHH', 0, 0, 0, 0)))
            rows, cols = size[0], size[1]
            
            # Update PTY size to match
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
            
            self.logger.debug(f"Window size updated: {cols}x{rows}")
        except Exception as e:
            self.logger.error(f"Error updating window size: {e}")

    def sync_window_size(self):
        """Synchronize PTY window size with current terminal size"""
        if self.master_fd is None:
            return

        try:
            # Get current terminal size
            size = struct.unpack('HHHH', fcntl.ioctl(sys.stdin, termios.TIOCGWINSZ, struct.pack('HHHH', 0, 0, 0, 0)))
            rows, cols = size[0], size[1]
            
            # Set PTY size to match
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
            
            self.logger.info(f"Initial PTY size set to: {cols}x{rows}")
        except Exception as e:
            self.logger.error(f"Error setting initial PTY size: {e}")

    def print_startup_banner(self):
        """Print startup banner with session information"""
        separator = "─" * 50

        print(f"\n{BOLD}{CYAN}{separator}{RESET}", file=sys.stderr)
        print(f"{BOLD}{CYAN}Claude Code Hybrid PTY Wrapper{RESET}", file=sys.stderr)
        print(f"{CYAN}Session ID: {BOLD}{self.session_id}{RESET}", file=sys.stderr)
        print(f"{CYAN}Project: {self.project_dir}{RESET}", file=sys.stderr)
        print(f"{CYAN}Input Socket: {self.socket_path}{RESET}", file=sys.stderr)
        if self.is_vibetunnel():
            print(f"{YELLOW}VibeTunnel: Using No-PTY mode{RESET}", file=sys.stderr)
        print(f"{GREEN}Hooks will handle output streaming to Slack{RESET}", file=sys.stderr)
        print(f"{BOLD}{CYAN}{separator}{RESET}\n", file=sys.stderr)

    def supports_alternate_screen(self):
        """Check if terminal supports alternate screen buffer"""
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
        term = os.environ.get('TERM', '')
        # Most modern terminals support it
        return term not in ['dumb', 'unknown', '']

    def enter_alternate_screen(self):
        """Enter alternate screen buffer (like vim/less)"""
        # SOLUTION 1: Disable alternate screen for VibeTunnel
        # VibeTunnel's xterm.js can handle Claude's output natively
        # Nested PTY alternate screen management causes artifacts
        if self.is_vibetunnel():
            self.logger.info("Skipping alternate screen buffer (VibeTunnel compatibility)")
            return
        
        if self.supports_alternate_screen():
            sys.stdout.write('\x1b[?1049h')  # Enter alternate screen
            sys.stdout.write('\x1b[2J')       # Clear screen
            sys.stdout.write('\x1b[H')        # Move cursor to home
            sys.stdout.flush()
            self.using_alternate_screen = True
            self.logger.info("Entered alternate screen buffer")

    def exit_alternate_screen(self):
        """Exit alternate screen buffer"""
        # Skip for VibeTunnel (matches enter_alternate_screen behavior)
        if self.is_vibetunnel():
            return
            
        if self.using_alternate_screen:
            sys.stdout.write('\x1b[?1049l')  # Exit alternate screen
            sys.stdout.flush()
            self.using_alternate_screen = False
            self.logger.info("Exited alternate screen buffer")

    def add_to_output_buffer(self, data):
        """
        Add output data to ring buffer and write to file.

        Args:
            data: Bytes to add to buffer
        """
        with self.buffer_lock:
            # Add to ring buffer (automatically drops oldest if full)
            self.output_buffer.extend(data)

            # Write entire buffer to file for notification hook to read
            try:
                with open(self.buffer_file, 'wb') as f:
                    f.write(bytes(self.output_buffer))
            except Exception as e:
                self.logger.error(f"Failed to write output buffer: {e}")

    def clear_output_buffer(self):
        """Clear the output buffer (called by notification hook after successful parse)"""
        with self.buffer_lock:
            self.output_buffer.clear()
            try:
                # Truncate file
                with open(self.buffer_file, 'wb') as f:
                    pass  # Empty file
                self.logger.debug("Output buffer cleared")
            except Exception as e:
                self.logger.error(f"Failed to clear output buffer: {e}")

    def update_buffer_file_path(self, claude_session_id):
        """
        Update buffer file path to use Claude's actual UUID session ID.

        Args:
            claude_session_id: Claude's full UUID session ID
        """
        old_buffer_file = self.buffer_file
        new_buffer_file = f"/tmp/claude_output_{claude_session_id}.txt"

        with self.buffer_lock:
            try:
                # Copy existing buffer data to new file if old file exists
                if os.path.exists(old_buffer_file):
                    with open(old_buffer_file, 'rb') as old_f:
                        data = old_f.read()
                    with open(new_buffer_file, 'wb') as new_f:
                        new_f.write(data)
                    # Remove old file
                    os.remove(old_buffer_file)
                    self.logger.info(f"Moved buffer file: {old_buffer_file} -> {new_buffer_file}")
                else:
                    # Create new file
                    with open(new_buffer_file, 'wb') as f:
                        f.write(bytes(self.output_buffer))
                    self.logger.info(f"Created new buffer file: {new_buffer_file}")

                # Update buffer file path
                self.buffer_file = new_buffer_file
                self.logger.info(f"Buffer file path updated to use Claude session ID: {claude_session_id[:8]}")

            except Exception as e:
                self.logger.error(f"Failed to update buffer file path: {e}")

    def cleanup(self):
        """Clean up resources"""
        self.logger.info("Starting cleanup")
        self.running = False

        # Close socket
        if self.socket:
            try:
                self.socket.close()
                self.logger.debug("Socket closed")
            except Exception as e:
                self.logger.error(f"Error closing socket: {e}")

        # Remove socket file
        if os.path.exists(self.socket_path):
            try:
                os.remove(self.socket_path)
                self.logger.debug(f"Socket file removed: {self.socket_path}")
            except Exception as e:
                self.logger.error(f"Error removing socket file: {e}")

        # Remove buffer file
        if os.path.exists(self.buffer_file):
            try:
                os.remove(self.buffer_file)
                self.logger.debug(f"Buffer file removed: {self.buffer_file}")
            except Exception as e:
                self.logger.error(f"Error removing buffer file: {e}")

        self.logger.info("Cleanup completed")

    def run(self):
        """Main wrapper logic - spawn Claude in PTY and handle I/O"""
        self.logger.info("Starting main run loop")
        start_time = time.time()

        # VIBETUNNEL OPTIMIZATION: Use no-PTY mode to avoid nested PTY artifacts
        if self.is_vibetunnel():
            self.logger.info("VibeTunnel detected - using no-PTY mode")
            print(f"{YELLOW}[VibeTunnel] Detected - switching to no-PTY mode{RESET}", file=sys.stderr)
            try:
                from claude_wrapper_vibetunnel import run_vibetunnel_mode
                self.logger.info("VibeTunnel module imported successfully")
                return run_vibetunnel_mode(self)
            except Exception as e:
                self.logger.error(f"Failed to import VibeTunnel module: {e}", exc_info=True)
                print(f"{RED}[VibeTunnel] Failed to load no-PTY mode: {e}{RESET}", file=sys.stderr)
                print(f"{YELLOW}[VibeTunnel] Falling back to standard PTY mode{RESET}", file=sys.stderr)
                # Fall through to standard mode

        # Setup socket directory
        self.setup_socket_directory()

        # Setup session-specific Unix socket for input
        self.setup_unix_socket()

        # Setup environment variables for hooks
        self.setup_environment()

        # Register with session registry to create Slack thread
        self.register_with_registry()

        # Start socket listener thread
        self.logger.info("Starting socket listener thread")
        self.socket_thread = threading.Thread(target=self.socket_listener, daemon=True)
        self.socket_thread.start()
        self.logger.debug("Socket listener thread started")

        # Find Claude Code binary using config
        claude_bin = get_claude_bin()

        if not claude_bin:
            print(f"{RED}Error: Claude Code binary not found!{RESET}", file=sys.stderr)
            sys.exit(1)

        # Generate a unique session ID for Claude to prevent resuming old sessions
        import uuid
        claude_session_uuid = str(uuid.uuid4())
        self.claude_session_uuid = claude_session_uuid  # Store for later registration
        self.logger.info(f"Forcing new Claude session with ID: {claude_session_uuid}")

        # Build Claude Code command with explicit session ID
        claude_cmd = [claude_bin, '--session-id', claude_session_uuid] + self.claude_args

        # Save terminal attributes to restore later (if available)
        try:
            old_tty = termios.tcgetattr(sys.stdin)
            has_terminal = True
        except:
            old_tty = None
            has_terminal = False
            self.logger.info("No terminal available - running in background mode")

        # Enter alternate screen buffer (like vim/less) for visual isolation
        if has_terminal:
            self.enter_alternate_screen()

        # Print startup banner (after entering alternate screen so it's visible)
        self.print_startup_banner()

        try:
            # Spawn Claude Code in a pseudo-terminal (pty)
            self.logger.info(f"Forking PTY to execute: {' '.join(claude_cmd)}")
            pid, self.master_fd = pty.fork()

            if pid == 0:  # Child process
                # Ensure we're in the project directory so Claude finds .claude/settings.local.json
                os.chdir(self.project_dir)

                # Execute Claude Code
                os.execvp(claude_bin, claude_cmd)

            else:  # Parent process
                self.logger.info(f"PTY forked successfully - PID: {pid}, master_fd: {self.master_fd}")

                # Wait for async Slack thread creation to complete
                # The REGISTER command creates the thread asynchronously, so we need to
                # wait for thread_ts and channel to be populated in the database
                if self.registry.available and not self.thread_ts:
                    self.logger.info("Waiting for async Slack thread creation...")
                    max_wait = 10  # seconds
                    start_time = time.time()

                    while time.time() - start_time < max_wait:
                        try:
                            # Query the database directly to check if thread was created
                            import sqlite3
                            # Use the hardcoded path instead of importing from config to avoid import issues
                            db_path = "/tmp/claude_sessions/registry.db"
                            conn = sqlite3.connect(db_path)
                            cursor = conn.cursor()
                            cursor.execute(
                                "SELECT slack_thread_ts, slack_channel FROM sessions WHERE session_id = ?",
                                (self.session_id,)
                            )
                            row = cursor.fetchone()
                            conn.close()

                            if row and row[0] and row[1]:
                                self.thread_ts = row[0]
                                self.channel = row[1]
                                self.logger.info(f"Slack thread created: {self.thread_ts} in {self.channel}")
                                break
                        except Exception as e:
                            self.logger.debug(f"Error checking thread status: {e}")

                        time.sleep(0.5)

                    if not self.thread_ts:
                        self.logger.warning("Timeout waiting for Slack thread creation")

                # Use the Claude session ID we explicitly set with --session-id
                # This ensures we register the correct session, not some other active session
                if hasattr(self, 'claude_session_uuid'):
                    self.logger.info(f"Using known Claude session ID: {self.claude_session_uuid}")
                    self.register_claude_session(self.claude_session_uuid)
                else:
                    # Fallback to detection if for some reason we don't have the UUID
                    self.logger.warning("No explicit Claude session UUID, attempting detection...")
                    claude_session_id = self.detect_claude_session_id(timeout=10)
                    if claude_session_id:
                        self.register_claude_session(claude_session_id)
                    else:
                        self.logger.warning("Could not detect Claude session ID - hooks may not work correctly")
                        print(f"{YELLOW}[Session {self.session_id}] Warning: Could not detect Claude session ID{RESET}", file=sys.stderr)
                        print(f"{YELLOW}[Session {self.session_id}] Stop hooks may not work correctly{RESET}", file=sys.stderr)

                # Only do I/O loop if we have a terminal
                if has_terminal:
                    # Set up signal handler for window size changes (SIGWINCH)
                    signal.signal(signal.SIGWINCH, self.handle_window_size_change)
                    self.logger.info("SIGWINCH handler registered for dynamic window resizing")

                    # Synchronize initial PTY size with terminal
                    self.sync_window_size()

                    # Set terminal to raw mode
                    self.logger.info("Setting terminal to raw mode")
                    tty.setraw(sys.stdin.fileno())
                    attrs = termios.tcgetattr(sys.stdin)
                    attrs[3] = attrs[3] & ~termios.ECHO  # Disable echo
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, attrs)

                    try:
                        # Main I/O loop - simple pass-through
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
                                # Claude output - pass through to terminal unchanged
                                data = os.read(self.master_fd, 1024)
                                if data:
                                    # Add to output buffer for permission prompt capture
                                    self.add_to_output_buffer(data)
                                    # Write to terminal (hooks will capture this)
                                    os.write(sys.stdout.fileno(), data)
                                else:
                                    break

                    except (OSError, KeyboardInterrupt):
                        pass
                else:
                    # No terminal - just monitor PTY output and forward to stdout
                    # Socket listener thread handles input from Slack
                    self.logger.info("Running in background mode - monitoring PTY output only")
                    try:
                        while True:
                            # Wait for Claude's output only
                            r, w, e = select.select([self.master_fd], [], [], 1.0)

                            if self.master_fd in r:
                                # Claude output - pass through to stdout (hooks will capture this)
                                data = os.read(self.master_fd, 1024)
                                if data:
                                    # Add to output buffer for permission prompt capture
                                    self.add_to_output_buffer(data)
                                    os.write(sys.stdout.fileno(), data)
                                else:
                                    # Claude exited
                                    break

                    except (OSError, KeyboardInterrupt):
                        pass

        finally:
            # Exit alternate screen buffer first (before restoring terminal)
            self.exit_alternate_screen()

            # Restore terminal settings (if we had a terminal)
            if old_tty is not None:
                try:
                    termios.tcsetattr(sys.stdin, termios.TCSAFLUSH, old_tty)
                except:
                    pass

            # Cleanup
            self.cleanup()

            print(f"\n{GREEN}[Session {self.session_id[:8]}] Session ended{RESET}", file=sys.stderr)


def main():
    """Entry point"""
    # Parse arguments
    parser = argparse.ArgumentParser(
        description="Claude Code Hybrid PTY Wrapper",
        add_help=False  # We'll handle --help ourselves
    )

    parser.add_argument("--session-id", help="Unique session ID (auto-generated if not provided)")
    parser.add_argument("--help", "-h", action="store_true", help="Show help message")

    # Parse known args, remaining go to Claude
    args, claude_args = parser.parse_known_args()

    # Show help
    if args.help:
        print(__doc__)
        sys.exit(0)

    # Generate or use provided session ID
    session_id = args.session_id or generate_session_id()

    # Auto-detect project directory
    project_dir = detect_project_dir()

    # Create wrapper
    wrapper = HybridPTYWrapper(
        session_id=session_id,
        project_dir=project_dir,
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
        print(f"\n{RED}[Session {session_id}] Error: {e}{RESET}", file=sys.stderr)
        wrapper.cleanup()
        sys.exit(1)


if __name__ == "__main__":
    main()
