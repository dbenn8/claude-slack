#!/usr/bin/env python3
"""
Claude Code PermissionRequest Hook - Handle Permission Prompts via Slack

Version: 1.0.0

This hook intercepts permission prompts and allows users to respond via Slack
instead of the terminal. It posts a permission request to Slack with Allow/Deny
buttons and waits for the user's response.

Flow:
1. Claude requests permission for a tool
2. This hook posts to Slack with Allow/Deny buttons
3. User clicks a button in Slack
4. Slack listener writes response to a file
5. This hook reads response and returns decision to Claude

Input (from Claude Code):
{
    "session_id": "...",
    "hook_event_name": "PermissionRequest",
    "tool_name": "Bash",
    "tool_input": {"command": "...", "description": "..."},
    "permission_suggestions": [...] // optional - present for 3-option prompts
}

Output (to Claude Code):
{
    "hookSpecificOutput": {
        "hookEventName": "PermissionRequest",
        "decision": {
            "behavior": "allow" | "deny",
            "message": "..." // for deny
        }
    }
}
"""

import json
import sys
import os
import time
import re
from pathlib import Path
from datetime import datetime

# Configuration
RESPONSE_DIR = Path.home() / ".claude" / "slack" / "permission_responses"
LOG_FILE = Path.home() / ".claude" / "slack" / "logs" / "permission_request_hook.log"
LOG_DIR = Path.home() / ".claude" / "slack" / "logs"
POLL_INTERVAL = 0.5  # seconds
DEFAULT_TIMEOUT = 300  # 5 minutes

# Ensure directories exist
RESPONSE_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def log(msg: str, level: str = "INFO"):
    """Log message to file."""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(LOG_FILE, "a") as f:
            f.write(f"[{timestamp}] [{level}] {msg}\n")
    except Exception:
        pass


def strip_ansi_codes(text: str) -> str:
    """Strip ANSI escape codes from text."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def get_terminal_prompt(session_id: str) -> str:
    """
    Read the terminal output buffer to get the actual permission prompt text.

    The output buffer file is written by claude_wrapper and contains the raw
    terminal output including the permission prompt with options.

    Returns:
        The full permission prompt text, or None if not available
    """
    # First, try the direct path based on session_id
    buffer_file = LOG_DIR / f"claude_output_{session_id}.txt"

    # If not found, try to look up buffer_file_path from registry
    if not buffer_file.exists():
        log(f"Direct buffer path not found: {buffer_file}", "DEBUG")
        try:
            # Find claude-slack directory and import registry
            claude_slack_dir = Path.home() / ".claude" / "claude-slack"
            if (claude_slack_dir / "core").exists():
                import sys
                sys.path.insert(0, str(claude_slack_dir / "core"))
                from registry_db import RegistryDatabase

                db_path = Path.home() / ".claude" / "slack" / "registry.db"
                if db_path.exists():
                    db = RegistryDatabase(str(db_path))
                    session = db.get_session(session_id)
                    if session and session.get("buffer_file_path"):
                        registry_buffer = Path(session["buffer_file_path"])
                        if registry_buffer.exists():
                            buffer_file = registry_buffer
                            log(f"Found buffer path from registry: {buffer_file}", "DEBUG")
                        else:
                            log(f"Registry buffer path doesn't exist: {registry_buffer}", "DEBUG")
                    else:
                        log(f"No buffer_file_path in registry for session {session_id[:8]}", "DEBUG")
        except Exception as e:
            log(f"Error looking up buffer from registry: {e}", "DEBUG")

    if not buffer_file.exists():
        log(f"Output buffer not found: {buffer_file}", "DEBUG")
        return None

    try:
        # Read buffer with retries (it may still be writing)
        max_retries = 5
        for attempt in range(max_retries):
            with open(buffer_file, 'rb') as f:
                content = f.read()

            if content:
                text = content.decode('utf-8', errors='ignore')
                clean_text = strip_ansi_codes(text)

                # Look for permission prompt markers
                # Claude Code prompts contain numbered options
                if re.search(r'^\s*1[\.\)]\s+', clean_text, re.MULTILINE):
                    log(f"Found terminal prompt ({len(clean_text)} chars)", "DEBUG")
                    return clean_text

            time.sleep(0.1)

        log("Buffer exists but no permission prompt found", "DEBUG")
        return None

    except Exception as e:
        log(f"Error reading output buffer: {e}", "ERROR")
        return None


def parse_permission_options(terminal_text: str) -> list:
    """
    Parse numbered permission options from terminal text.

    Returns:
        List of option strings like ["Yes", "Yes, allow...", "No, and tell..."]
    """
    if not terminal_text:
        return None

    try:
        # Find all numbered options (1. xxx, 2. xxx, etc)
        option_pattern = re.compile(r'^\s*(\d+)[\.\)]\s*(.+)$', re.MULTILINE)
        matches = option_pattern.findall(terminal_text)

        if not matches:
            return None

        # Extract consecutive numbered options
        options = []
        expected_num = 1

        for num_str, text in matches:
            num = int(num_str)
            if num == expected_num:
                options.append(text.strip())
                expected_num += 1
            elif num < expected_num:
                continue  # Skip duplicates
            else:
                break  # Gap in numbering, stop

        # Only return if we have 2-3 options (typical permission prompt)
        if 2 <= len(options) <= 3:
            log(f"Parsed {len(options)} permission options", "DEBUG")
            return options

        return None

    except Exception as e:
        log(f"Error parsing options: {e}", "ERROR")
        return None


def get_response_file(session_id: str, request_id: str) -> Path:
    """Get path to response file for a permission request."""
    return RESPONSE_DIR / f"{session_id}_{request_id}.json"


def cleanup_response_file(response_file: Path):
    """Remove response file after reading."""
    try:
        if response_file.exists():
            response_file.unlink()
    except Exception as e:
        log(f"Failed to cleanup response file: {e}", "WARN")


def post_to_slack(session_id: str, request_id: str, tool_name: str,
                  tool_input: dict, has_always_option: bool,
                  terminal_prompt: str = None, permission_options: list = None) -> bool:
    """Post permission request to Slack with buttons.

    Args:
        session_id: Claude session ID
        request_id: Unique request identifier
        tool_name: Name of tool requiring permission
        tool_input: Tool input parameters
        has_always_option: Whether "Allow Always" option is available
        terminal_prompt: Full terminal prompt text (if available)
        permission_options: Parsed permission options from terminal
    """
    try:
        # Find claude-slack directory
        claude_slack_dir = Path.home() / ".claude" / "claude-slack"
        if not (claude_slack_dir / "core").exists():
            log(f"claude-slack not found at {claude_slack_dir}", "ERROR")
            return False

        sys.path.insert(0, str(claude_slack_dir / "core"))

        from registry_db import RegistryDatabase
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError

        # Load environment
        env_path = claude_slack_dir / ".env"
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        os.environ.setdefault(key, value)

        bot_token = os.environ.get("SLACK_BOT_TOKEN")
        if not bot_token:
            log("SLACK_BOT_TOKEN not found", "ERROR")
            return False

        # Get session info from registry
        db_path = Path.home() / ".claude" / "slack" / "registry.db"
        if not db_path.exists():
            log(f"Registry not found: {db_path}", "ERROR")
            return False

        db = RegistryDatabase(str(db_path))
        session = db.get_session(session_id)

        if not session:
            log(f"Session not found: {session_id}", "ERROR")
            return False

        channel = session.get("channel")
        if not channel:
            log(f"No channel for session: {session_id}", "ERROR")
            return False

        # Build message
        client = WebClient(token=bot_token)

        # Build the message content
        # Priority: terminal_prompt > formatted tool_input
        if terminal_prompt:
            # Use the actual terminal prompt - truncate if needed
            prompt_text = terminal_prompt[:2500]  # Leave room for buttons
            if len(terminal_prompt) > 2500:
                prompt_text += "\n...(truncated)"
            details = f"```\n{prompt_text}\n```"
            log("Using terminal prompt text", "DEBUG")
        else:
            # Fallback to formatted tool input
            if tool_name == "Bash":
                command = tool_input.get("command", "")
                description = tool_input.get("description", "")
                details = f"*Command:* `{command[:200]}{'...' if len(command) > 200 else ''}`"
                if description:
                    details += f"\n*Purpose:* {description}"
            elif tool_name in ("Read", "Write", "Edit"):
                file_path = tool_input.get("file_path", "")
                details = f"*File:* `{file_path}`"
            else:
                details = f"*Input:* ```{json.dumps(tool_input, indent=2)[:500]}```"
            log("Using formatted tool input (no terminal prompt)", "DEBUG")

        # Build blocks with buttons
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"⚠️ *Permission Required: {tool_name}*\n\n{details}"
                }
            }
        ]

        # Build button elements based on permission_options or defaults
        button_elements = []

        if permission_options and len(permission_options) >= 2:
            # Use actual permission options from terminal
            log(f"Using {len(permission_options)} parsed options for buttons", "DEBUG")

            # Option 1: Always "Allow" (green button)
            button_elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": f"1. {permission_options[0][:30]}"},
                "style": "primary",
                "action_id": "permission_allow",
                "value": json.dumps({
                    "session_id": session_id,
                    "request_id": request_id,
                    "decision": "allow"
                })
            })

            # Option 2: "Allow Always" if 3 options, otherwise it's deny
            if len(permission_options) >= 3:
                # 3-option prompt: option 2 is "Allow Always"
                button_elements.append({
                    "type": "button",
                    "text": {"type": "plain_text", "text": f"2. {permission_options[1][:30]}..."},
                    "action_id": "permission_allow_always",
                    "value": json.dumps({
                        "session_id": session_id,
                        "request_id": request_id,
                        "decision": "allow_always"
                    })
                })
                # Option 3: Deny (red button)
                button_elements.append({
                    "type": "button",
                    "text": {"type": "plain_text", "text": f"3. Deny"},
                    "style": "danger",
                    "action_id": "permission_deny",
                    "value": json.dumps({
                        "session_id": session_id,
                        "request_id": request_id,
                        "decision": "deny"
                    })
                })
            else:
                # 2-option prompt: option 2 is deny
                button_elements.append({
                    "type": "button",
                    "text": {"type": "plain_text", "text": f"2. Deny"},
                    "style": "danger",
                    "action_id": "permission_deny",
                    "value": json.dumps({
                        "session_id": session_id,
                        "request_id": request_id,
                        "decision": "deny"
                    })
                })
        else:
            # Fallback: use permission_suggestions to determine button layout
            # If permission_suggestions is present, it's a 3-option prompt (Yes, Yes always, No)
            # Otherwise it's a 2-option prompt (Yes, No)
            if has_always_option:
                log("Using 3-button layout (Yes, Yes always, No)", "DEBUG")
                button_elements = [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "1. Yes"},
                        "style": "primary",
                        "action_id": "permission_allow",
                        "value": json.dumps({
                            "session_id": session_id,
                            "request_id": request_id,
                            "decision": "allow"
                        })
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "2. Yes, always"},
                        "action_id": "permission_allow_always",
                        "value": json.dumps({
                            "session_id": session_id,
                            "request_id": request_id,
                            "decision": "allow_always"
                        })
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "3. No"},
                        "style": "danger",
                        "action_id": "permission_deny",
                        "value": json.dumps({
                            "session_id": session_id,
                            "request_id": request_id,
                            "decision": "deny"
                        })
                    }
                ]
            else:
                log("Using 2-button layout (Yes, No)", "DEBUG")
                button_elements = [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "1. Yes"},
                        "style": "primary",
                        "action_id": "permission_allow",
                        "value": json.dumps({
                            "session_id": session_id,
                            "request_id": request_id,
                            "decision": "allow"
                        })
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "2. No"},
                        "style": "danger",
                        "action_id": "permission_deny",
                        "value": json.dumps({
                            "session_id": session_id,
                            "request_id": request_id,
                            "decision": "deny"
                        })
                    }
                ]

        blocks.append({
            "type": "actions",
            "block_id": f"permission_{request_id}",
            "elements": button_elements
        })

        # Post message
        response = client.chat_postMessage(
            channel=channel,
            text=f"⚠️ Permission Required: {tool_name}",
            blocks=blocks
        )

        # Store message ts for later update/deletion
        message_ts = response.get("ts")
        if message_ts:
            db.update_session(session_id, {"permission_message_ts": message_ts})

        log(f"Posted permission request to Slack: {channel}, ts={message_ts}")
        return True

    except Exception as e:
        log(f"Failed to post to Slack: {e}", "ERROR")
        import traceback
        log(traceback.format_exc(), "ERROR")
        return False


def wait_for_response(session_id: str, request_id: str, timeout: float) -> dict:
    """Wait for response from Slack listener."""
    response_file = get_response_file(session_id, request_id)
    start_time = time.time()

    log(f"Waiting for response: {response_file} (timeout: {timeout}s)")

    while time.time() - start_time < timeout:
        if response_file.exists():
            try:
                with open(response_file) as f:
                    response = json.load(f)
                log(f"Got response: {response}")
                cleanup_response_file(response_file)
                return response
            except Exception as e:
                log(f"Error reading response: {e}", "ERROR")
                cleanup_response_file(response_file)
                return None
        time.sleep(POLL_INTERVAL)

    log(f"Timeout waiting for response after {timeout}s")
    return None


def build_output(behavior: str, message: str = None) -> dict:
    """Build the hook output JSON."""
    decision = {"behavior": behavior}
    if message:
        decision["message"] = message

    return {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": decision
        }
    }


def main():
    log("=" * 60)
    log("PermissionRequest hook started")

    # Read input
    try:
        input_data = json.load(sys.stdin)
        log(f"Input: {json.dumps(input_data)[:500]}")
    except Exception as e:
        log(f"Failed to read input: {e}", "ERROR")
        sys.exit(0)  # Pass through on error

    session_id = input_data.get("session_id", "")
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})
    permission_suggestions = input_data.get("permission_suggestions")

    # Generate unique request ID
    request_id = f"{int(time.time() * 1000)}"

    log(f"Session: {session_id[:8]}, Tool: {tool_name}, Request: {request_id}")

    # Check if we have "allow always" option
    has_always_option = permission_suggestions is not None
    log(f"Has 'Allow Always' option: {has_always_option}")

    # Try to get the actual terminal prompt
    terminal_prompt = get_terminal_prompt(session_id)
    permission_options = None
    if terminal_prompt:
        permission_options = parse_permission_options(terminal_prompt)
        log(f"Parsed {len(permission_options) if permission_options else 0} options from terminal")
    else:
        log("No terminal prompt available, using tool input only")

    # Post to Slack
    if not post_to_slack(session_id, request_id, tool_name, tool_input, has_always_option,
                         terminal_prompt, permission_options):
        log("Failed to post to Slack, passing through to terminal")
        sys.exit(0)  # Pass through to normal terminal prompt

    # Wait for response
    timeout = float(os.environ.get("PERMISSION_TIMEOUT", DEFAULT_TIMEOUT))
    response = wait_for_response(session_id, request_id, timeout)

    if not response:
        log("No response, passing through to terminal")
        # TODO: Delete the Slack message since we're passing through
        sys.exit(0)

    # Process response
    decision = response.get("decision", "")

    if decision == "allow" or decision == "allow_always":
        log(f"Returning: allow")
        output = build_output("allow")
        print(json.dumps(output))
        sys.exit(0)
    elif decision == "deny":
        reason = response.get("reason", "User denied permission via Slack")
        log(f"Returning: deny - {reason}")
        output = build_output("deny", reason)
        print(json.dumps(output))
        sys.exit(0)
    else:
        log(f"Unknown decision: {decision}, passing through")
        sys.exit(0)


if __name__ == "__main__":
    main()
