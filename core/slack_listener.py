#!/usr/bin/env python3
"""
Slack Bot - Listens for messages and sends responses to Claude Code

This bot runs continuously in the background and:
1. Listens for messages in channels where it's invited
2. Listens for direct messages
3. Listens for @mentions
4. Listens for threaded replies (routes to correct session)
5. Sends responses to Claude Code via Unix socket or file
6. Acknowledges receipt with a checkmark reaction

Phase 3 Mode (registry-based routing, preferred):
    - Queries registry database to find session by thread_ts
    - Routes threaded messages to correct session socket
    - Supports multiple concurrent Claude sessions in different threads

Phase 2 Mode (legacy hard-coded socket):
    - Sends to Unix socket at /tmp/claude_slack.sock
    - Used for non-threaded messages as fallback

Phase 1 Mode (file-based fallback):
    - Writes to slack_response.txt
    - User runs /check command to read responses

Usage:
    python3 slack_listener.py

Environment Variables:
    SLACK_BOT_TOKEN - Bot User OAuth Token (required)
    SLACK_APP_TOKEN - App-Level Token for Socket Mode (required)
    SLACK_SOCKET_PATH - Unix socket path (default: /tmp/claude_slack.sock)

Registry Database:
    Location: ~/.claude/slack/registry.db (default, override via REGISTRY_DB_PATH)
    Schema: sessions table with slack_thread_ts -> socket_path mapping
    Handles multiple sessions per thread (wrapper + Claude UUID)
"""

import os
import sys
import socket as sock_module
from pathlib import Path
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from registry_db import RegistryDatabase
from config import get_registry_db_path, get_socket_dir
from dotenv import load_dotenv

# Load environment variables from .env file (in parent directory)
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
load_dotenv(env_path)

# Configuration - use centralized config for consistent paths
PROJECT_DIR = Path(__file__).parent.parent
RESPONSE_FILE = PROJECT_DIR / "slack_response.txt"
SOCKET_PATH = os.environ.get("SLACK_SOCKET_PATH", "/tmp/claude_slack.sock")
REGISTRY_DB_PATH = get_registry_db_path()  # Uses ~/.claude/slack/registry.db by default

# Initialize registry database - create directory and DB if needed
registry_db = None
try:
    registry_dir = os.path.dirname(REGISTRY_DB_PATH)

    # Create directory if it doesn't exist
    if not os.path.exists(registry_dir):
        os.makedirs(registry_dir, exist_ok=True)
        print(f"📁 Created registry directory: {registry_dir}", file=sys.stderr)

    # Initialize database (creates tables if they don't exist)
    registry_db = RegistryDatabase(REGISTRY_DB_PATH)
    print(f"✅ Connected to registry database: {REGISTRY_DB_PATH}", file=sys.stderr)
except Exception as e:
    print(f"⚠️  Failed to initialize registry database: {e}", file=sys.stderr)
    print(f"   Falling back to hard-coded socket path", file=sys.stderr)

# Initialize Slack app
try:
    app = App(token=os.environ["SLACK_BOT_TOKEN"])
except KeyError:
    print("❌ Error: SLACK_BOT_TOKEN environment variable not set", file=sys.stderr)
    print("   Create a .env file from .env.example and set your tokens", file=sys.stderr)
    sys.exit(1)


def get_socket_for_thread(thread_ts):
    """
    Look up socket path for a Slack thread using the registry database

    Args:
        thread_ts: Slack thread timestamp (e.g., "1762285247.297999")

    Returns:
        str: Socket path for the session, or None if not found

    Note:
        - Queries registry database to find session with matching thread_ts
        - Multiple sessions might have same thread_ts (wrapper + Claude UUID)
        - Prefers session with shortest session_id (8 chars = wrapper)
        - Falls back to any session if wrapper not found
    """
    if not registry_db:
        print(f"⚠️  No registry database - cannot lookup socket for thread {thread_ts}", file=sys.stderr)
        return None

    try:
        # Query all sessions with this thread_ts
        # (there might be multiple: wrapper session + Claude UUID session)
        with registry_db.session_scope() as session:
            from registry_db import SessionRecord
            records = session.query(SessionRecord).filter_by(
                slack_thread_ts=thread_ts,
                status='active'
            ).all()

            if not records:
                print(f"⚠️  No active session found for thread {thread_ts}", file=sys.stderr)
                return None

            # Prefer the wrapper session (8 chars) over Claude UUID (36 chars)
            # The wrapper session is the one that owns the socket
            wrapper_session = None
            fallback_session = None

            for record in records:
                if len(record.session_id) == 8:
                    wrapper_session = record
                    break
                else:
                    fallback_session = record

            chosen = wrapper_session or fallback_session

            if chosen:
                print(f"✅ Found socket for thread {thread_ts}: {chosen.socket_path} (session {chosen.session_id})", file=sys.stderr)
                return chosen.socket_path
            else:
                print(f"⚠️  Session found but no socket path for thread {thread_ts}", file=sys.stderr)
                return None

    except Exception as e:
        print(f"❌ Error querying registry for thread {thread_ts}: {e}", file=sys.stderr)
        return None


def _handle_slack_toggle(thread_ts: str, enabled: bool, channel: str, message_ts: str, say):
    """Toggle Slack mirroring for a session via Slack command."""
    try:
        db = RegistryDatabase(REGISTRY_DB_PATH)
        session = db.get_by_thread(thread_ts)

        if not session:
            say(f"⚠️ No active session found for this thread.", thread_ts=thread_ts)
            return

        session_id = session.get('session_id')

        # Update ALL sessions with this thread_ts (wrapper + Claude sessions)
        import sqlite3
        conn = sqlite3.connect(REGISTRY_DB_PATH)
        conn.execute("UPDATE sessions SET slack_enabled=? WHERE slack_thread_ts=?",
                     ('true' if enabled else 'false', thread_ts))
        conn.commit()
        conn.close()

        status = "ENABLED ✅" if enabled else "DISABLED 🔇"
        say(f"Slack mirroring {status} for this session.", thread_ts=thread_ts)
        print(f"🔄 Slack mirroring {'enabled' if enabled else 'disabled'} for session {session_id}")

        # Add reaction to acknowledge
        try:
            app.client.reactions_add(channel=channel, timestamp=message_ts, name="white_check_mark")
        except:
            pass

    except Exception as e:
        print(f"❌ Error toggling slack for thread {thread_ts}: {e}", file=sys.stderr)
        say(f"⚠️ Error toggling Slack mirroring: {e}", thread_ts=thread_ts)


def _handle_slack_status(thread_ts: str, channel: str, message_ts: str, say):
    """Check Slack mirroring status for a session."""
    try:
        db = RegistryDatabase(REGISTRY_DB_PATH)
        session = db.get_by_thread(thread_ts)

        if not session:
            say(f"⚠️ No active session found for this thread.", thread_ts=thread_ts)
            return

        enabled = session.get('slack_enabled', True)
        status = "ENABLED ✅" if enabled else "DISABLED 🔇"
        say(f"Slack mirroring is currently {status} for this session.", thread_ts=thread_ts)

        # Add reaction
        try:
            app.client.reactions_add(channel=channel, timestamp=message_ts, name="white_check_mark")
        except:
            pass

    except Exception as e:
        print(f"❌ Error checking slack status for thread {thread_ts}: {e}", file=sys.stderr)
        say(f"⚠️ Error checking status: {e}", thread_ts=thread_ts)


def _handle_restart(thread_ts: str, channel: str, message_ts: str, say):
    """
    Handle !restart command - kill current session and start a new one.

    This allows remote session management without needing terminal access.
    """
    import signal
    import subprocess
    import time

    try:
        db = RegistryDatabase(REGISTRY_DB_PATH)
        session = db.get_active_session_by_thread(thread_ts, channel)

        if not session:
            say(f"⚠️ No active session found for this thread.", thread_ts=thread_ts)
            return

        project_dir = session.get('project_dir')
        wrapper_pid = session.get('wrapper_pid')
        session_id = session.get('session_id')

        if not project_dir:
            say(f"⚠️ Session {session_id[:8]} has no project_dir recorded. Cannot restart.", thread_ts=thread_ts)
            return

        say(f"🔄 Restarting session...", thread_ts=thread_ts)
        print(f"🔄 Restart requested for session {session_id} (PID: {wrapper_pid})", file=sys.stderr)

        # Step 1: Kill the old session
        if wrapper_pid:
            try:
                os.kill(wrapper_pid, signal.SIGTERM)
                print(f"   Sent SIGTERM to PID {wrapper_pid}", file=sys.stderr)
                time.sleep(2)  # Give it time to cleanup

                # Force kill if still alive
                try:
                    os.kill(wrapper_pid, signal.SIGKILL)
                    print(f"   Sent SIGKILL to PID {wrapper_pid}", file=sys.stderr)
                except ProcessLookupError:
                    pass  # Already dead, good

            except ProcessLookupError:
                print(f"   Process {wrapper_pid} already gone", file=sys.stderr)

        # Step 2: Mark old session as ended
        db.end_session(session_id)
        print(f"   Marked session {session_id} as ended", file=sys.stderr)

        # Step 3: Find claude-slack binary
        claude_slack_bin = os.path.expanduser("~/.claude/claude-slack/bin/claude-slack")
        if not os.path.exists(claude_slack_bin):
            # Try the symlink location
            claude_slack_bin = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin", "claude-slack")

        if not os.path.exists(claude_slack_bin):
            say(f"❌ Could not find claude-slack binary", thread_ts=thread_ts)
            return

        # Step 4: Start new session in background
        print(f"   Starting new session in {project_dir}", file=sys.stderr)
        subprocess.Popen(
            [claude_slack_bin],
            cwd=project_dir,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Step 5: Wait for new session to register
        time.sleep(5)
        new_session = db.get_latest_session_for_project(project_dir)

        if new_session:
            new_id = new_session.get('session_id', 'unknown')[:8]
            say(f"✅ Session restarted (new ID: {new_id})", thread_ts=thread_ts)
            print(f"✅ Restart complete - new session {new_id}", file=sys.stderr)
        else:
            say(f"⚠️ New session started but not yet registered. Check terminal.", thread_ts=thread_ts)
            print(f"⚠️ New session not found in registry after restart", file=sys.stderr)

        # Add reaction
        try:
            app.client.reactions_add(channel=channel, timestamp=message_ts, name="arrows_counterclockwise")
        except:
            pass

    except Exception as e:
        print(f"❌ Error restarting session for thread {thread_ts}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        say(f"❌ Restart failed: {e}", thread_ts=thread_ts)


def send_response(text, thread_ts=None):
    """
    Send response to Claude Code

    Phase 3 Mode (registry-based, preferred):
        If thread_ts provided, lookup socket from registry
        Send to correct session socket for that thread

    Phase 2 Mode (legacy hard-coded):
        Send to hard-coded socket path (backward compatible)

    Phase 1 Mode (fallback):
        Write to file if socket doesn't exist
        User must run /check to read response

    Args:
        text: The response text to send
        thread_ts: Slack thread timestamp (for registry lookup)

    Returns:
        str: Mode used ("registry_socket", "socket", or "file")
    """
    socket_path = None

    # Phase 3: Try registry lookup first (if thread_ts provided)
    if thread_ts:
        socket_path = get_socket_for_thread(thread_ts)
        if socket_path:
            print(f"📋 Using registry socket for thread {thread_ts}: {socket_path}", file=sys.stderr)

    # Phase 2: Fall back to hard-coded socket path
    if not socket_path:
        socket_path = SOCKET_PATH if os.path.exists(SOCKET_PATH) else None

    # Try sending via socket with retries
    if socket_path and os.path.exists(socket_path):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Connect to wrapper's Unix socket
                client_socket = sock_module.socket(sock_module.AF_UNIX, sock_module.SOCK_STREAM)
                client_socket.settimeout(5.0)  # 5 second timeout
                client_socket.connect(socket_path)

                # Send response
                client_socket.sendall(text.encode('utf-8'))
                client_socket.close()

                mode = "registry_socket" if thread_ts else "socket"
                print(f"✅ Sent via {mode}: {text[:100]}", file=sys.stderr)
                return mode

            except Exception as e:
                if attempt < max_retries - 1:
                    backoff = 0.1 * (3 ** attempt)  # 0.1s, 0.3s, 0.9s
                    print(f"⚠️  Socket attempt {attempt + 1} failed, retrying in {backoff}s: {e}", file=sys.stderr)
                    import time
                    time.sleep(backoff)
                else:
                    print(f"⚠️  Socket send failed after {max_retries} attempts, falling back to file: {e}", file=sys.stderr)
                    # Fall through to file mode

    # Fall back to Phase 1 (file)
    with open(RESPONSE_FILE, "w") as f:
        f.write(text)

    print(f"✅ Wrote to file (Phase 1 - manual /check): {text[:100]}", file=sys.stderr)
    return "file"


@app.event("app_mention")
def handle_mention(event, say):
    """
    Handle @bot mentions in channels

    Example:
        User: "@ClaudeBot yes, proceed with analysis"
        Bot: Sends "yes, proceed with analysis" to Claude Code
    """
    user = event.get("user")
    text = event.get("text", "")
    channel = event.get("channel")
    thread_ts = event.get("thread_ts")  # Extract thread timestamp

    # Remove bot mention from text
    # Format is typically: "<@U12345>, your message here" or "<@U12345> your message here"
    clean_text = text.split(">", 1)[-1].strip()

    # Remove leading punctuation (comma, colon, etc.) that may follow the mention
    clean_text = clean_text.lstrip(',: ').strip()

    if not clean_text:
        say("👋 Hi! Send me a message and I'll forward it to Claude Code.")
        return

    # Send response to Claude Code (registry socket, legacy socket, or file)
    mode = send_response(clean_text, thread_ts=thread_ts)

    # Acknowledge with reaction
    try:
        app.client.reactions_add(
            channel=channel,
            timestamp=event["ts"],
            name="white_check_mark"
        )
    except Exception as e:
        print(f"⚠️  Warning: Could not add reaction: {e}", file=sys.stderr)

    # Confirm receipt with mode indicator (post to thread if in thread, otherwise channel)
    mode_emoji = "📋" if mode == "registry_socket" else ("⚡" if mode == "socket" else "📁")
    confirm_msg = f"✅ {mode_emoji} Got it! Sent to Claude: `{clean_text[:100]}`"
    thread_info = f" (thread {thread_ts})" if thread_ts else ""

    if thread_ts:
        # Post confirmation in the thread
        say(text=confirm_msg, thread_ts=thread_ts)
    else:
        # Post confirmation in the channel
        say(confirm_msg)
    print(f"📝 Sent mention from user {user}{thread_info}: {clean_text[:100]}")


@app.event("message")
def handle_message(event, say):
    """
    Handle direct messages and channel messages (including threaded replies)

    Ignores:
    - Bot messages (to avoid loops)
    - Empty messages

    Supports:
    - Direct messages
    - Channel messages with command prefix (/, !, or digits)
    - Threaded messages (uses registry to route to correct session)
    """
    # Ignore bot messages
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return

    text = event.get("text", "").strip()
    channel_type = event.get("channel_type")
    user = event.get("user")
    channel = event.get("channel")
    thread_ts = event.get("thread_ts")  # Extract thread timestamp for routing

    if not text:
        return

    # Only process direct messages or messages in channels we're monitoring
    # This prevents responding to every message in every channel
    is_dm = channel_type == "im"

    # For channel messages (not in threads), only process if the message starts with a command prefix
    # For threaded messages, process all messages (they're replies to Claude)
    if not is_dm and not thread_ts:
        # Skip messages that don't look like commands
        # Allow: /command, !command, or plain numbers (1, 2, 3)
        if not (text.startswith('/') or text.startswith('!') or text.isdigit()):
            return

    # Handle !slack on/off toggle commands (with shortcuts: !on, !off, !status)
    text_lower = text.lower().strip()
    if text_lower in ('!slack on', '!slack enable', '/slack on', '/slack enable', '!on', '/on', '!enable'):
        if thread_ts:
            _handle_slack_toggle(thread_ts, True, channel, event["ts"], say)
        else:
            say("⚠️ Use this command in a session thread to enable Slack mirroring.")
        return
    elif text_lower in ('!slack off', '!slack disable', '/slack off', '/slack disable', '!off', '/off', '!disable'):
        if thread_ts:
            _handle_slack_toggle(thread_ts, False, channel, event["ts"], say)
        else:
            say("⚠️ Use this command in a session thread to disable Slack mirroring.")
        return
    elif text_lower in ('!slack status', '/slack status', '!status', '/status'):
        if thread_ts:
            _handle_slack_status(thread_ts, channel, event["ts"], say)
        else:
            say("⚠️ Use this command in a session thread to check Slack mirroring status.")
        return
    elif text_lower in ('!restart', '/restart'):
        if thread_ts:
            _handle_restart(thread_ts, channel, event["ts"], say)
        else:
            say("⚠️ Use this command in a session thread to restart the session.")
        return

    # Auto-enable mirroring if sending a message from Slack while disabled
    # This ensures you see Claude's response when you engage from Slack
    if thread_ts and registry_db:
        session = registry_db.get_by_thread(thread_ts)
        if session and session.get('slack_enabled') in (False, 'false'):
            import sqlite3
            conn = sqlite3.connect(REGISTRY_DB_PATH)
            conn.execute("UPDATE sessions SET slack_enabled='true' WHERE slack_thread_ts=?", (thread_ts,))
            conn.commit()
            conn.close()
            print(f"🔔 Auto-enabled mirroring for thread {thread_ts} (message sent while disabled)", file=sys.stderr)
            say(text="✅ _Slack mirroring auto-enabled since you sent a message._", thread_ts=thread_ts)

    # Send response to Claude Code (registry socket, legacy socket, or file)
    mode = send_response(text, thread_ts=thread_ts)

    # Acknowledge with reaction
    try:
        app.client.reactions_add(
            channel=channel,
            timestamp=event["ts"],
            name="white_check_mark"
        )
    except Exception as e:
        print(f"⚠️  Warning: Could not add reaction: {e}", file=sys.stderr)

    response_type = "thread reply" if thread_ts else ("DM" if is_dm else "channel message")
    thread_info = f" in thread {thread_ts}" if thread_ts else ""
    print(f"📝 Sent {response_type} from user {user} via {mode}{thread_info}: {text[:100]}")


@app.event("reaction_added")
def handle_reaction(body, client):
    """
    Handle emoji reactions as quick numeric responses.

    Maps emoji reactions to number inputs for fast permission responses:
    - 1️⃣ / 👍 → "1" (approve this time)
    - 2️⃣ → "2" (approve for session/project)
    - 3️⃣ / 👎 → "3" (deny)
    """
    # Extract the inner event payload from the body
    event = body.get("event", {})

    print(f"📌 Reaction event received: {event}", file=sys.stderr)

    # Ignore bot's own reactions
    try:
        bot_user_id = client.auth_test()["user_id"]
        if event.get("user") == bot_user_id:
            print(f"📌 Ignoring bot's own reaction", file=sys.stderr)
            return
    except Exception as e:
        print(f"⚠️  Could not check bot user id: {e}", file=sys.stderr)

    emoji_name = event.get("reaction")
    item = event.get("item", {})
    channel = item.get("channel")
    message_ts = item.get("ts")
    user = event.get("user")

    print(f"📌 Parsed: emoji={emoji_name}, channel={channel}, ts={message_ts}, user={user}", file=sys.stderr)

    # Map emoji names to numeric responses
    emoji_to_number = {
        # Number emojis
        "one": "1",
        "two": "2",
        "three": "3",
        "four": "4",
        "five": "5",
        # Thumbs emojis as shortcuts
        "+1": "1",           # 👍 = approve
        "thumbsup": "1",
        "-1": "3",           # 👎 = deny
        "thumbsdown": "3",
        # Check/X emojis
        "white_check_mark": "1",  # ✅ = approve
        "x": "3",                  # ❌ = deny
        "heavy_check_mark": "1",
    }

    response = emoji_to_number.get(emoji_name)
    if not response:
        # Unmapped emoji, ignore
        return

    # Get thread_ts for routing - need to find the THREAD's parent ts, not the message ts
    # Fetch the message to get its thread_ts (parent of the thread)
    thread_ts = None
    try:
        # Get the message that was reacted to
        result = client.conversations_history(
            channel=channel,
            latest=message_ts,
            inclusive=True,
            limit=1
        )
        if result.get("messages"):
            msg = result["messages"][0]
            # thread_ts is the parent message ts (or the message itself if it's the parent)
            thread_ts = msg.get("thread_ts", message_ts)
            print(f"📌 Found thread_ts: {thread_ts} for message {message_ts}", file=sys.stderr)
    except Exception as e:
        print(f"⚠️  Could not fetch message for thread_ts: {e}", file=sys.stderr)
        # Fall back to message_ts
        thread_ts = message_ts

    # Send the numeric response to Claude
    mode = send_response(response, thread_ts=thread_ts)

    # Log the reaction-to-input conversion
    print(f"📌 Reaction '{emoji_name}' from user {user} → sent '{response}' via {mode}", file=sys.stderr)

    # Add a checkmark to confirm the reaction was processed
    try:
        client.reactions_add(
            channel=channel,
            timestamp=message_ts,
            name="white_check_mark"
        )
        print(f"📌 Added confirmation checkmark", file=sys.stderr)
    except Exception as e:
        print(f"⚠️  Could not add confirmation reaction: {e}", file=sys.stderr)


# ========================================
# Button Action Handlers
# ========================================

@app.action("slack_mirror_on")
def handle_mirror_on(ack, body, client):
    """Handle 'On' button click to enable Slack mirroring."""
    ack()

    action = body.get("actions", [{}])[0]
    session_id = action.get("value", "")
    channel = body.get("channel", {}).get("id")
    thread_ts = body.get("message", {}).get("ts")
    user = body.get("user", {}).get("id")

    print(f"🔔 Mirror ON button clicked by {user} for session {session_id[:8]}", file=sys.stderr)

    if not registry_db:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="⚠️ Registry not available"
        )
        return

    # Update ALL sessions with this thread_ts
    import sqlite3
    conn = sqlite3.connect(REGISTRY_DB_PATH)
    conn.execute("UPDATE sessions SET slack_enabled='true' WHERE slack_thread_ts=?", (thread_ts,))
    conn.commit()
    conn.close()

    client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text="✅ Slack mirroring *enabled* - you'll see Claude's responses here."
    )


@app.action("slack_mirror_off")
def handle_mirror_off(ack, body, client):
    """Handle 'Off' button click to disable Slack mirroring."""
    ack()

    action = body.get("actions", [{}])[0]
    session_id = action.get("value", "")
    channel = body.get("channel", {}).get("id")
    thread_ts = body.get("message", {}).get("ts")
    user = body.get("user", {}).get("id")

    print(f"🔇 Mirror OFF button clicked by {user} for session {session_id[:8]}", file=sys.stderr)

    if not registry_db:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="⚠️ Registry not available"
        )
        return

    # Update ALL sessions with this thread_ts
    import sqlite3
    conn = sqlite3.connect(REGISTRY_DB_PATH)
    conn.execute("UPDATE sessions SET slack_enabled='false' WHERE slack_thread_ts=?", (thread_ts,))
    conn.commit()
    conn.close()

    client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text="🔇 Slack mirroring *disabled* - Claude's responses won't appear here.\n_Send any message to re-enable._"
    )


@app.action("slack_mirror_status")
def handle_mirror_status(ack, body, client):
    """Handle 'Status' button click to show current session status."""
    ack()

    action = body.get("actions", [{}])[0]
    session_id = action.get("value", "")
    channel = body.get("channel", {}).get("id")
    thread_ts = body.get("message", {}).get("ts")

    print(f"📊 Status button clicked for session {session_id[:8]}", file=sys.stderr)

    if not registry_db:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="⚠️ Registry not available"
        )
        return

    # Get session info
    session = registry_db.get_by_thread(thread_ts)
    if not session:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="⚠️ Session not found in registry"
        )
        return

    slack_enabled = session.get('slack_enabled', 'true')
    mirror_status = "✅ Enabled" if slack_enabled in (True, 'true') else "🔇 Disabled"
    status = session.get('status', 'unknown')

    client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=f"📊 *Session Status*\n• Mirroring: {mirror_status}\n• Status: {status}\n• Session: `{session_id[:8]}`"
    )


def main():
    """Start the Slack bot in Socket Mode"""
    # Ensure single instance via PID file
    import fcntl
    PID_FILE = os.path.expanduser("~/.claude/slack/slack_listener.pid")

    # Create directory if needed
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)

    # Try to acquire exclusive lock
    pid_fp = open(PID_FILE, 'w')
    try:
        fcntl.flock(pid_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pid_fp.write(str(os.getpid()))
        pid_fp.flush()
    except IOError:
        print("❌ Another slack_listener instance is already running", file=sys.stderr)
        print(f"   PID file: {PID_FILE}", file=sys.stderr)
        sys.exit(1)

    print("🚀 Starting Slack bot...")
    print(f"📁 Response file (fallback): {RESPONSE_FILE}")
    print(f"🔌 Legacy socket path: {SOCKET_PATH}")
    print(f"📋 Registry database: {REGISTRY_DB_PATH}")

    # Check routing mode
    if registry_db:
        print("📋 Phase 3 Mode: Registry-based routing enabled")
        print("   - Threaded messages routed to correct session via registry lookup")
        print("   - Non-threaded messages fall back to legacy socket")
    elif os.path.exists(SOCKET_PATH):
        print("⚡ Phase 2 Mode: Legacy socket routing (no registry)")
    else:
        print("📁 Phase 1 Mode: File-based (use /check in Claude Code)")

    # Verify app token
    try:
        app_token = os.environ["SLACK_APP_TOKEN"]
    except KeyError:
        print("❌ Error: SLACK_APP_TOKEN environment variable not set", file=sys.stderr)
        print("   Socket Mode requires an app-level token", file=sys.stderr)
        sys.exit(1)

    # Start Socket Mode handler
    handler = SocketModeHandler(app, app_token)

    print("\n✅ Slack bot is running!")
    print("   Listening for:")
    print("   - @mentions in channels (and threads)")
    print("   - Direct messages")
    print("   - Channel messages starting with / or !")
    print("   - Single digit responses (1, 2, 3)")
    print("   - Threaded replies (routed to correct session)")
    print("")
    print("   Press Ctrl+C to stop")
    print("")

    try:
        handler.start()
    except KeyboardInterrupt:
        print("\n👋 Slack bot stopped")
    finally:
        # Release PID lock
        try:
            fcntl.flock(pid_fp, fcntl.LOCK_UN)
            pid_fp.close()
            os.remove(PID_FILE)
        except:
            pass
        sys.exit(0)


if __name__ == "__main__":
    main()
