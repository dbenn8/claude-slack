#!/usr/bin/env python3
"""
Claude Code PreToolUse Hook - Capture AskUserQuestion calls to Slack

Version: 1.1.0

Changelog:
- v1.1.0 (2025/11/18): Fixed early termination bug - continue posting remaining chunks on failure
- v1.0.0 (2025/11/18): Initial versioned release

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
    REGISTRY_DB_PATH - Registry database path (default: ~/.claude/slack/registry.db)

Architecture:
    1. Read hook data from stdin
    2. Check if tool_name is "AskUserQuestion"
    3. If yes, format the questions with all options
    4. Query registry_db for session metadata (Slack thread info)
    5. Post formatted questions to Slack thread
    6. Exit 0 (success or failure)

Debug Logging:
    - All execution logged to ~/.claude/slack/logs/pretooluse_hook_debug.log
"""

import sys
import json
import os
import time
import fcntl
from pathlib import Path
from datetime import datetime

# Hook version for auto-update detection
HOOK_VERSION = "1.1.0"

# Log directory - use ~/.claude/slack/logs as default
LOG_DIR = os.environ.get("SLACK_LOG_DIR", os.path.expanduser("~/.claude/slack/logs"))
os.makedirs(LOG_DIR, exist_ok=True)

# Debug log file path
DEBUG_LOG = os.path.join(LOG_DIR, "pretooluse_hook_debug.log")

# Response file directory for AskUserQuestion responses
ASKUSER_RESPONSE_DIR = Path.home() / ".claude" / "slack" / "askuser_responses"
ASKUSER_RESPONSE_DIR.mkdir(parents=True, exist_ok=True)

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
    # Emoji numbers for option display (1-indexed for UX, but stored as 0-indexed)
    # 1️⃣ displays as option 1 but maps to index '0' in response
    # 2️⃣ displays as option 2 but maps to index '1' in response
    # etc.
    # This list is displayed to users and used to identify which emoji to react with
    EMOJI_NUMBERS = ['1️⃣', '2️⃣', '3️⃣', '4️⃣']

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

    # Build list of emoji indicators for instructions
    emoji_list = []
    for i in range(min(len(options), len(EMOJI_NUMBERS))):
        emoji_list.append(EMOJI_NUMBERS[i])

    # Format each option with emoji number
    for i, option in enumerate(options):
        if i < len(EMOJI_NUMBERS):
            emoji = EMOJI_NUMBERS[i]
            label = option.get('label', f'Option {i+1}')
            description = option.get('description', '')

            lines.append(f"{emoji} **{label}**")
            if description:
                lines.append(f"   _{description}_")
            lines.append("")

    # Add "Other" option
    lines.append("💬 **Other** (reply in thread)")
    lines.append("")

    # Add reaction instruction
    if multi_select:
        instruction = f"React with one or more: {' '.join(emoji_list)}"
    else:
        instruction = f"React with {' '.join(emoji_list)}"

    lines.append(f"_{instruction}_")

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

    return "\n".join(lines)


def validate_askuser_input(tool_input: dict) -> tuple[bool, str]:
    """
    Validate AskUserQuestion tool_input structure.

    Args:
        tool_input: The tool_input dict to validate

    Returns:
        Tuple of (is_valid: bool, error_message: str)
    """
    # Check for questions array
    questions = tool_input.get('questions')
    if not questions:
        return False, "Missing 'questions' array"

    if not isinstance(questions, list):
        return False, "'questions' must be a list"

    if len(questions) > 4:
        return False, "Maximum 4 questions allowed"

    # Validate each question
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            return False, f"Question {i} must be a dict"

        if 'question' not in q:
            return False, f"Question {i} missing 'question' text"

        # Validate options array
        options = q.get('options', [])
        if not isinstance(options, list):
            return False, f"Question {i} 'options' must be a list"

        if len(options) > 4:
            return False, f"Question {i} has more than 4 options"

        # Validate each option
        for j, opt in enumerate(options):
            if not isinstance(opt, dict):
                return False, f"Question {i} option {j} must be a dict"
            if 'label' not in opt:
                return False, f"Question {i} option {j} missing 'label'"

    return True, ""


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


def post_to_slack(channel: str, thread_ts: str, text: str, bot_token: str, session_id: str = None, request_id: str = None, num_questions: int = 1):
    """
    Post message to Slack thread, handling long messages.

    Args:
        channel: Slack channel ID
        thread_ts: Thread timestamp
        text: Message text
        bot_token: Slack bot token
        session_id: Session ID (optional, for block_id)
        request_id: Request ID (optional, for block_id)
        num_questions: Number of questions (for multi-question block_ids)

    Returns:
        Tuple of (success: bool, message_ts: str or None)
    """
    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
    except ImportError:
        log_error("slack_sdk not installed. Run: pip install slack-sdk")
        return False, None

    client = WebClient(token=bot_token)

    # Split message if too long
    chunks = split_message(text)

    if len(chunks) > 5:
        # Too many chunks, truncate
        log_info(f"Message too long ({len(chunks)} chunks), truncating to 5 chunks")
        chunks = chunks[:5]

    # Post each chunk
    failed_chunks = []
    first_message_ts = None
    for i, chunk in enumerate(chunks):
        try:
            # Add part indicator for multi-part messages
            if len(chunks) > 1:
                message_text = f"{chunk}\n\n_(Part {i+1}/{len(chunks)})_"
            else:
                message_text = chunk

            # For first chunk with AskUserQuestion, use blocks with block_id(s)
            if i == 0 and session_id and request_id:
                # For multi-question, create multiple blocks with distinct block_ids
                if num_questions > 1:
                    # Split the message by question dividers
                    # Each question should get its own block with distinct block_id
                    blocks = []

                    # Parse the message to identify question sections
                    # Look for "Question N/M:" patterns to split
                    import re
                    question_pattern = r'\*\*Question (\d+)/\d+:'

                    # Find all question positions
                    question_matches = list(re.finditer(question_pattern, message_text))

                    if len(question_matches) >= num_questions:
                        # Split message by questions
                        for q_idx in range(num_questions):
                            start_pos = question_matches[q_idx].start() if q_idx < len(question_matches) else 0
                            end_pos = question_matches[q_idx + 1].start() if q_idx + 1 < len(question_matches) else len(message_text)

                            question_text = message_text[start_pos:end_pos].strip()

                            blocks.append({
                                "type": "section",
                                "block_id": f"askuser_Q{q_idx}_{session_id}_{request_id}",
                                "text": {"type": "mrkdwn", "text": question_text}
                            })
                    else:
                        # Fallback: single block for all questions
                        blocks = [
                            {
                                "type": "section",
                                "block_id": f"askuser_Q0_{session_id}_{request_id}",
                                "text": {"type": "mrkdwn", "text": message_text}
                            }
                        ]
                else:
                    # Single question - single block
                    blocks = [
                        {
                            "type": "section",
                            "block_id": f"askuser_Q0_{session_id}_{request_id}",
                            "text": {"type": "mrkdwn", "text": message_text}
                        }
                    ]

                response = client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=message_text,
                    blocks=blocks
                )
                first_message_ts = response['ts']
            else:
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
        return False, None

    return True, first_message_ts


def get_askuser_response_file(session_id: str, request_id: str) -> Path:
    """Get path to response file for an AskUserQuestion request.

    Args:
        session_id: Claude session ID
        request_id: Unique request identifier

    Returns:
        Path to the response file
    """
    return ASKUSER_RESPONSE_DIR / f"{session_id}_{request_id}.json"


def is_response_complete(response_data: dict, num_questions: int) -> bool:
    """Check if all questions have been answered.

    Args:
        response_data: Response data from Slack listener
        num_questions: Total number of questions in the prompt

    Returns:
        True if all questions answered, False otherwise
    """
    for i in range(num_questions):
        question_key = f"question_{i}"
        if question_key not in response_data:
            return False
    return True


def accumulate_askuser_response(session_id: str, request_id: str, new_data: dict):
    """Accumulate partial responses into response file.

    This allows users to answer questions one at a time. Each answer is merged
    into the existing response file.

    Args:
        session_id: Claude session ID
        request_id: Unique request identifier
        new_data: New response data to merge (e.g., {"question_1": "2"})
    """
    response_file = get_askuser_response_file(session_id, request_id)

    # Load existing data if file exists
    existing_data = {}
    if response_file.exists():
        try:
            with open(response_file) as f:
                existing_data = json.load(f)
        except Exception as e:
            debug_log(f"Failed to load existing response: {e}", "ACCUMULATE")

    # Merge new data
    existing_data.update(new_data)

    # Write back
    try:
        with open(response_file, 'w') as f:
            json.dump(existing_data, f)
        debug_log(f"Accumulated response: {existing_data}", "ACCUMULATE")
    except Exception as e:
        debug_log(f"Failed to accumulate response: {e}", "ACCUMULATE")


def cleanup_askuser_response_file(response_file: Path):
    """Remove response file after reading.

    Args:
        response_file: Path to the response file to delete
    """
    try:
        if response_file.exists():
            response_file.unlink()
            debug_log(f"Cleaned up response file: {response_file}", "CLEANUP")
    except Exception as e:
        debug_log(f"Failed to cleanup response file: {e}", "WARN")


def read_and_cleanup_response_file(response_file: Path) -> dict:
    """Atomically read and clean up response file with locking.

    Uses a lock file pattern to prevent race conditions when the Slack listener
    is writing to the file while the hook is reading/deleting it.

    Args:
        response_file: Path to the response file to read and delete

    Returns:
        dict: Parsed JSON data from the file, or None if file doesn't exist or error occurs
    """
    lock_file = Path(str(response_file) + '.lock')

    try:
        # Create and lock
        with open(lock_file, 'w') as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)  # Exclusive lock

            # Read if exists
            if response_file.exists():
                try:
                    with open(response_file) as f:
                        data = json.load(f)
                    response_file.unlink()  # Delete after reading
                    debug_log(f"Atomically read and cleaned up: {response_file}", "ATOMIC")
                    return data
                except json.JSONDecodeError as e:
                    debug_log(f"Error parsing JSON in {response_file}: {e}", "ERROR")
                    # Clean up corrupt file
                    try:
                        response_file.unlink()
                    except:
                        pass
                    return None
                except Exception as e:
                    debug_log(f"Error reading {response_file}: {e}", "ERROR")
                    return None
            else:
                debug_log(f"Response file not found: {response_file}", "ATOMIC")
                return None
    except Exception as e:
        debug_log(f"Error in atomic read: {e}", "ERROR")
        return None
    finally:
        # Clean up lock file
        try:
            if lock_file.exists():
                lock_file.unlink()
        except:
            pass


def cleanup_stale_response_files(max_age_seconds: int = 300):
    """Remove response files older than max_age_seconds.

    Args:
        max_age_seconds: Maximum age of files to keep (default: 300 = 5 minutes)
    """
    cutoff = time.time() - max_age_seconds

    for file in ASKUSER_RESPONSE_DIR.glob('*.json'):
        try:
            if file.stat().st_mtime < cutoff:
                file.unlink()
                debug_log(f"Cleaned up stale file: {file.name}", "CLEANUP")
        except Exception as e:
            pass  # Ignore errors


def build_askuser_output(response_data: dict, questions: list) -> dict:
    """Build the hook output JSON for Claude from response data.

    Args:
        response_data: Response data from Slack listener, format:
            {"question_0": "1", "question_1": ["0", "2"], ...}
            or {"question_0": "other", "question_0_text": "custom text"}
        questions: Original questions list from tool_input

    Returns:
        Hook output in Claude's expected format:
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "output": {
                    "decision": "answered",
                    "answers": {"question_0": "Selected Option Label"}
                }
            }
        }
    """
    answers = {}

    for i, question in enumerate(questions):
        question_key = f"question_{i}"

        if question_key not in response_data:
            continue

        response_value = response_data[question_key]
        options = question.get('options', [])

        # Handle "other" text input
        if response_value == "other":
            text_key = f"{question_key}_text"
            if text_key in response_data:
                answers[question_key] = response_data[text_key]
            else:
                answers[question_key] = "Other"
            continue

        # Handle multi-select (list of indices)
        if isinstance(response_value, list):
            selected_labels = []
            for idx_str in response_value:
                try:
                    idx = int(idx_str)
                    if 0 <= idx < len(options):
                        selected_labels.append(options[idx]['label'])
                except (ValueError, IndexError, KeyError):
                    continue
            answers[question_key] = selected_labels
        else:
            # Handle single-select (string index)
            try:
                idx = int(response_value)
                if 0 <= idx < len(options):
                    answers[question_key] = options[idx]['label']
            except (ValueError, IndexError, KeyError):
                answers[question_key] = str(response_value)

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "output": {
                "decision": "answered",
                "answers": answers
            }
        }
    }


def wait_for_askuser_response(session_id: str, request_id: str, timeout: float = 300, poll_interval: float = 0.5, num_questions: int = 1) -> dict:
    """Wait for response from Slack listener by polling for response file.

    For multi-question prompts, waits until ALL questions are answered.

    Args:
        session_id: Claude session ID
        request_id: Unique request identifier
        timeout: Maximum time to wait in seconds (default: 300 = 5 minutes)
        poll_interval: Time between polls in seconds (default: 0.5)
        num_questions: Number of questions to wait for (default: 1)

    Returns:
        Response data dict if file appears, None on timeout
    """
    response_file = get_askuser_response_file(session_id, request_id)
    start_time = time.time()

    debug_log(f"Waiting for response: {response_file} (timeout: {timeout}s, questions: {num_questions})", "WAIT")

    while time.time() - start_time < timeout:
        if response_file.exists():
            # Use atomic read to prevent race conditions
            response = read_and_cleanup_response_file(response_file)

            if response is not None:
                # Check if all questions are answered
                if is_response_complete(response, num_questions):
                    debug_log(f"Got complete response: {response}", "WAIT")
                    return response
                else:
                    # Partial response - rewrite it and keep waiting
                    # (the atomic read deleted it, so we need to restore it)
                    answered = sum(1 for i in range(num_questions) if f"question_{i}" in response)
                    debug_log(f"Partial response: {answered}/{num_questions} answered, waiting...", "WAIT")
                    try:
                        with open(response_file, 'w') as f:
                            json.dump(response, f)
                    except Exception as e:
                        debug_log(f"Error restoring partial response: {e}", "ERROR")

        time.sleep(poll_interval)

    debug_log(f"Timeout waiting for response after {timeout}s", "WAIT")
    cleanup_askuser_response_file(response_file)
    return None


def cleanup_askuser_message(client, channel: str, message_ts: str, selection: str, num_questions: int = 1):
    """Update or delete the Slack message after response.

    Args:
        client: Slack WebClient instance
        channel: Slack channel ID
        message_ts: Message timestamp
        selection: User's selection to display
        num_questions: Number of questions (for multi-question summary)
    """
    try:
        # Update message to show what was selected
        if num_questions > 1:
            # Show compact summary for multi-question
            text = f"✓ All {num_questions} questions answered"
        else:
            text = f"✓ Selected: {selection}"

        client.chat_update(
            channel=channel,
            ts=message_ts,
            text=text,
            blocks=[]
        )
        debug_log(f"Updated message {message_ts} with selection: {selection}", "CLEANUP")
    except Exception as e:
        debug_log(f"Failed to cleanup message: {e}", "WARN")


def main():
    """Main hook entry point"""
    debug_log("Entering main()", "LIFECYCLE")
    response_file = None  # Track for cleanup in finally block

    # Clean up stale response files from previous runs
    cleanup_stale_response_files()

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

        # Validate input structure
        is_valid, error_msg = validate_askuser_input(tool_input)
        if not is_valid:
            log_error(f"Invalid AskUserQuestion input: {error_msg}")
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

        # Generate unique request ID
        request_id = f"{int(time.time() * 1000)}"
        debug_log(f"Generated request_id: {request_id}", "REQUEST")

        # Track response file for cleanup in finally block
        response_file = get_askuser_response_file(session_id, request_id)

        # Get number of questions
        questions = tool_input.get('questions', [])
        num_questions = len(questions)
        debug_log(f"Number of questions: {num_questions}", "REQUEST")

        # Post question to Slack
        success, message_ts = post_to_slack(slack_channel, slack_thread_ts, slack_message, bot_token, session_id, request_id, num_questions)

        if not success:
            log_info("Failed to post to Slack (see errors above), passing through to terminal")
            debug_log("Slack post failed, exiting", "SLACK")
            sys.exit(0)

        log_info("Successfully posted to Slack")
        debug_log("Slack post successful", "SLACK")

        # Wait for user response
        timeout = 300  # 5 minutes default
        poll_interval = 0.5
        debug_log(f"Waiting for response (timeout: {timeout}s, poll: {poll_interval}s)", "WAIT")

        response_data = wait_for_askuser_response(session_id, request_id, timeout, poll_interval, num_questions)

        if not response_data:
            log_info("No response received (timeout), passing through to terminal")
            debug_log("Timeout waiting for response, exiting", "WAIT")
            sys.exit(0)

        log_info(f"Received response: {response_data}")
        debug_log(f"Response data: {json.dumps(response_data)}", "RESPONSE")

        # Build output for Claude
        questions = tool_input.get('questions', [])
        output = build_askuser_output(response_data, questions)
        debug_log(f"Built output: {json.dumps(output)}", "OUTPUT")

        # Print JSON output to stdout for Claude to read
        print(json.dumps(output))

        # Cleanup Slack message to show selection
        if message_ts:
            try:
                from slack_sdk import WebClient
                client = WebClient(token=bot_token)
                # Format the selection nicely
                answers = output["hookSpecificOutput"]["output"]["answers"]
                selection_text = ", ".join([f"{k}: {v}" for k, v in answers.items()])
                cleanup_askuser_message(client, slack_channel, message_ts, selection_text, num_questions)
            except Exception as e:
                debug_log(f"Failed to cleanup message: {e}", "WARN")

        log_info("Successfully returned answer to Claude")
        debug_log("Hook completed successfully", "LIFECYCLE")

    except Exception as e:
        # Catch-all error handler
        log_error(f"Unexpected error in hook: {e}")
        debug_log(f"EXCEPTION: {e}", "ERROR")
        import traceback
        tb = traceback.format_exc()
        debug_log(f"Traceback:\n{tb}", "ERROR")
        traceback.print_exc(file=sys.stderr)

    finally:
        # Always clean up response file if it exists
        if response_file:
            cleanup_askuser_response_file(response_file)
        # ALWAYS exit 0 (never block Claude)
        debug_log("Hook exiting (code 0)", "LIFECYCLE")
        debug_log("=" * 80, "LIFECYCLE")
        sys.exit(0)


if __name__ == "__main__":
    main()
