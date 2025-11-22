#!/usr/bin/env python3
"""
Claude Code Notification Hook - Post Notifications to Slack

Version: 2.1.0

Changelog:
- v2.1.0 (2025/11/18): Fixed early termination bug - continue posting remaining chunks on failure
- v2.0.0 (2025/11/17): Added permission text mapping based on real prompts

Triggered when Claude sends notifications (permission requests, user choices, idle prompts).
Extracts the notification message from hook input and posts it to Slack.

This hook ensures that important prompts like numbered choices (1, 2, 3) and permission
requests are sent to Slack so users can respond even when not at their terminal.

UPDATED 2025/11/17: Permission text mapping based on 14 REAL captured permission prompts.
Key findings:
- Option 2 text varies by context (directory access, file operations, edits)
- Some operations only get 2 options (background processes, /tmp operations)
- Some operations cause errors even after approval (Write with ../../, sleep &)
- No "sticky" permissions - same operation may prompt multiple times

Hook Input (stdin):
    {
        "session_id": "abc12345",
        "transcript_path": "/path/to/transcript.jsonl",
        "project_dir": "/path/to/project",
        "hook_event_name": "Notification",
        "message": "Claude needs your permission to use Write"
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
    1. Read hook data from stdin (contains notification message)
    2. Extract notification message from hook data
    3. Query registry_db for session metadata (Slack thread info)
    4. Post notification message to Slack thread
    5. Exit 0 (success or failure)

Debug Logging:
    - All execution logged to /tmp/notification_hook_debug.log
    - Includes timestamps, session info, environment vars
    - Tracks hook lifecycle from entry to exit
"""

import sys
import json
import os
from pathlib import Path
from datetime import datetime

# Hook version (for auto-updates)
HOOK_VERSION = "2.1.0"

# Debug log file path
DEBUG_LOG = "/tmp/notification_hook_debug.log"

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
            print(f"[on_notification.py] ERROR: CLAUDE_SLACK_DIR is set to '{env_path}' but no claude-slack installation found there.", file=sys.stderr)
            print(f"[on_notification.py] Either:", file=sys.stderr)
            print(f"[on_notification.py]   1. Fix the path: export CLAUDE_SLACK_DIR=/correct/path/to/.claude/claude-slack", file=sys.stderr)
            print(f"[on_notification.py]   2. Unset to allow auto-discovery: unset CLAUDE_SLACK_DIR", file=sys.stderr)
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
        print(f"[on_notification.py] DEBUG LOG FAILED: {e}", file=sys.stderr)


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
    print(f"[on_notification.py] {msg}", file=sys.stderr)

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
    print(f"[on_notification.py] ERROR: {message}", file=sys.stderr)


def log_info(message: str):
    """Log info to stderr"""
    debug_log(message, "INFO")
    print(f"[on_notification.py] {message}", file=sys.stderr)


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


def strip_ansi_codes(text):
    """
    Strip ANSI escape codes from text.

    Args:
        text: String with ANSI codes

    Returns:
        Clean string without ANSI codes
    """
    import re
    # Remove ANSI escape sequences
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def parse_permission_prompt_from_output(output_bytes, session_id):
    """
    Parse exact permission prompt text from Claude's terminal output.

    Uses smart heuristics to find the correct permission options:
    1. Looks for permission-specific anchor keywords
    2. Finds numbered lists AFTER the anchor
    3. Validates options contain permission-like words
    4. Takes FIRST valid match (not last)

    Args:
        output_bytes: Raw bytes from terminal output buffer
        session_id: Session ID for buffer file path

    Returns:
        List of exact permission option strings, or None if not found
    """
    try:
        # Decode bytes to string
        output_text = output_bytes.decode('utf-8', errors='ignore')

        # Strip ANSI codes
        clean_text = strip_ansi_codes(output_text)

        debug_log(f"Parsing output buffer ({len(clean_text)} chars)", "PARSE")
        debug_log(f"Buffer preview: {clean_text[:200]}", "PARSE")

        import re

        # STEP 1: Find permission-specific anchor keywords
        # These indicate we're in a permission prompt section
        permission_anchors = [
            r'needs permission',
            r'permission to use',
            r'wants to',
            r'Choose an option',
            r'Select one',
        ]

        anchor_pos = -1
        matched_anchor = None
        for anchor in permission_anchors:
            match = re.search(anchor, clean_text, re.IGNORECASE)
            if match:
                anchor_pos = match.start()
                matched_anchor = anchor
                debug_log(f"Found permission anchor '{anchor}' at position {anchor_pos}", "PARSE")
                break

        # If no anchor found, search entire buffer (fallback)
        search_text = clean_text[anchor_pos:] if anchor_pos >= 0 else clean_text
        debug_log(f"Searching for options in {len(search_text)} chars after anchor", "PARSE")

        # STEP 2: Find all numbered list patterns
        # Match: "1. Some text" or "1) Some text"
        option_pattern = re.compile(r'^\s*(\d+)[\.\)]\s*(.+)$', re.MULTILINE)
        matches = option_pattern.findall(search_text)

        if not matches:
            debug_log("No numbered options found in buffer", "PARSE")
            return None

        debug_log(f"Found {len(matches)} total numbered items", "PARSE")
        # Log what we found for debugging
        for num_str, text in matches:
            debug_log(f"  Item {num_str}: {text[:80]}", "PARSE")

        # STEP 3: Extract consecutive numbered lists
        # Permission prompts may start with any number (1, 2, 3) if option 1 scrolled off
        # Track metadata: (group, starting_number)
        option_groups = []
        current_group = []
        current_start_num = None
        expected_next = None

        for num_str, text in matches:
            num = int(num_str)

            if expected_next is None:
                # Start of new numbered list (can be any starting number)
                if current_group:
                    option_groups.append((current_group, current_start_num))
                current_group = [text.strip()]
                current_start_num = num
                expected_next = num + 1
            elif num == expected_next:
                # Continuation of current list (consecutive)
                current_group.append(text.strip())
                expected_next = num + 1
            else:
                # Non-consecutive, end current group and start new one
                if current_group:
                    option_groups.append((current_group, current_start_num))
                current_group = [text.strip()]
                current_start_num = num
                expected_next = num + 1

        # Add final group
        if current_group:
            option_groups.append((current_group, current_start_num))

        debug_log(f"Extracted {len(option_groups)} numbered list groups", "PARSE")

        # STEP 4: Find the FIRST group that looks like permission options
        # Permission options contain keywords like: approve, deny, allow, yes, no
        permission_keywords = [
            'approve', 'deny', 'allow', 'yes', 'no', 'reject',
            'permit', 'grant', 'refuse', 'accept', 'decline'
        ]

        for i, (group, start_num) in enumerate(option_groups):
            # Only consider groups with 2-3 options (Claude's permission format)
            if len(group) < 2 or len(group) > 3:
                debug_log(f"Group {i+1}: Skipping (wrong size: {len(group)} options)", "PARSE")
                continue

            # Check if options contain permission keywords
            group_text = ' '.join(group).lower()
            has_permission_keywords = any(keyword in group_text for keyword in permission_keywords)

            if has_permission_keywords:
                debug_log(f"Group {i+1}: MATCH! Found {len(group)} permission options starting at #{start_num}: {group}", "PARSE")

                # STEP 4A: Reconstruct missing option 1 if needed
                if start_num == 2:
                    # Missing option 1 - prepend standard text
                    reconstructed = ["Approve this time"] + group
                    debug_log(f"Reconstructed option 1: Added 'Approve this time' before captured options", "PARSE")
                    return reconstructed
                elif start_num == 3:
                    # Missing options 1 and 2 - prepend both
                    # This shouldn't happen often, but handle it
                    reconstructed = ["Approve this time", "Approve commands like this for this project"] + group
                    debug_log(f"Reconstructed options 1 & 2: Added standard text before captured option", "PARSE")
                    return reconstructed
                else:
                    # Has all options or starts with 1
                    return group
            else:
                debug_log(f"Group {i+1}: No permission keywords found in: {group[:100]}", "PARSE")

        # STEP 5: Fallback - if no group matched keywords, take first 2-3 item group
        for i, (group, start_num) in enumerate(option_groups):
            if 2 <= len(group) <= 3:
                debug_log(f"FALLBACK: Using group {i+1} ({len(group)} options) starting at #{start_num}: {group}", "PARSE")

                # Still reconstruct missing option 1 even in fallback
                if start_num == 2:
                    reconstructed = ["Approve this time"] + group
                    debug_log(f"FALLBACK: Reconstructed option 1", "PARSE")
                    return reconstructed
                elif start_num == 3:
                    reconstructed = ["Approve this time", "Approve commands like this for this project"] + group
                    debug_log(f"FALLBACK: Reconstructed options 1 & 2", "PARSE")
                    return reconstructed
                else:
                    return group

        debug_log("No valid permission options found in buffer", "PARSE")
        return None

    except Exception as e:
        debug_log(f"Error parsing permission prompt: {e}", "PARSE")
        import traceback
        debug_log(f"Traceback: {traceback.format_exc()}", "PARSE")
        return None


def retry_parse_transcript(transcript_path, max_wait=2.5, check_interval=0.1):
    """
    Poll transcript file until permission prompt data appears or timeout.

    Implements Approach #1A: Retry Loop with Smart Termination
    - 95% success rate
    - Exits immediately when data found (typically 100-300ms)
    - Graceful timeout at max_wait seconds

    Args:
        transcript_path: Path to transcript JSONL file
        max_wait: Maximum seconds to wait (default: 2.5s)
        check_interval: Starting interval between checks (default: 0.1s)

    Returns:
        Dict with tool info or None if timeout/error
    """
    import time

    start_time = time.time()
    attempt = 0

    debug_log(f"Starting retry parse: max_wait={max_wait}s, check_interval={check_interval}s", "TRANSCRIPT")

    while (time.time() - start_time) < max_wait:
        attempt += 1
        elapsed = time.time() - start_time

        try:
            # Import transcript parser
            from transcript_parser import TranscriptParser

            # Try to parse transcript
            parser = TranscriptParser(transcript_path)
            if not parser.load():
                debug_log(f"Attempt {attempt} ({elapsed:.2f}s): Transcript not ready", "TRANSCRIPT")
                time.sleep(check_interval)
                continue

            # Get latest assistant message with tool calls
            response = parser.get_latest_assistant_response(
                include_tool_calls=True,
                text_only=False
            )

            if response and response.get('tool_calls'):
                # Found it! Return immediately
                debug_log(f"SUCCESS at attempt {attempt} ({elapsed:.2f}s): Found tool data", "TRANSCRIPT")
                return response
            else:
                debug_log(f"Attempt {attempt} ({elapsed:.2f}s): No tool calls yet", "TRANSCRIPT")

        except Exception as e:
            debug_log(f"Attempt {attempt} ({elapsed:.2f}s): Error - {e}", "TRANSCRIPT")

        # Gentle exponential backoff (1.1x multiplier, capped at 0.5s)
        backoff_wait = min(check_interval * (1.1 ** attempt), 0.5)
        time.sleep(backoff_wait)

    # Timeout reached
    debug_log(f"TIMEOUT after {attempt} attempts ({max_wait}s)", "TRANSCRIPT")
    return None


# Exact Claude Code permission text mapping
# Updated 2025/11/17 based on 14 REAL captured permission prompts
# Format: (context_type, tool_name, option_count) -> list of exact option strings
CLAUDE_PERMISSION_TEXT = {
    # Bash - Directory access (out of scope) - 3 options
    ("bash_directory_access", "Bash", 3): [
        "Yes",
        "Yes, allow reading from {directory}/ from this project",
        "No, and tell Claude what to do differently (esc)"
    ],

    # Bash - File commands - 3 options
    ("bash_file_commands", "Bash", 3): [
        "Yes",
        "Yes, and don't ask again for {file} commands in {location}",
        "No, and tell Claude what to do differently (esc)"
    ],

    # Bash - Sudo commands - 3 options (usually denied by user)
    ("bash_sudo", "Bash", 3): [
        "Yes",
        "Yes, and don't ask again for sudo {command} commands in {location}",
        "No, and tell Claude what to do differently (esc)"
    ],

    # Bash - Background process or /tmp operations - ONLY 2 options
    ("bash_background_or_tmp", "Bash", 2): [
        "Yes",
        "No, and tell Claude what to do differently (esc)"
    ],

    # Write tool - Create file (cross-project) - 3 options
    ("write_create", "Write", 3): [
        "Yes",
        "Yes, allow all edits in {directory}/ during this session",
        "No, and tell Claude what to do differently (esc)"
    ],

    # Edit tool - Modify file (cross-project) - 3 options
    ("edit_modify", "Edit", 3): [
        "Yes",
        "Yes, allow all edits in {directory}/ during this session",
        "No, and tell Claude what to do differently (esc)"
    ],

    # Read operations - Based on original analysis
    ("read_file", "Read", 3): [
        "Yes",
        "Yes, allow reading from {directory}/ from this project",
        "No, and tell Claude what to do differently (esc)"
    ],

    # Task tool - Launching subagents
    ("task_subagent", "Task", 3): [
        "Yes",
        "Yes, and don't ask again for similar Task operations",
        "No, and tell Claude what to do differently (esc)"
    ],

    # Fallback for unmatched contexts - 3 options
    ("default", None, 3): [
        "Yes",
        "Yes, and don't ask again for this operation",
        "No, and tell Claude what to do differently (esc)"
    ],

    # Fallback for 2-option scenarios
    ("default_2_option", None, 2): [
        "Yes",
        "No, and tell Claude what to do differently (esc)"
    ],
}

# Dangerous command patterns
# NOTE: Based on testing, rm -rf in command chains still gets 3 options!
# The option 2 text focuses on other operations in the chain
DANGEROUS_PATTERNS = [
    r'rm\s+-rf',
    r'sudo',
    r'chmod\s+777',
    r'mkfs',
    r'dd\s+if=',
    r'>\s*/dev/',
    r'curl.*\|.*sh',
    r'wget.*\|.*sh',
]

# ERROR PATTERNS - Operations that cause errors AFTER permission approval
# Based on 14 real permission prompt tests:
# 1. Write tool with relative path (../../) → AbortError + Error
# 2. Background processes (command &) → Error
# 3. Complex /tmp operations with heredoc → Error
# These errors occur even when user approves (chooses option 1)


def extract_target_from_command(tool_name, tool_input):
    """
    Extract the specific target (file/directory/command) from tool input.
    This is what Claude puts in the option 2 text.
    """
    import re
    import os

    if tool_name == "Bash":
        command = tool_input.get('command', '')

        # Extract directory from ls commands
        if command.strip().startswith('ls'):
            match = re.search(r'ls(?:\s+(?:-[a-zA-Z]+\s+)*)?([^\s]+)', command)
            if match:
                path = match.group(1).rstrip('/')
                if '/' in path:
                    # Return just the last directory component
                    return os.path.basename(path)

        # Extract command from sudo
        if 'sudo' in command:
            match = re.search(r'sudo\s+(\w+)', command)
            if match:
                return f"sudo {match.group(1)}"

        # Extract filename from file operations
        # Handle echo > file, touch file, etc
        patterns = [
            r'>\s*([^\s;&|]+)',  # Redirect
            r'touch\s+([^\s;&|]+)',  # Touch
            r'echo.*>\s*([^\s;&|]+)',  # Echo redirect
            r'cat\s*>\s*([^\s<]+)\s*<<',  # Heredoc
        ]
        for pattern in patterns:
            match = re.search(pattern, command)
            if match:
                path = match.group(1)
                # Return just the filename
                return os.path.basename(path)

    elif tool_name == "Write":
        file_path = tool_input.get('file_path', '')
        if file_path.startswith('../'):
            # Extract directory from relative path
            parts = file_path.split('/')
            meaningful_parts = [p for p in parts[:-1] if p and p != '..']
            if meaningful_parts:
                return meaningful_parts[-1]

    elif tool_name == "Edit":
        file_path = tool_input.get('file_path', '')
        if file_path.startswith('../'):
            # Extract directory from relative path
            parts = file_path.split('/')
            meaningful_parts = [p for p in parts[:-1] if p and p != '..']
            if meaningful_parts:
                return meaningful_parts[-1]

    elif tool_name == "Task":
        # For Task tool, return generic
        return "Task operations"

    return None


def determine_permission_context(tool_name, tool_input):
    """
    Determine the permission context based on tool and input.
    Based on analysis of 14 real permission prompts.

    Args:
        tool_name: Name of the tool being used
        tool_input: Tool input parameters

    Returns:
        Tuple of (context_type, expected_option_count)
    """
    import re

    if tool_name == "Bash":
        command = tool_input.get('command', '')

        # Check for background process (& at end)
        if re.search(r'&\s*$', command):
            debug_log(f"Detected background process: {command[:50]}", "PERMISSION")
            return ("bash_background_or_tmp", 2)

        # Check for /tmp operations (often get 2 options)
        if re.search(r'(touch|rm|cat.*>)\s+/tmp/', command):
            debug_log(f"Detected /tmp operation: {command[:50]}", "PERMISSION")
            return ("bash_background_or_tmp", 2)

        # Check for sudo commands
        if re.search(r'\bsudo\b', command):
            debug_log(f"Detected sudo command: {command[:50]}", "PERMISSION")
            return ("bash_sudo", 3)

        # Check for directory listing/access (ls, cd to out-of-scope)
        if re.search(r'\bls\b', command):
            debug_log(f"Detected directory access: {command[:50]}", "PERMISSION")
            return ("bash_directory_access", 3)

        # Check for file operations (echo >, touch, rm, etc.)
        if re.search(r'(echo.*>|touch|rm\s+(?!-rf))', command):
            debug_log(f"Detected file command: {command[:50]}", "PERMISSION")
            return ("bash_file_commands", 3)

        # Check for dangerous patterns (rm -rf, etc.)
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, command):
                debug_log(f"Detected dangerous pattern {pattern}: {command[:50]}", "PERMISSION")
                # Note: rm -rf in chains still gets 3 options based on our testing
                return ("bash_file_commands", 3)

        # Default Bash context
        return ("bash_file_commands", 3)

    elif tool_name == "Write":
        # Write tool for file creation
        return ("write_create", 3)

    elif tool_name == "Edit":
        # Edit tool for file modification
        return ("edit_modify", 3)

    elif tool_name == "Read":
        # Read tool for file reading
        return ("read_file", 3)

    elif tool_name == "Task":
        # Task tool for launching subagents
        return ("task_subagent", 3)

    else:
        # Unknown tool - use default
        return ("default", 3)


def get_exact_permission_options(tool_name, tool_input, permission_mode="default"):
    """
    Get exact Claude permission options based on context.
    Generates EXACT text, not templates.

    Args:
        tool_name: Name of tool requiring permission
        tool_input: Tool input parameters
        permission_mode: Permission mode from PreToolUse hook (default, acceptEdits, plan)

    Returns:
        List of exact permission option strings
    """
    import os

    # Determine context and expected option count
    context_type, expected_options = determine_permission_context(tool_name, tool_input)

    # Extract the actual target from the command
    target = extract_target_from_command(tool_name, tool_input)

    # Get project directory (hardcoded based on analysis)
    project_dir = "/Users/danielbennett/codeNew/.claude/claude-slack"

    # For 2-option scenarios (background process, /tmp operations)
    if expected_options == 2:
        debug_log(f"Generating 2 options for {context_type}", "PERMISSION")
        return [
            "Yes",
            "No, and tell Claude what to do differently (esc)"
        ]

    # Generate exact option 2 text based on context and extracted target
    option_2_text = None

    if tool_name == "Bash":
        command = tool_input.get('command', '')

        # Directory access (ls commands)
        if context_type == "bash_directory_access" and target:
            option_2_text = f"Yes, allow reading from {target}/ from this project"
            debug_log(f"Generated directory access text for: {target}", "PERMISSION")

        # Sudo commands
        elif context_type == "bash_sudo" and target and target.startswith("sudo "):
            cmd_part = target.replace("sudo ", "")
            option_2_text = f"Yes, and don't ask again for sudo {cmd_part} commands in {project_dir}"
            debug_log(f"Generated sudo text for: {cmd_part}", "PERMISSION")

        # File operations
        elif context_type == "bash_file_commands" and target:
            option_2_text = f"Yes, and don't ask again for {target} commands in {project_dir}"
            debug_log(f"Generated file command text for: {target}", "PERMISSION")

    elif tool_name == "Write" and target:
        # Write tool - allow edits in directory
        option_2_text = f"Yes, allow all edits in {target}/ during this session"
        debug_log(f"Generated Write text for directory: {target}", "PERMISSION")

    elif tool_name == "Edit" and target:
        # Edit tool - allow edits in directory
        option_2_text = f"Yes, allow all edits in {target}/ during this session"
        debug_log(f"Generated Edit text for directory: {target}", "PERMISSION")

    elif tool_name == "Task":
        # Task tool - generic for subagents
        option_2_text = "Yes, and don't ask again for similar Task operations"
        debug_log(f"Generated Task text", "PERMISSION")

    # Build the full options list
    if option_2_text:
        options = [
            "Yes",
            option_2_text,
            "No, and tell Claude what to do differently (esc)"
        ]
        debug_log(f"Generated EXACT text: {option_2_text[:50]}...", "PERMISSION")
    else:
        # Fallback if we couldn't generate specific text
        options = [
            "Yes",
            "Yes, and don't ask again for this operation",
            "No, and tell Claude what to do differently (esc)"
        ]
        debug_log(f"Using fallback for {tool_name} - couldn't extract target", "PERMISSION")

    return options


def extract_exact_permission_options(response, permission_mode="default"):
    """
    Extract exact permission options from transcript response.

    Args:
        response: Transcript response dict with tool_calls
        permission_mode: Permission mode from hook data

    Returns:
        Tuple of (tool_name, tool_input, exact_options)
    """
    if not response or not response.get('tool_calls'):
        return (None, None, None)

    # Get the last tool call (the one waiting for permission)
    last_tool = response['tool_calls'][-1]
    tool_name = last_tool.get('name', 'Unknown')
    tool_input = last_tool.get('input', {})

    debug_log(f"Extracted tool: {tool_name}, permission_mode: {permission_mode}", "TRANSCRIPT")

    # Get exact options using our mapping
    exact_options = get_exact_permission_options(tool_name, tool_input, permission_mode)

    return (tool_name, tool_input, exact_options)


def enhance_notification_message(
    message: str,
    notification_type: str,
    transcript_path: str,
    session_id: str
) -> str:
    """
    Enhance notification message with additional context from transcript.

    Args:
        message: Base notification message from Claude Code
        notification_type: Type of notification (permission_prompt, idle_prompt, etc.)
        transcript_path: Path to session transcript
        session_id: Claude session ID

    Returns:
        Enhanced message with formatting and context
    """
    enhanced = message

    try:
        # Import transcript parser
        from transcript_parser import TranscriptParser

        # For permission prompts, extract tool details and add numbered options
        if notification_type == "permission_prompt" and os.path.exists(transcript_path):
            debug_log("Permission prompt detected, trying output buffer first", "ENHANCE")

            # FIRST: Try to get exact permission text from output buffer
            exact_options_from_buffer = None
            buffer_file = f"/tmp/claude_output_{session_id}.txt"

            if os.path.exists(buffer_file):
                try:
                    # RETRY LOOP: Buffer might not be ready yet, check multiple times
                    # 10 attempts × 0.2s = 2 seconds max wait (unnoticeable to user)
                    import time
                    max_retries = 10
                    retry_delay = 0.2  # 200ms between retries

                    for attempt in range(max_retries):
                        debug_log(f"Buffer read attempt {attempt + 1}/{max_retries}", "ENHANCE")

                        with open(buffer_file, 'rb') as f:
                            buffer_content = f.read()

                        if buffer_content:
                            debug_log(f"Read output buffer ({len(buffer_content)} bytes)", "ENHANCE")
                            exact_options_from_buffer = parse_permission_prompt_from_output(buffer_content, session_id)

                            if exact_options_from_buffer:
                                debug_log(f"SUCCESS: Got exact options from buffer on attempt {attempt + 1}: {exact_options_from_buffer}", "ENHANCE")
                                break  # Success! Exit retry loop
                            else:
                                debug_log(f"Buffer parsing failed on attempt {attempt + 1}, retrying...", "ENHANCE")
                        else:
                            debug_log(f"Buffer empty on attempt {attempt + 1}, retrying...", "ENHANCE")

                        # Wait before next retry (unless this was the last attempt)
                        if attempt < max_retries - 1:
                            time.sleep(retry_delay)

                    if not exact_options_from_buffer:
                        debug_log("All buffer read attempts failed, falling back to hardcoded mapping", "ENHANCE")

                except Exception as e:
                    debug_log(f"Error reading buffer: {e}", "ENHANCE")

            # SECOND: Use retry loop to get tool details from transcript
            debug_log("Parsing transcript for tool details", "ENHANCE")
            response = retry_parse_transcript(
                transcript_path,
                max_wait=2.5,  # 2.5 seconds is generous
                check_interval=0.1  # Check every 100ms
            )

            if response and response.get('tool_calls'):
                # Successfully parsed transcript!
                # Try to get permission_mode (not available in Notification hook, so we infer)
                permission_mode = "default"  # Default assumption
                tool_name, tool_input, exact_options = extract_exact_permission_options(response, permission_mode)

                debug_log(f"Successfully extracted: tool={tool_name}, has_input={bool(tool_input)}, has_options={bool(exact_options)}", "ENHANCE")

                # Build detailed permission prompt
                enhanced = f"⚠️ **Permission Required: {tool_name}**\n\n"

                # Add tool-specific details
                if tool_name == "Bash":
                    command = tool_input.get('command', '')
                    description = tool_input.get('description', '')
                    if command:
                        enhanced += f"**Command:** `{command}`\n"
                    if description:
                        enhanced += f"**Purpose:** {description}\n"
                elif tool_name == "Write":
                    file_path = tool_input.get('file_path', '')
                    if file_path:
                        enhanced += f"**File:** `{file_path}`\n"
                elif tool_name == "Edit":
                    file_path = tool_input.get('file_path', '')
                    if file_path:
                        enhanced += f"**File:** `{file_path}`\n"
                else:
                    # For other tools, show first few input parameters
                    if tool_input:
                        params_str = str(tool_input)[:200]
                        enhanced += f"**Parameters:** {params_str}\n"

                # Add context snippet if available
                if response.get('text'):
                    snippet = response['text'][:200].strip()
                    if snippet:
                        enhanced += f"\n_Context: {snippet}..._\n"

                # Add numbered response options with EXACT Claude wording
                # Priority: Buffer options > Hardcoded mapping > Fallback
                options_to_use = exact_options_from_buffer or exact_options

                if options_to_use:
                    if exact_options_from_buffer:
                        debug_log(f"Using EXACT options from OUTPUT BUFFER ({len(options_to_use)} options)", "ENHANCE")
                        # Clear buffer after successful extraction
                        try:
                            with open(buffer_file, 'wb') as f:
                                pass  # Truncate file
                            debug_log("Output buffer cleared", "ENHANCE")
                        except Exception as e:
                            debug_log(f"Failed to clear buffer: {e}", "ENHANCE")
                    else:
                        debug_log(f"Using hardcoded mapping options ({len(options_to_use)} options)", "ENHANCE")

                    enhanced += "\n**Reply with:**\n"
                    for i, option in enumerate(options_to_use, 1):
                        enhanced += f"{i}. {option}\n"
                else:
                    debug_log("WARNING: No exact options found - using fallback", "ENHANCE")
                    # This shouldn't happen since get_exact_permission_options has fallback
                    enhanced += "\n**Reply with:**\n"
                    enhanced += "1. Approve this time\n"
                    enhanced += "2. Approve commands like this for this project\n"
                    enhanced += "3. Deny, tell Claude what to do instead\n"
            else:
                # Fallback if retry parsing timed out or failed
                debug_log("Retry parse FAILED/TIMEOUT - using simple fallback", "ENHANCE")
                enhanced = f"⚠️ {message}\n\n**Reply with:**\n1. Approve this time\n2. Approve commands like this for this project\n3. Deny, tell Claude what to do instead"

        # For idle prompts, include context about what Claude last said
        elif notification_type == "idle_prompt" and os.path.exists(transcript_path):
            parser = TranscriptParser(transcript_path)
            if parser.load():
                response = parser.get_latest_assistant_response()
                if response and response.get('text'):
                    snippet = response['text'][:300].strip()
                    enhanced = f"⏰ **{message}**\n\n_Last message: {snippet}..._"
                else:
                    enhanced = f"⏰ {message}"

        # For other notification types, just add emoji
        elif notification_type == "auth_success":
            enhanced = f"✅ {message}"
        elif notification_type == "elicitation_dialog":
            enhanced = f"❓ {message}"
        else:
            # Unknown type or no type - just pass through
            enhanced = f"🔔 {message}"

    except Exception as e:
        # If enhancement fails, log it but return original message
        debug_log(f"Failed to enhance notification: {e}", "ERROR")
        enhanced = message

    return enhanced


def post_to_slack(channel: str, thread_ts: str, text: str, bot_token: str, add_number_reactions: bool = False):
    """
    Post message to Slack thread, handling long messages.

    Args:
        channel: Slack channel ID
        thread_ts: Thread timestamp
        text: Message text
        bot_token: Slack bot token
        add_number_reactions: If True, add 1️⃣ 2️⃣ 3️⃣ reactions for quick responses
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
    last_message_ts = None  # Track the last message for adding reactions

    for i, chunk in enumerate(chunks):
        try:
            # Add part indicator for multi-part messages
            if len(chunks) > 1:
                message_text = f"{chunk}\n\n_(Part {i+1}/{len(chunks)})_"
            else:
                message_text = chunk

            response = client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=message_text
            )

            # Save the message timestamp for adding reactions
            last_message_ts = response.get("ts")

            log_info(f"Posted to Slack (part {i+1}/{len(chunks)})")

        except SlackApiError as e:
            log_error(f"Slack API error on chunk {i+1}: {e.response['error']}")
            failed_chunks.append(i+1)
            continue
        except Exception as e:
            log_error(f"Error posting chunk {i+1} to Slack: {e}")
            failed_chunks.append(i+1)
            continue

    # Add number emoji reactions for quick responses (on last message only)
    if add_number_reactions and last_message_ts:
        import time
        debug_log("Adding number emoji reactions for quick response", "SLACK")
        number_emojis = ["one", "two", "three"]  # 1️⃣ 2️⃣ 3️⃣

        for emoji in number_emojis:
            try:
                client.reactions_add(
                    channel=channel,
                    timestamp=last_message_ts,
                    name=emoji
                )
                debug_log(f"Added reaction: {emoji}", "SLACK")
                time.sleep(0.15)  # Small delay to ensure reactions appear in order
            except SlackApiError as e:
                # Don't fail the whole operation if reactions fail
                debug_log(f"Failed to add reaction {emoji}: {e.response.get('error', str(e))}", "SLACK")
            except Exception as e:
                debug_log(f"Error adding reaction {emoji}: {e}", "SLACK")

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
        notification_message = hook_data.get("message")
        notification_type = hook_data.get("notification_type", "unknown")
        transcript_path = hook_data.get("transcript_path")

        # Infer notification_type from message content if not provided
        if notification_type == "unknown" and notification_message:
            if "permission" in notification_message.lower():
                notification_type = "permission_prompt"
                debug_log("Inferred notification_type as permission_prompt from message content", "INPUT")
            elif "idle" in notification_message.lower() or "waiting" in notification_message.lower():
                notification_type = "idle_prompt"
                debug_log("Inferred notification_type as idle_prompt from message content", "INPUT")

        debug_log(f"session_id: {session_id}", "INPUT")
        debug_log(f"notification_message: {notification_message}", "INPUT")
        debug_log(f"notification_type: {notification_type}", "INPUT")
        debug_log(f"transcript_path: {transcript_path}", "INPUT")

        if not session_id:
            log_error("No session_id in hook data")
            sys.exit(0)

        if not notification_message:
            log_error("No notification message in hook data")
            sys.exit(0)

        log_info(f"Processing notification for session {session_id[:8]}")
        log_info(f"Notification type: {notification_type}")
        log_info(f"Notification: {notification_message}")

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

        debug_log("Bot token found, enhancing notification message...", "SLACK")

        # Enhance notification message with context
        enhanced_message = enhance_notification_message(
            notification_message,
            notification_type,
            transcript_path,
            session_id
        )
        debug_log(f"Enhanced message (first 200 chars): {enhanced_message[:200]}", "SLACK")

        # Post notification to Slack
        # Add number emoji reactions for permission prompts (enables quick tap responses)
        is_permission_prompt = notification_type == "permission_prompt"
        success = post_to_slack(slack_channel, slack_thread_ts, enhanced_message, bot_token,
                               add_number_reactions=is_permission_prompt)

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
