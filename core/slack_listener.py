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
                # Defense-in-depth: Verify socket file actually exists
                # This catches stale entries where session wasn't properly ended
                if chosen.socket_path and os.path.exists(chosen.socket_path):
                    print(f"✅ Found socket for thread {thread_ts}: {chosen.socket_path} (session {chosen.session_id})", file=sys.stderr)
                    return chosen.socket_path
                else:
                    print(f"⚠️  Stale session {chosen.session_id}: socket {chosen.socket_path} no longer exists", file=sys.stderr)
                    return None
            else:
                print(f"⚠️  Session found but no socket path for thread {thread_ts}", file=sys.stderr)
                return None

    except Exception as e:
        print(f"❌ Error querying registry for thread {thread_ts}: {e}", file=sys.stderr)
        return None


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


def handle_pop_command(channel, thread_ts, say, count=1):
    """
    Handle !pop command - extract the latest assistant response(s) from the
    transcript and post to Slack. Used as a workaround when the on_stop
    hook fails to post (e.g., transcript flush lag).

    Args:
        count: Number of recent responses to post (default 1)
    """
    import glob
    import json as json_mod
    import re as re_mod

    if not thread_ts:
        say("⚠️ `!pop` must be used in a session thread.")
        return

    if not registry_db:
        say(text="⚠️ Registry database not available.", thread_ts=thread_ts)
        return

    try:
        # Find the Claude UUID session for this thread (longer session_id)
        from registry_db import SessionRecord
        with registry_db.session_scope() as db_session:
            records = db_session.query(SessionRecord).filter_by(
                slack_thread_ts=thread_ts,
                status='active'
            ).all()

            if not records:
                say(text="⚠️ No active session found for this thread.", thread_ts=thread_ts)
                return

            # Find the Claude UUID session (36 chars with dashes)
            claude_session = None
            for record in records:
                if len(record.session_id) > 8:
                    claude_session = record
                    break

            if not claude_session:
                say(text="⚠️ No Claude session ID found. Only wrapper session exists.", thread_ts=thread_ts)
                return

            session_id = claude_session.session_id

        # Find the transcript file
        claude_projects_dir = os.path.expanduser("~/.claude/projects")
        transcript_path = None
        for project_dir in glob.glob(os.path.join(claude_projects_dir, "*")):
            candidate = os.path.join(project_dir, f"{session_id}.jsonl")
            if os.path.exists(candidate):
                transcript_path = candidate
                break

        if not transcript_path:
            say(text=f"⚠️ Transcript not found for session `{session_id[:8]}...`", thread_ts=thread_ts)
            return

        # Parse transcript — collect all assistant text responses with timestamps
        # Group consecutive text entries that belong to the same logical response
        # A "response" = all text blocks between user messages
        timestamp_pattern = re_mod.compile(r'^\s*\d{4}/\d{2}/\d{2}:\d{2}:\d{2}:\d{2}\s*$')
        responses = []  # list of {"timestamp": str, "text": str}
        current_response_texts = []
        current_timestamp = None

        with open(transcript_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json_mod.loads(line)
                    entry_type = entry.get('type')

                    # When we hit a user message, finalize the current response
                    if entry_type == 'user' and current_response_texts:
                        combined = '\n\n'.join(current_response_texts)
                        responses.append({
                            "timestamp": current_timestamp,
                            "text": combined
                        })
                        current_response_texts = []
                        current_timestamp = None

                    if entry_type == 'assistant':
                        content = entry.get('message', {}).get('content', [])
                        ts = entry.get('timestamp', '')
                        text_blocks = [
                            c.get('text', '')
                            for c in content
                            if isinstance(c, dict) and c.get('type') == 'text' and c.get('text', '').strip()
                        ]
                        for tb in text_blocks:
                            stripped = tb.strip()
                            # If it's a bare timestamp, use it as the response timestamp
                            if timestamp_pattern.match(stripped):
                                current_timestamp = stripped
                            else:
                                if current_timestamp is None and ts:
                                    # Use the JSONL entry timestamp as fallback
                                    current_timestamp = ts
                                current_response_texts.append(tb.strip())
                except (json_mod.JSONDecodeError, KeyError):
                    continue

        # Finalize any remaining response
        if current_response_texts:
            combined = '\n\n'.join(current_response_texts)
            responses.append({
                "timestamp": current_timestamp,
                "text": combined
            })

        if not responses:
            say(text="⚠️ No assistant text responses found in transcript.", thread_ts=thread_ts)
            return

        # Get the last N responses
        to_post = responses[-count:]

        bot_token = os.environ.get("SLACK_BOT_TOKEN")
        if not bot_token:
            say(text="⚠️ SLACK_BOT_TOKEN not set.", thread_ts=thread_ts)
            return

        try:
            from slack_formatter import format_for_slack
        except ImportError:
            format_for_slack = None

        from slack_sdk import WebClient
        client = WebClient(token=bot_token)

        for resp in to_post:
            ts_label = resp["timestamp"] or "unknown time"
            text_body = resp["text"]

            if format_for_slack:
                text_body = format_for_slack(text_body)

            message = f"*{ts_label}*\n{text_body}"

            # Split into chunks if needed (Slack max 3000 chars per message)
            max_len = 3000
            chunks = [message[i:i+max_len] for i in range(0, len(message), max_len)]

            for i, chunk in enumerate(chunks):
                try:
                    client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text=chunk,
                        unfurl_links=False,
                        unfurl_media=False,
                    )
                except Exception as e:
                    print(f"❌ Pop: Failed to post chunk {i+1}: {e}", file=sys.stderr)

        print(f"✅ Pop: Posted {len(to_post)} response(s) for session {session_id[:8]}", file=sys.stderr)

    except Exception as e:
        print(f"❌ Pop command error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        say(text=f"⚠️ Error running pop: {e}", thread_ts=thread_ts)


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

    # Handle !pop / !pop N command - post latest Claude response(s) to Slack
    text_lower = text.lower().strip()
    if text_lower.startswith('!pop') or text_lower.startswith('/pop'):
        # Parse optional count: "!pop 3" → 3 messages
        parts = text_lower.split()
        count = 1
        if len(parts) > 1:
            try:
                count = max(1, int(parts[1]))
            except ValueError:
                count = 1
        handle_pop_command(channel, thread_ts, say, count=count)
        return

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


def main():
    """Start the Slack bot in Socket Mode"""
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
        sys.exit(0)


if __name__ == "__main__":
    main()
