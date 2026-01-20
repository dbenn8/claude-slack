#!/usr/bin/env python3
"""
Claude Code PostToolUse Hook - Post Todo Updates to Slack

Version: 1.0.0

Triggered after Claude executes any tool, allowing us to capture TodoWrite
calls and post/update todo status in Slack.

Hook Input (stdin):
    {
        "session_id": "abc12345",
        "transcript_path": "/path/to/transcript.jsonl",
        "cwd": "/path/to/project",
        "permission_mode": "default",
        "hook_event_name": "PostToolUse",
        "tool_name": "TodoWrite",
        "tool_input": {
            "todos": [
                {"content": "Fix bug", "status": "completed", "activeForm": "Fixing bug"},
                {"content": "Add tests", "status": "in_progress", "activeForm": "Adding tests"}
            ]
        },
        "tool_result": "Todos have been modified successfully..."
    }

Environment Variables:
    SLACK_BOT_TOKEN - Bot User OAuth Token (required)
    REGISTRY_DB_PATH - Registry database path (default: ~/.claude/slack/registry.db)

Architecture:
    1. Read hook data from stdin
    2. Check if tool_name is "TodoWrite"
    3. If yes, format the todo list for Slack
    4. Query registry_db for session metadata (Slack thread info, todo_message_ts)
    5. If todo_message_ts exists, UPDATE that message; otherwise POST new message
    6. Store the message_ts in registry for future updates
    7. Exit 0 (success or failure)

Debug Logging:
    - All execution logged to ~/.claude/slack/logs/posttooluse_hook_debug.log
"""

import sys
import json
import os
from pathlib import Path
from datetime import datetime

# Hook version for auto-update detection
HOOK_VERSION = "1.0.0"

# Log directory - use ~/.claude/slack/logs as default
LOG_DIR = os.environ.get("SLACK_LOG_DIR", os.path.expanduser("~/.claude/slack/logs"))
os.makedirs(LOG_DIR, exist_ok=True)

# Debug log file path
DEBUG_LOG = os.path.join(LOG_DIR, "posttooluse_hook_debug.log")

# Find claude-slack directory dynamically
def find_claude_slack_dir():
    """Find claude-slack directory using standard discovery patterns."""
    import os

    # 1. Environment variable override (takes precedence)
    if 'CLAUDE_SLACK_DIR' in os.environ:
        env_path = Path(os.environ['CLAUDE_SLACK_DIR'])
        if (env_path / 'core').exists():
            return env_path
        else:
            print(f"[on_posttooluse.py] ERROR: CLAUDE_SLACK_DIR is set to '{env_path}' but no claude-slack installation found there.", file=sys.stderr)
            sys.exit(0)

    # 2. Search upward from current directory (like git)
    current = Path.cwd()
    for parent in [current] + list(current.parents):
        candidate = parent / '.claude' / 'claude-slack'
        if (candidate / 'core').exists():
            return candidate

    # 3. Fall back to user home directory
    fallback = Path.home() / '.claude' / 'claude-slack'
    return fallback

CLAUDE_SLACK_DIR = find_claude_slack_dir()
CORE_DIR = CLAUDE_SLACK_DIR / "core"


def debug_log(message: str, section: str = "GENERAL"):
    """Log debug message to file with timestamp and section."""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[{timestamp}] [{section}] {message}\n")
    except Exception as e:
        print(f"[on_posttooluse.py] DEBUG LOG FAILED: {e}", file=sys.stderr)


# Log hook start immediately
debug_log("=" * 80, "LIFECYCLE")
debug_log("HOOK STARTED", "LIFECYCLE")
debug_log(f"Python executable: {sys.executable}", "INIT")
debug_log(f"Working directory: {os.getcwd()}", "INIT")

# Ensure core directory exists before adding to path
if os.path.isdir(CORE_DIR):
    sys.path.insert(0, str(CORE_DIR))
    debug_log(f"Added to sys.path: {CORE_DIR}", "INIT")
else:
    msg = f"WARNING: claude-slack core directory not found at {CORE_DIR}"
    debug_log(msg, "ERROR")
    print(f"[on_posttooluse.py] {msg}", file=sys.stderr)

# Load environment variables from .env file
def load_env_file():
    """Load environment variables from claude-slack/.env"""
    env_path = CLAUDE_SLACK_DIR / ".env"
    debug_log(f"Looking for .env at: {env_path}", "ENV")
    if env_path.exists():
        debug_log(".env file found, loading...", "ENV")
        loaded_count = 0
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    if key not in os.environ:
                        os.environ[key] = value
                        loaded_count += 1
        debug_log(f"Loaded {loaded_count} environment variables", "ENV")
    else:
        debug_log(".env file not found", "ENV")

load_env_file()


def log_error(message: str):
    """Log error to stderr"""
    debug_log(f"ERROR: {message}", "ERROR")
    print(f"[on_posttooluse.py] ERROR: {message}", file=sys.stderr)


def log_info(message: str):
    """Log info to stderr"""
    debug_log(message, "INFO")
    print(f"[on_posttooluse.py] {message}", file=sys.stderr)


def format_todo_for_slack(todos: list) -> dict:
    """
    Format todo list for Slack using Block Kit.

    Args:
        todos: List of todo dicts with content, status, activeForm

    Returns:
        Dict with 'text' (fallback) and 'blocks' (rich formatting)
    """
    if not todos:
        return {
            "text": "No tasks in todo list",
            "blocks": []
        }

    # Count by status
    completed = [t for t in todos if t.get('status') == 'completed']
    in_progress = [t for t in todos if t.get('status') == 'in_progress']
    pending = [t for t in todos if t.get('status') == 'pending']

    total = len(todos)
    completed_count = len(completed)

    # Progress bar
    progress_pct = (completed_count / total * 100) if total > 0 else 0
    filled = int(progress_pct / 10)
    progress_bar = "█" * filled + "░" * (10 - filled)

    # Build blocks
    blocks = []

    # Header with progress
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*Task Progress* {progress_bar} {completed_count}/{total} ({progress_pct:.0f}%)"
        }
    })

    # Divider
    blocks.append({"type": "divider"})

    # In Progress section
    if in_progress:
        in_progress_text = "*In Progress:*\n"
        for t in in_progress:
            in_progress_text += f"  :hourglass_flowing_sand: {t.get('content', 'Unknown task')}\n"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": in_progress_text.strip()}
        })

    # Pending section
    if pending:
        pending_text = "*Pending:*\n"
        for t in pending:
            pending_text += f"  :white_circle: {t.get('content', 'Unknown task')}\n"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": pending_text.strip()}
        })

    # Completed section (collapsed if many)
    if completed:
        if len(completed) <= 3:
            completed_text = "*Completed:*\n"
            for t in completed:
                completed_text += f"  :white_check_mark: ~{t.get('content', 'Unknown task')}~\n"
        else:
            # Show count and last few
            completed_text = f"*Completed:* ({len(completed)} tasks)\n"
            for t in completed[-2:]:
                completed_text += f"  :white_check_mark: ~{t.get('content', 'Unknown task')}~\n"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": completed_text.strip()}
        })

    # Fallback text
    fallback_text = f"Task Progress: {completed_count}/{total} complete"

    return {
        "text": fallback_text,
        "blocks": blocks
    }


def post_or_update_slack(channel: str, thread_ts: str, message_ts: str, todo_data: dict, bot_token: str) -> str:
    """
    Post new message or update existing message in Slack.

    Args:
        channel: Slack channel ID
        thread_ts: Thread timestamp (None for top-level in custom channel mode)
        message_ts: Existing message timestamp to update (None for new post)
        todo_data: Dict with 'text' and 'blocks'
        bot_token: Slack bot token

    Returns:
        Message timestamp of posted/updated message, or None on failure
    """
    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
    except ImportError:
        log_error("slack_sdk not installed. Run: pip install slack-sdk")
        return None

    client = WebClient(token=bot_token)

    try:
        if message_ts:
            # Update existing message
            debug_log(f"Updating existing message: {message_ts}", "SLACK")
            result = client.chat_update(
                channel=channel,
                ts=message_ts,
                text=todo_data["text"],
                blocks=todo_data["blocks"]
            )
            log_info(f"Updated todo message: {message_ts}")
            return result["ts"]
        else:
            # Post new message
            debug_log(f"Posting new todo message to thread: {thread_ts}", "SLACK")
            kwargs = {
                "channel": channel,
                "text": todo_data["text"],
                "blocks": todo_data["blocks"]
            }
            if thread_ts:
                kwargs["thread_ts"] = thread_ts

            result = client.chat_postMessage(**kwargs)
            new_ts = result["ts"]
            log_info(f"Posted new todo message: {new_ts}")
            return new_ts

    except SlackApiError as e:
        error_msg = e.response.get('error', str(e))
        log_error(f"Slack API error: {error_msg}")

        # If update failed (message deleted?), try posting new
        if message_ts and error_msg in ('message_not_found', 'channel_not_found'):
            log_info("Message not found, posting new message instead")
            try:
                kwargs = {
                    "channel": channel,
                    "text": todo_data["text"],
                    "blocks": todo_data["blocks"]
                }
                if thread_ts:
                    kwargs["thread_ts"] = thread_ts

                result = client.chat_postMessage(**kwargs)
                return result["ts"]
            except SlackApiError as e2:
                log_error(f"Failed to post new message: {e2.response.get('error', str(e2))}")
                return None

        return None

    except Exception as e:
        log_error(f"Error posting/updating Slack: {e}")
        return None


def cleanup_stale_permission_message(session, db, bot_token):
    """
    Clean up any stale permission message for a session.

    When a user responds to a permission prompt via terminal (not Slack),
    the Slack message with buttons stays visible. This function deletes
    that stale message when Claude continues working (i.e., a tool is executed,
    meaning permission was granted).

    Args:
        session: Session dict from registry
        db: RegistryDatabase instance
        bot_token: Slack bot token

    Returns:
        True if message was cleaned up, False otherwise
    """
    permission_ts = session.get('permission_message_ts')
    if not permission_ts:
        debug_log("No pending permission message to clean up", "CLEANUP")
        return False

    channel = session.get('channel')
    if not channel:
        debug_log("No channel for permission cleanup", "CLEANUP")
        return False

    debug_log(f"Found stale permission message: {permission_ts} in channel {channel}", "CLEANUP")

    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
    except ImportError:
        log_error("slack_sdk not installed")
        return False

    client = WebClient(token=bot_token)

    try:
        # Delete the stale permission message
        client.chat_delete(
            channel=channel,
            ts=permission_ts
        )
        debug_log(f"Successfully deleted stale permission message: {permission_ts}", "CLEANUP")
        log_info(f"Cleaned up stale permission message")

        # Clear the permission_message_ts in the registry
        session_id = session.get('session_id')
        if session_id:
            db.update_session(session_id, {'permission_message_ts': None})
            debug_log(f"Cleared permission_message_ts for session {session_id[:8]}", "CLEANUP")

        return True

    except SlackApiError as e:
        error_msg = e.response.get('error', str(e))
        if error_msg == 'message_not_found':
            # Message was already deleted (e.g., via button click)
            debug_log(f"Permission message already deleted: {permission_ts}", "CLEANUP")
            # Still clear the ts in registry
            session_id = session.get('session_id')
            if session_id:
                db.update_session(session_id, {'permission_message_ts': None})
        else:
            log_error(f"Failed to delete permission message: {error_msg}")
        return False

    except Exception as e:
        log_error(f"Error cleaning up permission message: {e}")
        return False


def main():
    """Main hook entry point"""
    debug_log("Entering main()", "LIFECYCLE")
    try:
        # Read hook data from stdin
        debug_log("Reading hook data from stdin...", "INPUT")
        try:
            hook_data = json.load(sys.stdin)
            debug_log(f"Hook data received: {json.dumps(hook_data, indent=2)}", "INPUT")
        except json.JSONDecodeError as e:
            log_error(f"Failed to parse hook input JSON: {e}")
            sys.exit(0)

        # Extract hook parameters
        session_id = hook_data.get("session_id")
        tool_name = hook_data.get("tool_name")
        tool_input = hook_data.get("tool_input", {})

        debug_log(f"session_id: {session_id}", "INPUT")
        debug_log(f"tool_name: {tool_name}", "INPUT")

        # PERMISSION CLEANUP: Clean up stale permission messages for ANY tool use
        # This handles the case where user responded via terminal instead of Slack
        if session_id:
            try:
                from registry_db import RegistryDatabase
                db_path = os.environ.get("REGISTRY_DB_PATH", os.path.expanduser("~/.claude/slack/registry.db"))
                if os.path.exists(db_path):
                    db = RegistryDatabase(db_path)
                    session = db.get_session(session_id)
                    if session and session.get('permission_message_ts'):
                        bot_token = os.environ.get("SLACK_BOT_TOKEN")
                        if bot_token:
                            debug_log(f"Attempting permission cleanup for session {session_id[:8]}", "CLEANUP")
                            cleanup_stale_permission_message(session, db, bot_token)
            except Exception as e:
                debug_log(f"Permission cleanup failed (non-fatal): {e}", "CLEANUP")
                # Don't fail the hook if cleanup fails

        # Only process TodoWrite calls for the rest of the hook
        if tool_name != "TodoWrite":
            debug_log(f"Skipping tool: {tool_name}", "FILTER")
            sys.exit(0)

        log_info(f"Processing TodoWrite for session {session_id[:8] if session_id else 'unknown'}")

        if not session_id:
            log_error("No session_id in hook data")
            sys.exit(0)

        # Get the todos from tool_input
        todos = tool_input.get('todos', [])
        if not todos:
            debug_log("Empty todos list, skipping", "FILTER")
            sys.exit(0)

        # Format for Slack
        todo_data = format_todo_for_slack(todos)
        debug_log(f"Formatted todo data: {todo_data['text']}", "FORMAT")

        # Query registry database for session metadata
        debug_log("Importing registry_db...", "REGISTRY")
        try:
            from registry_db import RegistryDatabase
            debug_log("registry_db imported successfully", "REGISTRY")
        except ImportError as e:
            log_error(f"registry_db module not found: {e}")
            sys.exit(0)

        db_path = os.environ.get("REGISTRY_DB_PATH", os.path.expanduser("~/.claude/slack/registry.db"))
        debug_log(f"Registry database path: {db_path}", "REGISTRY")

        if not os.path.exists(db_path):
            log_error(f"Registry database not found: {db_path}")
            sys.exit(0)

        debug_log("Opening registry database...", "REGISTRY")
        db = RegistryDatabase(db_path)
        debug_log(f"Querying session: {session_id}", "REGISTRY")
        session = db.get_session(session_id)
        debug_log(f"Session found: {session is not None}", "REGISTRY")

        if not session:
            log_error(f"Session {session_id[:8]} not found in registry")
            sys.exit(0)

        # Extract Slack metadata
        slack_channel = session.get("channel")
        slack_thread_ts = session.get("thread_ts")
        todo_message_ts = session.get("todo_message_ts")
        debug_log(f"Slack channel: {slack_channel}", "SLACK")
        debug_log(f"Slack thread_ts: {slack_thread_ts}", "SLACK")
        debug_log(f"Todo message_ts: {todo_message_ts}", "SLACK")

        # SELF-HEALING: If session exists but Slack metadata is missing
        if not slack_channel:
            log_info(f"Session {session_id[:8]} missing Slack channel, attempting self-heal...")

            if len(session_id) > 8:
                wrapper_session_id = session_id[:8]
                debug_log(f"Looking for wrapper session: {wrapper_session_id}", "REGISTRY")
                wrapper_session = db.get_session(wrapper_session_id)

                if wrapper_session and wrapper_session.get("channel"):
                    log_info(f"Found wrapper session {wrapper_session_id} with metadata, copying...")

                    db.update_session(session_id, {
                        'slack_thread_ts': wrapper_session.get("thread_ts"),
                        'slack_channel': wrapper_session.get("channel")
                    })

                    session = db.get_session(session_id)
                    slack_channel = session.get("channel")
                    slack_thread_ts = session.get("thread_ts")
                    log_info(f"Self-healed: thread_ts={slack_thread_ts}, channel={slack_channel}")
                else:
                    log_error("Self-healing failed: no wrapper session found")
                    sys.exit(0)
            else:
                log_error(f"Session {session_id[:8]} missing Slack metadata and self-healing not applicable")
                sys.exit(0)

        if not slack_channel:
            log_error(f"Session {session_id[:8]} missing Slack channel after self-healing")
            sys.exit(0)

        log_info(f"Found Slack channel: {slack_channel}, thread: {slack_thread_ts}")

        # Get Slack bot token
        bot_token = os.environ.get("SLACK_BOT_TOKEN")
        if not bot_token:
            log_error("SLACK_BOT_TOKEN not set")
            sys.exit(0)

        debug_log("Bot token found, posting/updating Slack...", "SLACK")

        # Post or update todo message
        new_ts = post_or_update_slack(
            channel=slack_channel,
            thread_ts=slack_thread_ts,
            message_ts=todo_message_ts,
            todo_data=todo_data,
            bot_token=bot_token
        )

        if new_ts:
            # Store the message_ts for future updates
            if new_ts != todo_message_ts:
                debug_log(f"Storing new todo_message_ts: {new_ts}", "REGISTRY")
                db.update_session(session_id, {'todo_message_ts': new_ts})
                log_info(f"Stored todo_message_ts: {new_ts}")
            log_info("Successfully posted/updated todo in Slack")
            debug_log("Slack post/update successful", "SLACK")
        else:
            log_info("Failed to post/update todo in Slack (see errors above)")
            debug_log("Slack post/update failed", "SLACK")

        # Forward todo update to DM subscribers
        try:
            from dm_mode import forward_to_dm_subscribers
            from slack_sdk import WebClient
            dm_client = WebClient(token=bot_token)
            todo_text = todo_data.get('text', 'Todo list updated')
            forward_to_dm_subscribers(db, session_id, todo_text, dm_client)
            debug_log("Forwarded todo update to DM subscribers", "DM")
        except ImportError:
            debug_log("dm_mode not available, skipping DM forwarding", "DM")
        except Exception as e:
            debug_log(f"Error forwarding todo to DM: {e}", "DM")

    except Exception as e:
        # Catch-all error handler
        log_error(f"Unexpected error in hook: {e}")
        debug_log(f"EXCEPTION: {e}", "ERROR")
        import traceback
        tb = traceback.format_exc()
        debug_log(f"Traceback:\n{tb}", "ERROR")
        traceback.print_exc(file=sys.stderr)

    finally:
        # ALWAYS exit 0 (never block Claude)
        debug_log("Hook exiting (code 0)", "LIFECYCLE")
        debug_log("=" * 80, "LIFECYCLE")
        sys.exit(0)


if __name__ == "__main__":
    main()
