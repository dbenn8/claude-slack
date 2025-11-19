#!/usr/bin/env python3
"""
Claude Code Stop Hook - Post Assistant Responses to Slack

Version: 1.1.0

Changelog:
- v1.1.0 (2025/11/18): Fixed early termination bug - continue posting remaining chunks on failure
- v1.0.0 (2025/11/18): Initial versioned release

Triggered when Claude finishes processing a user prompt.
Reads the transcript, extracts the latest assistant response, and posts it to Slack.

Hook Input (stdin):
    {
        "session_id": "abc12345",
        "transcript_path": "/path/to/transcript.jsonl",
        "project_dir": "/path/to/project"
    }

Environment Variables:
    SLACK_BOT_TOKEN - Bot User OAuth Token (required)
    REGISTRY_DATA_DIR - Registry database directory (default: /tmp/claude_sessions)

Error Handling:
    - Always exits with code 0 (never blocks Claude)
    - Logs all errors to stderr
    - Handles missing sessions, transcripts, Slack API errors
    - Splits long responses into multiple messages

Architecture:
    1. Read hook data from stdin
    2. Parse transcript using transcript_parser
    3. Query registry_db for session metadata
    4. Post response to Slack thread
    5. Exit 0 (success or failure)

Debug Logging:
    - All execution logged to /tmp/stop_hook_debug.log
    - Includes timestamps, session info, environment vars
    - Tracks hook lifecycle from entry to exit
"""

import sys
import json
import os
from pathlib import Path
from datetime import datetime

# Hook version for auto-update detection
HOOK_VERSION = "1.1.0"

# Debug log file path
DEBUG_LOG = "/tmp/stop_hook_debug.log"

# Find claude-slack directory dynamically
# Hooks are templates that get copied to project folders, but they need to find the
# universal claude-slack installation to import core modules
def find_claude_slack_dir():
    """
    Find claude-slack directory using standard discovery patterns.

    Search order:
    1. $CLAUDE_SLACK_DIR environment variable (explicit override - takes precedence)
    2. Search upward from current directory for .claude/claude-slack/
    3. Fall back to ~/.claude/claude-slack/ (user home)

    Returns:
        Path to claude-slack directory (verified by existence of core/ subdirectory)

    Raises:
        SystemExit: If $CLAUDE_SLACK_DIR is set but invalid (with helpful error message)
    """
    import os

    # 1. Environment variable override (takes precedence)
    if 'CLAUDE_SLACK_DIR' in os.environ:
        env_path = Path(os.environ['CLAUDE_SLACK_DIR'])
        if (env_path / 'core').exists():
            return env_path
        else:
            # User explicitly set env var but it's wrong - show helpful error
            print(f"[on_stop.py] ERROR: CLAUDE_SLACK_DIR is set to '{env_path}' but no claude-slack installation found there.", file=sys.stderr)
            print(f"[on_stop.py] Either:", file=sys.stderr)
            print(f"[on_stop.py]   1. Fix the path: export CLAUDE_SLACK_DIR=/correct/path/to/.claude/claude-slack", file=sys.stderr)
            print(f"[on_stop.py]   2. Unset to allow auto-discovery: unset CLAUDE_SLACK_DIR", file=sys.stderr)
            sys.exit(0)  # Don't block Claude

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
PROJECT_DIR = CLAUDE_SLACK_DIR


def debug_log(message: str, section: str = "GENERAL"):
    """
    Log debug message to file with timestamp and section.

    Args:
        message: Message to log
        section: Section identifier (e.g., 'INIT', 'TRANSCRIPT', 'SLACK')
    """
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[{timestamp}] [{section}] {message}\n")
    except Exception as e:
        # If debug logging fails, log to stderr but don't crash
        print(f"[on_stop.py] DEBUG LOG FAILED: {e}", file=sys.stderr)


# Log hook start immediately
debug_log("=" * 80, "LIFECYCLE")
debug_log("HOOK STARTED", "LIFECYCLE")
debug_log(f"Python executable: {sys.executable}", "INIT")
debug_log(f"Python version: {sys.version}", "INIT")
debug_log(f"Working directory: {os.getcwd()}", "INIT")
debug_log(f"Script path: {__file__}", "INIT")

# Ensure core directory exists before adding to path
if os.path.isdir(CORE_DIR):
    sys.path.insert(0, str(CORE_DIR))
    debug_log(f"Added to sys.path: {CORE_DIR}", "INIT")
else:
    msg = f"WARNING: claude-slack core directory not found at {CORE_DIR}"
    debug_log(msg, "ERROR")
    print(f"[on_stop.py] {msg}", file=sys.stderr)

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
                    # Only set if not already in environment
                    if key not in os.environ:
                        os.environ[key] = value
                        loaded_count += 1
                        # Log non-sensitive keys
                        if "TOKEN" not in key and "SECRET" not in key:
                            debug_log(f"Loaded: {key}={value}", "ENV")
                        else:
                            debug_log(f"Loaded: {key}=***REDACTED***", "ENV")
        debug_log(f"Loaded {loaded_count} environment variables", "ENV")
    else:
        debug_log(".env file not found", "ENV")

load_env_file()

# Log all relevant environment variables (redact sensitive ones)
debug_log("Environment variables:", "ENV")
for key in ["SLACK_BOT_TOKEN", "REGISTRY_DATA_DIR", "CLAUDE_TRANSCRIPT_PATH"]:
    value = os.environ.get(key)
    if value:
        if "TOKEN" in key:
            debug_log(f"  {key}=***REDACTED*** (length: {len(value)})", "ENV")
        else:
            debug_log(f"  {key}={value}", "ENV")
    else:
        debug_log(f"  {key}=<not set>", "ENV")


def log_error(message: str):
    """Log error to stderr (visible in Claude logs, doesn't block user)"""
    debug_log(f"ERROR: {message}", "ERROR")
    print(f"[on_stop.py] ERROR: {message}", file=sys.stderr)


def log_info(message: str):
    """Log info to stderr"""
    debug_log(message, "INFO")
    print(f"[on_stop.py] {message}", file=sys.stderr)


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
    failed_chunks = []
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
            log_error(f"Slack API error on chunk {i+1}: {e.response['error']}")
            failed_chunks.append(i+1)
            continue
        except Exception as e:
            log_error(f"Error posting chunk {i+1} to Slack: {e}")
            failed_chunks.append(i+1)
            continue

    if failed_chunks:
        log_error(f"Failed to post chunks: {failed_chunks}")
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
        transcript_path = hook_data.get("transcript_path")
        project_dir = hook_data.get("project_dir")

        debug_log(f"session_id: {session_id}", "INPUT")
        debug_log(f"transcript_path: {transcript_path}", "INPUT")
        debug_log(f"project_dir: {project_dir}", "INPUT")

        if not session_id:
            log_error("No session_id in hook data")
            sys.exit(0)

        # Use transcript path from hook data, or construct from environment
        if not transcript_path:
            transcript_path = os.environ.get("CLAUDE_TRANSCRIPT_PATH")

        if not transcript_path:
            log_error("No transcript_path provided")
            sys.exit(0)

        log_info(f"Processing session {session_id[:8]}")

        # Parse transcript
        debug_log("Importing transcript_parser...", "TRANSCRIPT")
        try:
            from transcript_parser import TranscriptParser
            debug_log("transcript_parser imported successfully", "TRANSCRIPT")
        except ImportError as e:
            log_error(f"transcript_parser module not found: {e}")
            sys.exit(0)

        debug_log(f"Creating TranscriptParser for: {transcript_path}", "TRANSCRIPT")
        parser = TranscriptParser(transcript_path)

        # Retry loading transcript (may not be flushed yet)
        max_retries = 3
        debug_log(f"Attempting to load transcript (max {max_retries} retries)...", "TRANSCRIPT")
        for attempt in range(max_retries):
            debug_log(f"Load attempt {attempt + 1}/{max_retries}", "TRANSCRIPT")
            if parser.load():
                debug_log("Transcript loaded successfully", "TRANSCRIPT")
                break

            if attempt < max_retries - 1:
                import time
                wait_time = 0.1 * (2 ** attempt)  # Exponential backoff: 100ms, 200ms, 400ms
                log_info(f"Transcript not ready, retrying in {wait_time}s...")
                time.sleep(wait_time)
        else:
            log_error(f"Transcript file not found after {max_retries} retries: {transcript_path}")
            sys.exit(0)

        # Extract latest assistant response
        debug_log("Extracting latest assistant response...", "TRANSCRIPT")
        response = parser.get_latest_assistant_response(text_only=True)
        debug_log(f"Response extracted: {response is not None}", "TRANSCRIPT")

        if not response:
            log_info("No assistant response with text found (tool-only response)")
            sys.exit(0)

        response_text = response['text']

        if not response_text.strip():
            log_info("Assistant response is empty")
            sys.exit(0)

        log_info(f"Extracted response: {len(response_text)} chars")

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
            debug_log("Attempting self-healing for missing Slack metadata", "REGISTRY")

            # Look for a shorter session ID (wrapper session) with matching project
            # Wrapper session IDs are 8 chars, Claude UUIDs are 36 chars (with dashes)
            if len(session_id) > 8:
                # Extract first 8 chars as potential wrapper ID
                wrapper_session_id = session_id[:8]
                debug_log(f"Looking for wrapper session: {wrapper_session_id}", "REGISTRY")
                wrapper_session = db.get_session(wrapper_session_id)

                if wrapper_session and wrapper_session.get("thread_ts") and wrapper_session.get("channel"):
                    log_info(f"Found wrapper session {wrapper_session_id} with metadata, copying...")
                    debug_log(f"Wrapper has thread_ts={wrapper_session.get('thread_ts')}, channel={wrapper_session.get('channel')}", "REGISTRY")

                    # Copy metadata to Claude session
                    db.update_session(session_id, {
                        'slack_thread_ts': wrapper_session.get("thread_ts"),
                        'slack_channel': wrapper_session.get("channel")
                    })

                    # Re-query to get updated session
                    session = db.get_session(session_id)
                    slack_channel = session.get("channel")
                    slack_thread_ts = session.get("thread_ts")

                    log_info(f"Self-healed: thread_ts={slack_thread_ts}, channel={slack_channel}")
                    debug_log("Self-healing successful", "REGISTRY")
                else:
                    log_error(f"Self-healing failed: no wrapper session found or it also missing metadata")
                    debug_log("Self-healing failed: no suitable wrapper session", "REGISTRY")
                    sys.exit(0)
            else:
                log_error(f"Session {session_id[:8]} missing Slack metadata and self-healing not applicable (wrapper session)")
                sys.exit(0)

        # Final check after self-healing attempt
        if not slack_channel or not slack_thread_ts:
            log_error(f"Session {session_id[:8]} missing Slack metadata after self-healing (channel={slack_channel}, thread_ts={slack_thread_ts})")
            sys.exit(0)

        log_info(f"Found Slack thread: {slack_channel} / {slack_thread_ts}")

        # Get Slack bot token
        bot_token = os.environ.get("SLACK_BOT_TOKEN")
        if not bot_token:
            log_error("SLACK_BOT_TOKEN not set")
            sys.exit(0)

        debug_log("Bot token found, posting to Slack...", "SLACK")

        # Post to Slack
        success = post_to_slack(slack_channel, slack_thread_ts, response_text, bot_token)

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
