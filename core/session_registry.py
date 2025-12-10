#!/usr/bin/env python3
"""
Session Registry Service - Multi-session management for Claude Code + Slack

Manages multiple concurrent Claude Code sessions, each with:
- Unique Unix socket for communication
- Dedicated Slack thread for user interaction
- Session metadata (project, terminal, status, etc.)
- Lifecycle tracking and cleanup

Architecture:
    - Singleton registry (one per system)
    - SQLite database storage with WAL mode for concurrency
    - Thread-safe operations via SQLAlchemy transactions
    - Unix socket server for IPC with Claude sessions
    - Slack integration for thread management

Usage:
    # Start registry server
    registry = SessionRegistry()
    registry.start_server()

    # Register session (typically called by claude_wrapper.py)
    session_data = registry.register_session({
        "session_id": "abc123",
        "project": "btcbot",
        "terminal": "Terminal 1",
        "socket_path": "/tmp/claude_socks/abc123.sock"
    })

    # Lookup by Slack thread
    session = registry.get_by_thread("1234567890.123456")

    # Cleanup old sessions
    registry.cleanup_old_sessions(max_age_hours=24)
"""

import os
import sys
import json
import socket
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from enum import Enum
from dotenv import load_dotenv

# Load environment variables from .env file (in parent directory)
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
load_dotenv(env_path)

# Database backend
try:
    from core.registry_db import RegistryDatabase
    from core.config import get_socket_dir, get_registry_db_path
except ModuleNotFoundError:
    from registry_db import RegistryDatabase
    from config import get_socket_dir, get_registry_db_path

# Optional Slack integration
try:
    from slack_sdk import WebClient
    SLACK_AVAILABLE = True
except ImportError:
    SLACK_AVAILABLE = False
    print("[Registry] Warning: slack_sdk not installed, Slack features disabled", file=sys.stderr)


class SessionStatus(Enum):
    """Session status states"""
    ACTIVE = "active"
    IDLE = "idle"
    ENDED = "ended"
    CRASHED = "crashed"


class SessionRegistry:
    """
    Singleton session registry for managing multiple Claude Code sessions

    Thread-safe registry that:
    - Stores session metadata in memory
    - Persists to disk for recovery after restarts
    - Provides Unix socket server for IPC
    - Integrates with Slack for thread management
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """Singleton pattern - only one registry per system"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(SessionRegistry, cls).__new__(cls)
        return cls._instance

    def __init__(
        self,
        registry_dir: Optional[str] = None,
        socket_path: Optional[str] = None,
        slack_token: Optional[str] = None,
        slack_channel: str = "claude-sessions"
    ):
        """
        Initialize session registry

        Args:
            registry_dir: Directory for persistent storage
            socket_path: Unix socket path for IPC
            slack_token: Slack bot token (optional)
            slack_channel: Slack channel for session threads
        """
        # Prevent re-initialization of singleton
        if hasattr(self, '_initialized'):
            return

        # Use config defaults if not provided
        if registry_dir is None:
            registry_dir = os.path.dirname(get_registry_db_path())
        if socket_path is None:
            socket_path = os.path.join(get_socket_dir(), "registry.sock")

        self.registry_dir = Path(registry_dir)
        self.socket_path = socket_path
        self.slack_channel = slack_channel

        # Database backend (replaces JSON file + manual locking)
        db_path = self.registry_dir / "registry.db"
        self.db = RegistryDatabase(str(db_path))
        self._log(f"Database initialized: {db_path}")

        # Slack integration
        self.slack_client = None
        self.pinned_message_ts = None
        if slack_token and SLACK_AVAILABLE:
            try:
                # Set 3-second timeout to prevent blocking during registration
                self.slack_client = WebClient(token=slack_token, timeout=3)
                self._log("Slack integration enabled (3s timeout)")
            except Exception as e:
                self._log(f"Slack client init failed: {e}")
        elif not SLACK_AVAILABLE:
            self._log("Slack SDK not available, running without Slack integration")

        # Socket server
        self.server_socket = None
        self.server_thread = None
        self.running = False

        # Create directories
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        Path(socket_path).parent.mkdir(parents=True, exist_ok=True)

        self._initialized = True

        # Log existing sessions count
        sessions = self.db.list_sessions()
        self._log(f"Session registry initialized ({len(sessions)} existing sessions)")

    def _log(self, message: str):
        """Log message with timestamp"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[Registry {timestamp}] {message}", file=sys.stderr)

    def register_session(self, session_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Register a new session and create Slack thread

        Args:
            session_data: Session metadata with required fields:
                - session_id: Unique session identifier
                - project: Project name
                - terminal: Terminal identifier
                - socket_path: Unix socket path for this session
                Optional fields:
                - user_label: Human-readable description
                - vibe_tunnel_id: Vibe tunnel identifier

        Returns:
            Complete session data with thread_ts and channel added

        Raises:
            ValueError: If required fields missing or session already exists
        """
        # Validate required fields
        required_fields = ["session_id", "project", "terminal", "socket_path"]
        for field in required_fields:
            if field not in session_data:
                raise ValueError(f"Missing required field: {field}")

        session_id = session_data["session_id"]
        self._log(f"Registering session {session_id}")

        # Check if session already exists
        existing = self.db.get_session(session_id)
        if existing:
            raise ValueError(f"Session already registered: {session_id}")

        # Create session in database (atomic, no manual locking needed)
        session_record = self.db.create_session(session_data)
        self._log(f"Session {session_id} stored in database")

        # Create Slack thread ASYNCHRONOUSLY (don't block registration)
        if self.slack_client:
            self._log(f"Starting async Slack thread creation for {session_id}")
            def create_thread_async():
                try:
                    self._log(f"[Async] Creating Slack thread for {session_id}")
                    thread_data = self._create_slack_thread(session_data)

                    # Update session with thread info (atomic database update)
                    self.db.update_session(session_id, {
                        'slack_thread_ts': thread_data["slack_thread_ts"],
                        'slack_channel': thread_data["slack_channel"]
                    })

                    self._log(f"[Async] Created Slack thread for session {session_id}")
                except Exception as e:
                    self._log(f"[Async] Failed to create Slack thread: {e}")

            # Start thread creation in background
            thread = threading.Thread(target=create_thread_async, daemon=True)
            thread.start()
            self._log(f"Background thread started for {session_id}")

        self._log(f"Registered session: {session_id} ({session_data.get('project', 'unknown')})")

        return session_record

    def register_session_simple(self, session_id: str, project: str, terminal: str,
                                socket_path: str, slack_user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Simplified registration for hooks-based system

        Args:
            session_id: Unique session identifier
            project: Project name
            terminal: Terminal identifier
            socket_path: Unix socket path for this session
            slack_user_id: Slack user ID who created this session (optional)

        Returns:
            Complete session data with thread_ts and channel added
        """
        session_data = {
            "session_id": session_id,
            "project": project,
            "terminal": terminal,
            "socket_path": socket_path,
            "slack_user_id": slack_user_id
        }

        # Create in DB
        session = self.db.create_session(session_data)
        self._log(f"Session {session_id} created in database")

        # Create Slack thread synchronously (hooks need thread_ts immediately)
        if self.slack_client:
            try:
                self._log(f"Creating Slack thread for session {session_id}")
                thread_data = self._create_slack_thread(session_data)
                self._log(f"Thread data received: {thread_data}")
                self.db.update_session(session_id, thread_data)
                self._log(f"Database updated with thread data")
                session.update(thread_data)
                self._log(f"Slack thread created for session {session_id}: thread_ts={thread_data.get('slack_thread_ts')}")
            except Exception as e:
                self._log(f"Failed to create Slack thread: {e}")
                import traceback
                self._log(f"Traceback: {traceback.format_exc()}")

        return session

    def unregister_session(self, session_id: str) -> bool:
        """
        Unregister a session and archive Slack thread

        Args:
            session_id: Session identifier

        Returns:
            True if session was removed, False if not found
        """
        session = self.db.get_session(session_id)
        if not session:
            self._log(f"Session not found: {session_id}")
            return False

        # Archive Slack thread
        if self.slack_client and session.get("thread_ts"):
            try:
                self._archive_slack_thread(session)
            except Exception as e:
                self._log(f"Failed to archive Slack thread: {e}")

        # Remove from database (atomic operation)
        self.db.delete_session(session_id)

        # Update pinned message
        if self.slack_client:
            self._update_pinned_message()

        self._log(f"Unregistered session: {session_id}")

        return True

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get session data by session ID

        Args:
            session_id: Session identifier

        Returns:
            Session data dict or None if not found
        """
        return self.db.get_session(session_id)

    def list_sessions(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List all sessions, optionally filtered by status

        Args:
            status: Filter by status (active/idle/ended/crashed) or None for all

        Returns:
            List of session data dicts
        """
        return self.db.list_sessions(status)


    def get_by_thread(self, thread_ts: str) -> Optional[Dict[str, Any]]:
        """
        Reverse lookup: Get session by Slack thread timestamp

        Args:
            thread_ts: Slack thread timestamp

        Returns:
            Session data dict or None if not found
        """
        return self.db.get_by_thread(thread_ts)

    def cleanup_old_sessions(self, max_age_hours: int = 24) -> int:
        """
        Archive old ended/crashed sessions

        Args:
            max_age_hours: Maximum age in hours for ended sessions

        Returns:
            Number of sessions cleaned up
        """
        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
        cleaned = 0

        # Get all sessions that need cleanup
        all_sessions = self.db.list_sessions()
        sessions_to_remove = []

        for session in all_sessions:
            status = session.get("status")
            last_activity = session.get("last_activity", "")

            # Only cleanup ended/crashed sessions
            if status not in [SessionStatus.ENDED.value, SessionStatus.CRASHED.value]:
                continue

            try:
                last_activity_dt = datetime.fromisoformat(last_activity)
                if last_activity_dt < cutoff_time:
                    sessions_to_remove.append(session)
            except ValueError:
                # Invalid timestamp, cleanup anyway
                sessions_to_remove.append(session)

        # Remove sessions
        for session in sessions_to_remove:
            session_id = session['session_id']

            # Archive Slack thread
            if self.slack_client and session.get("thread_ts"):
                try:
                    self._archive_slack_thread(session)
                except Exception as e:
                    self._log(f"Failed to archive Slack thread: {e}")

            # Delete from database
            self.db.delete_session(session_id)
            cleaned += 1
            self._log(f"Cleaned up old session: {session_id}")

        if cleaned > 0:
            # Update pinned message
            if self.slack_client:
                self._update_pinned_message()

        return cleaned


    # ========================================
    # Unix Socket Server for IPC
    # ========================================

    def start_server(self):
        """Start Unix socket server in background thread"""
        if self.running:
            self._log("Server already running")
            return

        # Remove stale socket if exists
        socket_path = Path(self.socket_path)
        if socket_path.exists():
            try:
                socket_path.unlink()
                self._log(f"Removed stale socket: {self.socket_path}")
            except Exception as e:
                self._log(f"Failed to remove stale socket: {e}")
                raise

        # Create server socket
        try:
            self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind(self.socket_path)
            self.server_socket.listen(128)
            self.running = True
            self._log(f"Listening on {self.socket_path}")
        except Exception as e:
            self._log(f"Failed to create server socket: {e}")
            raise

        # Start server thread
        self.server_thread = threading.Thread(target=self._server_loop, daemon=True)
        self.server_thread.start()
        self._log("Server thread started")

    def stop_server(self):
        """Stop Unix socket server"""
        if not self.running:
            return

        self.running = False

        # Close socket
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception as e:
                self._log(f"Error closing socket: {e}")

        # Remove socket file
        socket_path = Path(self.socket_path)
        if socket_path.exists():
            try:
                socket_path.unlink()
            except Exception as e:
                self._log(f"Error removing socket: {e}")

        self._log("Server stopped")

    def _server_loop(self):
        """Server loop - handle incoming socket connections"""
        while self.running:
            try:
                # Accept connection with timeout
                self.server_socket.settimeout(1.0)
                try:
                    conn, _ = self.server_socket.accept()
                except socket.timeout:
                    continue

                # Handle connection in separate thread
                handler = threading.Thread(
                    target=self._handle_connection,
                    args=(conn,),
                    daemon=True
                )
                handler.start()

            except Exception as e:
                if self.running:
                    self._log(f"Server loop error: {e}")

    def _handle_connection(self, conn: socket.socket):
        """Handle individual socket connection"""
        try:
            self._log("Incoming connection received")

            # Receive data (expecting newline-terminated JSON)
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    self._log("Connection closed by client (no data)")
                    break
                data += chunk
                # Stop when we receive a newline (end of JSON message)
                if b"\n" in data:
                    self._log(f"Received complete message ({len(data)} bytes)")
                    break
                if len(data) > 1024 * 1024:  # 1MB limit
                    raise ValueError("Request too large")

            if not data:
                self._log("No data received, closing connection")
                return

            # Remove trailing newline
            data = data.rstrip(b"\n")
            self._log(f"Raw request: {data[:200]}")  # Log first 200 chars

            # Parse JSON command
            try:
                request = json.loads(data.decode('utf-8'))
                self._log(f"Parsed command: {request.get('command')}")
            except json.JSONDecodeError as e:
                self._log(f"JSON decode error: {e}")
                response = {"success": False, "error": "Invalid JSON"}
                conn.sendall((json.dumps(response) + "\n").encode('utf-8'))
                return

            # Process command
            response = self._process_command(request)
            self._log(f"Response: success={response.get('success')}")

            # Send response (with newline terminator)
            conn.sendall((json.dumps(response) + "\n").encode('utf-8'))

        except Exception as e:
            self._log(f"Error handling connection: {e}")
            error_response = {"success": False, "error": str(e)}
            try:
                conn.sendall((json.dumps(error_response) + "\n").encode('utf-8'))
            except:
                pass
        finally:
            conn.close()

    def _process_command(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process socket command

        Commands:
            REGISTER: {"command": "REGISTER", "data": {...}}
            REGISTER_SIMPLE: {"command": "REGISTER_SIMPLE", "data": {"session_id": "...", "project": "...", ...}}
            UNREGISTER: {"command": "UNREGISTER", "data": {"session_id": "..."}}
            GET: {"command": "GET", "data": {"session_id": "..."}}
            LIST: {"command": "LIST", "data": {"status": "active"}}  # status optional
        """
        command = request.get("command")
        data = request.get("data", {})

        try:
            if command == "REGISTER":
                self._log(f"Processing REGISTER command for {data.get('session_id', 'unknown')}")
                session = self.register_session(data)
                self._log(f"REGISTER completed, returning response")
                return {"success": True, "session": session}

            elif command == "REGISTER_SIMPLE":
                self._log(f"Processing REGISTER_SIMPLE command for {data.get('session_id', 'unknown')}")
                session = self.register_session_simple(
                    session_id=data.get("session_id"),
                    project=data.get("project"),
                    terminal=data.get("terminal"),
                    socket_path=data.get("socket_path"),
                    slack_user_id=data.get("slack_user_id")
                )
                self._log(f"REGISTER_SIMPLE completed, returning response")
                return {"success": True, "session": session}

            elif command == "REGISTER_EXISTING":
                # Register a new session ID pointing to an existing Slack thread
                # Used to register Claude's UUID with the same thread as the wrapper
                self._log(f"Processing REGISTER_EXISTING command for {data.get('session_id', 'unknown')}")
                session_id = data.get("session_id")
                thread_ts = data.get("thread_ts")
                channel = data.get("channel")

                if not session_id or not thread_ts or not channel:
                    return {"success": False, "error": "Missing required fields: session_id, thread_ts, channel"}

                # Create session with existing Slack metadata
                session_data = {
                    'session_id': session_id,
                    'project': data.get("project", "Unknown"),
                    'terminal': data.get("terminal", "Unknown"),
                    'socket_path': data.get("socket_path", ""),
                    'thread_ts': thread_ts,  # Note: create_session expects 'thread_ts' not 'slack_thread_ts'
                    'channel': channel,       # Note: create_session expects 'channel' not 'slack_channel'
                    'slack_user_id': data.get("slack_user_id")
                }
                session = self.db.create_session(session_data)
                self._log(f"REGISTER_EXISTING completed for {session_id} -> thread {thread_ts}")
                return {"success": True, "session": session}

            elif command == "UNREGISTER":
                session_id = data.get("session_id")
                if not session_id:
                    return {"success": False, "error": "Missing session_id"}
                result = self.unregister_session(session_id)
                return {"success": result}

            elif command == "GET":
                session_id = data.get("session_id")
                if not session_id:
                    return {"success": False, "error": "Missing session_id"}
                session = self.get_session(session_id)
                return {"success": True, "session": session}

            elif command == "LIST":
                status = data.get("status")
                sessions = self.list_sessions(status)
                return {"success": True, "sessions": sessions}

            else:
                return {"success": False, "error": f"Unknown command: {command}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    # ========================================
    # Slack Integration
    # ========================================

    def _create_slack_thread(self, session_data: Dict[str, Any]) -> Dict[str, str]:
        """
        Create Slack thread for new session (simplified for hooks-based system)

        Returns:
            {"thread_ts": "...", "channel": "..."}
        """
        if not self.slack_client:
            raise RuntimeError("Slack client not initialized")

        # Create simple parent message in channel (no status tracking)
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🚀 {session_data.get('project', 'Unknown')}"
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Session:* `{session_data['session_id'][:12]}...`"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Terminal:* {session_data.get('terminal', 'Unknown')}"
                    }
                ]
            }
        ]

        response = self.slack_client.chat_postMessage(
            channel=self.slack_channel,
            text=f"New Session: {session_data.get('project', 'Unknown')}",
            blocks=blocks
        )

        return {
            "slack_thread_ts": response["ts"],
            "slack_channel": response["channel"]
        }

    def _archive_slack_thread(self, session: Dict[str, Any]):
        """Archive Slack thread with final status"""
        if not self.slack_client or not session.get("thread_ts"):
            return

        status = session.get("status", "ended")
        emoji = "✅" if status == "ended" else "💥"

        self.slack_client.chat_postMessage(
            channel=session["channel"],
            thread_ts=session["thread_ts"],
            text=f"{emoji} Session {status} at {datetime.now().strftime('%H:%M:%S')}"
        )

    def _update_pinned_message(self):
        """Update pinned message with current active sessions (DISABLED for hooks-based system)"""
        # Pinned messages disabled - hooks-based system doesn't need status tracking
        return

        # Get active sessions
        active_sessions = self.list_sessions(status="active")

        # Build message blocks
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"📊 Active Sessions ({len(active_sessions)})"
                }
            }
        ]

        if not active_sessions:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "_No active sessions_"
                }
            })
        else:
            for session in active_sessions[:10]:  # Limit to 10
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"• *{session['project']}* - {session['terminal']} (`{session['session_id'][:12]}...`)"
                    }
                })

        # Find or create pinned message
        # Note: Implementation depends on storing pinned_message_ts
        # For now, just post a new message (Phase 3 can add pinning logic)
        try:
            self.slack_client.chat_postMessage(
                channel=self.slack_channel,
                text=f"Active Sessions: {len(active_sessions)}",
                blocks=blocks
            )
        except Exception as e:
            self._log(f"Failed to update pinned message: {e}")


# ========================================
# Example Usage / Testing
# ========================================

if __name__ == "__main__":
    import uuid

    # Check if we're in test mode (explicit flag)
    test_mode = os.getenv("TEST_MODE") == "1"

    if test_mode:
        print("=" * 60)
        print("Session Registry - Test Mode")
        print("=" * 60)
        registry_dir = os.path.join(os.path.dirname(get_registry_db_path()), "_test")
        socket_path = os.path.join(get_socket_dir(), "registry_test.sock")
    else:
        print("=" * 60)
        print("Session Registry - Production Mode")
        print("=" * 60)
        # Use config defaults (supports environment variables)
        registry_dir = os.getenv("REGISTRY_DATA_DIR", os.path.dirname(get_registry_db_path()))
        socket_dir = os.getenv("SLACK_SOCKET_DIR", get_socket_dir())
        socket_path = f"{socket_dir}/registry.sock"

        # Slack configuration
        slack_token = os.getenv("SLACK_BOT_TOKEN")
        slack_channel = os.getenv("SLACK_CHANNEL", "#claude-sessions").lstrip("#")

    # Initialize registry
    if test_mode or not os.getenv("SLACK_BOT_TOKEN"):
        # No Slack integration
        registry = SessionRegistry(
            registry_dir=registry_dir,
            socket_path=socket_path
        )
    else:
        # With Slack integration
        registry = SessionRegistry(
            registry_dir=registry_dir,
            socket_path=socket_path,
            slack_token=slack_token,
            slack_channel=slack_channel
        )

    # Start server
    print("\n1. Starting socket server...")
    registry.start_server()
    time.sleep(1)

    if not test_mode:
        # Production mode: just keep running
        print("✅ Registry is running and accepting connections")
        print("   Press Ctrl+C to stop")
        print()
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            print("\n👋 Shutting down registry...")
        sys.exit(0)

    # TEST MODE ONLY: Register test sessions
    print("\n2. Registering test sessions...")
    session1_id = str(uuid.uuid4())
    session1 = registry.register_session({
        "session_id": session1_id,
        "project": "btcbot",
        "terminal": "Terminal 1",
        "socket_path": f"/tmp/claude_socks/{session1_id}.sock",
        "user_label": "Bitcoin analysis"
    })
    print(f"   ✓ Session 1: {session1_id[:12]}...")

    session2_id = str(uuid.uuid4())
    session2 = registry.register_session({
        "session_id": session2_id,
        "project": "webapp",
        "terminal": "Terminal 2",
        "socket_path": f"/tmp/claude_socks/{session2_id}.sock"
    })
    print(f"   ✓ Session 2: {session2_id[:12]}...")

    # List sessions
    print("\n3. Listing all sessions...")
    sessions = registry.list_sessions()
    for s in sessions:
        print(f"   - {s['project']} ({s['status']}) - {s['session_id'][:12]}...")

    # Test socket communication
    print("\n4. Testing socket communication...")
    try:
        client_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client_socket.connect(registry.socket_path)

        # Send LIST command
        request = {"command": "LIST"}
        client_socket.sendall(json.dumps(request).encode('utf-8'))

        # Receive response
        response_data = client_socket.recv(4096)
        response = json.loads(response_data.decode('utf-8'))

        print(f"   ✓ Socket response: {response['success']}, {len(response['sessions'])} sessions")
        client_socket.close()
    except Exception as e:
        print(f"   ✗ Socket test failed: {e}")

    # Cleanup
    print("\n5. Unregistering sessions...")
    registry.unregister_session(session1_id)
    registry.unregister_session(session2_id)
    print(f"   ✓ Unregistered all sessions")

    # Stop server
    print("\n6. Stopping server...")
    registry.stop_server()
    print("   ✓ Server stopped")

    print("\n" + "=" * 60)
    print("All tests completed successfully!")
    print("=" * 60)
