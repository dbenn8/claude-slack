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

Phase 2 Mode (legacy socket):
    - Sends to Unix socket at ~/.claude/slack/sockets/claude_slack.sock
    - Used for non-threaded messages as fallback

Phase 1 Mode (file-based fallback):
    - Writes to slack_response.txt
    - User runs /check command to read responses

Usage:
    python3 slack_listener.py

Environment Variables:
    SLACK_BOT_TOKEN - Bot User OAuth Token (required)
    SLACK_APP_TOKEN - App-Level Token for Socket Mode (required)
    SLACK_SOCKET_PATH - Unix socket path (default: ~/.claude/slack/sockets/claude_slack.sock)

Registry Database:
    Location: ~/.claude/slack/registry.db (default, override via REGISTRY_DB_PATH)
    Schema: sessions table with slack_thread_ts -> socket_path mapping
    Handles multiple sessions per thread (wrapper + Claude UUID)
"""

import os
import sys
import json
import time
import fcntl
import socket as sock_module
from pathlib import Path
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from registry_db import RegistryDatabase
from config import get_registry_db_path, get_socket_dir
from dotenv import load_dotenv

# AskUserQuestion response handling
ASKUSER_RESPONSE_DIR = Path.home() / ".claude" / "slack" / "askuser_responses"
ASKUSER_RESPONSE_DIR.mkdir(parents=True, exist_ok=True)

# Load environment variables from .env file (in parent directory)
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
load_dotenv(env_path)

# Configuration - use centralized config for consistent paths
PROJECT_DIR = Path(__file__).parent.parent
RESPONSE_FILE = PROJECT_DIR / "slack_response.txt"
SOCKET_DIR = get_socket_dir()
SOCKET_PATH = os.environ.get("SLACK_SOCKET_PATH", os.path.join(SOCKET_DIR, "claude_slack.sock"))
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
# Note: We defer the sys.exit() to main() so that tests can import this module
# without requiring SLACK_BOT_TOKEN to be set
_slack_app_error = None
try:
    app = App(token=os.environ["SLACK_BOT_TOKEN"])
except KeyError:
    _slack_app_error = "SLACK_BOT_TOKEN environment variable not set"
    # Create a dummy app for testing - decorators will work but do nothing
    class _DummyClient:
        """Dummy client that returns safe defaults for all methods."""
        def __getattr__(self, name):
            # Return a callable that returns empty dict for any method
            return lambda *args, **kwargs: {}

    class _DummyApp:
        """Dummy App class that accepts decorators but does nothing."""
        def __init__(self):
            self.client = _DummyClient()
        def event(self, *args, **kwargs):
            return lambda f: f
        def action(self, *args, **kwargs):
            return lambda f: f
        def message(self, *args, **kwargs):
            return lambda f: f
        def shortcut(self, *args, **kwargs):
            return lambda f: f
        def view(self, *args, **kwargs):
            return lambda f: f
    app = _DummyApp()


def atomic_write_response_file(response_file: Path, data: dict) -> bool:
    """Atomically write response data to file with locking.

    Uses a lock file pattern to prevent race conditions when the hook
    is reading/deleting the file while this function is writing to it.

    Args:
        response_file: Path to the response file to write
        data: Dictionary data to write as JSON

    Returns:
        bool: True if write succeeded, False otherwise
    """
    lock_file = Path(str(response_file) + '.lock')

    try:
        # Create and lock
        with open(lock_file, 'w') as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)  # Exclusive lock

            # Write data
            with open(response_file, 'w') as f:
                json.dump(data, f)

            print(f"✅ Atomically wrote response file: {response_file}", file=sys.stderr)
            return True

    except Exception as e:
        print(f"❌ Error in atomic write: {e}", file=sys.stderr)
        return False
    finally:
        # Clean up lock file
        try:
            if lock_file.exists():
                lock_file.unlink()
        except:
            pass


def atomic_read_and_update_response_file(response_file: Path, update_data: dict) -> bool:
    """Atomically read existing response, merge with new data, and write back.

    Uses a lock file pattern to prevent race conditions.

    Args:
        response_file: Path to the response file
        update_data: Dictionary data to merge with existing data

    Returns:
        bool: True if operation succeeded, False otherwise
    """
    lock_file = Path(str(response_file) + '.lock')

    try:
        # Create and lock
        with open(lock_file, 'w') as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)  # Exclusive lock

            # Load existing data if file exists
            existing_data = {}
            if response_file.exists():
                try:
                    with open(response_file) as f:
                        existing_data = json.load(f)
                    print(f"📖 Loaded existing response: {existing_data}", file=sys.stderr)
                except Exception as e:
                    print(f"⚠️  Could not load existing response: {e}", file=sys.stderr)

            # Merge new data
            existing_data.update(update_data)

            # Write merged data back
            with open(response_file, 'w') as f:
                json.dump(existing_data, f)

            print(f"✅ Atomically updated response file: {response_file}", file=sys.stderr)
            return True

    except Exception as e:
        print(f"❌ Error in atomic update: {e}", file=sys.stderr)
        return False
    finally:
        # Clean up lock file
        try:
            if lock_file.exists():
                lock_file.unlink()
        except:
            pass


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


def get_socket_for_channel(channel):
    """
    Look up socket path for a custom channel session (where thread_ts is None).

    This is used for custom channel mode where messages are posted as top-level
    messages instead of in threads.

    Args:
        channel: Slack channel ID (e.g., "C1234567890") or channel name

    Returns:
        str: Socket path for the session, or None if not found

    Note:
        - Only matches sessions where thread_ts is NULL (custom channel mode)
        - Prefers session with shortest session_id (8 chars = wrapper)
        - Resolves channel ID to name for matching (DB stores names)
    """
    if not registry_db:
        print(f"⚠️  No registry database - cannot lookup socket for channel {channel}", file=sys.stderr)
        return None

    try:
        # Resolve channel ID to name if it looks like an ID (starts with C)
        channel_name = channel
        if channel and channel.startswith('C'):
            try:
                result = app.client.conversations_info(channel=channel)
                if result.get("ok") and result.get("channel"):
                    channel_name = result["channel"].get("name", channel)
                    print(f"📋 Resolved channel ID {channel} to name: {channel_name}", file=sys.stderr)
            except Exception as e:
                print(f"⚠️  Could not resolve channel ID {channel}: {e}", file=sys.stderr)
                # Continue with the ID as fallback

        with registry_db.session_scope() as session:
            from registry_db import SessionRecord
            # Find sessions for this channel where thread_ts is NULL (custom channel mode)
            # Try both channel ID and resolved name
            records = session.query(SessionRecord).filter(
                SessionRecord.slack_channel.in_([channel, channel_name]),
                SessionRecord.slack_thread_ts.is_(None),
                SessionRecord.status == 'active'
            ).all()

            if not records:
                print(f"⚠️  No active custom channel session found for channel {channel} (name: {channel_name})", file=sys.stderr)
                return None

            # Prefer the wrapper session (8 chars) over Claude UUID (36 chars)
            # BUT only if the socket file actually exists (filter out stale sessions)
            wrapper_session = None
            fallback_session = None

            for record in records:
                # Skip sessions whose socket doesn't exist (stale)
                if not record.socket_path or not os.path.exists(record.socket_path):
                    print(f"⚠️  Skipping stale session {record.session_id} - socket doesn't exist", file=sys.stderr)
                    continue

                if len(record.session_id) == 8:
                    wrapper_session = record
                    break
                else:
                    fallback_session = record

            chosen = wrapper_session or fallback_session

            if chosen and chosen.socket_path:
                print(f"✅ Found socket for custom channel {channel}: {chosen.socket_path} (session {chosen.session_id})", file=sys.stderr)
                return chosen.socket_path
            else:
                print(f"⚠️  No session with existing socket found for channel {channel}", file=sys.stderr)
                return None

    except Exception as e:
        print(f"❌ Error querying registry for channel {channel}: {e}", file=sys.stderr)
        return None


def send_to_session_socket(text: str, socket_path: str) -> bool:
    """
    Send a message directly to a session's Unix socket.

    Args:
        text: Message to send
        socket_path: Path to the session's Unix socket

    Returns:
        True if sent successfully, False otherwise
    """
    if not socket_path or not os.path.exists(socket_path):
        return False

    try:
        client_socket = sock_module.socket(sock_module.AF_UNIX, sock_module.SOCK_STREAM)
        client_socket.settimeout(5.0)
        client_socket.connect(socket_path)
        client_socket.sendall(text.encode('utf-8'))
        client_socket.close()
        return True
    except Exception as e:
        print(f"⚠️  Failed to send to session socket: {e}", file=sys.stderr)
        return False


def handle_dm_message(text: str, user_id: str, dm_channel_id: str, db, slack_client, say) -> bool:
    """
    Handle DM commands (/sessions, /attach, /detach, /mode) and forward messages to attached sessions.

    Args:
        text: Message text from Slack
        user_id: Slack user ID
        dm_channel_id: DM channel ID
        db: RegistryDatabase instance
        slack_client: Slack WebClient instance
        say: Function to send message back to user

    Returns:
        True if message was handled (command or forwarded), False otherwise
    """
    try:
        from dm_mode import (
            parse_dm_command,
            format_session_list_for_slack,
            attach_to_session,
            detach_from_session,
            handle_mode_command,
            get_mode_prompt
        )
    except ImportError:
        return False

    # Parse the command
    command = parse_dm_command(text)
    if command is None:
        # Not a command - check if user is subscribed to a session
        subscription = db.get_dm_subscription_for_user(user_id)
        if subscription:
            session_id = subscription.get('session_id')
            session = db.get_session(session_id)
            if session and session.get('socket_path'):
                # Get user's mode and append mode prompt if not 'execute'
                user_mode = db.get_user_mode(user_id)
                message_to_send = text
                if user_mode != 'execute':
                    mode_prompt = get_mode_prompt(user_mode)
                    if mode_prompt:
                        message_to_send = text + mode_prompt

                # Forward message to session's socket
                if send_to_session_socket(message_to_send, session['socket_path']):
                    mode_indicator = f" [{user_mode}]" if user_mode != 'execute' else ""
                    say(text=f"✅ Sent to Claude{mode_indicator}")
                    return True
                else:
                    say(text="❌ Failed to send message. Session may have ended.")
                    return True
            else:
                say(text="❌ Session not found or has no active socket. Use `/sessions` to see active sessions.")
                return True
        else:
            # Not subscribed - tell them how to attach
            say(text="💡 You're not attached to any session.\n\nUse `/sessions` to list sessions and `/attach <id>` to subscribe.")
            return True

    # Handle each command type
    if command.command == 'sessions':
        message = format_session_list_for_slack(db)
        say(text=message)
        return True

    elif command.command == 'attach':
        session_id = command.args.get('session_id')
        history_count = command.args.get('history_count', 0)
        result = attach_to_session(
            db, user_id, session_id, dm_channel_id,
            slack_client, history_count
        )
        say(text=result['message'])
        return True

    elif command.command == 'detach':
        result = detach_from_session(db, user_id, slack_client, dm_channel_id)
        say(text=result['message'])
        return True

    elif command.command == 'mode':
        action = command.args.get('action')
        mode = command.args.get('mode')
        result = handle_mode_command(db, user_id, action, mode)
        say(text=result['message'])
        return True

    elif command.command == 'error':
        # Error from parse_dm_command (e.g., missing session ID)
        say(text=f"❌ {command.args.get('message', 'Invalid command')}")
        return True

    return False


def send_response(text, thread_ts=None, channel=None):
    """
    Send response to Claude Code

    Phase 3 Mode (registry-based, preferred):
        If thread_ts provided, lookup socket from registry by thread
        If no thread_ts but channel provided, try custom channel lookup

    Phase 2 Mode (legacy hard-coded):
        Send to hard-coded socket path (backward compatible)

    Phase 1 Mode (fallback):
        Write to file if socket doesn't exist
        User must run /check to read response

    Args:
        text: The response text to send
        thread_ts: Slack thread timestamp (for registry lookup)
        channel: Slack channel ID (for custom channel mode lookup)

    Returns:
        str: Mode used ("registry_socket", "custom_channel_socket", "socket", or "file")
    """
    socket_path = None
    routing_mode = None

    # Phase 3a: Try registry lookup by thread_ts first
    if thread_ts:
        socket_path = get_socket_for_thread(thread_ts)
        if socket_path:
            print(f"📋 Using registry socket for thread {thread_ts}: {socket_path}", file=sys.stderr)
            routing_mode = "registry_socket"

    # Phase 3b: Try custom channel lookup (where thread_ts is NULL)
    if not socket_path and channel:
        socket_path = get_socket_for_channel(channel)
        if socket_path:
            print(f"📋 Using custom channel socket for channel {channel}: {socket_path}", file=sys.stderr)
            routing_mode = "custom_channel_socket"

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

                mode = routing_mode or "socket"
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

    # Send response to Claude Code (registry socket, custom channel socket, legacy socket, or file)
    mode = send_response(clean_text, thread_ts=thread_ts, channel=channel)

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
    - Join/leave messages (channel_join, channel_leave, group_join, group_leave)
    - Empty messages

    Supports:
    - Direct messages
    - Channel messages with command prefix (/, !, or digits)
    - Threaded messages (uses registry to route to correct session)
    """
    # Ignore bot messages
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return

    # Ignore join/leave messages
    subtype = event.get("subtype")
    if subtype in ("channel_join", "channel_leave", "group_join", "group_leave"):
        return

    text = event.get("text", "").strip()
    channel_type = event.get("channel_type")
    user = event.get("user")
    channel = event.get("channel")
    thread_ts = event.get("thread_ts")  # Extract thread timestamp for routing

    if not text:
        return

    # Check if this is a thread reply to an AskUserQuestion message
    if thread_ts:
        result = handle_askuser_thread_reply(event, app.client)
        if result:
            # This was an AskUser "Other" response, handled
            print(f"💬 Handled as AskUser 'Other' response", file=sys.stderr)
            return

    # Check if this is a DM channel and try to handle as DM command
    if channel_type == 'im':
        if handle_dm_message(text, user, channel, registry_db, app.client, say):
            return  # DM command handled, don't process further

    # Ignore messages with @mentions - those are handled by app_mention handler
    # This prevents duplicate processing when someone @mentions the bot
    if "<@" in text and ">" in text:
        # Check if it's a bot mention (not just any user mention)
        try:
            bot_user_id = app.client.auth_test()["user_id"]
            if f"<@{bot_user_id}>" in text:
                print(f"📝 Skipping message with bot mention (handled by app_mention)", file=sys.stderr)
                return
        except Exception:
            pass  # If we can't check, let it through

    # Only process direct messages or messages in channels we're monitoring
    # This prevents responding to every message in every channel
    is_dm = channel_type == "im"

    # For channel messages (not in threads), check if this is a custom channel session
    # Custom channel mode: messages are top-level, not threaded
    is_custom_channel = False
    if not is_dm and not thread_ts and channel:
        # Check if there's an active custom channel session for this channel
        socket_path = get_socket_for_channel(channel)
        if socket_path:
            is_custom_channel = True
            print(f"📋 Custom channel mode detected for {channel}", file=sys.stderr)

    # For channel messages (not in threads and not custom channel), only process command-like messages
    # For threaded messages and custom channels, process all messages (they're replies to Claude)
    if not is_dm and not thread_ts and not is_custom_channel:
        # Skip messages that don't look like commands
        # Allow: /command, !command, or plain numbers (1, 2, 3)
        if not (text.startswith('/') or text.startswith('!') or text.isdigit()):
            return

    # Send response to Claude Code (registry socket, custom channel socket, legacy socket, or file)
    mode = send_response(text, thread_ts=thread_ts, channel=channel)

    # Store the message ts so Claude's response can be threaded to it
    message_ts = event.get("ts")
    if message_ts and registry_db:
        try:
            # Find the session to update
            session_id = None
            if thread_ts:
                # For threaded messages, find session by thread_ts
                with registry_db.session_scope() as db_session:
                    from registry_db import SessionRecord
                    record = db_session.query(SessionRecord).filter_by(
                        slack_thread_ts=thread_ts,
                        status='active'
                    ).first()
                    if record:
                        session_id = record.session_id
            elif is_custom_channel and channel:
                # For custom channel, find session by channel
                with registry_db.session_scope() as db_session:
                    from registry_db import SessionRecord
                    record = db_session.query(SessionRecord).filter(
                        SessionRecord.slack_channel == channel,
                        SessionRecord.slack_thread_ts.is_(None),
                        SessionRecord.status == 'active'
                    ).first()
                    if record:
                        session_id = record.session_id

            if session_id:
                registry_db.update_session(session_id, {'reply_to_ts': message_ts})
                print(f"📋 Set reply_to_ts={message_ts} for session {session_id[:8]}", file=sys.stderr)
        except Exception as e:
            print(f"⚠️  Could not set reply_to_ts: {e}", file=sys.stderr)

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

    First checks if this is an AskUserQuestion reaction (number emojis 1-4).
    If not, treats as permission prompt response.

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

    # Try handling as AskUserQuestion reaction first
    if handle_askuser_reaction(body, client):
        print(f"📌 Handled as AskUserQuestion reaction", file=sys.stderr)
        return

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

    # Send the numeric response to Claude (pass channel for custom channel mode fallback)
    mode = send_response(response, thread_ts=thread_ts, channel=channel)

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


@app.action("permission_response_1")
@app.action("permission_response_2")
@app.action("permission_response_3")
def handle_permission_button(ack, body, client):
    """
    Handle interactive button clicks for permission prompts.

    When a user clicks a permission button (1, 2, or 3), this handler:
    1. Acknowledges the button click immediately (required by Slack)
    2. Extracts the button value (the numeric response)
    3. Gets the thread_ts for routing to the correct Claude session
    4. Sends the numeric response to Claude
    5. Updates the message to show the selection

    The button action_ids are: permission_response_1, permission_response_2, permission_response_3
    The button values are: "1", "2", "3"
    """
    # Acknowledge immediately (Slack requires response within 3 seconds)
    ack()

    print(f"🔘 Button click event received", file=sys.stderr)

    try:
        # Extract action info
        actions = body.get("actions", [])
        if not actions:
            print(f"⚠️  No actions in button click body", file=sys.stderr)
            return

        action = actions[0]
        response = action.get("value")  # "1", "2", or "3"
        action_id = action.get("action_id")
        button_style = action.get("style")  # "primary", "danger", or None
        user_id = body.get("user", {}).get("id")
        user_name = body.get("user", {}).get("name", "Unknown")

        # Check if this is the deny button (danger style = red button = "No")
        # This handles both 2-option (button 2 = deny) and 3-option (button 3 = deny) prompts
        is_deny_button = button_style == "danger"

        print(f"🔘 Action: {action_id}, Value: {response}, Style: {button_style}, User: {user_name}", file=sys.stderr)

        # Get message and thread info from the body
        message = body.get("message", {})
        channel = body.get("channel", {}).get("id")
        message_ts = message.get("ts")
        thread_ts = message.get("thread_ts", message_ts)  # Thread parent or message itself

        print(f"🔘 Channel: {channel}, Thread: {thread_ts}", file=sys.stderr)

        if not response:
            print(f"⚠️  Missing response in button click", file=sys.stderr)
            return

        # Check if this is a custom channel session (no thread, but channel has active session)
        is_custom_channel = False
        if channel:
            custom_socket = get_socket_for_channel(channel)
            if custom_socket:
                is_custom_channel = True
                print(f"🔘 Custom channel mode detected for button click", file=sys.stderr)

        # For "deny" option (danger-styled button), prompt user for feedback instead of sending immediately
        # But for custom channels, just send the value since there's no thread to reply in
        if is_deny_button and not is_custom_channel:
            print(f"🔘 Deny button clicked - prompting for feedback", file=sys.stderr)
            try:
                # Update the message to prompt for feedback
                client.chat_update(
                    channel=channel,
                    ts=message_ts,
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"❌ *<@{user_id}> denied the request*\n\n💬 Please reply in this thread with instructions for Claude:"
                            }
                        }
                    ],
                    text="Permission denied - please reply with feedback"
                )
                print(f"🔘 Prompting user for feedback in thread", file=sys.stderr)
                # Don't send response yet - wait for user's follow-up message
                return
            except Exception as e:
                print(f"⚠️  Could not update message for feedback prompt: {e}", file=sys.stderr)
                # Fall through to send response directly
        elif is_deny_button and is_custom_channel:
            print(f"🔘 Deny button clicked in custom channel - sending '{response}' directly (no thread for feedback)", file=sys.stderr)

        # Send the numeric response to Claude (for approve options, or fallback for deny)
        # Pass channel for custom channel mode fallback routing
        mode = send_response(response, thread_ts=thread_ts, channel=channel)
        print(f"🔘 Button '{response}' from {user_name} → sent via {mode}", file=sys.stderr)

        # Delete the permission message to keep the channel clean
        try:
            client.chat_delete(
                channel=channel,
                ts=message_ts
            )
            print(f"🔘 Permission message deleted (keeping channel clean)", file=sys.stderr)

            # Clear permission_message_ts in registry so posttooluse hook doesn't try to delete again
            if registry_db:
                try:
                    # Find session by thread_ts or channel
                    session = None
                    if thread_ts:
                        session = registry_db.get_by_thread(thread_ts)
                    if not session and channel:
                        session = registry_db.get_by_channel(channel) if hasattr(registry_db, 'get_by_channel') else None
                    if session:
                        registry_db.update_session(session['session_id'], {'permission_message_ts': None})
                        print(f"🔘 Cleared permission_message_ts for session", file=sys.stderr)
                except Exception as db_e:
                    print(f"⚠️  Could not clear permission_message_ts: {db_e}", file=sys.stderr)

        except Exception as e:
            # If deletion fails (e.g., bot lacks permissions), fall back to updating the message
            print(f"⚠️  Could not delete message, falling back to update: {e}", file=sys.stderr)
            try:
                # Update to show selection confirmation
                client.chat_update(
                    channel=channel,
                    ts=message_ts,
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"✅ *<@{user_id}> approved* (option {response})"
                            }
                        }
                    ],
                    text=f"Permission approved (option {response})"
                )
                print(f"🔘 Message updated to show approval (fallback)", file=sys.stderr)
            except Exception as e2:
                print(f"⚠️  Could not update message either: {e2}", file=sys.stderr)
                # Don't fail - the response was already sent

    except Exception as e:
        print(f"❌ Error handling button click: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)


# ─────────────────────────────────────────────────────────────────────────────
# PermissionRequest Hook Button Handlers
# These handle Allow/Deny/Allow Always buttons from on_permission_request.py hook
# ─────────────────────────────────────────────────────────────────────────────

PERMISSION_RESPONSE_DIR = Path.home() / ".claude" / "slack" / "permission_responses"
PERMISSION_RESPONSE_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# AskUserQuestion Reaction Handler
# Handles emoji reactions on AskUserQuestion messages
# ─────────────────────────────────────────────────────────────────────────────

# Map emoji reactions to option indices (0-based)
# Emoji reaction names to 0-indexed option values
# 'one' (1️⃣) -> '0' (first option, 0-indexed)
# 'two' (2️⃣) -> '1' (second option, 0-indexed)
# 'three' (3️⃣) -> '2' (third option, 0-indexed)
# 'four' (4️⃣) -> '3' (fourth option, 0-indexed)
# IMPORTANT: Display is 1-indexed (shows "Option 1, Option 2") but responses are 0-indexed
# This ensures consistent option indexing across display and storage layers
ASKUSER_EMOJI_MAP = {
    'one': '0', 'two': '1', 'three': '2', 'four': '3',  # Emoji name format
    '1️⃣': '0', '2️⃣': '1', '3️⃣': '2', '4️⃣': '3',  # Unicode emoji format
}


def handle_askuser_reaction(body, client):
    """
    Handle emoji reactions on AskUserQuestion messages.

    When a user reacts with a number emoji (1️⃣ 2️⃣ 3️⃣ 4️⃣) to an AskUserQuestion message,
    this handler:
    1. Fetches the message to check for askuser block_id
    2. Extracts metadata (session_id, request_id, question_index) from block_id
    3. Maps emoji to option index
    4. Writes response file for the hook to read
    5. Updates the message to show the selection

    Block ID format: askuser_Q{n}_{session_id}_{request_id}

    Returns:
        True if handled as AskUser reaction, False otherwise
    """
    print(f"🔢 Checking if reaction is for AskUserQuestion", file=sys.stderr)

    try:
        # Extract reaction event details
        event = body.get("event", {})
        emoji_name = event.get("reaction")
        item = event.get("item", {})
        channel = item.get("channel")
        message_ts = item.get("ts")
        user_id = event.get("user")

        print(f"🔢 Emoji: {emoji_name}, Channel: {channel}, TS: {message_ts}, User: {user_id}", file=sys.stderr)

        # Check if this emoji is mapped to an option index
        option_index = ASKUSER_EMOJI_MAP.get(emoji_name)
        if not option_index:
            print(f"🔢 Emoji '{emoji_name}' not mapped for AskUser", file=sys.stderr)
            return False

        # Fetch the message to check if it's an AskUserQuestion message
        try:
            result = client.conversations_history(
                channel=channel,
                latest=message_ts,
                inclusive=True,
                limit=1
            )
            messages = result.get("messages", [])
            if not messages:
                print(f"🔢 Message not found", file=sys.stderr)
                return False

            message = messages[0]
        except Exception as e:
            print(f"⚠️  Could not fetch message: {e}", file=sys.stderr)
            return False

        # Check for AskUserQuestion block_id
        blocks = message.get("blocks", [])
        askuser_block = None
        for block in blocks:
            block_id = block.get("block_id", "")
            if block_id.startswith("askuser_"):
                askuser_block = block
                break

        if not askuser_block:
            print(f"🔢 Not an AskUserQuestion message", file=sys.stderr)
            return False

        # Extract metadata from block_id: askuser_Q{n}_{session_id}_{request_id}
        block_id = askuser_block.get("block_id", "")
        parts = block_id.split("_")
        if len(parts) < 4:
            print(f"⚠️  Invalid block_id format: {block_id}", file=sys.stderr)
            return False

        question_num = parts[1]  # e.g., "Q0"
        session_id = parts[2]
        request_id = parts[3]

        # Extract question index from "Q0" -> "0"
        if not question_num.startswith("Q"):
            print(f"⚠️  Invalid question number format: {question_num}", file=sys.stderr)
            return False

        question_index = question_num[1:]  # Remove "Q" prefix

        print(f"🔢 Parsed: question={question_index}, session={session_id[:8]}, request={request_id}, option={option_index}", file=sys.stderr)

        # Accumulate response (merge with existing answers if any)
        response_file = ASKUSER_RESPONSE_DIR / f"{session_id}_{request_id}.json"

        # Add new answer (use atomic update to prevent race conditions)
        new_data = {
            f"question_{question_index}": option_index,
            "user_id": user_id,
            "timestamp": time.time()
        }
        atomic_read_and_update_response_file(response_file, new_data)

        # Read back the merged data for message update
        try:
            with open(response_file) as f:
                merged_data = json.load(f)
        except:
            merged_data = new_data

        # Update message to show selection and progress
        try:
            # Count total questions by finding all askuser blocks
            total_questions = sum(1 for b in blocks if b.get("block_id", "").startswith("askuser_Q"))

            # Count answered questions from the response data
            answered_questions = sum(1 for i in range(total_questions) if f"question_{i}" in merged_data)

            # Build updated blocks showing the selection and progress
            updated_blocks = []
            for block in blocks:
                if block.get("block_id") == block_id:
                    # Update this block to show the selection
                    text = block.get("text", {}).get("text", "")
                    option_num = int(option_index) + 1  # Convert 0-based to 1-based for display

                    # Add progress indicator if multi-question
                    if total_questions > 1:
                        progress = f"✅ *Q{int(question_index)+1}: <@{user_id}> selected option {option_num}* ({answered_questions}/{total_questions} answered)\n\n{text}"
                    else:
                        progress = f"✅ *<@{user_id}> selected option {option_num}*\n\n{text}"

                    updated_blocks.append({
                        "type": "section",
                        "block_id": block_id,
                        "text": {
                            "type": "mrkdwn",
                            "text": progress
                        }
                    })
                else:
                    updated_blocks.append(block)

            # Update summary text
            if total_questions > 1:
                summary_text = f"AskUserQuestion: {answered_questions}/{total_questions} answered"
            else:
                summary_text = f"AskUserQuestion answered: Option {int(option_index) + 1}"

            client.chat_update(
                channel=channel,
                ts=message_ts,
                blocks=updated_blocks,
                text=summary_text
            )

            print(f"🔢 Updated message to show selection (progress: {answered_questions}/{total_questions})", file=sys.stderr)

        except Exception as e:
            print(f"⚠️  Could not update message: {e}", file=sys.stderr)
            # Continue - response file was written successfully

        return True

    except Exception as e:
        print(f"❌ Error in AskUser reaction handler: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return False


def handle_askuser_thread_reply(event, client):
    """
    Handle thread replies to AskUserQuestion messages as "Other" responses.

    When a user replies in a thread to an AskUserQuestion message, treat it as
    selecting the "Other" option with custom text.

    Args:
        event: Slack message event
        client: Slack WebClient

    Returns:
        True if handled as AskUser reply, None otherwise
    """
    print(f"💬 Checking if thread reply is AskUser response", file=sys.stderr)

    try:
        thread_ts = event.get("thread_ts")
        if not thread_ts:
            return None

        channel = event.get("channel")
        user_id = event.get("user")
        text = event.get("text", "")

        # Fetch parent message to check if it's an AskUserQuestion
        try:
            result = client.conversations_history(
                channel=channel,
                latest=thread_ts,
                inclusive=True,
                limit=1
            )
            messages = result.get("messages", [])
            if not messages:
                return None

            parent_message = messages[0]
        except Exception as e:
            print(f"⚠️  Could not fetch parent message: {e}", file=sys.stderr)
            return None

        # Check for AskUserQuestion block_id in parent
        blocks = parent_message.get("blocks", [])
        askuser_block = None
        for block in blocks:
            block_id = block.get("block_id", "")
            if block_id.startswith("askuser_"):
                askuser_block = block
                break

        if not askuser_block:
            # Not an AskUser message
            return None

        # Extract metadata from block_id: askuser_Q{n}_{session_id}_{request_id}
        block_id = askuser_block.get("block_id", "")
        parts = block_id.split("_")
        if len(parts) < 4:
            print(f"⚠️  Invalid block_id format: {block_id}", file=sys.stderr)
            return None

        question_num = parts[1]  # e.g., "Q0"
        session_id = parts[2]
        request_id = parts[3]

        # Extract question index
        if not question_num.startswith("Q"):
            print(f"⚠️  Invalid question number format: {question_num}", file=sys.stderr)
            return None

        question_index = question_num[1:]

        print(f"💬 Thread reply is AskUser 'Other' response: question={question_index}, session={session_id[:8]}", file=sys.stderr)

        # Accumulate response (merge with existing answers if any)
        response_file = ASKUSER_RESPONSE_DIR / f"{session_id}_{request_id}.json"

        # Add new answer (use atomic update to prevent race conditions)
        new_data = {
            f"question_{question_index}": "other",
            f"question_{question_index}_text": text,
            "user_id": user_id,
            "timestamp": time.time()
        }
        atomic_read_and_update_response_file(response_file, new_data)

        # Update parent message to show "Other" selection
        try:
            # Truncate text for display (max 100 chars)
            text_preview = text[:100] + "..." if len(text) > 100 else text

            updated_blocks = []
            for block in blocks:
                if block.get("block_id") == block_id:
                    original_text = block.get("text", {}).get("text", "")
                    updated_text = f"✅ *<@{user_id}> selected: Other*\n\n_{text_preview}_\n\n{original_text}"
                    updated_blocks.append({
                        "type": "section",
                        "block_id": block_id,
                        "text": {
                            "type": "mrkdwn",
                            "text": updated_text
                        }
                    })
                else:
                    updated_blocks.append(block)

            client.chat_update(
                channel=channel,
                ts=thread_ts,
                blocks=updated_blocks,
                text="AskUserQuestion answered: Other"
            )

            print(f"💬 Updated parent message to show 'Other' selection", file=sys.stderr)

        except Exception as e:
            print(f"⚠️  Could not update parent message: {e}", file=sys.stderr)

        return True

    except Exception as e:
        print(f"❌ Error in AskUser thread reply handler: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return None


@app.action("permission_allow")
@app.action("permission_deny")
@app.action("permission_allow_always")
def handle_permission_hook_button(ack, body, client):
    """
    Handle permission buttons from PermissionRequest hook.

    These buttons come from on_permission_request.py hook, not the notification-based
    permission prompts. They write a response file that the hook is polling for.

    Button value contains JSON: {"session_id": "...", "request_id": "...", "decision": "allow|deny|allow_always"}
    """
    # Acknowledge immediately (Slack requires response within 3 seconds)
    ack()

    print(f"🔐 PermissionRequest hook button clicked", file=sys.stderr)

    try:
        # Extract action info
        actions = body.get("actions", [])
        if not actions:
            print(f"⚠️  No actions in button click body", file=sys.stderr)
            return

        action = actions[0]
        action_id = action.get("action_id")
        value_json = action.get("value", "{}")
        user_id = body.get("user", {}).get("id")
        user_name = body.get("user", {}).get("name", "Unknown")

        print(f"🔐 Action: {action_id}, User: {user_name}", file=sys.stderr)

        # Parse the button value
        try:
            value = json.loads(value_json)
        except json.JSONDecodeError:
            print(f"⚠️  Invalid JSON in button value: {value_json}", file=sys.stderr)
            return

        session_id = value.get("session_id")
        request_id = value.get("request_id")
        decision = value.get("decision")

        if not all([session_id, request_id, decision]):
            print(f"⚠️  Missing required fields in button value", file=sys.stderr)
            return

        print(f"🔐 Session: {session_id[:8]}, Request: {request_id}, Decision: {decision}", file=sys.stderr)

        # Write response file for the hook to read
        response_file = PERMISSION_RESPONSE_DIR / f"{session_id}_{request_id}.json"
        response_data = {
            "decision": decision,
            "user_id": user_id,
            "user_name": user_name,
            "timestamp": time.time()
        }

        with open(response_file, 'w') as f:
            json.dump(response_data, f)

        print(f"🔐 Wrote response file: {response_file}", file=sys.stderr)

        # Get message info for updating/deleting
        message = body.get("message", {})
        channel = body.get("channel", {}).get("id")
        message_ts = message.get("ts")

        # Update the message to show the result
        if decision == "allow":
            result_text = f"✅ *<@{user_id}> allowed* this action"
            result_emoji = "✅"
        elif decision == "allow_always":
            result_text = f"✅ *<@{user_id}> allowed* (always for this session)"
            result_emoji = "✅"
        elif decision == "deny":
            result_text = f"❌ *<@{user_id}> denied* this action"
            result_emoji = "❌"
        else:
            result_text = f"*<@{user_id}>* responded: {decision}"
            result_emoji = "🔔"

        try:
            # Try to delete the message first (keeps channel clean)
            client.chat_delete(
                channel=channel,
                ts=message_ts
            )
            print(f"🔐 Permission message deleted", file=sys.stderr)

            # Clear permission_message_ts in registry
            if registry_db:
                try:
                    session = registry_db.get_session(session_id)
                    if session:
                        registry_db.update_session(session_id, {'permission_message_ts': None})
                except Exception as db_e:
                    print(f"⚠️  Could not clear permission_message_ts: {db_e}", file=sys.stderr)

        except Exception as del_e:
            # If deletion fails, update the message instead
            print(f"⚠️  Could not delete message, updating instead: {del_e}", file=sys.stderr)
            try:
                client.chat_update(
                    channel=channel,
                    ts=message_ts,
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": result_text
                            }
                        }
                    ],
                    text=f"Permission {decision}"
                )
            except Exception as update_e:
                print(f"⚠️  Could not update message either: {update_e}", file=sys.stderr)

    except Exception as e:
        print(f"❌ Error handling permission hook button: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)


# ─────────────────────────────────────────────────────────────────────────────
# Shortcut Handlers - Global shortcuts from Slack's ⚡ menu
# ─────────────────────────────────────────────────────────────────────────────

@app.shortcut("get_sessions")
def handle_get_sessions_shortcut(ack, shortcut, client):
    """
    Handle the 'Get Sessions' global shortcut.
    Shows a modal with a list of active Claude sessions.
    """
    ack()
    user_id = shortcut["user"]["id"]
    trigger_id = shortcut["trigger_id"]

    print(f"⚡ Shortcut: get_sessions from user {user_id}", file=sys.stderr)

    try:
        from dm_mode import format_session_list_for_slack, list_active_sessions

        # Get sessions list
        sessions = list_active_sessions(registry_db) if registry_db else []

        if not sessions:
            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "📭 *No active sessions*\n\nStart a Claude session with `claude-slack -c channel-name` first."
                    }
                }
            ]
        else:
            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*🖥️ Active Claude Sessions*"
                    }
                },
                {"type": "divider"}
            ]

            for session in sessions:
                session_id = session['session_id']
                project = session['project']
                created = session.get('created_at', '')[:10] if session.get('created_at') else ''

                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{project}*\n`{session_id}`\n_Started: {created}_"
                    }
                })

            blocks.append({"type": "divider"})
            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "💡 Use the *Attach to Session* shortcut to subscribe to output"
                    }
                ]
            })

        # Open modal with sessions list
        client.views_open(
            trigger_id=trigger_id,
            view={
                "type": "modal",
                "title": {"type": "plain_text", "text": "Claude Sessions"},
                "close": {"type": "plain_text", "text": "Close"},
                "blocks": blocks
            }
        )

    except Exception as e:
        print(f"❌ Error in get_sessions shortcut: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)


@app.shortcut("attach_to_session")
def handle_attach_shortcut(ack, shortcut, client):
    """
    Handle the 'Attach to Session' global shortcut.
    Opens a modal with a dropdown to select a session.
    """
    ack()
    user_id = shortcut["user"]["id"]
    trigger_id = shortcut["trigger_id"]

    print(f"⚡ Shortcut: attach_to_session from user {user_id}", file=sys.stderr)

    try:
        from dm_mode import list_active_sessions

        sessions = list_active_sessions(registry_db) if registry_db else []

        if not sessions:
            # No sessions available
            client.views_open(
                trigger_id=trigger_id,
                view={
                    "type": "modal",
                    "title": {"type": "plain_text", "text": "Attach to Session"},
                    "close": {"type": "plain_text", "text": "Close"},
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "📭 *No active sessions*\n\nStart a Claude session first with:\n```claude-slack -c channel-name```"
                            }
                        }
                    ]
                }
            )
            return

        # Build session options for dropdown
        session_options = [
            {
                "text": {"type": "plain_text", "text": f"{s['project']} ({s['session_id'][:8]}...)"},
                "value": s['session_id']
            }
            for s in sessions
        ]

        # Open modal with session picker
        client.views_open(
            trigger_id=trigger_id,
            view={
                "type": "modal",
                "callback_id": "attach_session_modal",
                "title": {"type": "plain_text", "text": "Attach to Session"},
                "submit": {"type": "plain_text", "text": "Attach"},
                "close": {"type": "plain_text", "text": "Cancel"},
                "private_metadata": user_id,
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "Select a session to receive its output in your DMs:"
                        }
                    },
                    {
                        "type": "input",
                        "block_id": "session_select_block",
                        "element": {
                            "type": "static_select",
                            "action_id": "session_select",
                            "placeholder": {"type": "plain_text", "text": "Select a session"},
                            "options": session_options
                        },
                        "label": {"type": "plain_text", "text": "Session"}
                    },
                    {
                        "type": "input",
                        "block_id": "history_block",
                        "optional": True,
                        "element": {
                            "type": "static_select",
                            "action_id": "history_select",
                            "placeholder": {"type": "plain_text", "text": "No history"},
                            "options": [
                                {"text": {"type": "plain_text", "text": "No history"}, "value": "0"},
                                {"text": {"type": "plain_text", "text": "Last 5 messages"}, "value": "5"},
                                {"text": {"type": "plain_text", "text": "Last 10 messages"}, "value": "10"},
                                {"text": {"type": "plain_text", "text": "Last 25 messages"}, "value": "25"}
                            ]
                        },
                        "label": {"type": "plain_text", "text": "Fetch recent history?"}
                    }
                ]
            }
        )

    except Exception as e:
        print(f"❌ Error in attach_to_session shortcut: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)


@app.view("attach_session_modal")
def handle_attach_modal_submission(ack, body, client, view):
    """Handle submission of the attach session modal."""
    ack()

    user_id = body["user"]["id"]
    values = view["state"]["values"]

    # Extract selected session
    session_id = values["session_select_block"]["session_select"]["selected_option"]["value"]

    # Extract history count (optional)
    history_selection = values.get("history_block", {}).get("history_select", {}).get("selected_option")
    history_count = int(history_selection["value"]) if history_selection else 0

    print(f"⚡ Modal submit: attach {user_id} to {session_id} (history: {history_count})", file=sys.stderr)

    try:
        from dm_mode import attach_to_session

        # Open a DM channel with the user
        dm_response = client.conversations_open(users=[user_id])
        dm_channel_id = dm_response["channel"]["id"]

        # Attach to session
        result = attach_to_session(
            registry_db, user_id, session_id, dm_channel_id, client, history_count
        )

        # Send confirmation to user's DM
        client.chat_postMessage(
            channel=dm_channel_id,
            text=result['message']
        )

    except Exception as e:
        print(f"❌ Error attaching to session: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)


@app.shortcut("research_mode")
def handle_research_mode_shortcut(ack, shortcut, client):
    """Handle the 'Research Mode' global shortcut."""
    ack()
    user_id = shortcut["user"]["id"]

    print(f"⚡ Shortcut: research_mode from user {user_id}", file=sys.stderr)
    _set_user_mode(user_id, "research", client)


@app.shortcut("plan_mode")
def handle_plan_mode_shortcut(ack, shortcut, client):
    """Handle the 'Plan Mode' global shortcut."""
    ack()
    user_id = shortcut["user"]["id"]

    print(f"⚡ Shortcut: plan_mode from user {user_id}", file=sys.stderr)
    _set_user_mode(user_id, "plan", client)


@app.shortcut("execute_mode")
def handle_execute_mode_shortcut(ack, shortcut, client):
    """Handle the 'Execute Mode' global shortcut."""
    ack()
    user_id = shortcut["user"]["id"]

    print(f"⚡ Shortcut: execute_mode from user {user_id}", file=sys.stderr)
    _set_user_mode(user_id, "execute", client)


def _set_user_mode(user_id: str, mode: str, client):
    """
    Helper to set user mode and send confirmation via DM.

    Args:
        user_id: Slack user ID
        mode: Mode to set (research, plan, execute)
        client: Slack WebClient
    """
    try:
        from dm_mode import handle_mode_command

        result = handle_mode_command(registry_db, user_id, action='set', mode=mode)

        # Open DM and send confirmation
        dm_response = client.conversations_open(users=[user_id])
        dm_channel_id = dm_response["channel"]["id"]

        client.chat_postMessage(
            channel=dm_channel_id,
            text=result['message']
        )

        print(f"✅ Set mode to {mode} for user {user_id}", file=sys.stderr)

    except Exception as e:
        print(f"❌ Error setting mode: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)


def main():
    """Start the Slack bot in Socket Mode"""
    # Check if app initialization failed (deferred from module load)
    if _slack_app_error:
        print(f"❌ Error: {_slack_app_error}", file=sys.stderr)
        print("   Create a .env file from .env.example and set your tokens", file=sys.stderr)
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
    print("   - Emoji reactions (1️⃣ 2️⃣ 3️⃣ 👍 👎)")
    print("   - Interactive button clicks")
    print("   - Threaded replies (routed to correct session)")
    print("   - Global shortcuts (⚡ menu)")
    print("")
    print("   Shortcuts: Get Sessions, Attach to Session, Research/Plan/Execute Mode")
    print("")
    print("   Press Ctrl+C to stop")
    print("")

    try:
        handler.start()
    except KeyboardInterrupt:
        print("\n👋 Slack bot stopped")
        sys.exit(0)


if __name__ == "__main__":
    main()
