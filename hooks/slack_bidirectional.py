#!/usr/bin/env python3
"""
Claude Code Hook - Bidirectional Slack Integration

This hook serves two purposes:
1. Send Claude Code messages/events to Slack (automatic via hooks)
2. Check for and display Slack responses (manual via /check command)

Hook Events:
    - UserPromptSubmit: User sends a prompt to Claude
    - Stop: Claude finishes processing
    - PostToolUse: Claude executes a tool (optional, can be noisy)
    - CHECK_SLACK: Special event to read Slack responses

Environment Variables:
    SLACK_BOT_TOKEN - Bot User OAuth Token (required for sending)
    SLACK_CHANNEL - Default channel (default: #btcbot-claude)
    HOOK_EVENT_TYPE - Set by hook configuration to identify event type
"""

import sys
import json
import os
from pathlib import Path

# Path to Slack response file (written by slack_listener.py)
PROJECT_DIR = Path(__file__).parent.parent.parent
RESPONSE_FILE = PROJECT_DIR / "slack_response.txt"


def check_slack_response():
    """
    Check for Slack responses and display them
    Called when user runs /check command
    """
    if not RESPONSE_FILE.exists():
        print("\n📱 No Slack responses yet\n", file=sys.stderr)
        return

    response = RESPONSE_FILE.read_text().strip()

    if not response:
        print("\n📱 No Slack responses yet\n", file=sys.stderr)
        return

    # Display response prominently
    print("\n" + "=" * 60, file=sys.stderr)
    print("📱 SLACK RESPONSE:", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"\n{response}\n", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("\nYou can now type this response in Claude Code (or just press Enter to accept it)\n", file=sys.stderr)

    # Clear the file after displaying
    RESPONSE_FILE.unlink()


def send_to_slack(message_text, event_type):
    """
    Send a message to Slack using the Slack Web API
    """
    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
    except ImportError:
        print("⚠️  Warning: slack_sdk not installed. Run: pip install slack-sdk", file=sys.stderr)
        return

    # Get configuration
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    if not bot_token:
        print("⚠️  Warning: SLACK_BOT_TOKEN not set. Skipping Slack notification.", file=sys.stderr)
        return

    channel = os.environ.get("SLACK_CHANNEL", "#btcbot-claude")

    # Initialize Slack client
    client = WebClient(token=bot_token)

    # Format message based on event type
    emoji_map = {
        "UserPromptSubmit": "📝",
        "Stop": "✅",
        "PostToolUse": "⚙️",
        "Bash": "💻",
        "Write": "📄",
        "Edit": "✏️",
        "Read": "📖",
    }

    emoji = emoji_map.get(event_type, "🤖")

    # Truncate long messages
    if len(message_text) > 1000:
        message_text = message_text[:997] + "..."

    # Create Slack message
    slack_message = f"{emoji} *{event_type}*\n```{message_text}```\n\n_Reply with: 1, 2, 3, or custom text_"

    # Send to Slack
    try:
        response = client.chat_postMessage(
            channel=channel,
            text=slack_message,
            mrkdwn=True
        )
        print(f"✅ Sent to Slack: {channel}", file=sys.stderr)
    except SlackApiError as e:
        print(f"⚠️  Slack API error: {e.response['error']}", file=sys.stderr)
    except Exception as e:
        print(f"⚠️  Error sending to Slack: {e}", file=sys.stderr)


def main():
    """Main hook entry point"""
    # Read hook event data from stdin
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        input_data = {}

    # Get event type from environment
    event_type = os.environ.get("HOOK_EVENT_TYPE", "Event")

    # Special case: /check command to read Slack responses
    if event_type == "CHECK_SLACK":
        check_slack_response()
        sys.exit(0)

    # Extract relevant information from hook data
    project_dir = input_data.get("project_dir", "")
    project_name = project_dir.split("/")[-1] if project_dir else "unknown"
    session_id = input_data.get("session_id", "unknown")[:8]

    # Format message based on event type
    if event_type == "UserPromptSubmit":
        # User sent a prompt to Claude
        user_prompt = input_data.get("prompt", "")
        message_text = f"Session: {session_id}\nProject: {project_name}\n\nPrompt: {user_prompt[:500]}"

    elif event_type == "Stop":
        # Claude finished processing
        message_text = f"Session: {session_id}\nProject: {project_name}\n\nClaude has finished processing."

    elif event_type == "PostToolUse":
        # Claude executed a tool (can be noisy, optional)
        tool_name = input_data.get("tool_name", "Unknown")
        # Don't send full parameters, just tool name
        message_text = f"Session: {session_id}\nTool: {tool_name}"

    else:
        # Generic event
        message_text = json.dumps(input_data, indent=2)[:500]

    # Send to Slack
    send_to_slack(message_text, event_type)

    # Always exit 0 (don't block Claude even if Slack fails)
    sys.exit(0)


if __name__ == "__main__":
    main()
