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
    print("   - Emoji reactions (1️⃣ 2️⃣ 3️⃣ 👍 👎)")
    print("   - Interactive button clicks")
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
