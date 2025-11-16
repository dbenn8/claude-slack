#!/usr/bin/env python3
"""
Claude Code PreToolUse Hook - Capture AskUserQuestion calls to Slack

Triggered before Claude executes any tool, allowing us to capture AskUserQuestion
calls with their full question text and options, which are not available in the
Notification hook.

Hook Input (stdin):
    {
        "session_id": "abc12345",
        "transcript_path": "/path/to/transcript.jsonl",
        "cwd": "/path/to/project",
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [
                {
                    "question": "Which approach should we use?",
                    "header": "Approach",
                    "multiSelect": false,
                    "options": [
                        {"label": "Option 1", "description": "..."},
                        {"label": "Option 2", "description": "..."}
                    ]
                }
            ]
        }
    }

Environment Variables:
    SLACK_BOT_TOKEN - Bot User OAuth Token (required)
    REGISTRY_DATA_DIR - Registry database directory (default: /tmp/claude_sessions)

Architecture:
    1. Read hook data from stdin
    2. Check if tool_name is "AskUserQuestion"
    3. If yes, format the questions with all options
    4. Query registry_db for session metadata (Slack thread info)
    5. Post formatted questions to Slack thread
    6. Exit 0 (success or failure)

Debug Logging:
    - All execution logged to /tmp/pretooluse_hook_debug.log
"""

import sys
import json
import os
from pathlib import Path
from datetime import datetime

# Debug log file path
DEBUG_LOG = "/tmp/pretooluse_hook_debug.log"

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
            print(f"[on_pretooluse.py] ERROR: CLAUDE_SLACK_DIR is set to '{env_path}' but no claude-slack installation found there.", file=sys.stderr)
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
        print(f"[on_pretooluse.py] DEBUG LOG FAILED: {e}", file=sys.stderr)


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
    print(f"[on_pretooluse.py] {msg}", file=sys.stderr)

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
    print(f"[on_pretooluse.py] ERROR: {message}", file=sys.stderr)


def log_info(message: str):
    """Log info to stderr"""
    debug_log(message, "INFO")
    print(f"[on_pretooluse.py] {message}", file=sys.stderr)


def format_question_for_slack(question: dict, index: int, total: int) -> str:
    """
    Format a single question with options for Slack.

    Args:
        question: Question dict with question, header, options, multiSelect
        index: Question number (0-indexed)
        total: Total number of questions

    Returns:
        Formatted markdown string
    """
    lines = []

    # Question header
    if total > 1:
        lines.append(f"**Question {index + 1}/{total}: {question.get('question', 'N/A')}**")
    else:
        lines.append(f"**{question.get('question', 'N/A')}**")

    lines.append("")

    # Options
    options = question.get('options', [])
    multi_select = question.get('multiSelect', False)

    if multi_select:
        lines.append("_(Multiple selections allowed)_")
        lines.append("")

    for i, option in enumerate(options, 1):
        label = option.get('label', f'Option {i}')
        description = option.get('description', '')

        lines.append(f"{i}. **{label}**")
        if description:
            lines.append(f"   _{description}_")
        lines.append("")

    return "\n".join(lines)


def format_askuserquestion_for_slack(tool_input: dict) -> str:
    """
    Format AskUserQuestion tool input for Slack message.

    Args:
        tool_input: The tool_input dict containing questions array

    Returns:
        Formatted markdown string ready for Slack
    """
    questions = tool_input.get('questions', [])

    if not questions:
        return "❓ Claude has a question (no details available)"

    lines = ["❓ **Claude needs your input:**", ""]

    for i, question in enumerate(questions):
        lines.append(format_question_for_slack(question, i, len(questions)))
        if i < len(questions) - 1:
            lines.append("---")
            lines.append("")

    lines.append("_Reply with the number(s) of your choice._")

    return "\n".join(lines)


def split_message(text: str, max_length: int = 39000) -> list:
    """
    Split long message into chunks that fit in Slack's 40K char limit.

    Args:
        text: Message text to split
        max_length: Max chars per chunk (default: 39000, leaves room for part indicators)

    Returns:
        List of text chunks
    """
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        # Find a good breaking point (newline near max_length)
        if len(text) <= max_length:
            chunks.append(text)
            break

        # Look for newline near the max length
        break_point = text.rfind('\n', max_length - 500, max_length)
        if break_point == -1:
            # No newline found, just split at max_length
            break_point = max_length

        chunks.append(text[:break_point])
        text = text[break_point:].lstrip('\n')

    return chunks


def post_to_slack(channel: str, thread_ts: str, text: str, bot_token: str):
    """
    Post message to Slack thread, handling long messages.

    Args:
        channel: Slack channel ID
        thread_ts: Thread timestamp
        text: Message text
        bot_token: Slack bot token
    """
    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
    except ImportError:
        log_error("slack_sdk not installed. Run: pip install slack-sdk")
        return False

    client = WebClient(token=bot_token)

    # Split message if too long
    chunks = split_message(text)

    if len(chunks) > 5:
        # Too many chunks, truncate
        log_info(f"Message too long ({len(chunks)} chunks), truncating to 5 chunks")
        chunks = chunks[:5]

    # Post each chunk
    for i, chunk in enumerate(chunks):
        try:
            # Add part indicator for multi-part messages
            if len(chunks) > 1:
                message_text = f"{chunk}\n\n_(Part {i+1}/{len(chunks)})_"
            else:
                message_text = chunk

            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=message_text
            )

            log_info(f"Posted to Slack (part {i+1}/{len(chunks)})")

        except SlackApiError as e:
            log_error(f"Slack API error: {e.response['error']}")
            return False
        except Exception as e:
            log_error(f"Error posting to Slack: {e}")
            return False

    return True


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

        # Only process AskUserQuestion calls
        if tool_name != "AskUserQuestion":
            debug_log(f"Skipping tool: {tool_name}", "FILTER")
            sys.exit(0)

        log_info(f"Processing AskUserQuestion for session {session_id[:8] if session_id else 'unknown'}")

        if not session_id:
            log_error("No session_id in hook data")
            sys.exit(0)

        # Format the question for Slack
        slack_message = format_askuserquestion_for_slack(tool_input)
        debug_log(f"Formatted message (first 200 chars): {slack_message[:200]}", "FORMAT")

        # Query registry database for session metadata
        debug_log("Importing registry_db...", "REGISTRY")
        try:
            from registry_db import RegistryDatabase
            debug_log("registry_db imported successfully", "REGISTRY")
        except ImportError as e:
            log_error(f"registry_db module not found: {e}")
            sys.exit(0)

        registry_dir = os.environ.get("REGISTRY_DATA_DIR", "/tmp/claude_sessions")
        db_path = os.path.join(registry_dir, "registry.db")
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
        debug_log(f"Slack channel: {slack_channel}", "SLACK")
        debug_log(f"Slack thread_ts: {slack_thread_ts}", "SLACK")

        # SELF-HEALING: If session exists but Slack metadata is missing
        if not slack_channel or not slack_thread_ts:
            log_info(f"Session {session_id[:8]} missing Slack metadata, attempting self-heal...")

            if len(session_id) > 8:
                wrapper_session_id = session_id[:8]
                debug_log(f"Looking for wrapper session: {wrapper_session_id}", "REGISTRY")
                wrapper_session = db.get_session(wrapper_session_id)

                if wrapper_session and wrapper_session.get("thread_ts") and wrapper_session.get("channel"):
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

        if not slack_channel or not slack_thread_ts:
            log_error(f"Session {session_id[:8]} missing Slack metadata after self-healing")
            sys.exit(0)

        log_info(f"Found Slack thread: {slack_channel} / {slack_thread_ts}")

        # Get Slack bot token
        bot_token = os.environ.get("SLACK_BOT_TOKEN")
        if not bot_token:
            log_error("SLACK_BOT_TOKEN not set")
            sys.exit(0)

        debug_log("Bot token found, posting to Slack...", "SLACK")

        # Post question to Slack
        success = post_to_slack(slack_channel, slack_thread_ts, slack_message, bot_token)

        if success:
            log_info("Successfully posted to Slack")
            debug_log("Slack post successful", "SLACK")
        else:
            log_info("Failed to post to Slack (see errors above)")
            debug_log("Slack post failed", "SLACK")

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
